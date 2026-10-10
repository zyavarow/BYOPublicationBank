"""Tiny end-to-end test of the batch reader on 2 papers (about 10 cents at batch prices).
  .venv/Scripts/python.exe scripts/test_batch_run.py
Creates a run for the organization 'DADA2 Papers', follows it until it ends, and prints what happened. Safe to delete the run afterwards:
  DELETE FROM extractions WHERE run_label = 'run-<id>';  DELETE FROM extraction_runs WHERE id = <id>;
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import docling_parse.pdf_parsers  # noqa: E402,F401  (must come before psycopg)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from pubbank import batch_run  # noqa: E402
from pubbank.db import connect  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
conn = connect()
oid = conn.execute("SELECT id FROM organizations WHERE slug = 'DADA2 Papers'").fetchone()[0]
ids = [r[0] for r in conn.execute("SELECT id FROM papers WHERE org_id = %s AND in_bank AND (filename LIKE 'Al-Hebshi%%' OR filename LIKE 'Chung et al.%%') ORDER BY id", (oid,))]
print("papers", ids, "estimate", batch_run.estimate(conn, "dada2", ids), flush=True)
run_id = batch_run.create(conn, oid, "dada2", ids, 1.00)
print("run", run_id, flush=True)
batch_run.start("dada2", run_id)
t0 = time.time()
while True:
    batch_run.advance(run_id)
    r = conn.execute("SELECT status, round, message, cost_usd, batch_id FROM extraction_runs WHERE id = %s", (run_id,)).fetchone()
    print(f"{int(time.time() - t0):>5}s  {r[0]:<8} round {r[1]}  ${float(r[3] or 0):.3f}  {r[2]}", flush=True)
    if r[0] != "running":
        break
    time.sleep(30)
for row in conn.execute("SELECT paper_id, status, cost_usd, quote_stats, template_version FROM extractions WHERE run_label = %s", (f"run-{run_id}",)):
    print(row, flush=True)
