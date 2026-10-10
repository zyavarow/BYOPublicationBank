"""Decide which papers are in the searchable bank (papers.in_bank) and record why (papers.bank_reason).
Defaults: duplicates, DADA2-software papers, scanned/no-text files and possible non-papers are out.
Optionally apply the organization's decisions from the 'YOUR DECISION' column (Keep / Exclude / Unsure) of a
suggested_exclusions_*.xlsx file; decisions are stored in papers.bank_decision so they survive re-runs.
  .venv\\Scripts\\python.exe scripts\\set_bank.py --org "DADA2 Papers"
  .venv\\Scripts\\python.exe scripts\\set_bank.py --org "DADA2 Papers" --decisions data\\reports\\suggested_exclusions_....xlsx
'Keep' can override the software and possible-non-paper flags; it cannot bring back a duplicate copy or a file with no text.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank.bank import apply_rules  # noqa: E402
from pubbank.db import connect  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

ALTER = """
ALTER TABLE papers ADD COLUMN IF NOT EXISTS in_bank boolean;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS bank_reason text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS bank_decision text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS text_level text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS topic_status text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS topic_reason text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS topic_method text;
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--decisions", help="suggested_exclusions_*.xlsx with the YOUR DECISION column filled in")
    args = ap.parse_args()
    with connect() as conn:
        conn.execute(ALTER)
        org_id = conn.execute("SELECT id FROM organizations WHERE slug=%s", (args.org,)).fetchone()[0]
        if args.decisions:
            import pandas as pd
            n = 0
            for sheet in ("All suggested", "Possible duplicates"):
                df = pd.read_excel(args.decisions, sheet_name=sheet)
                for _, r in df.dropna(subset=["YOUR DECISION"]).iterrows():
                    d = str(r["YOUR DECISION"]).strip().capitalize()
                    if d in ("Keep", "Exclude", "Unsure"):
                        conn.execute("UPDATE papers SET bank_decision=%s WHERE org_id=%s AND filename=%s",
                                     (None if d == "Unsure" else d, org_id, r["File"]))
                        n += 1
            print(f"Applied {n} decisions from {Path(args.decisions).name}")
        apply_rules(conn, org_id)
        print("\nBank membership:")
        for reason, n in conn.execute(
                "SELECT bank_reason, count(*) FROM papers WHERE org_id=%s GROUP BY 1 ORDER BY 2 DESC", (org_id,)):
            print(f"  {n:>4}  {reason}")
        total = conn.execute("SELECT count(*) FROM papers WHERE org_id=%s AND in_bank", (org_id,)).fetchone()[0]
        print(f"\nPapers in the bank: {total}")


if __name__ == "__main__":
    main()
