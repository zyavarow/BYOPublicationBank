"""Build (or continue building) the search index of an organization: passages + word index + vectors. Free; runs on this computer.
  .venv/Scripts/python.exe scripts/build_index.py --org "DADA2 Papers"            # new/missing papers only; continues after an interruption
  .venv/Scripts/python.exe scripts/build_index.py --org "DADA2 Papers" --rebuild  # start again from scratch
  .venv/Scripts/python.exe scripts/build_index.py --org "DADA2 Papers" --chunks-only   # cut passages but do not compute vectors yet
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from pubbank import index  # noqa: E402
from pubbank.db import connect  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--chunks-only", action="store_true")
    ap.add_argument("--shard", help="K/N: this worker computes vectors for passages whose id %% N == K (start N workers to go faster)")
    args = ap.parse_args()
    conn = connect()
    org_id = conn.execute("SELECT id FROM organizations WHERE slug = %s", (args.org,)).fetchone()[0]
    t0 = time.time()
    last = [0.0]

    def progress(stage, done, total):
        if time.time() - last[0] > 20 or done == total:
            last[0] = time.time()
            rate = done / max(1, time.time() - t0)
            left = (total - done) / rate / 60 if rate and stage.startswith("Reading") else 0
            print(f"{stage}: {done}/{total}  ({int(time.time() - t0)}s elapsed" + (f", about {left:.0f} min left" if left else "") + ")", flush=True)
    shard = tuple(int(x) for x in args.shard.split("/")) if args.shard else None
    if not shard or shard[0] == 0:
        index.chunk_new_papers(conn, org_id, args.rebuild, progress)
    print(index.status(conn, org_id), flush=True)
    if not args.chunks_only:
        index.embed_pending(conn, org_id, on_progress=progress, shard=shard)
        print("done:", index.status(conn, org_id), f"in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
