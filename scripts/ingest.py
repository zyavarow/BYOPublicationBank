"""Phase 1 ingestion: parse PDFs, resolve DOI/PMID, fetch metadata, dedupe, report.

Usage (from project root):
  .venv\\Scripts\\python.exe scripts\\ingest.py --org "DADA2 Papers" --limit 10
  .venv\\Scripts\\python.exe scripts\\ingest.py --org "DADA2 Papers"          # everything
Re-running skips PDFs that already parsed successfully (use --force to redo).
"""
import argparse
import hashlib
import json
import os
import random
import re
import socket
import sys
import time
import traceback
from pathlib import Path

socket.setdefaulttimeout(60)  # PubMed lookups must fail fast instead of hanging for many minutes

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:  # Windows loads one DLL per name per process: the PDF parser must load BEFORE the database library (libpq),
    import docling_parse.pdf_parsers  # noqa: F401,E402   or it fails with "specified procedure could not be found"
except ImportError:
    pass
from pubbank.db import psycopg  # noqa: E402  (must come before any other psycopg import)
import pypdfium2 as pdfium  # noqa: E402
import requests  # noqa: E402
from Bio import Entrez  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(ROOT / ".env")

SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id serial PRIMARY KEY,
    slug text UNIQUE NOT NULL,
    name text
);
CREATE TABLE IF NOT EXISTS papers (
    id serial PRIMARY KEY,
    org_id int NOT NULL REFERENCES organizations(id),
    filename text NOT NULL,
    relpath text NOT NULL,
    file_sha256 text,
    file_bytes bigint,
    page_count int,
    parse_status text NOT NULL DEFAULT 'pending',
    parse_error text,
    parse_seconds real,
    needs_ocr boolean DEFAULT false,
    chars_per_page real,
    char_count int,
    extracted_title text,
    doi text, pmid text, pmcid text,
    id_source text,
    meta_source text,
    match_score real,
    lookup_error text,
    title text,
    authors jsonb,
    journal text,
    year int,
    abstract text,
    pub_types jsonb,
    full_text text,
    sections jsonb,
    duplicate_of int REFERENCES papers(id),
    duplicate_reason text,
    possible_duplicate_of int REFERENCES papers(id),
    possible_duplicate_reason text,
    possible_non_paper boolean DEFAULT false,
    non_paper_reason text,
    created_at timestamptz DEFAULT now(),
    UNIQUE (org_id, relpath)
);
"""

DOI_RE = re.compile(r'(10\.\d{4,9}/[^\s"<>\]\)\}]+)', re.I)
PMID_RE = re.compile(r'PMID\s*[:#]?\s*(\d{5,9})|PubMed\s*(?:ID|id)\s*[:#]?\s*(\d{5,9})', re.I)

NON_PAPER_PUBTYPES = {
    "Published Erratum", "Retraction of Publication", "Retracted Publication", "Comment",
    "Editorial", "News", "Newspaper Article", "Interview", "Biography", "Portrait",
    "Introductory Journal Article", "Congress", "Lecture", "Webcast",
}
NON_PAPER_FILENAME = re.compile(r"suppl|supporting.?information|appendix|poster|slides|table of contents", re.I)

from pubbank.sections import LEADING_NUMBERING, SECTION_KINDS, section_kind  # noqa: E402,F401
BAD_TITLE = re.compile(
    r"universit|department|institut|corresponding|received|accepted|copyright|©|doi|http|www\.|@|open access|"
    r"research article|original (article|research)|published|keywords|affiliat|abstract|^figure|^table|license|"
    r"journal|volume|issn|author", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split()


def norm_str(s):
    return " ".join(norm(s))


def retry(fn, tries=3, delay=2.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # network hiccups
            last = e
            time.sleep(delay * (i + 1))
    raise last


# ---------------------------------------------------------------- PDF basics
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_text_probe(path):
    """Fast per-page text via pdfium: page count, chars/page, early-page text, metadata."""
    pdf = pdfium.PdfDocument(str(path))
    n = len(pdf)
    counts, early = [], []
    for i in range(n):
        page = pdf[i]
        tp = page.get_textpage()
        txt = tp.get_text_range() or ""
        counts.append(len(txt.strip()))
        if i < 2:
            early.append(txt)
        tp.close()
        page.close()
    try:
        meta = pdf.get_metadata_dict()
    except Exception:
        meta = {}
    pdf.close()
    pages_with_text = sum(1 for c in counts if c >= 200)
    return {
        "page_count": n,
        "chars_per_page": (sum(counts) / n) if n else 0.0,
        "needs_ocr": n > 0 and (pages_with_text / n) < 0.5,
        "early_text": "\n".join(early),
        "meta": meta,
    }


# ------------------------------------------------------------------- Docling
_converter = None


THREADS = os.cpu_count() or 4


def get_converter(tables):
    global _converter
    if _converter is None:
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.do_ocr = False
        opts.do_table_structure = tables
        opts.accelerator_options = AcceleratorOptions(num_threads=THREADS)
        _converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _converter


def title_candidates(md, limit=4):
    """Plausible title lines from the top of the parsed text, for papers where the parser did not
    mark a title. Candidates are only ever used after being verified against PubMed/Crossref."""
    out = []
    for block in re.split(r"\n\s*\n", (md or "")[:6000]):
        b = re.sub(r"<!--.*?-->", "", re.sub(r"^#+\s*", "", block.strip())).strip()
        if re.match(r"^(abstract|summary)\b", b, re.I) and len(b.split()) <= 3:
            break
        if "\n" in b:
            continue
        n_words = len(b.split())
        if 5 <= n_words <= 45 and not BAD_TITLE.search(b) and sum(ch.isalpha() for ch in b) / len(b) > 0.7:
            out.append(b)
            if len(out) >= limit:
                break
    return out


def parse_with_docling(path, tables):
    doc = get_converter(tables).convert(str(path)).document
    sections = []
    cur = {"heading": "(front matter)", "level": 0, "page_start": 1, "kind": "front", "parts": []}
    page1_headers = []
    for item, _lvl in doc.iterate_items():
        label = item.label.value if hasattr(item.label, "value") else str(item.label)
        page = item.prov[0].page_no if getattr(item, "prov", None) else None
        if label in ("page_header", "page_footer", "picture"):
            continue
        if label in ("section_header", "title"):
            if cur["parts"] or cur["heading"] != "(front matter)":
                sections.append(cur)
            heading = (item.text or "").strip()
            cur = {
                "heading": heading, "level": getattr(item, "level", 1) or 1,
                "page_start": page or cur["page_start"],
                "kind": section_kind(heading, cur["kind"]), "parts": [],
            }
            if page == 1 and label in ("section_header", "title"):
                page1_headers.append((label, heading, cur["kind"]))
            continue
        if label == "table":
            try:
                text = item.export_to_markdown(doc=doc)
            except Exception:
                text = getattr(item, "text", "") or ""
        else:
            text = (getattr(item, "text", "") or "").strip()
            if label == "list_item":
                text = "- " + text
        if text:
            cur["parts"].append(text)
    sections.append(cur)
    out = []
    for s in sections:
        out.append({
            "heading": s["heading"], "level": s["level"], "page_start": s["page_start"],
            "kind": s["kind"], "text": "\n\n".join(s["parts"]),
        })
    # title: explicit title label, else longest page-1 header before the abstract
    title = None
    for label, heading, _k in page1_headers:
        if label == "title":
            title = heading
            break
    if not title:
        cands = []
        for label, heading, k in page1_headers:
            if k in ("abstract", "introduction", "methods", "results"):
                break
            if len(heading.split()) >= 4:
                cands.append(heading)
        if cands:
            title = max(cands, key=len)
    return {
        "page_count": len(doc.pages),
        "markdown": doc.export_to_markdown(),
        "sections": out,
        "title": title,
    }


# ------------------------------------------------------- identifiers & lookup
def extract_identifiers(early_text, meta):
    blob_meta = " ".join(str(v) for v in (meta or {}).values())
    dois, pmid = [], None
    for src, text in (("pdf_metadata", blob_meta), ("page_text", early_text)):
        for m in DOI_RE.finditer(text):
            d = m.group(1).rstrip(".,;:")
            prefix = text[max(0, m.start() - 12):m.start()].lower()
            if d.lower() not in [x[0].lower() for x in dois]:
                dois.append((d, src, "doi" in prefix))
    dois.sort(key=lambda x: (not x[2], x[1] != "pdf_metadata"))
    m = PMID_RE.search(early_text)
    if m:
        pmid = m.group(1) or m.group(2)
    return [d for d, _s, _p in dois][:5], pmid


def similarity(cand_title, extracted_title, early_text, allow_page_match=False):
    """0-100 score of how well a looked-up title matches the paper's own title. token_sort_ratio
    penalizes a candidate that is only a fragment of the real title. The page-text check (title
    appears near the top of page 1) is only used when the identifier itself came from the PDF."""
    c = norm_str(cand_title)
    if not c:
        return 0.0
    scores = []
    if extracted_title:
        scores.append(fuzz.token_sort_ratio(c, norm_str(extracted_title)))
    if allow_page_match and len(c) >= 30 and early_text:
        scores.append(fuzz.partial_ratio(c, norm_str(early_text[:2500])))
    return max(scores) if scores else 0.0


def crossref_headers():
    return {"User-Agent": f"PublicationBank/0.1 (mailto:{os.environ.get('NCBI_EMAIL', '')})"}


def crossref_record(msg):
    def year_of(m):
        for k in ("issued", "published-print", "published-online", "published"):
            dp = (m.get(k) or {}).get("date-parts") or [[None]]
            if dp[0] and dp[0][0]:
                return int(dp[0][0])
        return None

    authors = []
    for a in msg.get("author", []) or []:
        nm = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if nm:
            authors.append(nm)
    abstract = re.sub(r"<[^>]+>", " ", msg.get("abstract", "") or "").strip() or None
    return {
        "title": ((msg.get("title") or [""])[0]).strip(),
        "authors": authors,
        "journal": (msg.get("container-title") or [None])[0],
        "year": year_of(msg),
        "abstract": abstract,
        "doi": (msg.get("DOI") or "").lower() or None,
        "type": msg.get("type"),
    }


def crossref_by_doi(doi):
    def go():
        r = requests.get(f"https://api.crossref.org/works/{requests.utils.quote(doi, safe='/')}",
                         headers=crossref_headers(), timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return crossref_record(r.json()["message"])
    return retry(go)


def crossref_by_title(title):
    def go():
        r = requests.get("https://api.crossref.org/works", headers=crossref_headers(), timeout=30,
                         params={"query.bibliographic": title, "rows": 3})
        r.raise_for_status()
        return [crossref_record(m) for m in r.json()["message"].get("items", [])]
    return retry(go)


def entrez_pause():
    time.sleep(0.11 if Entrez.api_key else 0.4)


def pubmed_search(term, retmax=3):
    def go():
        entrez_pause()
        h = Entrez.esearch(db="pubmed", term=term, retmax=retmax)
        try:
            return Entrez.read(h)["IdList"]
        finally:
            h.close()
    return retry(go)


def pubmed_fetch(pmid):
    def go():
        entrez_pause()
        h = Entrez.efetch(db="pubmed", id=str(pmid), retmode="xml")
        try:
            return Entrez.read(h)
        finally:
            h.close()
    data = retry(go)
    arts = data.get("PubmedArticle", [])
    if not arts:
        return None
    rec = arts[0]
    art = rec["MedlineCitation"]["Article"]
    abs_parts = []
    for el in (art.get("Abstract", {}) or {}).get("AbstractText", []) or []:
        label = getattr(el, "attributes", {}).get("Label")
        abs_parts.append(f"{label}: {el}" if label else str(el))
    authors = []
    for a in art.get("AuthorList", []) or []:
        if "CollectiveName" in a:
            authors.append(str(a["CollectiveName"]))
        else:
            nm = " ".join(str(x) for x in (a.get("ForeName") or a.get("Initials"), a.get("LastName")) if x)
            if nm:
                authors.append(nm)
    pd = art["Journal"]["JournalIssue"].get("PubDate", {})
    year = pd.get("Year") or (re.search(r"\d{4}", str(pd.get("MedlineDate", ""))) or [None])[0]
    doi = pmcid = None
    for aid in rec.get("PubmedData", {}).get("ArticleIdList", []) or []:
        t = getattr(aid, "attributes", {}).get("IdType")
        if t == "doi":
            doi = str(aid).lower()
        elif t == "pmc":
            pmcid = str(aid)
    return {
        "pmid": str(rec["MedlineCitation"]["PMID"]),
        "title": str(art["ArticleTitle"]).strip(),
        "abstract": "\n".join(abs_parts) or None,
        "authors": authors,
        "journal": str(art["Journal"].get("Title", "")) or None,
        "year": int(year) if year else None,
        "doi": doi,
        "pmcid": pmcid,
        "pub_types": [str(x) for x in art.get("PublicationTypeList", []) or []],
    }


def build_titles(parsed_title, markdown, filename):
    """Ordered (title, kind) guesses for a paper: parser's title, title-like lines, then the filename
    when it looks like a title fragment rather than 'Author et al. - Year'."""
    titles, seen = [], set()

    def add(t, kind):
        k = norm_str(t)
        if k and k not in seen:
            seen.add(k)
            titles.append((t, kind))

    if parsed_title:
        add(parsed_title, "doc")
    for t in title_candidates(markdown):
        add(t, "block")
    stem = Path(filename).stem
    if " et al" not in stem.lower() and not re.search(r"\s-\s(19|20)\d{2}\s-", stem) and len(stem.split()) >= 4:
        add(stem, "filename")
    return titles


def resolve_metadata(dois, pmid_in_text, titles, early_text, filename):
    """Returns dict of resolved fields (possibly empty) + diagnostics. Accepts a match only if the
    fetched title matches one of the paper's own title guesses (guards against grabbing a cited
    paper's DOI). `titles` is a list of (title, kind) from build_titles()."""
    THRESH = 85
    res = {"meta_source": "none", "id_source": None, "match_score": None, "lookup_error": None}

    def sim(cand_title, page=False):
        return max((similarity(cand_title, t, early_text, allow_page_match=page) for t, _k in titles),
                   default=similarity(cand_title, None, early_text, allow_page_match=page))

    def title_ok(cand_title, t, kind, threshold):
        if kind == "filename":  # filename is often a truncated title: accept a prefix match
            return 90.0 if norm_str(cand_title).startswith(norm_str(t)) else 0.0
        sc = similarity(cand_title, t, early_text)
        return sc if sc >= threshold else 0.0

    try:
        pm = None
        cr = None
        fetched = {}

        def fetch(pid):
            if pid not in fetched:
                fetched[pid] = pubmed_fetch(pid)
            return fetched[pid]

        # 1) DOI candidates from the PDF itself
        for d in dois:
            c = crossref_by_doi(d)
            sc = sim(c["title"], page=True) if c else 0
            if c and sc >= THRESH:
                cr = c
                res.update(id_source="text_doi", match_score=sc)
                ids = pubmed_search(f"{d}[AID]", retmax=1)
                if ids:
                    pm = fetch(ids[0])
                break

        # 2) PMID printed in the PDF
        if not cr and not pm and pmid_in_text:
            cand = fetch(pmid_in_text)
            sc = sim(cand["title"], page=True) if cand else 0
            if cand and sc >= THRESH:
                pm = cand
                res.update(id_source="text_pmid", match_score=sc)

        # 3) Title search in PubMed (exact-title field first, then free text), then Crossref
        if not cr and not pm:
            seen = set()
            for t, kind in titles:
                clean = " ".join(norm(t))
                if len(clean.split()) < 4:
                    continue
                for term in (f"{clean}[Title]", clean):
                    for pid in pubmed_search(term, retmax=3):
                        if pid in seen:
                            continue
                        seen.add(pid)
                        cand = fetch(pid)
                        sc = title_ok(cand["title"], t, kind, 85) if cand else 0
                        if sc:
                            pm = cand
                            res.update(id_source="title_search_pubmed", match_score=sc)
                            break
                    if pm:
                        break
                if pm:
                    break
            if not pm:
                for t, kind in titles:
                    for c in crossref_by_title(t):
                        sc = title_ok(c["title"], t, kind, 90)
                        if sc:
                            cr = c
                            res.update(id_source="title_search_crossref", match_score=sc)
                            ids = pubmed_search(f"{c['doi']}[AID]", retmax=1) if c.get("doi") else []
                            if ids:
                                pm = fetch(ids[0])
                            break
                    if cr:
                        break
    except Exception as e:
        res["lookup_error"] = f"{type(e).__name__}: {e}"[:300]

    src = pm or cr
    if src:
        res["meta_source"] = "pubmed" if pm else "crossref"
        res.update(
            title=src["title"], authors=src["authors"], journal=src["journal"], year=src["year"],
            abstract=src["abstract"] or (cr or {}).get("abstract"),
            doi=(pm or {}).get("doi") or (cr or {}).get("doi"),
            pmid=(pm or {}).get("pmid"), pmcid=(pm or {}).get("pmcid"),
            pub_types=(pm or {}).get("pub_types") or ([cr["type"]] if cr and cr.get("type") else []),
        )
    return res


# ------------------------------------------------------------ classification
def classify_non_paper(row):
    reasons = []
    bad = [t for t in (row.get("pub_types") or []) if t in NON_PAPER_PUBTYPES]
    if bad:
        reasons.append("PubMed type: " + ", ".join(bad))
    if NON_PAPER_FILENAME.search(row["filename"]):
        reasons.append("filename suggests supplement/poster/slides")
    if row["parse_status"] == "ok":
        if (row.get("char_count") or 0) < 2500:
            reasons.append("very little text")
        kinds = {s["kind"] for s in (row.get("sections") or [])}
        if not ({"abstract", "introduction", "methods", "results"} & kinds) and row.get("meta_source") == "none":
            reasons.append("no paper-like sections and no PubMed/Crossref match")
    return "; ".join(reasons) or None


# --------------------------------------------------------------------- main
def process_pdf(path, rel, tables):
    row = {"filename": path.name, "relpath": rel, "parse_status": "pending"}
    t0 = time.time()
    row["file_sha256"] = sha256_of(path)
    row["file_bytes"] = path.stat().st_size
    try:
        probe = pdf_text_probe(path)
    except Exception as e:
        row.update(parse_status="failed", parse_error=f"PDF unreadable: {type(e).__name__}: {e}"[:400],
                   parse_seconds=time.time() - t0)
        return row
    row.update(page_count=probe["page_count"], chars_per_page=probe["chars_per_page"], needs_ocr=probe["needs_ocr"])
    early = probe["early_text"]
    extracted_title = None
    if probe["needs_ocr"]:
        row["parse_status"] = "skipped_ocr"
        row["full_text"] = ""
        row["sections"] = []
        row["char_count"] = 0
    else:
        try:
            parsed = parse_with_docling(path, tables)
            row.update(parse_status="ok", full_text=parsed["markdown"], sections=parsed["sections"],
                       char_count=len(parsed["markdown"]), page_count=parsed["page_count"])
            extracted_title = parsed["title"]
        except Exception as e:
            row.update(parse_status="failed", parse_error=f"{type(e).__name__}: {e}"[:400])
    titles = build_titles(extracted_title, row.get("full_text"), path.name)
    row["extracted_title"] = extracted_title or next((t for t, k in titles if k == "block"), None)
    if row["parse_status"] != "failed" or early.strip():
        dois, pmid = extract_identifiers(early, probe["meta"])
        row.update(resolve_metadata(dois, pmid, titles, early, path.name))
    row["parse_seconds"] = time.time() - t0
    row["possible_non_paper_reason"] = classify_non_paper(row)
    return row


def relabel_sections(conn, org_id):
    """Recompute section kinds from stored headings (no re-parsing)."""
    n = 0
    for pid, secs in conn.execute(
            "SELECT id, sections FROM papers WHERE org_id=%s AND parse_status='ok' AND sections IS NOT NULL",
            (org_id,)).fetchall():
        prev = "front"
        for s in secs:
            s["kind"] = "front" if s["heading"] == "(front matter)" else section_kind(s["heading"], prev)
            prev = s["kind"]
        conn.execute("UPDATE papers SET sections=%s WHERE id=%s", (Jsonb(secs), pid))
        n += 1
    conn.commit()
    print(f"Relabeled sections for {n} papers")


def rematch_unmatched(conn, org_id, folder):
    """Retry identifier/metadata lookup for papers still unmatched or with lookup errors, using the
    stored text (no re-parsing). Also refreshes the non-paper flag."""
    rows = conn.execute(
        "SELECT id, relpath, filename, extracted_title, full_text, parse_status, char_count, sections "
        "FROM papers WHERE org_id=%s AND parse_status IN ('ok','skipped_ocr') "
        "AND (meta_source='none' OR lookup_error IS NOT NULL) ORDER BY filename", (org_id,)).fetchall()
    print(f"Re-trying lookup for {len(rows)} papers")
    for pid, rel, fn, ext_title, text, status, nchars, secs in rows:
        probe = pdf_text_probe(folder / rel)
        titles = build_titles(ext_title, text, fn)
        dois, pmid = extract_identifiers(probe["early_text"], probe["meta"])
        res = resolve_metadata(dois, pmid, titles, probe["early_text"], fn)
        fields = {k: None for k in ("doi", "pmid", "pmcid", "id_source", "match_score", "lookup_error", "title",
                                    "authors", "journal", "year", "abstract", "pub_types")}
        fields.update({k: v for k, v in res.items() if k in fields or k == "meta_source"})
        fields["extracted_title"] = ext_title or next((t for t, k in titles if k == "block"), None)
        reason = classify_non_paper({"filename": fn, "parse_status": status, "char_count": nchars,
                                     "sections": secs, "meta_source": fields["meta_source"],
                                     "pub_types": fields["pub_types"]})
        fields.update(possible_non_paper=bool(reason), non_paper_reason=reason)
        fields = clean_nul(fields)
        for k in ("authors", "pub_types"):
            if fields.get(k) is not None:
                fields[k] = Jsonb(fields[k])
        conn.execute(f"UPDATE papers SET {', '.join(f'{k}=%s' for k in fields)} WHERE id=%s",
                     [*fields.values(), pid])
        conn.commit()
        print(f"  {fields['meta_source']:<8} {fn[:70]}", flush=True)


def clean_nul(obj):
    """Postgres text/jsonb cannot hold NUL characters, which some PDFs contain."""
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, list):
        return [clean_nul(x) for x in obj]
    if isinstance(obj, dict):
        return {k: clean_nul(v) for k, v in obj.items()}
    return obj


RESETTABLE = ["page_count", "parse_error", "parse_seconds", "needs_ocr", "chars_per_page", "char_count",
              "extracted_title", "doi", "pmid", "pmcid", "id_source", "meta_source", "match_score",
              "lookup_error", "title", "authors", "journal", "year", "abstract", "pub_types", "full_text",
              "sections", "file_sha256", "file_bytes"]


def save_row(conn, org_id, row):
    row = {**{k: None for k in RESETTABLE}, **clean_nul(row)}
    non_paper = row.pop("possible_non_paper_reason", None)
    row["possible_non_paper"] = bool(non_paper)
    row["non_paper_reason"] = non_paper
    row["org_id"] = org_id
    for k in ("authors", "pub_types", "sections"):
        if k in row and row[k] is not None:
            row[k] = Jsonb(row[k])
    cols = list(row.keys())
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("org_id", "relpath"))
    conn.execute(
        f"INSERT INTO papers ({', '.join(cols)}) VALUES ({', '.join('%s' for _ in cols)}) "
        f"ON CONFLICT (org_id, relpath) DO UPDATE SET {upd}",
        [row[c] for c in cols],
    )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True, help="folder name inside data/raw/")
    ap.add_argument("--limit", type=int, help="process only a random sample of N PDFs")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--also", action="append", default=[], help="also include PDFs whose name contains this text")
    ap.add_argument("--force", action="store_true", help="re-process PDFs already done")
    ap.add_argument("--tables", action="store_true", help="enable Docling table analysis (slower)")
    ap.add_argument("--only", action="append", default=[], help="restrict to PDFs whose name contains this text")
    ap.add_argument("--shard", help="K/N: process only every N-th PDF starting at K (run N copies in parallel)")
    ap.add_argument("--threads", type=int, help="CPU threads for this process (default: all cores)")
    ap.add_argument("--no-report", action="store_true", help="skip dedupe + report (use with --shard)")
    ap.add_argument("--flag-software", action="store_true",
                    help="also flag DADA2-software/sequencing papers (only meaningful for the DADA2 library)")
    ap.add_argument("--relabel", action="store_true", help="recompute section labels from stored headings, then report")
    ap.add_argument("--rematch", action="store_true", help="retry PubMed/Crossref lookup for unmatched papers, then report")
    ap.add_argument("--mark-failed", help="record this PDF (relative path) as a parse failure and exit")
    ap.add_argument("--reason", help="failure reason for --mark-failed")
    ap.add_argument("--list", help="text file with one PDF filename per line; process only these")
    ap.add_argument("--min-pages", type=int, help="only PDFs with at least this many pages")
    ap.add_argument("--max-pages", type=int, help="only PDFs with at most this many pages")
    args = ap.parse_args()
    global THREADS
    if args.threads:
        THREADS = args.threads
    if sys.platform == "win32":
        import ctypes  # each worker must hold its own request, or Windows Modern Standby freezes it
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

    Entrez.email = os.environ.get("NCBI_EMAIL") or None
    Entrez.api_key = os.environ.get("NCBI_API_KEY") or None
    if not Entrez.email:
        sys.exit("NCBI_EMAIL missing from .env")

    folder = ROOT / "data" / "raw" / args.org
    pdfs = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".pdf")
    if not pdfs:
        sys.exit(f"No PDFs found in {folder}")
    chosen = pdfs
    if args.limit:
        chosen = random.Random(args.seed).sample(pdfs, min(args.limit, len(pdfs)))
    for pat in args.also:
        chosen += [p for p in pdfs if pat.lower() in p.name.lower() and p not in chosen]
    chosen = sorted(set(chosen))
    if args.only:
        chosen = [p for p in chosen if any(o.lower() in p.name.lower() for o in args.only)]
    if args.list:
        wanted = {ln.strip() for ln in Path(args.list).read_text(encoding="utf-8-sig").splitlines() if ln.strip()}
        chosen = [p for p in chosen if p.name in wanted]
    if args.min_pages or args.max_pages:
        def n_pages(p):
            try:
                pdf = pdfium.PdfDocument(str(p))
                n = len(pdf)
                pdf.close()
                return n
            except Exception:
                return None  # unreadable files stay in; they get recorded as failures
        kept = []
        for p in chosen:
            n = n_pages(p)
            if n is None or ((not args.min_pages or n >= args.min_pages) and (not args.max_pages or n <= args.max_pages)):
                kept.append(p)
        chosen = kept
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        chosen = [p for i, p in enumerate(chosen) if i % n == k - 1]
    print(f"{len(pdfs)} PDFs in folder; processing {len(chosen)}", flush=True)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(SCHEMA)
        conn.commit()
        org_id = conn.execute(
            "INSERT INTO organizations (slug, name) VALUES (%s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id", (args.org, args.org)
        ).fetchone()[0]
        conn.commit()
        if args.mark_failed:
            save_row(conn, org_id, {"filename": Path(args.mark_failed).name, "relpath": args.mark_failed,
                                    "parse_status": "failed", "parse_error": (args.reason or "marked failed")[:400]})
            print(f"Recorded as failed: {args.mark_failed}")
            return
        if args.relabel or args.rematch:
            if args.relabel:
                relabel_sections(conn, org_id)
            if args.rematch:
                rematch_unmatched(conn, org_id, folder)
            from dedupe import dedupe_org
            dedupe_org(conn, org_id)
            if args.flag_software:
                from flag_software import flag_software
                flag_software(conn, org_id)
            from report import build_report
            build_report(conn, org_id, args.org, ROOT / "data" / "reports")
            return
        done = {r[0] for r in conn.execute(
            "SELECT relpath FROM papers WHERE org_id=%s AND parse_status IN ('ok','skipped_ocr','duplicate_skipped')",
            (org_id,))}

        # Exact-duplicate files (same bytes): keep one canonical copy, record the others without parsing.
        sha_of = {p: sha256_of(p) for p in pdfs}
        by_sha = {}
        for p in pdfs:
            by_sha.setdefault(sha_of[p], []).append(p)
        non_canonical = set()
        for group in by_sha.values():
            group.sort(key=lambda p: (bool(re.search(r"-\d+\.pdf$", p.name, re.I)), p.name))
            non_canonical.update(group[1:])

        t_start = time.time()
        n_done = 0
        for i, path in enumerate(chosen, 1):
            rel = path.relative_to(folder).as_posix()
            if rel in done and not args.force:
                print(f"[{i}/{len(chosen)}] skip (already done): {path.name}", flush=True)
                continue
            if path in non_canonical:
                row = {"filename": path.name, "relpath": rel, "parse_status": "duplicate_skipped",
                       "file_sha256": sha_of[path], "file_bytes": path.stat().st_size}
                save_row(conn, org_id, row)
                print(f"[{i}/{len(chosen)}] duplicate copy, not parsed: {path.name}", flush=True)
                continue
            print(f"[{i}/{len(chosen)}] starting: {path.name}", flush=True)
            try:
                row = process_pdf(path, rel, args.tables)
            except Exception:
                row = {"filename": path.name, "relpath": rel, "parse_status": "failed",
                       "parse_error": traceback.format_exc()[-400:]}
            try:
                save_row(conn, org_id, row)
            except Exception as e:
                conn.rollback()
                row = {"filename": path.name, "relpath": rel, "parse_status": "failed",
                       "parse_error": f"could not save to database: {type(e).__name__}: {e}"[:400]}
                save_row(conn, org_id, row)
            n_done += 1
            print(f"[{i}/{len(chosen)}] {row['parse_status']:<11} {row.get('parse_seconds') or 0:6.1f}s "
                  f"{row.get('page_count') or '?':>3}p  match={row.get('meta_source', '-'):<8} {path.name[:60]}", flush=True)
        print(f"Parsed {n_done} files in {(time.time() - t_start) / 60:.1f} min", flush=True)

        if args.no_report:
            return
        from dedupe import dedupe_org
        dedupe_org(conn, org_id)
        if args.flag_software:
            from flag_software import flag_software
            flag_software(conn, org_id)
        from report import build_report
        build_report(conn, org_id, args.org, ROOT / "data" / "reports")


if __name__ == "__main__":
    main()
