"""How well does search find the right paper? Runs the questions in docs/gold_questions_DADA2.csv through three kinds of search and reports
how often an expected paper is in the top 1 / 5 / 10 results (hit rate) and the average rank (MRR). Free.
  .venv/Scripts/python.exe scripts/eval_retrieval.py --org "DADA2 Papers"
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from pubbank import orgs, search  # noqa: E402
from pubbank.db import connect  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--folder", default="dada2")
    ap.add_argument("--gold", default=str(ROOT / "docs" / "gold_questions_DADA2.csv"))
    ap.add_argument("--show", action="store_true", help="print the misses")
    args = ap.parse_args()
    conn = connect()
    org_id = conn.execute("SELECT id FROM organizations WHERE slug = %s", (args.org,)).fetchone()[0]
    syn = orgs_synonyms(args.folder)
    rows = list(csv.DictReader(open(args.gold, encoding="utf-8")))
    total_chunks, embedded = conn.execute("SELECT count(*), count(embedding) FROM chunks WHERE org_id = %s", (org_id,)).fetchone()
    print(f"{len(rows)} questions; passages {total_chunks}, with vectors {embedded}\n")
    modes = [("words only", "words"), ("meaning only", "meaning"), ("hybrid (all three)", "hybrid")]
    results = {}
    for label, mode in modes:
        hits = {1: 0, 5: 0, 10: 0}
        rr = 0.0
        misses = []
        for r in rows:
            expected = {int(x) for x in r["expected_paper_ids"].split(";")}
            res = search.search(conn, org_id, r["question"], k=30, per_paper=1, mode=mode, synonyms=syn)
            ranks = [i + 1 for i, h in enumerate(res) if h["paper_id"] in expected]
            first = ranks[0] if ranks else None
            for k in hits:
                hits[k] += bool(first and first <= k)
            rr += 1 / first if first else 0
            if not first or first > 5:
                misses.append((r["id"], r["question"][:70], first))
        n = len(rows)
        results[label] = (hits, rr / n, misses)
        print(f"{label:<20} top-1 {hits[1]}/{n}  top-5 {hits[5]}/{n}  top-10 {hits[10]}/{n}  average rank score (MRR) {rr / n:.2f}")
    if args.show:
        for label, (_h, _m, misses) in results.items():
            print(f"\nNot in the top 5 for '{label}':")
            for m in misses:
                print("  ", m)


def orgs_synonyms(folder):
    from pubbank.template import load_template
    p = orgs.ORGS_DIR / folder / "overlay.xlsx"
    if not p.exists():
        return []
    return [[g["symbol"]] + list(g["aliases"]) for g in load_template(p).get("genes", [])]


if __name__ == "__main__":
    main()
