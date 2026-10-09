"""Attach standardized terms to stored extractions (free: no API calls; safe to re-run).
  - mutations           -> HGVS-style notation (offline syntax canonicalization; see pubbank/normalize/hgvs.py)
  - clinical_features   -> Human Phenotype Ontology ids (needs a local hp.json; see pubbank/normalize/hpo.py)
Results go to the entry_terms table and are shown in the review workbook.
  .venv\\Scripts\\python.exe scripts\\normalize_extractions.py --org "DADA2 Papers" --run-label v2test3
  .venv\\Scripts\\python.exe scripts\\normalize_extractions.py --org "DADA2 Papers" --run-label v2test3 --hpo data\\ontologies\\hp.json
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
from pubbank.normalize.hgvs import normalize_variant  # noqa: E402
from pubbank.template import SCHEMA_VERSION, load_template  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS entry_terms (
    id serial PRIMARY KEY,
    extraction_id int NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
    category text NOT NULL,
    entry_index int NOT NULL,
    rank int NOT NULL DEFAULT 0,
    value_text text,
    norm_system text NOT NULL,
    norm_id text,
    norm_label text,
    score real,
    method text,
    details jsonb,
    UNIQUE (extraction_id, category, entry_index, norm_system, rank)
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--run-label", default="default")
    ap.add_argument("--hpo", default=str(ROOT / "data" / "ontologies" / "hp.json"))
    ap.add_argument("--template")
    args = ap.parse_args()
    t = load_template(args.template)
    genes = t.get("genes", [])
    hpo = None
    if Path(args.hpo).exists():
        from pubbank.normalize.hpo import Hpo
        hpo = Hpo(args.hpo)
        print(f"HPO loaded: {len(hpo.terms):,} terms, release {hpo.version}")
    else:
        print(f"No HPO file at {args.hpo}: phenotypes will be skipped (variants are still normalized).")

    from pubbank.db import connect
    with connect() as conn:
        conn.execute(DDL)
        org_id = conn.execute("SELECT id FROM organizations WHERE slug=%s", (args.org,)).fetchone()[0]
        rows = conn.execute(
            """SELECT e.id, p.filename, e.result FROM extractions e JOIN papers p ON p.id=e.paper_id
               WHERE e.org_id=%s AND e.schema_version=%s AND e.run_label=%s AND e.status='ok' ORDER BY p.filename""",
            (org_id, SCHEMA_VERSION, args.run_label)).fetchall()
        stats = {"variants": 0, "variants_valid": 0, "phenotypes": 0, "phenotypes_matched": 0}
        unmatched = []
        for eid, fn, result in rows:
            conn.execute("DELETE FROM entry_terms WHERE extraction_id=%s", (eid,))
            for i, e in enumerate(result.get("mutations", {}).get("entries", [])):
                n = normalize_variant(e.get("variant", ""), gene=e.get("gene"), genes=genes)
                if n["status"] == "invalid" and e.get("as_written"):
                    n = normalize_variant(e["as_written"], gene=e.get("gene"), genes=genes)
                stats["variants"] += 1
                stats["variants_valid"] += n["status"] == "valid"
                conn.execute(
                    """INSERT INTO entry_terms (extraction_id, category, entry_index, rank, value_text, norm_system, norm_id,
                           norm_label, score, method, details) VALUES (%s,'mutations',%s,0,%s,'HGVS',%s,%s,%s,'offline-parse',%s)""",
                    (eid, i, e.get("as_written") or e.get("variant"), n["hgvs_p"] or n["hgvs_c"], n["one_letter"] or n["hgvs_c"],
                     {"valid": 1.0, "ambiguous": 0.5}.get(n["status"], 0.0), Jsonb(n)))
            if hpo:
                for i, e in enumerate(result.get("clinical_features", {}).get("entries", [])):
                    # the feature term first; the free-text detail (severity, patient numbers, ...) only as a fallback
                    texts = [x for x in (e.get("feature"), e.get("detail")) if x and x.strip().lower() != "other"]
                    hits = []
                    for text in texts:
                        hits = hpo.match_phrase(text)
                        if hits:
                            break
                    stats["phenotypes"] += 1
                    if hits:
                        stats["phenotypes_matched"] += 1
                        for rank, h in enumerate(hits):
                            conn.execute(
                                """INSERT INTO entry_terms (extraction_id, category, entry_index, rank, value_text, norm_system,
                                       norm_id, norm_label, score, method, details)
                                   VALUES (%s,'clinical_features',%s,%s,%s,'HPO',%s,%s,%s,%s,%s)""",
                                (eid, i, rank, h["part"], h["id"], h["label"], h["score"], h["method"], Jsonb({"matched": h["matched"], "hpo_release": hpo.version})))
                    else:
                        unmatched.append((fn, texts))
                        conn.execute(
                            """INSERT INTO entry_terms (extraction_id, category, entry_index, rank, value_text, norm_system, method)
                               VALUES (%s,'clinical_features',%s,0,%s,'HPO','no-match')""", (eid, i, " / ".join(texts) or e.get("feature")))
        conn.commit()
    print(f"\n{len(rows)} extractions processed")
    print(f"  variants: {stats['variants_valid']}/{stats['variants']} normalized to HGVS syntax")
    if hpo:
        print(f"  phenotypes: {stats['phenotypes_matched']}/{stats['phenotypes']} mapped to HPO ids")
        for fn, texts in unmatched[:15]:
            print(f"     no match: {texts}  ({fn[:40]})")


if __name__ == "__main__":
    main()
