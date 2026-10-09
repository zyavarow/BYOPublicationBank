"""Write the extraction review spreadsheet (XLSX) from the database. Free: no API calls.
  .venv\\Scripts\\python.exe scripts\\export_extractions.py --org "DADA2 Papers"
Sheets: Extractions (one row per paper), Evidence (one row per extracted value, with its quote and a
quote check), Needs attention (quotes not found in the paper, failures, last author not among corresponding authors).
"""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank.db import psycopg  # noqa: E402  (must come before any other psycopg import)
import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from pubbank.extraction_schema import SCHEMA_VERSION  # noqa: E402

LIST_FIELDS = {
    "mutations": lambda e: f"{e['gene']} {e['variant']}",
    "model_systems": lambda e: e["system"] + (f" ({e['detail']})" if e.get("detail") else ""),
    "techniques": lambda e: e["technique"] + (f" ({e['detail']})" if e.get("detail") else ""),
    "sample_sources": lambda e: e["source"] + (f" ({e['detail']})" if e.get("detail") else ""),
    "reagents": lambda e: e["name"] + (f" [{e['vendor']}]" if e.get("vendor") else "")
                          + (f" cat# {e['catalog_number']}" if e.get("catalog_number") else "")
                          + (f" - {e['target_or_use']}" if e.get("target_or_use") else ""),
    "corresponding_authors": lambda e: e["name"] + (f" ({e['institution']})" if e.get("institution") else ""),
    "treatments": lambda e: e["treatment"] + (f" ({e['detail']})" if e.get("detail") else ""),
    "clinical_features": lambda e: e["feature"] + (f" ({e['detail']})" if e.get("detail") else ""),
}


def surname(name):
    parts = re.sub(r"[^\w\s-]", " ", name or "").split()
    return parts[-1].lower() if parts else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--run-label", default="default")
    args = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        org_id = conn.execute("SELECT id FROM organizations WHERE slug=%s", (args.org,)).fetchone()[0]
        rows = conn.execute(
            """SELECT p.filename, p.title, p.journal, p.doi, p.pmid, e.status, e.error, e.result, e.derived,
                      e.quote_stats, e.cost_usd, e.model
               FROM extractions e JOIN papers p ON p.id = e.paper_id
               WHERE e.org_id=%s AND e.schema_version=%s AND e.run_label=%s ORDER BY p.filename""",
            (org_id, SCHEMA_VERSION, args.run_label)).fetchall()
    if not rows:
        sys.exit("No extractions found yet.")

    summary, evidence, attention = [], [], []
    for fn, title, journal, doi, pmid, status, error, result, derived, qs, cost, model in rows:
        row = {"File": fn, "Title": title, "Journal": journal, "DOI": doi, "PMID": pmid, "Status": status}
        if status != "ok" or not result:
            row["Notes"] = error
            summary.append(row)
            attention.append({"File": fn, "Issue": f"extraction {status}", "Detail": error})
            continue
        d = derived or {}
        corr = [a["name"] for a in result["corresponding_authors"]]
        row.update({
            "Year": d.get("publication_year"),
            "All authors": "; ".join(d.get("all_authors") or []),
            "Author list source": d.get("author_source"),
            "Last author": d.get("last_author"),
            "Corresponding author(s)": "; ".join(LIST_FIELDS["corresponding_authors"](a) for a in result["corresponding_authors"]),
            "Study type": result["study_type"]["value"],
            "Gene/protein focus": result["gene_focus"]["value"],
        })
        for key, label in (("mutations", "Mutations studied"), ("model_systems", "Model system"),
                           ("techniques", "Techniques"), ("sample_sources", "Sample source"),
                           ("reagents", "Reagents"), ("treatments", "Treatments / interventions"),
                           ("clinical_features", "Clinical features")):
            row[label] = "; ".join(LIST_FIELDS[key](e) for e in result[key])
        row["Reviewer note from AI"] = result.get("extraction_notes")
        row["Quotes verified"] = f"{qs['verified']}/{qs['total']}" if qs else None
        row["Quotes approximate"] = qs.get("approximate") if qs else None
        row["Quotes NOT found"] = qs.get("not_found") if qs else None
        row["Cost USD"] = float(cost) if cost is not None else None
        row["REVIEWER: correct? (Yes / Partly / No)"] = None
        row["REVIEWER: comments"] = None
        summary.append(row)

        authors = d.get("all_authors") or []
        if corr and authors:
            ends = {surname(authors[0]), surname(authors[-1])}
            if not ends & {surname(c) for c in corr}:  # first or last author as corresponding author is normal
                attention.append({"File": fn, "Issue": "corresponding author is neither the first nor the last author",
                                  "Detail": f"first: {authors[0]} | last: {authors[-1]} | corresponding: {', '.join(corr)}"})
        if not corr:
            attention.append({"File": fn, "Issue": "no corresponding author found in the text", "Detail": ""})

        def add(field, value, ev):
            evidence.append({"File": fn, "Field": field, "Value": value, "Section": ev["section"], "Page": ev["page"] or None,
                             "Quote": ev["quote"], "Quote check": ev.get("verification")})
            if ev.get("verification") in ("not_found", "too_short"):
                attention.append({"File": fn, "Issue": f"quote {ev['verification']}: {field}",
                                  "Detail": f"{value} | \"{ev['quote'][:160]}\""})

        add("Study type", result["study_type"]["value"], result["study_type"]["evidence"])
        add("Gene/protein focus", result["gene_focus"]["value"], result["gene_focus"]["evidence"])
        for key, label in (("mutations", "Mutations studied"), ("model_systems", "Model system"), ("techniques", "Techniques"),
                           ("sample_sources", "Sample source"), ("reagents", "Reagents"),
                           ("corresponding_authors", "Corresponding author(s)"), ("treatments", "Treatments / interventions"),
                           ("clinical_features", "Clinical features")):
            for e in result[key]:
                add(label, LIST_FIELDS[key](e), e["evidence"])

    out_dir = ROOT / "data" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"extraction_review_{args.org.replace(' ', '_')}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    how_to = [
        "HOW TO REVIEW THIS WORKBOOK",
        "",
        "What this is: for each paper, an AI model read the full text and filled in a fixed set of fields. Every value comes with a quote copied from the paper, so you can check it in seconds.",
        "",
        "1. 'Extractions' tab: one row per paper. Read across the row. Use the last two (yellow) columns to mark each row Yes / Partly / No and add comments.",
        "2. 'Evidence' tab: one row per extracted value with the exact quote, section and page. Filter on a paper's File name to check its values.",
        "3. 'Needs attention' tab: places where something looks odd (no corresponding author found; a corresponding author who is neither the first nor the last author; a quote that could not be matched in the paper).",
        "",
        "WHAT TO LOOK FOR",
        "- Wrong or missing values: anything the paper clearly states that the row leaves out, or anything that is wrong.",
        "- Reagents and catalog numbers: are the listed reagents the ones that matter? Are catalog numbers right? (Catalog numbers are copied exactly as printed; blank means the paper did not print one.)",
        "- Corresponding authors: correct, and complete when there is more than one? Blank means the paper did not label one.",
        "- Gene focus: ADA2 (the DADA2 gene) versus ADA1 (a different enzyme). Check that these are not mixed up.",
        "- Off-topic papers: the library contains some papers that are only loosely related to DADA2; the reviewer note column says so when that happens.",
        "",
        "HOW TO READ THE QUOTE CHECK",
        "'Quotes verified' counts quotes found word for word in the paper text. 'Approximate' means a near match (usually a hyphenation difference in the PDF). 'NOT found' means the quote could not be located and should be treated with suspicion.",
        "",
        "LIMITS TO KEEP IN MIND",
        "- Text was machine-extracted from PDFs, so tables and figures are partly or poorly captured; values that only appear in figures or tables may be missed.",
        "- Running the same paper twice can give small differences in judgment fields (for example whether a technique is tagged or listed under 'Other'). Core facts such as variants, catalog numbers and corresponding authors were stable in our checks.",
        "- Page numbers refer to the page where the quoted section starts and can be a page off.",
        "- Year, all authors and last author come from PubMed/Crossref, not from the AI.",
    ]
    sheets = {"Extractions": pd.DataFrame(summary), "Evidence": pd.DataFrame(evidence),
              "Needs attention": pd.DataFrame(attention, columns=["File", "Issue", "Detail"])}
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "B2"
            for i, col in enumerate(df.columns, 1):
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(100)])
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(width + 2, 10), 70)
        from openpyxl.styles import Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
        ws = xw.sheets["Extractions"]
        for i, col in enumerate(sheets["Extractions"].columns, 1):
            if str(col).startswith("REVIEWER"):
                for r in range(1, ws.max_row + 1):
                    ws.cell(r, i).fill = PatternFill("solid", fgColor="FFFBE6")
                ws.column_dimensions[ws.cell(1, i).column_letter].width = 26 if "correct" in col else 50
                if "correct" in col:
                    dv = DataValidation(type="list", formula1='"Yes,Partly,No"', allow_blank=True)
                    ws.add_data_validation(dv)
                    dv.add(f"{ws.cell(2, i).column_letter}2:{ws.cell(2, i).column_letter}{ws.max_row + 200}")
        wh = xw.book.create_sheet("How to review", 0)
        for line in how_to:
            wh.append([line])
        wh.column_dimensions["A"].width = 140
        wh["A1"].font = Font(bold=True, size=14)
        for r in (9, 16, 19):
            wh.cell(r, 1).font = Font(bold=True)
        for row in wh.iter_rows():
            for c in row:
                c.alignment = c.alignment.copy(wrap_text=True, vertical="top")
    total = sum(float(r[10] or 0) for r in rows)
    print(f"Wrote {out}\n{len(rows)} papers, {len(evidence)} evidence rows, {len(attention)} items needing attention. "
          f"Total extraction cost so far: ${total:.4f}")


if __name__ == "__main__":
    main()
