"""Back up the Postgres database to data/backups/ (git-ignored) and optionally prove the backup restores.
  .venv\\Scripts\\python.exe scripts\\backup_db.py            # make a backup
  .venv\\Scripts\\python.exe scripts\\backup_db.py --verify   # also restore into a throwaway database and compare row counts
Restore for real (replaces the current data!) only when needed:
  pg_restore --clean --if-exists --no-owner -d <database> <file.dump>
Uses PostgreSQL's own signed tools (pg_dump, pg_restore, psql, createdb, dropdb), not Python, to read the data.
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank.db import pg_bin  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
COUNT_TABLES = ["organizations", "papers", "extractions", "chunks"]


def run(tool, args, env, check=True):
    exe = str(pg_bin() / f"{tool}.exe")
    r = subprocess.run([exe, *args], env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"{tool} failed (exit {r.returncode}): {r.stderr.strip()[:400]}")
    return r


def counts(db, base_args, env):
    out = {}
    for t in COUNT_TABLES:
        r = run("psql", [*base_args, "-d", db, "-tA", "-c", f"SELECT count(*) FROM {t}"], env, check=False)
        out[t] = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="restore into a scratch database and compare row counts")
    args = ap.parse_args()
    if pg_bin() is None:
        sys.exit("Could not find the PostgreSQL tools folder (set PG_BIN in .env).")
    u = urlparse(os.environ["DATABASE_URL"])
    db = u.path.lstrip("/")
    env = {**os.environ, "PGPASSWORD": u.password or ""}
    conn_args = ["-h", u.hostname or "localhost", "-p", str(u.port or 5432), "-U", u.username or "postgres"]

    out_dir = ROOT / "data" / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dump = out_dir / f"{db}_{datetime.now():%Y%m%d_%H%M}.dump"
    run("pg_dump", [*conn_args, "-d", db, "-Fc", "-f", str(dump)], env)
    print(f"Backup written: {dump}  ({dump.stat().st_size / 1e6:.1f} MB)")

    if args.verify:
        scratch = f"{db}_restore_test"
        run("dropdb", [*conn_args, "--if-exists", scratch], env)
        run("createdb", [*conn_args, scratch], env)
        try:
            r = run("pg_restore", [*conn_args, "--no-owner", "-d", scratch, str(dump)], env, check=False)
            if r.returncode not in (0, 1):  # 1 = warnings only
                sys.exit(f"pg_restore failed: {r.stderr.strip()[:400]}")
            src, dst = counts(db, conn_args, env), counts(scratch, conn_args, env)
            ok = True
            print(f"{'table':<14}{'original':>10}{'restored':>10}")
            for t in COUNT_TABLES:
                if src[t] is None and dst[t] is None:
                    continue  # table does not exist yet (e.g. chunks)
                same = src[t] == dst[t]
                ok &= same
                print(f"{t:<14}{src[t]!s:>10}{dst[t]!s:>10}  {'OK' if same else 'MISMATCH'}")
            print("RESTORE TEST PASSED: the backup is usable." if ok else "RESTORE TEST FAILED: do not rely on this backup.")
        finally:
            run("dropdb", [*conn_args, "--if-exists", scratch], env, check=False)
            print(f"(scratch database '{scratch}' removed)")


if __name__ == "__main__":
    main()
