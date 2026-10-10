"""Best-effort brand color reader. It fetches a public web page and its stylesheets and reports the colors it found, with the
reason for each, so a person can pick. It only fills in the colors by itself when the site names them (a theme-color tag or a
CSS variable such as --primary); otherwise it just suggests, because the most-used colors on a page are often not the brand's."""
import colorsys
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ResearchPortalSetupKit/0.1)"}
HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
RGB = re.compile(r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})")
THEME = re.compile(r"""<meta[^>]+name=["']theme-color["'][^>]*content=["']([^"']+)["']""", re.I)
THEME2 = re.compile(r"""<meta[^>]+content=["']([^"']+)["'][^>]*name=["']theme-color["']""", re.I)
SHEETS = re.compile(r"""<link[^>]+rel=["']stylesheet["'][^>]*href=["']([^"']+)["']""", re.I)
SHEETS2 = re.compile(r"""<link[^>]+href=["']([^"']+)["'][^>]*rel=["']stylesheet["']""", re.I)
VARS = re.compile(r"--([\w-]*(?:primary|brand|accent|secondary|highlight|theme)[\w-]*)\s*:\s*([^;}{]{3,60})", re.I)
MAX_BYTES = 1_500_000
# colors that belong to social buttons, widgets or framework defaults, not to the organization
NOT_BRAND = {"#1877F2", "#3B5998", "#1DA1F2", "#0A66C2", "#0077B5", "#FF0000", "#CD201F", "#E4405F", "#25D366", "#0D6EFD",
              "#007BFF", "#0073AA", "#21759B", "#4285F4", "#EA4335", "#34A853", "#FBBC05", "#DC3545", "#28A745", "#FFC107",
              # WordPress built-in palette and admin blues: present in the code of almost every WordPress site, usually unused
              "#CF2E2E", "#FF6900", "#FCB900", "#7BDCB5", "#00D084", "#8ED1FC", "#0693E3", "#ABB8C3", "#F78DA7", "#9B51E0",
              "#007CBA", "#005A87", "#3858E9"}


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=10, stream=True)
    r.raise_for_status()
    data = r.raw.read(MAX_BYTES, decode_content=True)
    return data.decode(r.encoding or "utf-8", errors="replace")


def _get_bytes(url, limit=2_000_000):
    r = requests.get(url, headers=HEADERS, timeout=10, stream=True)
    r.raise_for_status()
    return r.raw.read(limit, decode_content=True)


IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
ATTR_SRC = re.compile(r"""\bsrc=["']([^"']+)["']""", re.I)
TOUCH = re.compile(r"""<link[^>]+rel=["']apple-touch-icon[^"']*["'][^>]*href=["']([^"']+)["']""", re.I)
SKIP_LOGO = re.compile(r"white|footer|partner|sponsor|funder|czi|facebook|twitter|linkedin|instagram|youtube", re.I)


def _site_images(html, base):
    """The site's own logo images (first <img> tags that look like logos) plus its touch icon, as full URLs."""
    out = []
    for tag in IMG_TAG.findall(html):
        m = ATTR_SRC.search(tag)
        if m and re.search(r"logo", tag, re.I) and not SKIP_LOGO.search(m.group(1)) and m.group(1) not in out:
            out.append(m.group(1))
    out = out[:2]
    m = TOUCH.search(html)
    if m:
        out.append(m.group(1))
    return [urljoin(base, u) for u in out if not u.startswith("data:")]


def _hex6(h):
    h = h.lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h[:6].upper()


def _find_color(text):
    """The first color in a CSS value, as #RRGGBB, or None."""
    m = HEX.search(text)
    if m:
        return _hex6(m.group(1))
    m = RGB.search(text)
    if m:
        r, g, b = (min(255, int(x)) for x in m.groups())
        return f"#{r:02X}{g:02X}{b:02X}"
    return None


def _hls(col):
    r, g, b = (int(col[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, l, s


def _close_to(a, b, limit):
    return sum(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (1, 3, 5)) < limit


def _hue_gap(a, b):
    d = abs(_hls(a)[0] - _hls(b)[0])
    return min(d, 360 - d)


def _brandish(col):
    _h, l, s = _hls(col)
    return s >= 0.25 and 0.12 <= l <= 0.9 and col not in NOT_BRAND


def _close(a, b):
    return sum(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (1, 3, 5)) < 24


def pull_colors(url):
    """Returns {primary, accent, candidates: [{color, why}], message}. Only http(s) addresses are read."""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if not urlparse(url).hostname:
        return {"message": "That does not look like a web address.", "candidates": []}
    try:
        html = _get(url)
    except Exception as e:
        return {"message": f"Could not read that page ({type(e).__name__}). Set the colors by hand below.", "candidates": []}
    texts = [html]
    links = (SHEETS.findall(html) + SHEETS2.findall(html))[:8]
    for href in dict.fromkeys(links):
        try:
            texts.append(_get(urljoin(url, href)))
        except Exception:
            pass

    named = []   # (color, why) the site itself names
    theme = THEME.search(html) or THEME2.search(html)
    if theme and _find_color(theme.group(1)):
        named.append((_find_color(theme.group(1)), "the page's theme color"))
    for t in texts:
        for name, val in VARS.findall(t):
            col = _find_color(val)
            if col and col not in NOT_BRAND:
                named.append((col, f"named --{name} in the site's styling"))
    counts = Counter()
    for t in texts:
        for m in HEX.findall(t):
            counts[_hex6(m)] += 1
        for m in RGB.findall(t):
            counts["#%02X%02X%02X" % tuple(min(255, int(x)) for x in m)] += 1

    # 1) The most reliable source: the site's own logo and icon images (what a visitor recognizes as the brand).
    from_logo = []
    for img_url in _site_images(html, url):
        try:
            data = _get_bytes(img_url)
        except Exception:
            continue
        ext = "svg" if (img_url.lower().split("?")[0].endswith(".svg") or b"<svg" in data[:4096].lower()) else "img"
        label = img_url.split("?")[0].rsplit("/", 1)[-1][:40]
        for c in logo_colors(data, ext):
            if not any(_close(c["color"], x["color"]) for x in from_logo):
                from_logo.append({**c, "why": f"from the site's logo ({label})"})
    colored = [c for c in from_logo if not c.get("neutral")]
    if colored:
        primary = colored[0]["color"]
        # an accent has to look clearly different from the primary (not just a lighter shade of it)
        accent = next((c["color"] for c in colored[1:]
                      if _hue_gap(c["color"], primary) > 40 and 0.2 <= _hls(c["color"])[1] <= 0.7 and _hls(c["color"])[2] >= 0.35), None)
        return {"primary": primary, "accent": accent, "candidates": from_logo[:5],
                "message": ("Colors read from the site's logo. Check them against the brand guide; "
                            "you can also type the exact hex codes into Primary and Accent below.")}

    # 2) Otherwise the page's styling, which is much less reliable: shown as suggestions only, never filled in.
    candidates = []

    def add(col, why):
        if _brandish(col) and not any(_close(col, c["color"]) for c in candidates):
            candidates.append({"color": col, "why": why})
    for col, n in counts.most_common(80):
        add(col, f"used {n} times in the page's code")
    for col, why in named:
        add(col, why)
    candidates = candidates[:4]
    if not candidates:
        return {"message": "No clear brand colors found on that page. Type your hex codes into Primary and Accent below.",
                "candidates": []}
    return {"candidates": candidates,
            "message": ("Could not find a logo to read, so nothing was filled in. These colors come from the page's code and may be "
                        "from plugins or hidden elements, not the brand. Pick one, or type your hex codes into Primary and Accent below.")}


def logo_colors(data, ext):
    """Colors in a logo (PNG or SVG), most prominent first: the most reliable hint of a brand's colors. Near-white is ignored;
    black and grays are listed only after the real colors."""
    found = Counter()
    if ext == "svg":
        text = data.decode("utf-8", errors="replace")
        for m in HEX.findall(text):
            found[_hex6(m)] += 1
        for m in RGB.findall(text):
            found["#%02X%02X%02X" % tuple(min(255, int(x)) for x in m)] += 1
    else:
        import io

        from PIL import Image
        try:
            img = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:
            return []
        img.thumbnail((120, 120))
        sums = {}
        for r, g, b, a in img.getdata():
            if a > 200:
                key = (r // 24, g // 24, b // 24)
                s = sums.setdefault(key, [0, 0, 0, 0])
                s[0] += r; s[1] += g; s[2] += b; s[3] += 1
        # report the average color of each group of similar pixels, not the group's rounded center
        for s in sums.values():
            found["#%02X%02X%02X" % (s[0] // s[3], s[1] // s[3], s[2] // s[3])] += s[3]
    colored, neutral = [], []
    for col, _n in found.most_common(40):
        _h, l, s = _hls(col)
        if l > 0.95:
            continue
        target = colored if s >= 0.2 and 0.1 <= l <= 0.9 else neutral
        if not any(_close(col, c["color"]) for c in target):
            target.append({"color": col, "why": "from the logo", "neutral": target is neutral})
    return (colored + neutral)[:5]


# ---------------------------------------------------------------------------------------------- brand guide PDFs
RGB_TEXT = re.compile(r"R\s*[:=]?\s*(\d{1,3})\s*[,/ ]\s*G\s*[:=]?\s*(\d{1,3})\s*[,/ ]\s*B\s*[:=]?\s*(\d{1,3})|RGB\W{0,3}(\d{1,3})\s*[,/ ]\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})", re.I)
CMYK_TEXT = re.compile(r"C\s*[:=]?\s*(\d{1,3})\s*[,/ ]\s*M\s*[:=]?\s*(\d{1,3})\s*[,/ ]\s*Y\s*[:=]?\s*(\d{1,3})\s*[,/ ]\s*K\s*[:=]?\s*(\d{1,3})|CMYK\W{0,3}(\d{1,3})\s*[,/ ]\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})", re.I)
OP_RGB = re.compile(rb"([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+rg\b")
OP_CMYK = re.compile(rb"([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+k\b")
PRIMARY_WORDS = re.compile(r"primary|main colou?r|brand colou?r|core colou?r", re.I)
ACCENT_WORDS = re.compile(r"accent|secondary|highlight|supporting", re.I)


def _hex_of(r, g, b):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in (r, g, b))


def _cmyk(c, m, y, k):
    c, m, y, k = (min(100.0, float(v)) / 100.0 for v in (c, m, y, k))
    return _hex_of(255 * (1 - c) * (1 - k), 255 * (1 - m) * (1 - k), 255 * (1 - y) * (1 - k))


def guide_colors(data):
    """Brand colors from a brand-guide PDF. Returns {primary, accent, candidates:[{color, why}], message}.
    Order of trust: colors written out in the text (hex, RGB or CMYK, especially next to the words primary / accent), then the fill colors
    the PDF's graphics use, then the dominant colors of the rendered pages."""
    import io

    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return {"message": "That PDF could not be read.", "candidates": []}
    pages = reader.pages[:40]
    found = []              # (color, why, role)

    def role_near(text, pos):
        """'primary' or 'accent' if one of those words sits just before the color; the closest word wins."""
        window = text[max(0, pos - 90):pos + 10]
        best = [(m.end(), "primary") for m in PRIMARY_WORDS.finditer(window)] + [(m.end(), "accent") for m in ACCENT_WORDS.finditer(window)]
        return max(best)[1] if best else ""
    for pg in pages:
        try:
            text = pg.extract_text() or ""
        except Exception:
            continue
        for m in HEX.finditer(text):
            if len(m.group(1)) in (6, 8):
                found.append((_hex6(m.group(1)), "written in the guide", role_near(text, m.start())))
        for m in RGB_TEXT.finditer(text):
            v = [x for x in m.groups() if x is not None][:3]
            if all(int(x) <= 255 for x in v):
                found.append((_hex_of(*map(int, v)), "RGB value written in the guide", role_near(text, m.start())))
        for m in CMYK_TEXT.finditer(text):
            v = [x for x in m.groups() if x is not None][:4]
            if all(int(x) <= 100 for x in v):
                found.append((_cmyk(*map(int, v)), "CMYK value written in the guide (converted)", role_near(text, m.start())))
    written = [f for f in found if _brandish(f[0])]
    if written:
        out = []
        for col, why, role in written:
            if not any(_close(col, c["color"]) for c in out):
                out.append({"color": col, "why": why, "role": role})
        primary = next((c["color"] for c in out if c["role"] == "primary"), None)
        accent = next((c["color"] for c in out if c["role"] == "accent" and c["color"] != primary), None)
        if not primary:
            primary = out[0]["color"]
        msg = "Colors written in the guide."
        if not accent and len(out) > 1:
            msg += " Pick an accent below."
        return {"primary": primary, "accent": accent, "candidates": out[:8], "message": msg + " Check them against the guide."}
    # graphics: fill colors used in the page drawing instructions
    counts = Counter()
    for pg in pages:
        try:
            raw = pg.get_contents().get_data()
        except Exception:
            continue
        for m in OP_RGB.finditer(raw):
            r, g, b = (float(x) for x in m.groups())
            if max(r, g, b) <= 1.0:
                counts[_hex_of(r * 255, g * 255, b * 255)] += 1
        for m in OP_CMYK.finditer(raw):
            c, mm, y, k = (float(x) for x in m.groups())
            if max(c, mm, y, k) <= 1.0:
                counts[_cmyk(c * 100, mm * 100, y * 100, k * 100)] += 1
    out = []
    for col, n in counts.most_common(40):
        if _brandish(col) and not any(_close(col, c["color"]) for c in out):
            out.append({"color": col, "why": f"used {n} times in the guide's graphics", "role": ""})
    if not out:                                       # last resort: what the pages look like
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(data)
            tally = Counter()
            for i in range(min(len(pdf), 6)):
                img = pdf[i].render(scale=0.5).to_pil().convert("RGB")
                img.thumbnail((200, 200))
                for r, g, b in img.getdata():
                    tally["#%02X%02X%02X" % ((r // 24) * 24 + 12, (g // 24) * 24 + 12, (b // 24) * 24 + 12)] += 1
            for col, n in tally.most_common(60):
                if _brandish(col) and not any(_close(col, c["color"]) for c in out):
                    out.append({"color": col, "why": "prominent on the guide's pages", "role": ""})
        except Exception:
            pass
    out = out[:6]
    if not out:
        return {"message": "No brand colors could be found in that PDF. Type your hex codes below.", "candidates": []}
    return {"primary": None, "accent": None, "candidates": out,
            "message": "The guide does not write its colors out, so these are the colors its graphics use. Pick the right ones, or type your hex codes below."}
