"""Clean-up review: lists of papers that were set aside or flagged, a person's Keep / Exclude decisions, and the spreadsheet round trip."""
import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from pubbank.bank import SOURCE_LABELS, apply_rules, clean_title

GROUPS = {
    "offtopic": ("May be off-topic", "p.topic_status = 'off_topic'",
                 "Papers the topic check thinks are not about the disease. They are set aside unless you choose to keep them."),
    "borderline": ("Worth a look", "p.topic_status = 'borderline'",
                   "The AI check was unsure. They stay in the bank unless you exclude them."),
    "software": ("Flagged as look-alikes", "coalesce(p.excluded, false)",
                 "Papers a custom rule flagged because they share the disease's name but are about something else (for example sequencing software)."),
    "possible_dup": ("Possible duplicates", "p.possible_duplicate_of IS NOT NULL AND p.duplicate_of IS NULL",
                     "Similar titles. Both copies are still in the bank until you exclude one."),
    "nonpaper": ("Not research papers", "coalesce(p.possible_non_paper, false) AND p.duplicate_of IS NULL",
                 "Errata, supplements, posters, very short files and retractions."),
    "duplicate": ("Duplicate copies", "(p.duplicate_of IS NOT NULL OR p.parse_status = 'duplicate_skipped')",
                  "The same file, DOI or PubMed ID as another paper. Only one copy is kept."),
    "ocr": ("Scanned, no text", "(coalesce(p.needs_ocr, false) OR p.parse_status = 'skipped_ocr')",
            "Images of pages with no readable text. They need text recognition first."),
    "failed": ("Could not be read", "p.parse_status NOT IN ('ok', 'skipped_ocr', 'duplicate_skipped', 'pending')",
               "Damaged or unreadable files."),
}
# Heading and one-line status for each list, so the first thing a reader sees says what the papers below are.
HEADINGS = {
    "offtopic": ("Suggested to exclude: may be off-topic", "Set aside by default. Choose Keep in bank for any that are on topic."),
    "borderline": ("Worth a look: the AI was unsure", "In the bank by default. Choose Exclude for any that are not on topic."),
    "software": ("Suggested to exclude: look-alikes", "Set aside by default. Choose Keep in bank for any that are really about your disease."),
    "possible_dup": ("Possible duplicates: suggested to exclude one copy of each pair", "Both copies are in the bank by default. Choose Exclude on the copy you do not want."),
    "nonpaper": ("Suggested to exclude: not research papers", "Set aside by default. Choose Keep in bank for any that should count as papers."),
    "duplicate": ("Already excluded: duplicate copies", "These are left out because an identical copy is kept."),
    "ocr": ("Already excluded: scanned files with no readable text", "These cannot be read until text recognition is added."),
    "failed": ("Already excluded: files that could not be read", "These are damaged or unreadable."),
}
DEFAULT_IN = {"borderline", "possible_dup"}      # every other list starts out set aside; a person can only put a paper back with Keep
UNKEEPABLE = {"duplicate", "ocr", "failed"}      # nothing usable to keep, so a person cannot override these


def counts(conn, org_id):
    out = {k: conn.execute(f"SELECT count(*) FROM papers p WHERE p.org_id = %s AND ({cond})", (org_id,)).fetchone()[0]
           for k, (_l, cond, _h) in GROUPS.items()}
    out["decided"] = conn.execute("SELECT count(*) FROM papers WHERE org_id = %s AND bank_decision IS NOT NULL", (org_id,)).fetchone()[0]
    return out


def page(conn, org_id, group, pageno=1, per=30):
    cond = GROUPS[group][1]
    total = conn.execute(f"SELECT count(*) FROM papers p WHERE p.org_id = %s AND ({cond})", (org_id,)).fetchone()[0]
    pages = max(1, -(-total // per))
    pageno = min(max(1, pageno), pages)
    rows = conn.execute(
        f"""SELECT p.id, coalesce(p.title, p.extracted_title, p.filename), coalesce(p.source, 'pdf'), p.in_bank, p.bank_decision,
                   p.topic_reason, p.topic_method, p.duplicate_reason, p.possible_duplicate_reason, p.non_paper_reason, p.bank_reason,
                   d.id, coalesce(d.title, d.extracted_title, d.filename), p.year, p.journal
            FROM papers p LEFT JOIN papers d ON d.id = coalesce(p.duplicate_of, p.possible_duplicate_of)
            WHERE p.org_id = %s AND ({cond})
            ORDER BY coalesce(p.title, p.extracted_title, p.filename) LIMIT %s OFFSET %s""",
        (org_id, per, (pageno - 1) * per)).fetchall()
    items = []
    for pid, title, source, in_bank, decision, t_reason, t_method, dup_reason, pdup_reason, np_reason, bank_reason, oid, other, year, journal in rows:
        why = {"offtopic": t_reason, "borderline": t_reason, "possible_dup": pdup_reason, "duplicate": dup_reason, "nonpaper": np_reason,
               "ocr": "scanned pages with no readable text", "failed": "the file could not be read"}.get(group) or bank_reason
        items.append({"id": pid, "title": clean_title(title), "source": SOURCE_LABELS.get(source, source), "in_bank": bool(in_bank),
                      "decision": decision or "", "why": why or "", "method": t_method or "", "other": clean_title(other) if oid else "",
                      "meta": " ".join(str(x) for x in (journal, year) if x), "can_keep": group not in UNKEEPABLE, "default_in": group in DEFAULT_IN})
    return items, total, pages, pageno


def set_decision(conn, org_id, paper_id, decision):
    """Record a person's Keep / Exclude (or clear it) and refresh the bank. Returns False if the paper is not this organization's."""
    if decision not in ("Keep", "Exclude", ""):
        raise ValueError("decision must be Keep, Exclude or empty")
    n = conn.execute("UPDATE papers SET bank_decision = %s WHERE id = %s AND org_id = %s", (decision or None, paper_id, org_id)).rowcount
    apply_rules(conn, org_id)
    return bool(n)


def export_xlsx(conn, org_id, org_name):
    """Spreadsheet of everything set aside (and anything worth a look) with a blank YOUR DECISION column."""
    wb = Workbook()
    guide = wb.active
    guide.title = "How to use"
    for line in (f"{org_name}: papers set aside from the publication bank", "",
                 "Sheet 'Set aside' lists every paper that is not in the bank, plus papers flagged 'worth a look'.",
                 "Sheet 'Possible duplicates' lists papers with a similar title to another paper.",
                 "To overrule the system, type Keep or Exclude in the YOUR DECISION column. Leave it blank to accept the system's choice.",
                 "Then upload this file again on the review screen. Duplicate copies and scanned files cannot be kept: there is nothing usable in them."):
        guide.append([line])
    guide.column_dimensions["A"].width = 130
    guide["A1"].font = Font(bold=True, size=13)

    def sheet(title, header, rows, widths):
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="2B6E5E")
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for r in rows:
            ws.append(list(r))
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        col = ws.cell(1, len(header)).column_letter
        dv = DataValidation(type="list", formula1='"Keep,Exclude"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{max(ws.max_row, 2)}")
        ws.freeze_panes = "B2"

    rows = conn.execute(
        """SELECT p.id, coalesce(p.title, p.extracted_title, p.filename), coalesce(p.source, 'pdf'), p.bank_reason, p.topic_reason, p.year,
                  p.journal, p.pmid, p.doi, p.bank_decision, p.in_bank
           FROM papers p WHERE p.org_id = %s AND (NOT coalesce(p.in_bank, false) OR p.topic_status = 'borderline')
           ORDER BY p.bank_reason, coalesce(p.title, p.extracted_title, p.filename)""", (org_id,)).fetchall()
    sheet("Set aside", ["ID (do not change)", "Title or file", "Source", "Why", "Year", "Journal", "PMID", "DOI", "Currently", "YOUR DECISION"],
          [(r[0], r[1], SOURCE_LABELS.get(r[2], r[2]), r[4] or r[3] or "", r[5], r[6], r[7], r[8],
            ("in the bank (worth a look)" if r[10] else "set aside") + (f"; you chose {r[9]}" if r[9] else ""), r[9] or "") for r in rows],
          [10, 70, 18, 55, 8, 30, 12, 26, 28, 16])
    dups = conn.execute(
        """SELECT p.id, coalesce(p.title, p.extracted_title, p.filename), coalesce(d.title, d.extracted_title, d.filename),
                  p.possible_duplicate_reason, p.bank_decision
           FROM papers p JOIN papers d ON d.id = p.possible_duplicate_of
           WHERE p.org_id = %s AND p.duplicate_of IS NULL ORDER BY 2""", (org_id,)).fetchall()
    sheet("Possible duplicates", ["ID (do not change)", "This paper", "Looks like", "Why", "YOUR DECISION"],
          [(r[0], r[1], r[2], r[3] or "", r[4] or "") for r in dups], [10, 65, 65, 40, 16])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_xlsx(conn, org_id, data):
    """Apply the YOUR DECISION column of a returned spreadsheet. Returns (applied, ignored)."""
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    applied = ignored = 0
    for name in ("Set aside", "Possible duplicates"):
        if name not in wb.sheetnames:
            continue
        rows = wb[name].iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(rows, [])]
        if "YOUR DECISION" not in header or not header[0].startswith("ID"):
            continue
        di = header.index("YOUR DECISION")
        for r in rows:
            dec = str(r[di] or "").strip().capitalize()
            if dec not in ("Keep", "Exclude"):
                continue
            try:
                ok = conn.execute("UPDATE papers SET bank_decision = %s WHERE id = %s AND org_id = %s", (dec, int(r[0]), org_id)).rowcount
            except (TypeError, ValueError):
                ok = 0
            applied += bool(ok)
            ignored += not ok
    apply_rules(conn, org_id)
    return applied, ignored
