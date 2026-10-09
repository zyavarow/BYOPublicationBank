"""Builds the Phase 1 ingestion report (XLSX + short Markdown summary) from the database.
Can also be run on its own:  .venv\\Scripts\\python.exe scripts\\report.py --org "DADA2 Papers"
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pubbank.db import psycopg  # noqa: E402  (must come before any other psycopg import)
import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

ALL_SQL = """
SELECT p.filename, p.parse_status, p.page_count, p.char_count, p.needs_ocr,
       p.title, p.extracted_title, p.authors, p.year, p.journal, p.doi, p.pmid, p.pmcid,
       p.meta_source, p.id_source, p.match_score, p.lookup_error, p.pub_types,
       d.filename AS duplicate_of_file, p.duplicate_reason,
       q.filename AS possible_duplicate_of_file, p.possible_duplicate_reason,
       p.possible_non_paper, p.non_paper_reason, p.parse_seconds, p.parse_error,
       coalesce(p.excluded, false) AS excluded, p.exclusion_reason
FROM papers p
LEFT JOIN papers d ON d.id = p.duplicate_of
LEFT JOIN papers q ON q.id = p.possible_duplicate_of
WHERE p.org_id = %s
ORDER BY p.filename
"""


def _first_author(a):
    if not a:
        return None
    return a[0] + (" et al." if len(a) > 1 else "")


def build_report(conn, org_id, org_slug, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = conn.execute(ALL_SQL, (org_id,))
    cols = [c.name for c in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    if df.empty:
        print("No papers in database for this organization yet.")
        return
    df["authors"] = df["authors"].apply(_first_author)
    df["pub_types"] = df["pub_types"].apply(lambda x: ", ".join(x) if x else None)
    df["parse_seconds"] = df["parse_seconds"].round(1)
    df["match_score"] = df["match_score"].round(0)
    df["chars_k"] = (df["char_count"].fillna(0) / 1000).round(1)

    folder_total = len(list((ROOT / "data" / "raw" / org_slug).rglob("*.pdf")))
    ok = df[df.parse_status == "ok"]
    n = len(df)
    pubmed = int((df.meta_source == "pubmed").sum())
    crossref = int((df.meta_source == "crossref").sum())
    unmatched = int((df.meta_source == "none").sum())
    dup_files = int(df.duplicate_of_file.notna().sum())
    dup_groups = int(df.duplicate_of_file.nunique())
    avg = float(ok.parse_seconds.mean()) if len(ok) else 0.0

    summary = [
        ("PDFs in folder", folder_total),
        ("PDFs processed so far (in this report)", n),
        ("Parsed successfully (text + sections)", int((df.parse_status == "ok").sum())),
        ("Scanned / needs OCR (no usable text yet)", int(df.needs_ocr.fillna(False).sum())),
        ("Exact duplicate copies skipped (not parsed)", int((df.parse_status == "duplicate_skipped").sum())),
        ("Failures (unreadable / error)", int((df.parse_status == "failed").sum())),
        ("Matched to PubMed", pubmed),
        ("Matched to Crossref only (not in PubMed)", crossref),
        ("Unmatched (no confident match)", unmatched),
        ("Lookup errors (network etc., worth re-running)", int(df.lookup_error.notna().sum())),
        ("Definite duplicate files (same file/DOI/PMID)", dup_files),
        ("  ...number of papers they duplicate", dup_groups),
        ("Possible duplicates (need your eye)", int(df.possible_duplicate_of_file.notna().sum())),
        ("Possible non-papers (need your eye)", int(df.possible_non_paper.sum())),
        ("Excluded: DADA2 software / sequencing papers", int(df.excluded.sum())),
        ("Average parse time per parsed paper (sec)", round(avg, 1)),
        ("Rough parse time for whole folder (hours; avg per paper x files)", round(avg * folder_total / 3600, 1)),
    ]
    summary_df = pd.DataFrame(summary, columns=["Measure", "Value"])

    show = ["filename", "parse_status", "page_count", "chars_k", "authors", "year", "title", "journal",
            "doi", "pmid", "pmcid", "meta_source", "match_score", "id_source", "needs_ocr"]
    sheets = {
        "Summary": summary_df,
        "All papers": df[show + ["excluded","duplicate_of_file", "possible_duplicate_of_file", "non_paper_reason",
                                  "parse_seconds"]],
        "Excluded (software papers)": df[df.excluded][["filename", "title", "exclusion_reason"]],
        "Duplicates": df[df.duplicate_of_file.notna()][["filename", "duplicate_of_file", "duplicate_reason"]],
        "Possible duplicates": df[df.possible_duplicate_of_file.notna()][
            ["filename", "possible_duplicate_of_file", "possible_duplicate_reason", "title"]],
        "Needs OCR": df[df.needs_ocr.fillna(False).astype(bool)][["filename", "page_count", "chars_k"]],
        "Possible non-papers": df[df.possible_non_paper][["filename", "non_paper_reason", "pub_types", "page_count",
                                                           "title"]],
        "Unmatched": df[df.meta_source == "none"][["filename", "extracted_title", "page_count", "lookup_error"]],
        "Failures": df[df.parse_status == "failed"][["filename", "parse_error"]],
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    xlsx = out_dir / f"ingest_report_{org_slug.replace(' ', '_')}_{stamp}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        for name, frame in sheets.items():
            frame.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"
            for i, col in enumerate(frame.columns, 1):
                width = max([len(str(col))] + [len(str(v)) for v in frame[col].head(200)])
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(width + 2, 10), 60)

    md = out_dir / xlsx.with_suffix(".md").name
    lines = [f"# Ingestion report: {org_slug}", f"Generated {datetime.now():%Y-%m-%d %H:%M}", ""]
    lines += [f"- {k}: {v}" for k, v in summary]
    md.write_text("\n".join(lines), encoding="utf-8")

    print("\n=== INGESTION SUMMARY ===")
    for k, v in summary:
        print(f"{k:<52} {v}")
    print(f"\nReport: {xlsx}")


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    a = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        org_id = conn.execute("SELECT id FROM organizations WHERE slug=%s", (a.org,)).fetchone()[0]
        from dedupe import dedupe_org
        dedupe_org(conn, org_id)
        build_report(conn, org_id, a.org, ROOT / "data" / "reports")
