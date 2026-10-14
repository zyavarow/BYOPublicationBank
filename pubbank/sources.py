"""Ways of filling the publication bank other than PDFs: a list of PubMed IDs, or a reference-manager export (RIS, BibTeX, CSV).

For each paper we get clean metadata and the abstract from PubMed; when PubMed Central (PMC) has the article text we fetch that too,
so the paper is read in full. Otherwise it is stored as "abstract only" and that label follows the paper everywhere.
Everything here is plain Python (no PDF parsing), so it can run inside the web app.
"""
import csv
import hashlib
import io
import os
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET

from Bio import Entrez
from rapidfuzz import fuzz

from pubbank.db import Jsonb, connect
from pubbank.sections import section_kind

socket.setdefaulttimeout(60)
BATCH = 100
MIN_BODY_CHARS = 1500            # a PMC "article" with less body text than this is a stub, not full text
MIN_ABSTRACT_CHARS = 200
NON_PAPER_TYPES = {"Published Erratum", "Retraction of Publication", "Retracted Publication", "Comment", "Editorial", "News",
                   "Newspaper Article", "Interview", "Biography", "Portrait", "Congress", "Lecture", "Webcast"}


def _entrez():
    Entrez.email = os.environ.get("NCBI_EMAIL") or "unknown@example.org"
    Entrez.api_key = os.environ.get("NCBI_API_KEY") or None
    return 0.11 if Entrez.api_key else 0.34


NEW_COLUMNS = {"source": "text", "text_level": "text", "topic_status": "text", "topic_reason": "text", "topic_method": "text"}


def ensure_columns(conn):
    """Columns that say where a paper came from, how much of it we have, and whether it is on topic."""
    have = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'papers' AND column_name = ANY(%s)", (list(NEW_COLUMNS),))}
    if have == set(NEW_COLUMNS):
        conn.commit()      # nothing to change (and no table lock taken, which could wait on other readers)
        return
    for col, typ in NEW_COLUMNS.items():
        conn.execute(f"ALTER TABLE papers ADD COLUMN IF NOT EXISTS {col} {typ}")
    conn.execute("UPDATE papers SET source = 'pdf' WHERE source IS NULL AND relpath NOT LIKE 'pmid:%' AND relpath NOT LIKE 'ref:%'")
    conn.execute("UPDATE papers SET text_level = 'full_text' WHERE text_level IS NULL AND source = 'pdf' AND parse_status = 'ok'")
    conn.commit()


# ---------------------------------------------------------------------------------------------- PubMed IDs
def parse_pmids(text):
    """Returns (unique PubMed IDs in the order given, the pieces that are not IDs)."""
    good, bad = [], []
    text = re.sub(r"(?i)\b(pmid|pubmed id|pubmed)\s*[:\-]?\s*", "", text)
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        t = tok
        if re.fullmatch(r"[1-9]\d{0,8}", t):
            if t not in good:
                good.append(t)
        else:
            bad.append(tok)
    return good, bad


def _txt(el):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(el))).strip()


def _year(s):
    m = re.search(r"(19|20)\d{2}", str(s or ""))
    return int(m.group(0)) if m else None


def fetch_pubmed(pmids, on_progress=None):
    """PubMed records for the given IDs: {pmid: {title, authors, journal, year, doi, pmcid, abstract, pub_types}}."""
    pause = _entrez()
    out = {}
    for i in range(0, len(pmids), BATCH):
        batch = pmids[i:i + BATCH]
        h = Entrez.efetch(db="pubmed", id=",".join(batch), rettype="xml", retmode="xml")
        data = Entrez.read(h)
        h.close()
        for art in data.get("PubmedArticle", []):
            mc, a = art["MedlineCitation"], art["MedlineCitation"]["Article"]
            pmid = str(mc["PMID"])
            names = []
            for au in a.get("AuthorList", []):
                if "CollectiveName" in au:
                    names.append(_txt(au["CollectiveName"]))
                elif "LastName" in au:
                    names.append(f"{au.get('ForeName', au.get('Initials', ''))} {au['LastName']}".strip())
            pd = a.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
            year = _year(pd.get("Year") or pd.get("MedlineDate")) or _year(next(iter(a.get("ArticleDate", [])), {}).get("Year"))
            parts = []
            for t in a.get("Abstract", {}).get("AbstractText", []):
                label = getattr(t, "attributes", {}).get("Label")
                parts.append((f"{label}: " if label else "") + _txt(t))
            ids = {str(x.attributes.get("IdType")): str(x) for x in art.get("PubmedData", {}).get("ArticleIdList", [])}
            out[pmid] = {
                "pmid": pmid, "title": _txt(a.get("ArticleTitle", "")), "authors": names,
                "journal": _txt(a.get("Journal", {}).get("Title", "")), "year": year,
                "doi": ids.get("doi", "").lower() or None, "pmcid": ids.get("pmc") or None,
                "abstract": "\n".join(p for p in parts if p).strip(),
                "pub_types": [str(x) for x in a.get("PublicationTypeList", [])],
            }
        if on_progress:
            on_progress(min(i + BATCH, len(pmids)), len(pmids))
        time.sleep(pause)
    return out


# ---------------------------------------------------------------------------------------------- PMC full text
def _jats_text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _table_text(tw):
    rows = []
    cap = tw.find("caption")
    if cap is not None:
        rows.append(_jats_text(cap))
    for tr in tw.iter("tr"):
        cells = [_jats_text(c) for c in tr if c.tag in ("td", "th")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(r for r in rows if r)


def jats_sections(xml_bytes):
    """Sections [{heading, level, page_start, kind, text}] from a PMC (JATS) article, or None if it has no real body text."""
    root = ET.fromstring(xml_bytes)
    body = root.find(".//body")
    if body is None:
        return None
    sections = []

    def walk(sec, level, parent_kind):
        title = sec.find("title")
        heading = _jats_text(title) if title is not None else ""
        kind = section_kind(heading, parent_kind) if heading else parent_kind
        paras = []
        for ch in sec:
            if ch.tag == "p":
                paras.append(_jats_text(ch))
            elif ch.tag == "table-wrap":
                paras.append(_table_text(ch))
        if heading or paras:
            sections.append({"heading": heading or "(untitled)", "level": level, "page_start": None, "kind": kind,
                             "text": "\n\n".join(p for p in paras if p)})
        for ch in sec:
            if ch.tag == "sec":
                walk(ch, level + 1, kind)

    loose = [_jats_text(p) for p in body if p.tag == "p"]
    if loose:
        sections.append({"heading": "(body)", "level": 1, "page_start": None, "kind": "introduction", "text": "\n\n".join(loose)})
    prev = "introduction"
    for sec in body.findall("sec"):
        walk(sec, 1, prev)
        prev = sections[-1]["kind"] if sections else prev
    if sum(len(s["text"]) for s in sections) < MIN_BODY_CHARS:
        return None
    return sections


def fetch_pmc_sections(pmcid):
    """Full-text sections for one PMC article, or None when PMC has no article body for it (not open access)."""
    num = re.sub(r"\D", "", pmcid)
    try:
        h = Entrez.efetch(db="pmc", id=num, rettype="xml", retmode="xml")
        data = h.read()
        h.close()
        return jats_sections(data)
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------- reference files
def _name_first_last(n):
    n = n.strip()
    if "," in n:
        last, first = n.split(",", 1)
        return f"{first.strip()} {last.strip()}".strip()
    return n


def parse_ris(text):
    recs, cur, last = [], {}, None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            last = tag
            if tag == "TY":
                cur = {"_authors": [], "_notes": []}
            elif tag == "ER":
                if cur:
                    recs.append(cur)
                cur, last = {}, None
            elif cur is not None and cur != {}:
                if tag in ("AU", "A1", "A2"):
                    cur["_authors"].append(_name_first_last(val))
                elif tag in ("N1", "AN", "ID", "UR", "L2", "M3"):
                    cur["_notes"].append(val)
                else:
                    cur.setdefault(tag, val)
        elif cur and last in ("TI", "T1", "AB", "N2") and line.strip():
            cur[last] = cur.get(last, "") + " " + line.strip()
    out = []
    for r in recs:
        blob = " ".join(r["_notes"])
        pm = re.search(r"(?:pmid|pubmed)[:\s/]*?(\d{5,9})", blob, re.I) or re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", blob)
        out.append({"title": r.get("TI") or r.get("T1") or "", "authors": r["_authors"], "year": _year(r.get("PY") or r.get("Y1") or r.get("DA")),
                    "journal": r.get("JO") or r.get("JF") or r.get("T2") or r.get("JA") or "", "doi": (r.get("DO") or "").lower().replace("https://doi.org/", "") or None,
                    "abstract": r.get("AB") or r.get("N2") or "", "pmid": pm.group(1) if pm else None})
    return out


def _bib_fields(body):
    """Fields of one BibTeX entry: handles {nested braces}, "quotes" and bare numbers."""
    fields, i, n = {}, 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z][\w-]*)\s*=\s*").match(body, i)
        if not m:
            i += 1
            continue
        key, i = m.group(1).lower(), m.end()
        if i < n and body[i] == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                depth += (body[j] == "{") - (body[j] == "}")
                j += 1
            val, i = body[i + 1:j - 1], j
        elif i < n and body[i] == '"':
            j = body.index('"', i + 1) if '"' in body[i + 1:] else n
            val, i = body[i + 1:j], j + 1
        else:
            m2 = re.compile(r"[^,\n]*").match(body, i)
            val, i = m2.group(0).strip(), m2.end()
        fields[key] = re.sub(r"\s+", " ", re.sub(r"[{}]", "", val)).strip()
    return fields


def parse_bibtex(text):
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        start, depth, j = m.end(), 1, m.end()
        while j < len(text) and depth:
            depth += (text[j] == "{") - (text[j] == "}")
            j += 1
        f = _bib_fields(text[start:j - 1])
        pm = f.get("pmid") or (f.get("eprint") if "pub" in f.get("archiveprefix", "").lower() else None)
        pm = pm or (re.search(r"(?:pmid|pubmed)[:\s/]*?(\d{5,9})", f.get("note", "") + " " + f.get("url", ""), re.I) or [None, None])[1]
        out.append({"title": f.get("title", ""), "authors": [_name_first_last(a) for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()],
                    "year": _year(f.get("year") or f.get("date")), "journal": f.get("journal") or f.get("journaltitle") or f.get("booktitle", ""),
                    "doi": (f.get("doi") or "").lower().replace("https://doi.org/", "") or None, "abstract": f.get("abstract", ""),
                    "pmid": pm if pm and re.fullmatch(r"\d{1,9}", str(pm)) else None})
    return out


def parse_csv(text):
    """Zotero / EndNote / generic CSV: picks columns by common header names."""
    rd = csv.DictReader(io.StringIO(text))
    cols = {(c or "").strip().lower(): c for c in (rd.fieldnames or [])}

    def col(row, *names):
        for n in names:
            if n in cols and (row.get(cols[n]) or "").strip():
                return row[cols[n]].strip()
        return ""
    out = []
    for row in rd:
        authors = [a for a in re.split(r"\s*;\s*|\s+and\s+", col(row, "author", "authors", "author(s)")) if a]
        extra = col(row, "extra", "notes", "note", "url", "link")
        pm = col(row, "pmid", "pubmed id", "pubmed_id") or (re.search(r"(?:pmid|pubmed)[:\s/]*?(\d{5,9})", extra, re.I) or [None, None])[1]
        out.append({"title": col(row, "title", "article title"), "authors": [_name_first_last(a) for a in authors],
                    "year": _year(col(row, "publication year", "year", "date", "publication date")),
                    "journal": col(row, "publication title", "journal", "journal/book", "source title", "secondary title"),
                    "doi": col(row, "doi").lower().replace("https://doi.org/", "") or None,
                    "abstract": col(row, "abstract note", "abstract"), "pmid": pm if pm and re.fullmatch(r"\d{1,9}", str(pm)) else None})
    return out


def parse_reference_file(filename, data):
    """Parse an uploaded export. Returns (records, format name). Raises ValueError for a format we cannot read."""
    text = data.decode("utf-8-sig", errors="replace") if data[:3] != b"\xff\xfe" else data.decode("utf-16", errors="replace")
    low = filename.lower()
    if low.endswith((".ris", ".txt")) and re.search(r"^TY  - ", text, re.M):
        fmt, recs = "RIS", parse_ris(text)
    elif low.endswith((".bib", ".bibtex")) or re.search(r"^@\w+\s*\{", text, re.M):
        fmt, recs = "BibTeX", parse_bibtex(text)
    elif low.endswith(".csv"):
        fmt, recs = "CSV", parse_csv(text)
    elif re.search(r"^TY  - ", text, re.M):
        fmt, recs = "RIS", parse_ris(text)
    elif low.endswith(".xml"):
        raise ValueError("EndNote XML is not supported yet. In EndNote choose File > Export and save as RIS, or use Zotero's CSV.")
    else:
        raise ValueError("Could not read that file. Export your references as RIS, BibTeX or CSV (Zotero and EndNote can all do this).")
    recs = [r for r in recs if r["title"] or r["doi"] or r["pmid"]]
    if not recs:
        raise ValueError("No references were found in that file.")
    return recs, fmt


# ---------------------------------------------------------------------------------------------- matching to PubMed
def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split())


def _esearch_one(term, retmax=3):
    h = Entrez.esearch(db="pubmed", term=term, retmax=retmax)
    ids = Entrez.read(h)["IdList"]
    h.close()
    return ids


STOPWORDS = {"with", "from", "that", "this", "have", "been", "were", "which", "their", "into", "than", "such", "also", "between",
             "among", "after", "before", "using", "about", "over", "under", "does", "what", "when", "where", "associated", "study"}


def _title_search(title, pause):
    """Candidate PubMed IDs for a title: the exact phrase first, then the title's main words (punctuation breaks exact phrases)."""
    clean = _norm(title)
    ids = _esearch_one(f'"{clean}"[Title]')
    time.sleep(pause)
    if ids:
        return ids
    words = [w for w in dict.fromkeys(clean.split()) if len(w) >= 4 and w not in STOPWORDS][:9]
    if len(words) < 3:
        return []
    ids = _esearch_one(" AND ".join(f"{w}[Title]" for w in words), retmax=15)
    time.sleep(pause)
    return ids


def match_references(records, on_progress=None):
    """Attach a PubMed ID to each reference where one can be found (given PMID, then DOI, then title). Returns the list of
    PMIDs found; each record gets 'pmid' set or stays unmatched."""
    pause = _entrez()
    pending_titles = []
    for i, r in enumerate(records):
        if r.get("pmid"):
            continue
        try:
            if r.get("doi"):
                ids = _esearch_one(f'"{r["doi"]}"[AID]')
                time.sleep(pause)
                if len(ids) == 1:
                    r["pmid"], r["match"] = ids[0], "doi"
                    continue
            if len(_norm(r["title"])) >= 15:
                ids = _title_search(r["title"], pause)
                if ids:
                    pending_titles.append((r, ids))
        except Exception:
            pass
        if on_progress:
            on_progress(i + 1, len(records))
    cand = sorted({x for _r, ids in pending_titles for x in ids})
    found = fetch_pubmed(cand) if cand else {}
    for r, ids in pending_titles:
        best = max(((fuzz.token_sort_ratio(_norm(r["title"]), _norm(found[x]["title"])), x) for x in ids if x in found), default=(0, None))
        ok_year = not r.get("year") or not found.get(best[1], {}).get("year") or abs(r["year"] - found[best[1]]["year"]) <= 1
        if best[0] >= 90 and ok_year:
            r["pmid"], r["match"] = best[1], "title"
    for r in records:
        if r.get("pmid") and "match" not in r:
            r["match"] = "pmid"
    return [r["pmid"] for r in records if r.get("pmid")]


# ---------------------------------------------------------------------------------------------- saving
def _ref_key(r):
    basis = (r.get("doi") or "") + "|" + _norm(r.get("title"))
    return "ref:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def existing_keys(conn, org_id):
    pm, doi, rel = set(), set(), set()
    for p, d, rp in conn.execute("SELECT pmid, doi, relpath FROM papers WHERE org_id = %s", (org_id,)):
        if p:
            pm.add(str(p))
        if d:
            doi.add(d.lower())
        rel.add(rp)
    return pm, doi, rel


def save_paper(conn, org_id, rec, source, sections=None):
    """Insert one paper. `rec` has the PubMed (or reference) metadata. Returns the text level stored."""
    abstract = rec.get("abstract") or ""
    if sections:
        level = "full_text"
        abs_sec = [{"heading": "Abstract", "level": 1, "page_start": None, "kind": "abstract", "text": abstract}] if abstract else []
        secs = abs_sec + sections
    elif len(abstract) >= MIN_ABSTRACT_CHARS:
        level = "abstract_only"
        secs = [{"heading": "Abstract", "level": 1, "page_start": None, "kind": "abstract", "text": abstract}]
    else:
        level, secs = "citation_only", []
    full_text = "\n\n".join((f"{s['heading']}\n{s['text']}" if s["heading"] else s["text"]) for s in secs if s["text"] or s["heading"])
    types = rec.get("pub_types") or []
    reason = None
    bad = [t for t in types if t in NON_PAPER_TYPES]
    if bad:
        reason = ("retracted" if any("Retract" in t for t in bad) else "possible non-paper: " + bad[0])
    elif level == "citation_only":
        reason = "no abstract or full text available"
    in_bank = reason is None
    relpath = f"pmid:{rec['pmid']}" if rec.get("pmid") else _ref_key(rec)
    label = rec.get("title") or rec.get("doi") or relpath
    conn.execute(
        """INSERT INTO papers (org_id, filename, relpath, parse_status, needs_ocr, char_count, doi, pmid, pmcid, id_source, meta_source,
               match_score, title, authors, journal, year, abstract, pub_types, full_text, sections, in_bank, bank_reason,
               bank_decision, source, text_level, possible_non_paper, non_paper_reason)
           VALUES (%s,%s,%s,'ok',false,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s)
           ON CONFLICT (org_id, relpath) DO NOTHING""",
        (org_id, label[:300], relpath, len(full_text), rec.get("doi"), rec.get("pmid"), rec.get("pmcid"),
         "reference_list" if source == "reference_list" else "pmid_list", "pubmed" if rec.get("pmid") else "reference",
         100.0 if rec.get("pmid") else None, rec.get("title"), Jsonb(rec.get("authors") or []), rec.get("journal"), rec.get("year"),
         abstract or None, Jsonb(types), full_text or None, Jsonb(secs), in_bank, reason, source, level, bool(bad),
         ("retracted" if any("Retract" in t for t in bad) else bad[0]) if bad else None))
    conn.commit()
    return level


# ---------------------------------------------------------------------------------------------- background jobs
JOBS = {}
_lock = threading.Lock()


def busy(org_id):
    """True while a job for this organization is still running (jobs for one organization run one at a time)."""
    with _lock:
        return any(j["org_id"] == org_id and not j["finished"] for j in JOBS.values())


def running_job(org_id, kind):
    """Id of the unfinished job of this kind for the organization, if any."""
    with _lock:
        return next((j["id"] for j in JOBS.values() if j["org_id"] == org_id and j["kind"] == kind and not j["finished"]), None)


def new_job(kind, label, org_id=None, hint=""):
    jid = hashlib.sha1(f"{kind}{label}{time.time()}".encode()).hexdigest()[:10]
    with _lock:
        JOBS[jid] = {"id": jid, "kind": kind, "label": label, "org_id": org_id, "hint": hint, "stage": "starting", "done": 0, "total": 0, "finished": False,
                     "error": None, "result": None, "started": time.time()}
    return jid


def _set(jid, **kw):
    with _lock:
        JOBS[jid].update(kw)


def get_job(jid):
    with _lock:
        return dict(JOBS.get(jid) or {})


def prepare_search(jid, org_id):
    """Cut newly added papers into passages and compute their search vectors: the same step as the "Build the search index" button, which only
    touches papers that do not have passages yet. Returns (done, message): a failure here never hides that the papers themselves were added."""
    from pubbank import index as search_index
    try:
        _set(jid, stage="Preparing the new papers for search", done=0, total=0)
        search_index.build(org_id, on_progress=lambda stage, d, t: _set(jid, stage=stage, done=d, total=t))
        return True, ""
    except Exception as e:
        return False, f"The papers were added, but preparing them for search did not finish ({type(e).__name__}). Use Prepare for search to try again."


def _ingest(jid, org_id, records_by_pmid, extra_refs, source, bad_tokens=()):
    """Shared tail of both jobs: fetch PMC text, save everything, summarise."""
    conn = connect()
    try:
        ensure_columns(conn)
        have_pm, have_doi, have_rel = existing_keys(conn, org_id)
        new, already = [], 0
        for pmid, rec in records_by_pmid.items():
            if pmid in have_pm or (rec.get("doi") and rec["doi"] in have_doi):
                already += 1
            else:
                new.append(rec)
        for r in extra_refs:       # references PubMed could not match: kept as plain citations
            if (r.get("doi") and r["doi"] in have_doi) or _ref_key(r) in have_rel:
                already += 1
            else:
                new.append(r)
        _set(jid, stage="Checking for open-access full text", done=0, total=len(new))
        counts = {"full_text": 0, "abstract_only": 0, "citation_only": 0}
        pause = _entrez()
        for i, rec in enumerate(new, 1):
            sections = None
            if rec.get("pmcid"):
                sections = fetch_pmc_sections(rec["pmcid"])
                time.sleep(pause)
            counts[save_paper(conn, org_id, rec, source, sections)] += 1
            _set(jid, done=i)
        indexed, index_error = prepare_search(jid, org_id) if new else (True, "")
        _set(jid, finished=True, stage="Done", result={"added": len(new), "already": already, "unmatched": len(extra_refs),
                                                       "not_in_pubmed": list(bad_tokens), "indexed": indexed, "index_error": index_error, **counts})
    except Exception as e:
        _set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


def run_pmid_job(jid, org_id, pmids, bad_tokens):
    try:
        _entrez()
        _set(jid, stage="Fetching records from PubMed", total=len(pmids))
        found = fetch_pubmed(pmids, lambda d, t: _set(jid, done=d, total=t))
        missing = [p for p in pmids if p not in found]
        _ingest(jid, org_id, found, [], "pubmed_id", [f"PMID {p} (not found in PubMed)" for p in missing] + [f"{b} (not a PubMed ID)" for b in bad_tokens])
    except Exception as e:
        _set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")


def run_reference_job(jid, org_id, records):
    try:
        _entrez()
        _set(jid, stage="Matching references to PubMed", total=len(records))
        match_references(records, lambda d, t: _set(jid, done=d, total=t))
        pmids = list(dict.fromkeys(r["pmid"] for r in records if r.get("pmid")))
        _set(jid, stage="Fetching records from PubMed", done=0, total=len(pmids))
        found = fetch_pubmed(pmids, lambda d, t: _set(jid, done=d, total=t)) if pmids else {}
        # a reference whose PMID was not returned stays a plain citation
        unmatched = [r for r in records if not r.get("pmid") or r["pmid"] not in found]
        for r in records:                       # keep the reference's own abstract when PubMed has none
            if r.get("pmid") in found and not found[r["pmid"]]["abstract"] and r.get("abstract"):
                found[r["pmid"]]["abstract"] = r["abstract"]
        _ingest(jid, org_id, found, unmatched, "reference_list")
    except Exception as e:
        _set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")


def start(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()
