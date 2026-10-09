"""Flag papers about the EXD *software* (amplicon-sequencing tool) so they are excluded from the bank.
Nothing is deleted: papers get excluded=true plus a reason, and show up on the report's 'Excluded' sheet.
  .venv\\Scripts\\python.exe scripts\\flag_software.py --org "EXD Papers"
Rule: lots of sequencing-analysis vocabulary AND clearly more of it than disease vocabulary.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pubbank.db import psycopg  # noqa: E402  (must come before any other psycopg import)
from dotenv import load_dotenv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

SEQ_RX = re.compile(r"amplicon|\basvs?\b|16s|denois|phyloseq|\botus?\b|qiime|metabarcod|illumina|microbiota|microbiome", re.I)
DISEASE_RX = re.compile(r"example enzyme|\bgene1\b|gene1a|vasculitis|polyarteritis|vasculopathy|immunodeficiency|autoinflammat", re.I)
MIN_SEQ_HITS = 10


def flag_software(conn, org_id):
    conn.execute("ALTER TABLE papers ADD COLUMN IF NOT EXISTS excluded boolean DEFAULT false")
    conn.execute("ALTER TABLE papers ADD COLUMN IF NOT EXISTS exclusion_reason text")
    conn.execute("UPDATE papers SET excluded=false, exclusion_reason=NULL WHERE org_id=%s AND excluded", (org_id,))
    flagged = []
    for pid, fn, title, text in conn.execute(
            "SELECT id, filename, coalesce(title, extracted_title), full_text FROM papers "
            "WHERE org_id=%s AND parse_status='ok' AND full_text IS NOT NULL", (org_id,)).fetchall():
        seq = len(SEQ_RX.findall(text))
        dis = len(DISEASE_RX.findall(text))
        if seq >= MIN_SEQ_HITS and seq > dis:
            reason = f"appears to be about EXD software / sequencing analysis (sequencing terms {seq}, disease terms {dis})"
            conn.execute("UPDATE papers SET excluded=true, exclusion_reason=%s WHERE id=%s", (reason, pid))
            flagged.append((fn, title, seq, dis))
    conn.commit()
    print(f"Excluded {len(flagged)} papers as software/sequencing papers:")
    for fn, title, seq, dis in flagged:
        print(f"  seq={seq:<4} disease={dis:<4} {fn[:40]:<40} {(title or '')[:60]}")
    return flagged


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    a = ap.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        org_id = conn.execute("SELECT id FROM organizations WHERE slug=%s", (a.org,)).fetchone()[0]
        flag_software(conn, org_id)
