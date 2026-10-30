"""Research Portal Setup Kit: local web app (setup wizard for an organization's portal). Runs on this computer only (127.0.0.1)."""
import logging
import os
import re
import threading
import time
import sys
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi import Body, FastAPI, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from starlette.concurrency import run_in_threadpool  # noqa: E402

from app import access, auth_routes, terms_routes  # noqa: E402
from app.colors import guide_colors, logo_colors, pull_colors  # noqa: E402
from pubbank import adminlog, apikey, ask as asker, auth, bundle, mailer, migrations, registry, bank, batch_run, index as search_index, orgs, overlay_edit, pdfs, review, samples, schema_ai, schema_edit, search, sources, terms, topic  # noqa: E402
from pubbank.db import connect  # noqa: E402

LINK_KEY = re.compile(r"^(/(?:invite|signin)/)[^/?\s]+")


class _HideLinkKeys(logging.Filter):
    """The web server's request log would otherwise keep the secret part of every invitation, reset and sign-in link."""
    def filter(self, record):
        a = record.args
        if isinstance(a, tuple) and len(a) >= 3 and isinstance(a[2], str):
            record.args = (*a[:2], LINK_KEY.sub(r"\1<hidden>", a[2]), *a[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_HideLinkKeys())

APP = Path(__file__).resolve().parent
app = FastAPI(title="Research Portal Setup Kit", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=APP / "static"), name="static")
templates = Jinja2Templates(directory=APP / "templates")
def _static_v(path):
    """/static/<path>?v=<time the file last changed>, so a browser never shows an old script or style sheet."""
    f = APP / "static" / path
    return f"/static/{path}?v={int(f.stat().st_mtime)}" if f.exists() else f"/static/{path}"


templates.env.globals["static_v"] = _static_v
templates.env.globals["can_admin"] = lambda user, folder: auth.allows(user, folder, "admin")
templates.env.globals["login_mode"] = auth.login_mode            # password, both or link: templates show the matching sign-in controls
templates.env.globals["test_env"] = os.environ.get("PUBBANK_ENV", "").strip().lower() == "test"      # shows the TEST ENVIRONMENT bar on every page
templates.env.globals["money"] = lambda v: "under 1 cent" if v < 0.01 else f"${v:.2f}"


def _lum(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(a, b, t):
    ha, hb = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(f"{round(int(ha[i:i + 2], 16) * (1 - t) + int(hb[i:i + 2], 16) * t):02X}" for i in (0, 2, 4))


def _brand_vars(org=None):
    """CSS variables for an organization's colors. Whatever colors it picks, text on them stays readable:
    --on-primary is white or near-black, and --link/--on-accent-text are darkened until they pass 4.5:1 on the paper background."""
    ink, paper = "#1E1C18", "#F7F4EC"
    ok = lambda c: bool(re.fullmatch(r"#[0-9a-fA-F]{6}", c or ""))
    primary = org.get("primary") if org and ok(org.get("primary")) else ink
    accent = org.get("accent") if org and ok(org.get("accent")) else "#B5592E"
    def readable(c):
        t = 0.0
        while _contrast(_mix(c, ink, t), paper) < 4.5 and t < 1:
            t += 0.05
        return _mix(c, ink, t)
    on_primary = "#FFFFFF" if _contrast(primary, "#FFFFFF") >= 4.5 else ink
    on_accent = "#FFFFFF" if _contrast(accent, "#FFFFFF") >= 4.5 else ink
    return f"--primary:{primary};--accent:{accent};--on-primary:{on_primary};--on-accent:{on_accent};--link:{readable(primary)};--accent-text:{readable(accent)}"


templates.env.globals["brand_vars"] = _brand_vars

STEP_TITLES = {}
LOGO_MAX, GUIDE_MAX = 2 * 1024 * 1024, 20 * 1024 * 1024
REFERENCE_MAX = 25 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


BLOCKED_PAID = [("full_run", "/run/start"), ("topic_ai", "/bank/topic/ai"), ("sample_run", "/sample/run"), ("schema_ai", "/schema/propose")]


def _blocked_paid_step(path):
    """Paid steps an installation owner has switched off (PUBBANK_BLOCK=full_run,topic_ai,sample_run,schema_ai), for previews and demos."""
    off = {x.strip() for x in os.environ.get("PUBBANK_BLOCK", "").split(",") if x.strip()}
    return any(kind in off and path.endswith(tail) for kind, tail in BLOCKED_PAID)


def _gate(request):
    """None when the request may go ahead; otherwise the response to send instead (sign-in page, 401, 403)."""
    request.state.user = None
    level, folder = access.classify(request.url.path)
    if level == "public":
        return None
    wants_page = "text/html" in request.headers.get("accept", "")
    with connect() as conn:
        if not auth.has_users(conn):
            return RedirectResponse("/first-run", status_code=303) if wants_page else JSONResponse({"error": "Create the first account first."}, status_code=401)
        user = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
        terms_due = bool(user) and request.url.path not in terms.EXEMPT and terms.member_terms_due(conn, user["id"])
    if not user:
        if wants_page and request.method == "GET":
            nxt = request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse("/login?next=" + quote_plus(nxt), status_code=303)
        return JSONResponse({"error": "Please sign in again."}, status_code=401)
    request.state.user = user
    if terms_due:           # nothing but the terms screen until the current Member Terms are accepted
        if wants_page and request.method == "GET":
            nxt = request.url.path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse("/terms?next=" + quote_plus(nxt), status_code=303)
        return JSONResponse({"error": "Please open the site again and accept the Member Terms first.", "terms": True}, status_code=403)
    ok = {"any": True, "super": user["is_super"], "admin_any": auth.is_any_admin(user),
          "member": auth.allows(user, folder, "member"), "admin": auth.allows(user, folder, "admin")}[level]
    if not ok:
        if wants_page:
            return HTMLResponse("<p style='font-family:sans-serif;margin:40px'>You do not have access to this page. <a href='/'>Back to your organizations</a></p>", status_code=403)
        return JSONResponse({"error": "You do not have access to this."}, status_code=403)
    if request.method == "POST" and _blocked_paid_step(request.url.path):
        return JSONResponse({"error": "This is a preview installation, so this paid step is switched off."}, status_code=403)
    return None


@app.middleware("http")
async def guard(request: Request, call_next):
    """Refuse form posts that come from another website, check who is signed in, and add safety headers to every response."""
    if request.method == "POST":
        origin = request.headers.get("origin")
        if (origin and origin.split("://", 1)[-1] != request.headers.get("host")) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "cross-site request refused"}, status_code=403)
    response = await run_in_threadpool(_gate, request)
    if response is None:
        response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if auth.HOSTED:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.url.path.startswith("/static/") and not request.url.path.startswith("/static/fonts/"):
        response.headers["Cache-Control"] = "no-cache"      # always check for a newer script or style sheet, so updates show up straight away
    elif "text/html" in response.headers.get("content-type", "") or "json" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "no-store"      # pages and answers belong to the signed-in person: never kept in a shared cache
    return response


@app.on_event("startup")
def run_migrations():
    """Bring the database up to date before serving anything."""
    with connect() as conn:
        applied = migrations.run(conn)
    if applied:
        print("Applied database migrations:", ", ".join(applied))


def _mins(seconds):
    """A rough time as words: '40 seconds', '3 minutes', '2 hours'."""
    if seconds < 90:
        return f"{max(10, round(seconds / 10) * 10)} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{seconds / 3600:.1f} hours"


def _org_or_404(folder):
    if not orgs.valid_folder(folder):
        return None
    cfg = orgs.load(folder)
    cfg["initials"] = orgs.initials(cfg["name"])
    return cfg


def _page(request, name, status_code=200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


auth_routes.register(app, _page)
terms_routes.register(app, _page)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, msg: str = "", err: str = ""):
    cards = []
    user = request.state.user
    with connect() as conn:
        for cfg in orgs.list_orgs():
            cfg["role"] = auth.role_in(user, cfg["folder"])
            if not cfg["role"]:
                continue
            cfg["initials"] = orgs.initials(cfg["name"])
            cfg["received"], cfg["in_bank"] = orgs.bank_counts(conn, cfg["db_slug"]) if cfg["db_slug"] else (0, 0)
            cards.append(cfg)
    if len(cards) == 1 and cards[0]["role"] == "member":
        return RedirectResponse(f"/org/{cards[0]['folder']}/workspace", status_code=303)      # members have nowhere else to go
    return _page(request, "home.html", orgs=cards, flash=msg or err, flash_error=bool(err))


@app.get("/admin/schema-registry.xlsx")
def schema_registry():
    """Back-end overview (Excel): the standard categories and what each organization has customized."""
    return Response(registry.workbook(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="schema_registry.xlsx"'})


@app.get("/admin/org-bundle/{folder}.zip")
def org_bundle_download(request: Request, folder: str):
    """An organization's settings as a zip (no key, papers or database), to move it to another installation."""
    if not orgs.valid_folder(folder):
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        auth.audit(conn, request.state.user["id"], "bundle_exported", folder)
        conn.commit()
    return Response(bundle.export(folder), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{folder}-settings.zip"'})


@app.post("/admin/org-bundle")
def org_bundle_upload(request: Request, folder: str = Form(""), file: UploadFile = None):
    """Bring an organization's settings in from another installation. Also makes sure it has a row in the database."""
    folder = folder.strip().lower()
    if file is None:
        return RedirectResponse("/?msg=Choose+a+settings+zip+first", status_code=303)
    try:
        written = bundle.import_bundle(folder, file.file.read(bundle.MAX_BYTES + 1))
    except ValueError as e:
        return RedirectResponse("/?msg=" + quote_plus(str(e)), status_code=303)
    cfg = orgs.load(folder)
    with connect() as conn:
        slug = cfg["db_slug"] or cfg["name"]
        conn.execute("INSERT INTO organizations (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING", (slug, cfg["name"]))
        auth.audit(conn, request.state.user["id"], "bundle_imported", f"{folder}: {len(written)} files")
        conn.commit()
    return RedirectResponse(f"/?msg={quote_plus(f'Imported {len(written)} files for {cfg['name']}.')}", status_code=303)


@app.post("/orgs/new")
def new_org(request: Request, name: str = Form(...), agree: str = Form(""), version: str = Form("")):
    """Create a publication bank. The person creating it must first accept the current Organization Agreement; the acceptance is recorded with the new bank."""
    name = name.strip()
    if not name:
        return RedirectResponse("/?msg=Please+enter+a+name", status_code=303)
    user = request.state.user
    key = "organization_agreement"
    current = terms.current(key)
    if agree != "on" or version != current["version"]:       # asked every time a bank is created: the agreement is about that bank
        err = "Please tick the box to continue." if version else ""
        return _page(request, "terms.html", err=err, action="/orgs/new", hidden={"name": name}, button="Agree and create", cancel="/", doc=current, updated=False, before=None)
    with connect() as conn:
        folder = orgs.create(name, conn)
        terms.record(conn, user, key, folder, terms.org_id_for(conn, orgs.load(folder).get("db_slug")))
        auth.audit(conn, user["id"], "terms_accepted", f"organization_agreement {current['version']} for {folder}")
        conn.commit()
    return RedirectResponse(f"/org/{folder}/step/1", status_code=303)


@app.get("/org/{folder}/step/1", response_class=HTMLResponse)
def step1(request: Request, folder: str, msg: str = "", err: str = ""):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    return _page(request, "step1.html", org=org, flash=err or msg, flash_error=bool(err))


def _save_upload(upload: UploadFile, dest: Path, max_bytes, kind):
    """Read an upload (size-limited and content-checked) and save it. Returns an error message or ''."""
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        return f"The {kind} is larger than {max_bytes // (1024 * 1024)} MB."
    if kind == "logo":
        if not (data.startswith(PNG_MAGIC) or (b"<svg" in data[:4096].lower())):
            return "The logo must be a PNG or an SVG image."
    elif not data.startswith(b"%PDF"):
        return "The brand guide must be a PDF."
    dest.write_bytes(data)
    return ""


@app.post("/org/{folder}/step/1")
async def save_step1(request: Request, folder: str, name: str = Form(...), description: str = Form(""), website: str = Form(""),
                     primary: str = Form("#2B6E5E"), accent: str = Form("#B5592E"), action: str = Form("continue"),
                     logo: UploadFile | None = None, brand_guide: UploadFile | None = None):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    problems = []
    if not orgs.HEX.match(primary) or not orgs.HEX.match(accent):
        problems.append("Colors must look like #2B6E5E.")
    if website and not re.match(r"^https?://", website.strip(), re.I):
        website = "https://" + website.strip()
    cfg = {k: org[k] for k in orgs.DEFAULTS}
    cfg.update(name=name.strip(), description=description.strip(), website=website.strip())
    if not problems:
        cfg.update(primary=primary.upper(), accent=accent.upper())
    d = orgs.ORGS_DIR / folder
    if logo and logo.filename:
        ext = "png" if logo.filename.lower().endswith(".png") else "svg" if logo.filename.lower().endswith(".svg") else ""
        err = "Logo: use a PNG or SVG file." if not ext else _save_upload(logo, d / f"logo.{ext}", LOGO_MAX, "logo")
        if err:
            problems.append(err)
        else:
            cfg["logo"] = f"logo.{ext}"
            for other in ("png", "svg"):
                if other != ext:
                    (d / f"logo.{other}").unlink(missing_ok=True)
    if brand_guide and brand_guide.filename:
        err = _save_upload(brand_guide, d / "brand_guide.pdf", GUIDE_MAX, "brand guide")
        if err:
            problems.append(err)
        else:
            cfg["brand_guide"] = "brand_guide.pdf"
    orgs.save(folder, cfg)
    if cfg["db_slug"]:
        with connect() as conn:
            conn.execute("UPDATE organizations SET name = %s WHERE slug = %s", (cfg["name"], cfg["db_slug"]))
            conn.commit()
    if problems:
        return RedirectResponse(f"/org/{folder}/step/1?err=" + quote_plus(" ".join(problems)), status_code=303)
    return RedirectResponse("/?msg=Saved" if action == "exit" else f"/org/{folder}/step/2", status_code=303)


@app.get("/org/{folder}/logo")
def logo(folder: str):
    org = _org_or_404(folder)
    if org is None or not org["logo"] or not re.match(r"^logo\.(png|svg)$", org["logo"]):
        return HTMLResponse("No logo", status_code=404)
    path = orgs.ORGS_DIR / folder / org["logo"]
    if not path.exists():
        return HTMLResponse("No logo", status_code=404)
    # sandbox + no-sniff: an uploaded SVG is shown as a picture only and can never run scripts
    return FileResponse(path, media_type="image/png" if path.suffix == ".png" else "image/svg+xml",
                        headers={"Content-Security-Policy": "sandbox", "Cache-Control": "no-cache"})


@app.get("/org/{folder}/logo-colors")
def saved_logo_colors(folder: str):
    org = _org_or_404(folder)
    if org is None or not re.match(r"^logo\.(png|svg)$", org["logo"] or ""):
        return JSONResponse({"candidates": []})
    path = orgs.ORGS_DIR / folder / org["logo"]
    if not path.exists():
        return JSONResponse({"candidates": []})
    return JSONResponse({"candidates": logo_colors(path.read_bytes(), path.suffix[1:])})


@app.post("/logo-colors")
def new_logo_colors(logo: UploadFile):
    data = logo.file.read(LOGO_MAX + 1)
    ext = "png" if data.startswith(PNG_MAGIC) else "svg" if b"<svg" in data[:4096].lower() else ""
    if not ext or len(data) > LOGO_MAX:
        return JSONResponse({"candidates": []})
    return JSONResponse({"candidates": logo_colors(data, ext)})


@app.get("/org/{folder}/guide-colors")
def saved_guide_colors(folder: str):
    org = _org_or_404(folder)
    path = orgs.ORGS_DIR / folder / "brand_guide.pdf"
    if org is None or not org["brand_guide"] or not path.exists():
        return JSONResponse({"candidates": []})
    return JSONResponse(guide_colors(path.read_bytes()))


@app.post("/guide-colors")
def new_guide_colors(guide: UploadFile):
    data = guide.file.read(GUIDE_MAX + 1)
    if len(data) > GUIDE_MAX or not data.startswith(b"%PDF"):
        return JSONResponse({"message": "Please choose a PDF file up to 20 MB.", "candidates": []})
    return JSONResponse(guide_colors(data))


@app.post("/pull-colors")
def pull_colors_route(body: dict = Body(...)):
    # a plain (not async) function: FastAPI runs it in a worker thread, so a slow website cannot freeze the app
    return JSONResponse(pull_colors(str(body.get("url", "")).strip()))


def _org_id(conn, org):
    """Database id of the organization, creating its row if the folder was made by hand."""
    slug = org["db_slug"] or org["name"]
    conn.execute("INSERT INTO organizations (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING", (slug, org["name"]))
    conn.commit()
    return conn.execute("SELECT id FROM organizations WHERE slug = %s", (slug,)).fetchone()[0]


@app.get("/org/{folder}/step/2", response_class=HTMLResponse)
def step2(request: Request, folder: str, q: str = "", status: str = "", p: int = 1):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        sources.ensure_columns(conn)
        oid = _org_id(conn, org)
        counts = bank.counts(conn, oid)
        items, total, pages, p = bank.page(conn, oid, q.strip(), status, p)
        idx = search_index.status(conn, oid)
    return _page(request, "step2.html", org=org, idx=idx, running_job=sources.running_import(oid), counts=counts, items=items, total=total, pages=pages, p=p, q=q, status=status,
                 statuses=bank.STATUS_LABELS)


@app.post("/org/{folder}/bank/pmids")
def add_pmids(folder: str, ids: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    good, bad = sources.parse_pmids(ids)
    if not good:
        return JSONResponse({"error": "No PubMed IDs found. Paste numbers such as 38291045, one per line or separated by commas."},
                            status_code=400)
    if len(good) > 10000:
        return JSONResponse({"error": "That is more than 10,000 IDs at once; please add them in smaller batches."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
    if sources.busy(oid):
        return JSONResponse({"error": "Another import for this organization is still running. Wait for it to finish."}, status_code=409)
    jid = sources.new_job("pmid", f"{len(good)} PubMed IDs", oid, f"about {_mins(len(good) * 1.5)} for {len(good)} papers")
    sources.start(sources.run_pmid_job, jid, oid, good, bad)
    return {"job": jid, "count": len(good)}


@app.post("/org/{folder}/bank/reference")
def add_reference(folder: str, file: UploadFile):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    data = file.file.read(REFERENCE_MAX + 1)
    if len(data) > REFERENCE_MAX:
        return JSONResponse({"error": "That file is larger than 25 MB."}, status_code=400)
    try:
        records, fmt = sources.parse_reference_file(file.filename or "", data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if len(records) > 3000:
        return JSONResponse({"error": f"That file has {len(records)} references; please split it into files of up to 3,000."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
    if sources.busy(oid):
        return JSONResponse({"error": "Another import for this organization is still running. Wait for it to finish."}, status_code=409)
    jid = sources.new_job("reference", f"{len(records)} references ({fmt})", oid, f"about {_mins(len(records) * 2.5)} for {len(records)} references")
    sources.start(sources.run_reference_job, jid, oid, records)
    return {"job": jid, "count": len(records), "format": fmt}


def _pdf_slug_problem(org):
    slug = org["db_slug"] or org["name"]
    if pdfs.raw_dir(slug).name != slug:
        return ("The organization's name has characters that cannot be used in a folder name. "
                "Change the name on step 1 to letters, numbers and spaces.")
    return ""


@app.post("/org/{folder}/bank/pdfs/upload")
def upload_pdfs(folder: str, files: list[UploadFile]):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if (problem := _pdf_slug_problem(org)):
        return JSONResponse({"error": problem}, status_code=400)
    saved, skipped = 0, []
    for f in files:
        data = f.file.read(pdfs.MAX_PDF_BYTES + 1)
        result = pdfs.save_upload(org["db_slug"] or org["name"], f.filename or "", data)
        if result == "saved":
            saved += 1
        else:
            skipped.append(f"{Path((f.filename or '').replace(chr(92), '/')).name}: {result}")
    return {"saved": saved, "skipped": skipped}


def _start_pdf_job(org, copy_files, label):
    with connect() as conn:
        oid = _org_id(conn, org)
    if sources.busy(oid):
        return JSONResponse({"error": "Another import for this organization is still running. Wait for it to finish."}, status_code=409)
    jid = sources.new_job("pdf", label, oid, "about a minute per PDF")
    sources.start(pdfs.run_job, jid, oid, org["db_slug"] or org["name"], copy_files)
    return {"job": jid}


@app.post("/org/{folder}/bank/pdfs/process")
def process_pdfs(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if (problem := _pdf_slug_problem(org)):
        return JSONResponse({"error": problem}, status_code=400)
    return _start_pdf_job(org, None, "uploaded PDFs")


@app.post("/org/{folder}/bank/pdfs/folder-check")
def folder_check(folder: str, path: str = Form("")):
    if auth.HOSTED:
        return JSONResponse({"error": "Adding a folder from this computer is not available on a hosted installation. Upload the PDFs instead."}, status_code=400)
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    try:
        files, size = pdfs.scan_folder(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    dest = pdfs.raw_dir(org["db_slug"] or org["name"])
    new = sum(1 for _f, rel in files if not (dest / rel).exists())
    return {"count": len(files), "megabytes": round(size / 1048576), "new": new}


@app.post("/org/{folder}/bank/pdfs/folder-add")
def folder_add(folder: str, path: str = Form("")):
    if auth.HOSTED:
        return JSONResponse({"error": "Adding a folder from this computer is not available on a hosted installation. Upload the PDFs instead."}, status_code=400)
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if (problem := _pdf_slug_problem(org)):
        return JSONResponse({"error": problem}, status_code=400)
    try:
        files, _size = pdfs.scan_folder(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return _start_pdf_job(org, files, f"{len(files)} PDFs from a folder")


@app.get("/org/{folder}/step/2/review")
def review_old_address(folder: str):
    return RedirectResponse(f"/org/{folder}/step/3", status_code=301)


@app.get("/org/{folder}/step/3", response_class=HTMLResponse)
def review_page(request: Request, folder: str, g: str = "", p: int = 1, partial: int = 0):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        sources.ensure_columns(conn)
        oid = _org_id(conn, org)
        counts = review.counts(conn, oid)
        if g not in review.GROUPS:       # open on the first group that has something in it
            g = next((k for k in review.GROUPS if counts[k]), "nonpaper")
        items, total, pages, p = review.page(conn, oid, g, p)
        bank_counts = bank.counts(conn, oid)
        estimate = topic.estimate_ai(conn, oid, topic_disease(org))
    terms = topic.get_terms(org)
    if partial:      # only the tabs and the list, so switching tabs does not reload (or scroll) the page
        return _page(request, "_review_list.html", org=org, counts=counts, groups=review.GROUPS, headings=review.HEADINGS, g=g, items=items, total=total,
                     pages=pages, p=p)
    return _page(request, "review.html", org=org, counts=counts, groups=review.GROUPS, headings=review.HEADINGS, g=g, items=items, total=total, pages=pages, p=p,
                 bank=bank_counts, terms="; ".join(terms), estimate=estimate, has_key=apikey.has_key(folder))


@app.post("/org/{folder}/bank/decision")
def set_decision(folder: str, paper_id: int = Form(...), decision: str = Form("")):
    org = _org_or_404(folder)
    if org is None or decision not in ("Keep", "Exclude", ""):
        return JSONResponse({"error": "Bad request"}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        if not review.set_decision(conn, oid, paper_id, decision):
            return JSONResponse({"error": "Unknown paper"}, status_code=404)
        c = bank.counts(conn, oid)
        row = conn.execute("SELECT in_bank FROM papers WHERE id = %s", (paper_id,)).fetchone()
    return {"in_bank": bool(row[0]), "bank_total": c["inbank"] + c["abstract"]}


@app.get("/org/{folder}/bank/set-aside.xlsx")
def download_set_aside(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        data = review.export_xlsx(conn, _org_id(conn, org), org["name"])
    name = re.sub(r"[^A-Za-z0-9]+", "_", org["name"]).strip("_") or "organization"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="set_aside_{name}.xlsx"'})


@app.post("/org/{folder}/bank/decisions-upload")
def upload_decisions(folder: str, file: UploadFile):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    data = file.file.read(REFERENCE_MAX + 1)
    if len(data) > REFERENCE_MAX or not data.startswith(b"PK"):
        return JSONResponse({"error": "Please upload the Excel file you downloaded from this screen (.xlsx)."}, status_code=400)
    try:
        with connect() as conn:
            applied, ignored = review.import_xlsx(conn, _org_id(conn, org), data)
    except Exception:
        return JSONResponse({"error": "That file could not be read as the set-aside spreadsheet."}, status_code=400)
    return {"applied": applied, "ignored": ignored}


def _clean_terms(text):
    out, seen = [], set()
    for t in re.split(r"[;\n]+", text):
        t = t.strip()
        if 2 <= len(t) <= 80 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:60]


@app.post("/org/{folder}/bank/topic/keywords")
def topic_keywords(folder: str, terms: str = Form(""), apply: str = Form("no")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    clean = _clean_terms(terms)
    if not clean:
        return JSONResponse({"error": "Add at least one disease term (separate several with semicolons)."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        flagged, checked = topic.keyword_check(conn, oid, clean, apply=apply == "yes")
    if apply == "yes":
        cfg = {k: org[k] for k in orgs.DEFAULTS}
        cfg["topic_terms"] = "; ".join(clean)
        orgs.save(folder, cfg)
    return {"checked": checked, "flagged": len(flagged), "applied": apply == "yes",
            "examples": [{"title": t, "reason": r} for _pid, t, r in flagged[:8]]}


@app.post("/org/{folder}/bank/topic/ai")
def topic_ai(folder: str, confirm: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if confirm != "yes":
        return JSONResponse({"error": "Please confirm the cost first."}, status_code=400)
    if not apikey.has_key(folder):
        return JSONResponse({"error": "No Anthropic API key is set. Add one on the Review & Launch page."}, status_code=400)
    disease = topic_disease(org)
    with connect() as conn:
        oid = _org_id(conn, org)
        est = topic.estimate_ai(conn, oid, disease)
    if not est["papers"]:
        return JSONResponse({"error": "There are no papers left to check."}, status_code=400)
    if sources.busy(oid):
        return JSONResponse({"error": "Another job for this organization is still running."}, status_code=409)
    jid = sources.new_job("topic", f"AI topic check of {est['papers']} papers", oid, f"about {_mins(est['papers'] * 0.7)} for {est['papers']} papers")
    sources.start(topic.run_ai_job, jid, oid, disease, est["high"])
    return {"job": jid, "estimate": est}


def topic_disease(org):
    """The disease description given to the AI: the template's if there is one, otherwise the organization's own words."""
    overlay = orgs.ORGS_DIR / org["folder"] / "overlay.xlsx"
    if overlay.exists():
        from pubbank.template import load_template
        d = (load_template(overlay).get("context") or {}).get("disease")
        if d:
            return d
    return " ".join(x for x in (org["name"], org["description"]) if x)


@app.get("/org/{folder}/step/5", response_class=HTMLResponse)
def step5(request: Request, folder: str, paper: int = 0):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    path = samples.template_path(folder)
    ctx_ = {"org": org, "state": "no_template", "step": 4}
    if path:
        from pubbank.template import load_context
        with connect() as conn:
            oid = _org_id(conn, org)
            smp = samples.latest(conn, oid)
            ctx_["pool"] = len([c for c in samples.candidates(conn, oid, topic.get_terms(org)) if c["good"]])
            if smp is None:
                ctx_.update(state="none", default_size=samples.DEFAULT_SIZE, max_size=samples.MAX_SIZE)
            elif sources.running_job(oid, "sample"):
                ctx_.update(state="running", smp=smp)
            elif smp["status"] == "running":      # the app was restarted while the read was going: offer to run it again
                conn.execute("UPDATE samples SET status = 'draft' WHERE id = %s", (smp["id"],))
                conn.commit()
                return RedirectResponse(f"/org/{folder}/step/5", status_code=303)
            elif smp["status"] == "draft":
                ids = smp["paper_ids"]
                ctx_.update(state="draft", smp=smp, papers=samples.papers_info(conn, oid, ids), est=samples.estimate(conn, folder, ids),
                            has_key=apikey.has_key(folder), max_size=samples.MAX_SIZE)
            else:
                ctx = load_context(path)
                plist = samples.papers_info(conn, oid, smp["paper_ids"])
                idx = min(max(0, paper), max(0, len(plist) - 1))
                done_rows = dict(conn.execute("SELECT paper_id, count(*) FROM sample_verdicts WHERE sample_id = %s AND verdict IS NOT NULL "
                                              "GROUP BY 1", (smp["id"],)).fetchall())
                cur = plist[idx] if plist else None
                ctx_.update(state="done", smp=smp, papers=plist, idx=idx, cur=cur, done_rows=done_rows, verdicts=samples.VERDICTS,
                            answers=samples.paper_answers(conn, smp["id"], cur["id"], ctx) if cur else None,
                            score=samples.scorecard(conn, smp["id"], ctx), stale=smp["template_version"] != ctx.version,
                            answers_total=len([c for c in ctx.template["categories"] if c["include"]]))
    return _page(request, "step5.html", **ctx_)


@app.post("/org/{folder}/sample/pick")
def sample_pick(folder: str, n: int = Form(15)):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        if len(samples.candidates(conn, oid)) < 1:
            return JSONResponse({"error": "There are no papers in the bank to sample yet. Add some on step 2."}, status_code=400)
        sid = samples.create(conn, oid, n, topic.get_terms(org))
    return {"sample": sid}


@app.post("/org/{folder}/sample/swap")
def sample_swap(folder: str, sample_id: int = Form(...), paper_id: int = Form(...)):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        ok = samples.replace_paper(conn, _org_id(conn, org), sample_id, paper_id, topic.get_terms(org))
    return {"ok": ok}


@app.post("/org/{folder}/sample/run")
def sample_run(folder: str, sample_id: int = Form(...), confirm: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if confirm != "yes":
        return JSONResponse({"error": "Please confirm the cost first."}, status_code=400)
    if not apikey.has_key(folder):
        return JSONResponse({"error": "No Anthropic API key is set. Add one on the Review & Launch page."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        smp = samples.latest(conn, oid)
        if not smp or smp["id"] != sample_id or smp["status"] != "draft":
            return JSONResponse({"error": "That sample is not ready to run."}, status_code=400)
        est = samples.estimate(conn, folder, smp["paper_ids"])
    if sources.busy(oid):
        return JSONResponse({"error": "Another job for this organization is still running."}, status_code=409)
    jid = sources.new_job("sample", f"extraction of {est['papers']} sample papers", oid, f"about {est['minutes']} minutes")
    sources.start(samples.run_job, jid, oid, folder, sample_id, est["high"])
    return {"job": jid, "estimate": est}


@app.post("/org/{folder}/sample/verdict")
def sample_verdict(folder: str, sample_id: int = Form(...), paper_id: int = Form(...), category: str = Form(...),
                   verdict: str | None = Form(None), note: str | None = Form(None)):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    from pubbank.template import load_context
    with connect() as conn:
        oid = _org_id(conn, org)
        try:
            def val(v, cap=None):      # an empty form value arrives as "not sent", so clearing is sent as __none__
                return samples.UNCHANGED if v is None else ("" if v == "__none__" else (v[:cap] if cap else v))
            ok = samples.save_verdict(conn, oid, sample_id, paper_id, category[:60], val(verdict), val(note, 2000))
        except ValueError:
            return JSONResponse({"error": "Unknown verdict"}, status_code=400)
        if not ok:
            return JSONResponse({"error": "Unknown sample"}, status_code=404)
        return {"score": samples.scorecard(conn, sample_id, load_context(samples.template_path(folder)))}


@app.get("/org/{folder}/sample/{sample_id}/check.xlsx")
def sample_export(folder: str, sample_id: int):
    org = _org_or_404(folder)
    if org is None or not samples.template_path(folder):
        return HTMLResponse("Not found", status_code=404)
    from pubbank.template import load_context
    with connect() as conn:
        data = samples.export_xlsx(conn, _org_id(conn, org), sample_id, load_context(samples.template_path(folder)), org["name"])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="sample_check.xlsx"'})


@app.post("/org/{folder}/sample/{sample_id}/import")
def sample_import(folder: str, sample_id: int, file: UploadFile):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    data = file.file.read(REFERENCE_MAX + 1)
    if len(data) > REFERENCE_MAX or not data.startswith(b"PK"):
        return JSONResponse({"error": "Please upload the Excel file you downloaded from this screen (.xlsx)."}, status_code=400)
    try:
        with connect() as conn:
            n = samples.import_xlsx(conn, _org_id(conn, org), sample_id, data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"applied": n}


@app.get("/org/{folder}/sample/status")
def sample_status(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
    return {"job": sources.running_job(oid, "sample")}


@app.get("/org/{folder}/paper/{paper_id}/pdf")
def paper_pdf(folder: str, paper_id: int):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Not found", status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        row = conn.execute("SELECT relpath, coalesce(source, 'pdf') FROM papers WHERE id = %s AND org_id = %s", (paper_id, oid)).fetchone()
    if not row or row[1] != "pdf":
        return HTMLResponse("No PDF for this paper", status_code=404)
    base = pdfs.raw_dir(org["db_slug"] or org["name"]).resolve()
    path = (base / row[0]).resolve()
    if base not in path.parents or not path.is_file():
        return HTMLResponse("PDF file not found on this computer", status_code=404)
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline", "Content-Security-Policy": "sandbox"})


@app.get("/org/{folder}/bank/jobs/{job_id}")
def job_status(request: Request, folder: str, job_id: str):
    job = sources.get_job(job_id)
    if not job or not orgs.valid_folder(folder):
        return JSONResponse({"error": "Unknown job"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, orgs.load(folder))
    if job.get("org_id") not in (None, oid):          # a job of another organization is not visible here
        return JSONResponse({"error": "Unknown job"}, status_code=404)
    if job["kind"] != "ask" and not auth.allows(request.state.user, folder, "admin"):
        return JSONResponse({"error": "You do not have access to this."}, status_code=403)      # members may follow their own questions, nothing else
    out = {k: job[k] for k in ("stage", "done", "total", "finished", "error", "result", "label", "hint")}
    out["elapsed"] = round(time.time() - job["started"])
    out["stage_elapsed"] = round(time.time() - job.get("stage_started", job["started"]))
    out["step"], out["steps"] = sources.step_info(job)
    out["note"], out["unit"] = job.get("note", ""), job.get("unit", "")
    return out


def _cfg_dict(org, **changes):
    cfg = {k: org[k] for k in orgs.DEFAULTS}
    cfg.update(changes)
    return cfg


@app.get("/org/{folder}/step/4", response_class=HTMLResponse)
def step4(request: Request, folder: str):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    v = schema_edit.view(folder)
    with connect() as conn:
        oid = _org_id(conn, org)
        n_papers = bank.counts(conn, oid)
        ctx_ = {"org": org, "view": v, "step": 3}
        if v is not None:
            ctx_.update(uploads=schema_ai.list_uploads(folder), notes=schema_ai.get_notes(folder), proposal=schema_ai.load_proposal(folder),
                        est=schema_ai.estimate(conn, oid, folder, org), est_revise=schema_ai.estimate(conn, oid, folder, org, revising=True), gene_roles=schema_ai.GENE_ROLES,
                        has_key=apikey.has_key(folder), papers_in_bank=n_papers["inbank"] + n_papers["abstract"],
                        approved=org["schema_approved"] == v["version"])
    return _page(request, "step4.html", **ctx_)


def _schema_call(folder, fn):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    try:
        result = fn(org)
    except (ValueError, KeyError) as e:
        return JSONResponse({"error": str(e.args[0]) if e.args else "That did not work."}, status_code=400)
    return {"ok": True, **(result or {})}


@app.post("/org/{folder}/schema/start")
def schema_start(folder: str, disease: str = Form("")):
    def go(org):
        if len(disease.strip()) < 10:
            raise ValueError("Describe the disease or topic in a sentence or two.")
        schema_edit.create_overlay(folder, disease)
    return _schema_call(folder, go)


@app.post("/org/{folder}/schema/context")
def schema_context(folder: str, disease: str = Form("")):
    def go(org):
        if len(disease.strip()) < 10:
            raise ValueError("Describe the disease or topic in a sentence or two.")
        ov = overlay_edit.Overlay(schema_edit.overlay_path(folder))
        ov.set_context("disease", disease.strip())
        ov.save()
    return _schema_call(folder, go)


@app.post("/org/{folder}/schema/category")
def schema_category(folder: str, cid: str = Form(...), label: str = Form(""), counts: str = Form(""), not_counts: str = Form(""),
                    interpret: str = Form(""), na_means: str = Form(""), nf_means: str = Form(""), options: str | None = Form(None),
                    filter_chip: str = Form("no")):
    def go(org):
        data = {"filter_chip": filter_chip == "yes"}
        for k, v in (("label", label), ("counts", counts), ("not_counts", not_counts), ("interpret", interpret), ("na_means", na_means), ("nf_means", nf_means)):
            if v.strip():
                data[k] = v
        if options is not None:
            data["options"] = [o for o in options.splitlines()]
        schema_edit.save_category(folder, cid, data)
    return _schema_call(folder, go)


@app.post("/org/{folder}/schema/include")
def schema_include(folder: str, cid: str = Form(...), include: str = Form("no")):
    return _schema_call(folder, lambda org: schema_edit.set_included(folder, cid, include == "yes"))


@app.post("/org/{folder}/schema/add")
def schema_add(folder: str, label: str = Form(""), kind: str = Form(""), options: str = Form(""), counts: str = Form(""),
               not_counts: str = Form(""), all_papers: str = Form("no")):
    return _schema_call(folder, lambda org: {"id": schema_edit.add_field(folder, label, kind, options.splitlines(), counts, not_counts,
                                                                          all_papers == "yes")})


@app.post("/org/{folder}/schema/upload")
def schema_upload(folder: str, files: list[UploadFile]):
    def go(org):
        saved, skipped = [], []
        for f in files:
            try:
                data = f.file.read(schema_ai.MAX_UPLOAD + 1)
                saved.append(schema_ai.save_upload(folder, f.filename or "document", data)[0])
            except ValueError as e:
                skipped.append(f"{f.filename}: {e}")
        if not saved and skipped:
            raise ValueError(skipped[0])
        return {"saved": saved, "skipped": skipped}
    return _schema_call(folder, go)


@app.post("/org/{folder}/schema/upload-delete")
def schema_upload_delete(folder: str, name: str = Form(...)):
    return _schema_call(folder, lambda org: schema_ai.delete_upload(folder, name))


@app.post("/org/{folder}/schema/notes")
def schema_notes(folder: str, notes: str = Form("")):
    return _schema_call(folder, lambda org: schema_ai.set_notes(folder, notes))


@app.post("/org/{folder}/schema/propose")
def schema_propose(folder: str, confirm: str = Form(""), feedback: str = Form(""), current: str = Form("")):
    import json as _json
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if confirm != "yes":
        return JSONResponse({"error": "Please confirm the cost first."}, status_code=400)
    if not apikey.has_key(folder):
        return JSONResponse({"error": "No Anthropic API key is set. Add one on the Review & Launch page."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        est = schema_ai.estimate(conn, oid, folder, org, revising=bool(feedback.strip()))
    if not est["has_material"]:
        return JSONResponse({"error": "Add a transcript, a document or some notes first."}, status_code=400)
    if feedback.strip() and current:
        try:
            schema_ai.save_edited(folder, _json.loads(current))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    if sources.busy(oid):
        return JSONResponse({"error": "Another job for this organization is still running."}, status_code=409)
    jid = sources.new_job("schema", "AI schema proposal", oid, "about 30 to 60 seconds")
    sources.start(schema_ai.run_job, jid, oid, folder, org, feedback)
    return {"job": jid, "estimate": est}


@app.post("/org/{folder}/schema/apply")
def schema_apply(folder: str, accept: str = Form("{}"), proposal: str = Form("")):
    import json as _json

    def go(org):
        try:
            acc = _json.loads(accept)
            edited = _json.loads(proposal) if proposal else None
        except ValueError:
            raise ValueError("Bad request.")
        return {"done": schema_ai.apply(folder, acc, edited)}
    return _schema_call(folder, go)


@app.post("/org/{folder}/schema/defaults")
def schema_defaults(folder: str, answer_format: str = Form("plain"), search_depth: str = Form("standard"), allow_depth_choice: str = Form("yes"),
                    daily_cap: str = Form("5")):
    def go(org):
        if answer_format not in ("plain", "technical") or search_depth not in ("quick", "standard", "deep") or allow_depth_choice not in ("yes", "no"):
            raise ValueError("Bad choice.")
        try:
            cap = max(0.5, min(500.0, float(daily_cap)))
        except ValueError:
            raise ValueError("The daily limit must be a number of dollars.")
        orgs.save(folder, _cfg_dict(org, answer_format=answer_format, search_depth=search_depth, allow_depth_choice=allow_depth_choice, daily_cap=f"{cap:.2f}"))
    return _schema_call(folder, go)


@app.post("/org/{folder}/schema/approve")
def schema_approve(folder: str):
    def go(org):
        v = schema_edit.view(folder)
        if v is None:
            raise ValueError("Create the template first.")
        orgs.save(folder, _cfg_dict(org, schema_approved=v["version"]))
    return _schema_call(folder, go)


@app.get("/org/{folder}/step/6", response_class=HTMLResponse)
def step6(request: Request, folder: str):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    v = schema_edit.view(folder)
    score = None
    with connect() as conn:
        oid = _org_id(conn, org)
        counts = bank.counts(conn, oid)
        idx = search_index.status(conn, oid)
        extracted = conn.execute("SELECT count(DISTINCT paper_id) FROM extractions WHERE org_id = %s AND status = 'ok'", (oid,)).fetchone()[0]
        if v is not None:
            from pubbank.template import load_context
            smp = samples.latest(conn, oid)
            if smp and smp["status"] == "done":
                score = samples.scorecard(conn, smp["id"], load_context(samples.template_path(folder)))
    fields = {"standard": 0, "customized": 0, "custom": 0}
    for c in (v["cards"] if v else []):
        if c["include"]:
            fields[c["kind"]] += 1
    return _page(request, "step6.html", org=org, view=v, bank=counts, idx=idx, extracted=extracted, fields=fields, score=score, depths=schema_edit.depth_options(),
                 approved=bool(v) and org["schema_approved"] == v["version"], key=apikey.status(folder))


def _key_reply(folder, ok, msg):
    return JSONResponse({"ok": ok, "message": msg, **apikey.status(folder)}, status_code=200 if ok else 400, headers={"Cache-Control": "no-store"})


@app.post("/org/{folder}/apikey")
def apikey_save(request: Request, folder: str, key: str = Form("")):
    """Check an organization's own Anthropic key and store it on the server. The key is never sent back to the browser."""
    if _org_or_404(folder) is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    ok, msg = apikey.save(folder, key)
    with connect() as conn:
        auth.audit(conn, request.state.user["id"], "api_key_saved" if ok else "api_key_rejected", folder)
        conn.commit()
    return _key_reply(folder, ok, msg)


@app.post("/org/{folder}/apikey/test")
def apikey_test(folder: str):
    """Check the key currently in use (free: it asks Anthropic whether the key is valid and uses no tokens)."""
    if _org_or_404(folder) is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    key = apikey.key_for(folder)
    if not key:
        return _key_reply(folder, False, "No key is set yet.")
    ok, msg = apikey.test(key)
    return _key_reply(folder, ok, msg)


@app.post("/org/{folder}/apikey/remove")
def apikey_remove(request: Request, folder: str):
    if _org_or_404(folder) is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    apikey.remove(folder)
    with connect() as conn:
        auth.audit(conn, request.state.user["id"], "api_key_removed", folder)
        conn.commit()
    return _key_reply(folder, True, "Removed. This organization is back to the shared default key.")


def _library_ids(conn, oid):
    return [r[0] for r in conn.execute("SELECT id FROM papers WHERE org_id = %s AND in_bank AND parse_status = 'ok' ORDER BY id", (oid,))]


@app.post("/org/{folder}/launch/estimate")
def launch_estimate(folder: str):
    org = _org_or_404(folder)
    if org is None or not samples.template_path(folder):
        return JSONResponse({"error": "Set up the extraction schema first."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        ids = _library_ids(conn, oid)
        if not ids:
            return JSONResponse({"error": "There are no papers in the bank yet."}, status_code=400)
        est = batch_run.estimate(conn, folder, ids)
    return est


@app.post("/org/{folder}/run/start")
def run_start(folder: str, limit: float = Form(...), confirm: str = Form("")):
    org = _org_or_404(folder)
    if org is None or not samples.template_path(folder):
        return JSONResponse({"error": "Set up the extraction schema first."}, status_code=400)
    if confirm != "yes":
        return JSONResponse({"error": "Please confirm the cost first."}, status_code=400)
    if not apikey.has_key(folder):
        return JSONResponse({"error": "No Anthropic API key is set. Add one on the Review & Launch page."}, status_code=400)
    v = schema_edit.view(folder)
    if org["schema_approved"] != v["version"]:
        return JSONResponse({"error": "Approve the extraction schema first (step 3). The schema was changed since it was approved."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        if batch_run.active(conn, oid):
            return JSONResponse({"error": "A run is already in progress for this organization."}, status_code=409)
        ids = _library_ids(conn, oid)
        est = batch_run.estimate(conn, folder, ids)
        if limit < est["expected"]:
            return JSONResponse({"error": f"The spending limit is below the expected cost (${est['expected']:.2f}). Raise it, or the run would stop partway."}, status_code=400)
        run_id = batch_run.create(conn, oid, folder, ids, round(limit, 2))
    threading.Thread(target=batch_run.start, args=(folder, run_id), daemon=True).start()
    return {"run": run_id, "estimate": est}


@app.get("/org/{folder}/run/status")
def run_status(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        r = batch_run.latest(conn, oid)
    if r and r["status"] == "running":
        threading.Thread(target=batch_run.advance, args=(r["id"],), daemon=True).start()      # check Anthropic now, without making the page wait
    return {"run": r}


@app.post("/org/{folder}/run/cancel")
def run_cancel(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        rid = batch_run.active(conn, _org_id(conn, org))
    if rid:
        batch_run.cancel(rid)
    return {"ok": True}


@app.on_event("startup")
def start_watcher():
    """Keep any running batch moving, even if the page is closed."""
    threading.Thread(target=batch_run.watch_forever, daemon=True).start()


def _synonyms(folder):
    """Name groups from the organization's gene list, e.g. [['GENE1', 'GENE1A']]; used to widen word searches."""
    p = schema_edit.overlay_path(folder)
    if not p:
        return []
    from pubbank.template import load_template
    return [[g["symbol"]] + list(g["aliases"]) for g in load_template(p).get("genes", [])]


def _facets(conn, oid, folder):
    """Filter chips: for each field marked as a search filter, the values found by the extraction and how many papers have each."""
    p = schema_edit.overlay_path(folder)
    if not p:
        return []
    from pubbank.template import load_template
    t = load_template(p)
    rows = conn.execute("""SELECT DISTINCT ON (e.paper_id) e.paper_id, e.result FROM extractions e JOIN papers p ON p.id = e.paper_id
                           WHERE e.org_id = %s AND e.status = 'ok' AND p.in_bank ORDER BY e.paper_id, e.created_at DESC""", (oid,)).fetchall()
    out = []
    for c in t["categories"]:
        if not (c["include"] and c["filter_chip"]):
            continue
        counts = {}
        for pid, res in rows:
            seen = set()
            for e in (res.get(c["id"]) or {}).get("entries", []):
                v = e.get(c["fields"][0]["name"]) if c["fields"] else None
                if isinstance(v, str) and v.strip() and v.strip()[:60] not in seen:
                    seen.add(v.strip()[:60])
                    counts.setdefault(v.strip()[:60], set()).add(pid)
        vals = sorted(((v, len(ps)) for v, ps in counts.items()), key=lambda x: (-x[1], x[0]))[:25]
        out.append({"id": c["id"], "label": c["label"], "values": [{"value": v, "papers": n} for v, n in vals]})
    return out


def _filtered_papers(conn, oid, filters):
    """Paper ids that match every chosen filter value (None when no filter is chosen)."""
    if not filters:
        return None
    rows = conn.execute("""SELECT DISTINCT ON (e.paper_id) e.paper_id, e.result FROM extractions e JOIN papers p ON p.id = e.paper_id
                           WHERE e.org_id = %s AND e.status = 'ok' AND p.in_bank ORDER BY e.paper_id, e.created_at DESC""", (oid,)).fetchall()
    keep = []
    for pid, res in rows:
        ok = True
        for cid, wanted in filters.items():
            have = {str(v).strip()[:60] for e in (res.get(cid) or {}).get("entries", []) for k, v in e.items() if k != "evidence" and isinstance(v, str)}
            if not (set(wanted) & have):
                ok = False
                break
        if ok:
            keep.append(pid)
    return keep


@app.get("/org/{folder}/workspace", response_class=HTMLResponse)
def workspace(request: Request, folder: str):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        counts = bank.counts(conn, oid)
        idx = search_index.status(conn, oid)
        asker.ensure_table(conn)
        recent = [dict(id=r[0], question=r[1]) for r in conn.execute(
            "SELECT id, question FROM ask_log WHERE org_id = %s AND user_id = %s ORDER BY id DESC LIMIT 8", (oid, request.state.user["id"]))]   # each person sees only their own questions
        today = asker.spent_today(conn, oid)
        facets = _facets(conn, oid, folder)
        extracted = conn.execute("SELECT count(DISTINCT paper_id) FROM extractions WHERE org_id = %s AND status = 'ok'", (oid,)).fetchone()[0]
    v = schema_edit.view(folder)
    cap = float(org.get("daily_cap") or 5)
    return _page(request, "workspace.html", org=org, bank=counts, idx=idx, recent=recent, today=today, cap=cap, facets=facets, extracted=extracted,
                 fields=len([c for c in v["cards"] if c["include"]]) if v else 0, roles={k: x[0] for k, x in asker.ROLES.items()},
                 depths=[dict(key=k, label=d["label"], cost=asker.estimate(k)) for k, d in asker.DEPTHS.items()],
                 has_key=apikey.has_key(folder), choose_depth=org.get("allow_depth_choice", "yes") != "no")


@app.get("/org/{folder}/workspace/facets")
def workspace_facets(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        return {"facets": _facets(conn, _org_id(conn, org), folder)}


@app.post("/org/{folder}/workspace/ask")
def workspace_ask(request: Request, folder: str, question: str = Form(""), role: str = Form("general"), depth: str = Form(""), fmt: str = Form(""),
                  mode: str = Form("answer"), filters: str = Form("{}")):
    import json as _json
    user_id = request.state.user["id"]
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    try:
        flt = _json.loads(filters) if filters else {}
    except ValueError:
        flt = {}
    depth = depth if org.get("allow_depth_choice", "yes") != "no" and depth in asker.DEPTHS else org.get("search_depth", "standard")
    fmt = fmt if fmt in asker.FORMATS else org.get("answer_format", "plain")
    syn = _synonyms(folder)
    with connect() as conn:
        oid = _org_id(conn, org)
        paper_ids = _filtered_papers(conn, oid, flt)
        if paper_ids is not None and not paper_ids:
            return JSONResponse({"error": "No extracted papers match those filters yet."}, status_code=400)
        if mode == "search":                     # free: show the best passages, no AI
            if not question.strip():
                return JSONResponse({"error": "Type a question or some search words."}, status_code=400)
            hits = search.search(conn, oid, question.strip(), k=20, per_paper=2, synonyms=syn, paper_ids=paper_ids)
            return {"mode": "search", "passages": search.passages(conn, hits)}
        if not apikey.has_key(folder):
            return JSONResponse({"error": "No Anthropic API key is set. Add one on the Review & Launch page."}, status_code=400)
        if sources.busy(oid):
            return JSONResponse({"error": "Another question is still being answered. Wait for it to finish."}, status_code=409)
    jid = sources.new_job("ask", question[:80], oid, {"quick": "about 15 seconds", "standard": "about 30 seconds", "deep": "about 1 to 2 minutes"}[depth])
    disease = topic_disease(org)
    cap = float(org.get("daily_cap") or 5)

    def work():
        c = connect()
        try:
            sources._set(jid, stage="Searching the library and writing the answer", total=0)
            r = asker.ask(c, oid, question, role=role, depth=depth, fmt=fmt, disease=disease, synonyms=syn, paper_ids=paper_ids, daily_cap=cap, user_id=user_id)
            sources._set(jid, finished=True, stage="Done", result={"kind": "ask", **r})
        except ValueError as e:
            sources._set(jid, finished=True, stage="Failed", error=str(e))
        except Exception as e:
            sources._set(jid, finished=True, stage="Failed", error=f"Something went wrong while answering ({type(e).__name__}). Try again, or use Quick depth.")
        finally:
            c.close()
    sources.start(work)
    return {"job": jid}


def _log_filters(days, who, depth, gaps, q):
    return dict(days=days if days in adminlog.PERIODS else 30, who=who if who == "none" or who.isdigit() else "", depth=depth, gaps=bool(gaps), q=q[:80])


@app.get("/org/{folder}/admin", response_class=HTMLResponse)
def org_admin(request: Request, folder: str, days: int = 30, who: str = "", depth: str = "", gaps: int = 0, q: str = "", p: int = 1, partial: str = ""):
    """Question log, gaps in the library and usage by person, for the organization's administrators."""
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    f = _log_filters(days, who, depth, gaps, q)
    with connect() as conn:
        oid = _org_id(conn, org)
        ctx = dict(org=org, periods=adminlog.PERIODS, days=f["days"], roles=asker.ROLES, filtered=bool(f["who"] or f["depth"] or f["gaps"] or f["q"]))
        if partial != "log":
            ctx.update(summary=adminlog.summary(conn, oid, f["days"]), gaps=adminlog.gaps(conn, oid, f["days"]), people=adminlog.people(conn, oid, f["days"]),
                       member_count=len(auth.people(conn, folder)), cap_note=f"limit ${float(org.get('daily_cap') or 5):.2f} per day")
        if partial == "top":
            return _page(request, "_admin_top.html", **ctx)
        items, total, pages, page = adminlog.log_page(conn, oid, p, **f)
        ctx.update(items=items, total=total, pages=pages, p=page)
        if partial == "log":
            return _page(request, "_admin_log.html", **ctx)
        ctx.update(all_people=adminlog.people(conn, oid, 0))
    return _page(request, "admin.html", **ctx)


@app.get("/org/{folder}/admin/questions.csv")
def org_admin_csv(request: Request, folder: str, days: int = 30, who: str = "", depth: str = "", gaps: int = 0, q: str = ""):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        data = adminlog.export_csv(conn, oid, **_log_filters(days, who, depth, gaps, q))
        auth.audit(conn, request.state.user["id"], "question_log_exported", folder)
        conn.commit()
    return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{folder}-questions.csv"'})


@app.get("/org/{folder}/workspace/answer/{ask_id}")
def workspace_answer(request: Request, folder: str, ask_id: int):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        r = conn.execute("SELECT id, question, answer, sources, user_id FROM ask_log WHERE id = %s AND org_id = %s", (ask_id, oid)).fetchone()
    user = request.state.user
    # a saved answer belongs to the person who asked; administrators can read every answer (they see them on the Admin page too)
    if r and r[4] != user["id"] and not auth.allows(user, folder, "admin"):
        r = None
    if not r:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"kind": "ask", "id": r[0], "question": r[1], "answer": r[2], "sources": r[3]}


@app.post("/org/{folder}/index/build")
def index_build(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
    if sources.busy(oid):
        return JSONResponse({"error": "Another job is still running."}, status_code=409)
    jid = sources.new_job("index", "search index", oid, "a few hours for hundreds of papers; plugged in is faster")

    def work():
        try:
            sources._set(jid, stage="Building the search index")
            res = search_index.build(oid, on_progress=lambda stage, d, t: sources._set(jid, stage=stage, done=d, total=t))
            sources._set(jid, finished=True, stage="Done", result={"kind": "index", **res})
        except Exception as e:
            sources._set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")
    sources.start(work)
    return {"job": jid}


@app.get("/org/{folder}/step/{n}", response_class=HTMLResponse)
def other_steps(request: Request, folder: str, n: int):
    org = _org_or_404(folder)
    if org is None or n not in STEP_TITLES:
        return HTMLResponse("Not found", status_code=404)
    return _page(request, "stub.html", org=org, step=n, title=STEP_TITLES[n])
