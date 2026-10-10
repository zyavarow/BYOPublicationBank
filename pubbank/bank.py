"""Read side of the publication bank: the list shown on setup screen 2 and its counts."""

import html as _html
import re as _re


def clean_title(t):
    """Titles from PubMed sometimes carry markup such as <sup>6</sup> or &lt;; show them as plain text."""
    return _re.sub(r"\s+", " ", _html.unescape(_re.sub(r"<[^>]+>", "", _html.unescape(t or "")))).strip()


STATUS_SQL = """
CASE
  WHEN duplicate_of IS NOT NULL OR parse_status = 'duplicate_skipped' THEN 'duplicate'
  WHEN needs_ocr OR parse_status = 'skipped_ocr' THEN 'ocr'
  WHEN parse_status = 'pending' THEN 'processing'
  WHEN parse_status <> 'ok' THEN 'failed'
  WHEN in_bank AND text_level = 'abstract_only' THEN 'abstract'
  WHEN in_bank THEN 'inbank'
  ELSE 'setaside'
END"""

STATUS_LABELS = {
    "inbank": ("In bank", "ok"), "abstract": ("Abstract only", "warn"), "processing": ("Processing", "warn"),
    "ocr": ("Needs OCR", "gray"), "duplicate": ("Duplicate", "gray"), "setaside": ("Set aside", "gray"), "failed": ("Failed", "bad"),
}
SOURCE_LABELS = {"pdf": "PDF upload", "pubmed_id": "PubMed ID", "reference_list": "Reference manager"}
PER_PAGE = 40


def counts(conn, org_id):
    """{status: n} for every paper of the organization, plus 'total'."""
    rows = conn.execute(f"SELECT {STATUS_SQL} AS st, count(*) FROM papers WHERE org_id = %s GROUP BY 1", (org_id,)).fetchall()
    out = {k: 0 for k in STATUS_LABELS}
    out.update({st: n for st, n in rows})
    out["total"] = sum(n for _st, n in rows)
    sources = conn.execute("SELECT coalesce(source, 'pdf'), count(*) FROM papers WHERE org_id = %s GROUP BY 1", (org_id,)).fetchall()
    out["sources"] = {s: n for s, n in sources}
    return out


def page(conn, org_id, q="", status="", pageno=1):
    """One page of the list. Returns (rows, total matching, number of pages)."""
    where, args = ["org_id = %s"], [org_id]
    if q:
        where.append("(coalesce(title, extracted_title, filename) ILIKE %s OR filename ILIKE %s OR pmid = %s OR doi ILIKE %s)")
        like = f"%{q}%"
        args += [like, like, q.strip(), like]
    if status in STATUS_LABELS:
        where.append(f"({STATUS_SQL}) = %s")
        args.append(status)
    w = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) FROM papers WHERE {w}", args).fetchone()[0]
    pages = max(1, -(-total // PER_PAGE))
    pageno = min(max(1, pageno), pages)
    rows = conn.execute(
        f"""SELECT id, coalesce(title, extracted_title, filename), filename, pmid, journal, year, page_count,
                   coalesce(source, 'pdf'), text_level, bank_reason, ({STATUS_SQL})
            FROM papers WHERE {w}
            ORDER BY (CASE WHEN in_bank THEN 0 ELSE 1 END), coalesce(title, extracted_title, filename)
            LIMIT %s OFFSET %s""", args + [PER_PAGE, (pageno - 1) * PER_PAGE]).fetchall()
    items = []
    for pid, title, filename, pmid, journal, year, pages_n, source, level, reason, st in rows:
        label, tone = STATUS_LABELS[st]
        bits = []
        if source == "pdf" and pages_n:
            bits.append(f"{pages_n} pages")
        if source == "pubmed_id" or (source == "reference_list" and pmid):
            bits.append(f"PMID {pmid}")
        if journal or year:
            bits.append(" ".join(str(x) for x in (journal, year) if x))
        items.append({"id": pid, "title": clean_title(title or filename), "meta": " · ".join(bits), "source": SOURCE_LABELS.get(source, source),
                      "label": label, "tone": tone, "note": reason if st == "setaside" else
                      ("Full text was not available; answers will say 'abstract only'." if st == "abstract" else "")})
    return items, total, pages, pageno


APPLY_RULES = """
UPDATE papers SET
  in_bank = CASE
    WHEN bank_decision = 'Exclude' THEN false
    WHEN duplicate_of IS NOT NULL OR coalesce(needs_ocr, false) OR parse_status <> 'ok' THEN false
    WHEN text_level = 'citation_only' THEN false
    WHEN bank_decision = 'Keep' THEN true
    WHEN coalesce(excluded, false) OR coalesce(possible_non_paper, false) THEN false
    WHEN topic_status = 'off_topic' THEN false
    ELSE true END,
  bank_reason = CASE
    WHEN bank_decision = 'Exclude' THEN 'excluded by reviewer'
    WHEN duplicate_of IS NOT NULL THEN 'duplicate copy'
    WHEN coalesce(needs_ocr, false) OR parse_status <> 'ok' THEN 'scanned / no usable text'
    WHEN text_level = 'citation_only' THEN 'no abstract or full text available'
    WHEN bank_decision = 'Keep' THEN 'kept by reviewer'
    WHEN coalesce(excluded, false) THEN 'about DADA2 software, not the disease'
    WHEN coalesce(possible_non_paper, false) AND non_paper_reason ILIKE 'retract%%' THEN 'retracted'
    WHEN coalesce(possible_non_paper, false) THEN 'possible non-paper'
    WHEN topic_status = 'off_topic' THEN 'may be off-topic'
    ELSE 'in bank' END
WHERE org_id = %s
"""


def apply_rules(conn, org_id):
    """Recompute papers.in_bank / bank_reason for one organization (respects the reviewer's Keep / Exclude decisions)."""
    conn.execute("UPDATE papers SET source = 'pdf' WHERE org_id = %s AND source IS NULL", (org_id,))
    conn.execute("UPDATE papers SET text_level = 'full_text' WHERE org_id = %s AND source = 'pdf' AND text_level IS NULL AND parse_status = 'ok'",
                 (org_id,))
    conn.execute(APPLY_RULES, (org_id,))
    conn.commit()
