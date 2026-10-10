"""Split papers into passages for search ("chunks"): about 350 tokens each, cut at paragraph and sentence boundaries inside one section,
with a little overlap, so every passage keeps its paper, section heading and page. The reference list and boilerplate sections are dropped.
(The search model reads at most 512 tokens, so passages are shorter than the 500-800 originally planned.)
"""
import re

TARGET_CHARS = 1350          # about 350 tokens
MAX_CHARS = 1900
OVERLAP_CHARS = 220
MIN_CHARS = 120
SKIP_KINDS = {"references"}
SKIP_HEADING = re.compile(r"^\W*(\d+\W*)?(references?|bibliography|literature cited|reference list|acknowledg\w*|funding|conflicts? of interest|"
                          r"competing interests?|declarations?|author contributions?|authors?'? contributions?|supplementary\b.*|supporting information|"
                          r"data availability.*|availability of data.*|ethics (statement|approval).*|abbreviations?|disclosures?|"
                          r"publisher'?s? note|copyright.*)\W*$", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


def _split_long(par):
    """Break an over-long paragraph at sentence boundaries into pieces of at most MAX_CHARS."""
    if len(par) <= MAX_CHARS:
        return [par]
    out, cur = [], ""
    for sent in SENTENCE_END.split(par):
        while len(sent) > MAX_CHARS:                        # a table row or a run-on with no sentence ends
            out.append(sent[:MAX_CHARS])
            sent = sent[MAX_CHARS - OVERLAP_CHARS:]
        if cur and len(cur) + len(sent) + 1 > TARGET_CHARS:
            out.append(cur)
            cur = cur[-OVERLAP_CHARS:].split(" ", 1)[-1] + " " + sent
        else:
            cur = (cur + " " + sent).strip()
    if cur:
        out.append(cur)
    return out


def chunk_section(text):
    """Passages (strings) for one section's text."""
    pieces = []
    for par in re.split(r"\n\s*\n", text or ""):
        par = re.sub(r"[ \t]+", " ", par).strip()
        if par:
            pieces += _split_long(par)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) + 2 > TARGET_CHARS:
            chunks.append(cur)
            cur = (cur[-OVERLAP_CHARS:].split(" ", 1)[-1] + "\n\n" + p) if not p.startswith("|") else p
        else:
            cur = (cur + "\n\n" + p).strip()
    if cur:
        chunks.append(cur)
    # a last scrap too short to stand alone joins the previous passage
    if len(chunks) > 1 and len(chunks[-1]) < MIN_CHARS:
        chunks[-2] = chunks[-2] + "\n\n" + chunks.pop()
    return [c for c in chunks if len(c.strip()) >= MIN_CHARS or len(chunks) == 1]


def chunk_paper(paper):
    """List of dicts(idx, section_kind, heading, page, text) for one paper (a dict with 'sections', 'abstract', 'full_text')."""
    out = []
    sections = paper.get("sections") or []
    for s in sections:
        kind, heading, body = s.get("kind") or "", (s.get("heading") or "").strip(), s.get("text") or ""
        if kind in SKIP_KINDS or SKIP_HEADING.match(heading):
            continue
        # title pages are noise, but short letters and case reports keep their whole text under unlabeled 'front' headings
        if kind == "front" and (heading == "(front matter)" or len(body) < 400):
            continue
        for text in chunk_section(body):
            if len(text.strip()) >= 60:
                out.append({"idx": len(out), "section_kind": kind, "heading": heading[:200], "page": s.get("page_start"), "text": text.strip()})
    if not out:                                     # nothing usable in the sections: fall back to the abstract or the whole text
        raw = paper.get("abstract") or paper.get("full_text") or ""
        for text in chunk_section(raw):
            if len(text.strip()) >= 60:
                out.append({"idx": len(out), "section_kind": "abstract" if paper.get("abstract") else "text", "heading": "", "page": None, "text": text.strip()})
    return out
