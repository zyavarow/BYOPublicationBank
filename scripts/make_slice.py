"""Pick a test slice of PDFs for the Phase 2 extraction trial.

Mostly random, plus a few deliberately awkward cases (reviews, case reports, animal studies, old papers,
trials/guidelines, off-topic papers) so the schema gets stress-tested. Papers already parsed in the
database count toward the slice. Writes data/slices/<org>_slice<N>.txt (one filename per line).
  .venv\\Scripts\\python.exe scripts\\make_slice.py --org "DADA2 Papers" --size 50
"""
import argparse
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pubbank.db import psycopg  # noqa: E402  (must come before any other psycopg import)
from dotenv import load_dotenv  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest import NON_PAPER_FILENAME, pdf_text_probe, sha256_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CATEGORIES = [
    ("review", 3, re.compile(r"(systematic|narrative|literature|clinical|comprehensive|critical)\s+review|review article|meta-analysis")),
    ("case report", 3, re.compile(r"case report|case series|we report|we describe a|a case of|report of a")),
    ("animal/model", 3, re.compile(r"zebrafish|\bmice\b|\bmouse\b|murine|knock-?out|xenograft")),
    ("older (<=1999)", 2, None),
    ("trial/guideline/treatment", 2, re.compile(r"guideline|consensus|recommendation|randomi[sz]ed|clinical trial|etanercept|adalimumab|transplantation")),
    ("off-topic (no ADA2 mention)", 2, "OFFTOPIC"),
]
ON_TOPIC = re.compile(r"adenosine deaminase|\bada2\b|dada2|cecr1")
EXTRA_NON_PAPER = re.compile(r"\bfig(ure)?s?\b|\btables?\b", re.I)


def year_of(name):
    m = re.search(r"\b(19|20)\d{2}\b", name)
    return int(m.group(0)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-pages", type=int, default=30)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    folder = ROOT / "data" / "raw" / args.org
    pdfs = sorted(folder.glob("*.pdf"))
    by_sha = {}
    for p in pdfs:
        by_sha.setdefault(sha256_of(p), []).append(p)
    non_canonical = set()
    for g in by_sha.values():
        g.sort(key=lambda p: (bool(re.search(r"-\d+\.pdf$", p.name, re.I)), p.name))
        non_canonical.update(g[1:])

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        pre = [r[0] for r in conn.execute(
            "SELECT filename FROM papers WHERE parse_status='ok' ORDER BY filename")]
    pre = [n for n in pre if (folder / n) not in non_canonical]

    print("Scanning first pages of all PDFs (about a minute)...", flush=True)
    info = {}
    for p in pdfs:
        if p in non_canonical or NON_PAPER_FILENAME.search(p.name) or EXTRA_NON_PAPER.search(p.name):
            continue
        try:
            pr = pdf_text_probe(p)
        except Exception:
            continue
        if pr["needs_ocr"] or not (2 <= pr["page_count"] <= args.max_pages):
            continue
        info[p.name] = {"pages": pr["page_count"], "text": pr["early_text"].lower()[:3000], "year": year_of(p.name)}

    picked = {n: "already parsed (random sample)" for n in pre}
    edge = {}
    for label, k, rx in CATEGORIES:
        if label.startswith("older"):
            pool = [n for n, v in info.items() if v["year"] and v["year"] <= 1999 and n not in picked and n not in edge]
        elif rx == "OFFTOPIC":
            pool = [n for n, v in info.items() if not ON_TOPIC.search(v["text"]) and n not in picked and n not in edge]
        else:
            pool = [n for n, v in info.items() if rx.search(v["text"]) and n not in picked and n not in edge]
        for n in rng.sample(pool, min(k, len(pool))):
            edge[n] = f"edge case: {label}"
    picked.update(edge)
    rest = [n for n in info if n not in picked]
    need = max(0, args.size - len(picked))
    for n in rng.sample(rest, min(need, len(rest))):
        picked[n] = "random"

    out_dir = ROOT / "data" / "slices"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.org.replace(' ', '_')}_slice{args.size}.txt"
    out.write_text("\n".join(sorted(picked)) + "\n", encoding="utf-8")
    print(f"\nSlice of {len(picked)} papers written to {out}\n")
    for n in sorted(picked, key=lambda x: (picked[x] != "random", picked[x], x)):
        pg = info.get(n, {}).get("pages", "?")
        print(f"  {picked[n]:<42} {str(pg):>3}p  {n}")


if __name__ == "__main__":
    main()
