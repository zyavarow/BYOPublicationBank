"""Re-run the quote check on stored extractions (free: no API calls). Use after tuning the checker.
  .venv\\Scripts\\python.exe scripts\\reverify_quotes.py --org "EXD Papers" --run-label test3
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank.db import psycopg  # noqa: E402  (must come before any other psycopg import)
from dotenv import load_dotenv  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

load_dotenv(ROOT / ".env")
from pubbank.extraction import build_paper_text  # noqa: E402
from pubbank.quote_check import verify_extraction  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--run-label", default="default")
    args = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        org_id = conn.execute("SELECT id FROM organizations WHERE slug=%s", (args.org,)).fetchone()[0]
        rows = conn.execute(
            """SELECT e.id, p.filename, p.title, p.extracted_title, p.journal, p.year, p.sections, p.full_text, e.result
               FROM extractions e JOIN papers p ON p.id=e.paper_id
               WHERE e.org_id=%s AND e.run_label=%s AND e.status='ok'""", (org_id, args.run_label)).fetchall()
        for eid, fn, title, ext_title, journal, year, sections, full_text, result in rows:
            paper = {"title": title, "extracted_title": ext_title, "journal": journal, "year": year,
                     "sections": sections, "full_text": full_text}
            stats = verify_extraction(result, build_paper_text(paper)[0])
            if "_meta" in result:  # v2 results: recompute evidence checks and final confidence
                from pubbank.template import finalize_result, load_template
                finalize_result(load_template(), result)
            conn.execute("UPDATE extractions SET result=%s, quote_stats=%s WHERE id=%s", (Jsonb(result), Jsonb(stats), eid))
            print(f"{fn[:50]:<50} verified {stats['verified']}/{stats['total']}  approximate {stats['approximate']}  "
                  f"not found {stats['not_found']}  too short {stats['too_short']}")
        conn.commit()


if __name__ == "__main__":
    main()
