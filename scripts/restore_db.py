"""Load a database copy made by `scripts/backup_db.py --for-host` into an online installation's (empty) database.
  .venv/Scripts/python.exe scripts/restore_db.py --dump data/backups/<file>_for_host.dump --url "<the host's DATABASE_URL>"
The address is the host's PUBLIC database address (Railway: the database service -> Connect -> public URL). It is used only for this command:
it is not saved anywhere and not printed. Refuses to run if the database already holds papers.
  --only "EXD Papers"   keep just this organization (by its database name); every other organization's rows are removed after loading
  --clear-questions       also empty the question log (test questions asked on the laptop)
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank.db import connect, pg_bin  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--only", default="", help="keep only the organization with this database name")
    ap.add_argument("--clear-questions", action="store_true")
    a = ap.parse_args()
    if pg_bin() is None:
        sys.exit("Could not find the PostgreSQL tools folder (set PG_BIN in .env).")
    if not Path(a.dump).is_file():
        sys.exit("That dump file does not exist.")
    try:
        with connect(a.url) as conn:
            has_papers = conn.execute("SELECT to_regclass('public.papers') IS NOT NULL").fetchone()[0]
            n = conn.execute("SELECT count(*) FROM papers").fetchone()[0] if has_papers else 0
    except Exception as e:
        sys.exit(f"Could not connect to the database ({type(e).__name__}). Check the address.")
    if n:
        sys.exit(f"The database already holds {n} papers. Refusing to overwrite it.")
    print("Loading the dump (a few minutes for a large library)...")
    # No --exit-on-error: a dump made by Postgres 18 contains settings an older server does not know; those lines fail harmlessly. The row counts
    # below are what shows whether the data really arrived.
    r = subprocess.run([str(pg_bin() / "pg_restore.exe"), "--no-owner", "--no-privileges", "-d", a.url, a.dump], capture_output=True, text=True)
    errors = [ln for ln in r.stderr.splitlines() if "error:" in ln.lower()]
    if r.returncode > 1:
        sys.exit("pg_restore failed:\n" + r.stderr.strip()[-800:])
    if errors:
        print(f"{len(errors)} messages from the restore (usually harmless version differences); the first ones:")
        for ln in errors[:4]:
            print("  ", ln[:200])
    with connect(a.url) as conn:
        if a.only:
            keep = conn.execute("SELECT id FROM organizations WHERE slug = %s", (a.only,)).fetchone()
            if not keep:
                sys.exit(f"No organization named {a.only!r} in the loaded data. Nothing was removed.")
            for t in ("samples", "extraction_runs", "ask_log", "extractions", "chunks"):      # entry_terms and sample_verdicts go with their parents
                conn.execute(f"DELETE FROM {t} WHERE org_id <> %s", (keep[0],))
            conn.execute("DELETE FROM papers WHERE org_id <> %s", (keep[0],))
            conn.execute("DELETE FROM organizations WHERE id <> %s", (keep[0],))
            print(f"Kept only {a.only}; other organizations removed.")
        if a.clear_questions:
            conn.execute("DELETE FROM ask_log")
            print("Question log emptied.")
        conn.commit()
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("organizations", "papers", "extractions", "chunks")}
        for t, n in counts.items():
            print(f"{t:<14}{n:>8}")
        vec = conn.execute("SELECT count(embedding) FROM chunks").fetchone()[0]
        print(f"{'passage vectors':<14}{vec:>8}")
        if not (counts["papers"] and counts["chunks"] and vec == counts["chunks"]):
            sys.exit("The data did not arrive completely (see the counts above). Do not start the app: use a new empty database and try again.")
    print("Done. Start (or restart) the app; it adds the account tables when it starts.")


if __name__ == "__main__":
    main()
