"""Load a database copy made by `scripts/backup_db.py --for-host` into an online installation's (empty) database.
  .venv/Scripts/python.exe scripts/restore_db.py --dump data/backups/<file>_for_host.dump --url "<the host's DATABASE_URL>"
The address is the host's PUBLIC database address (Railway: the database service -> Connect -> public URL). It is used only for this command:
it is not saved anywhere and not printed. Refuses to run if the database already holds papers.
  --only "EXD Papers"   keep just this organization (by its database name); every other organization's rows are removed after loading
  --clear-questions       also empty the question log (test questions asked on the laptop)
"""
import argparse
import getpass
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank.db import connect, pg_bin  # noqa: E402


_SECRETS = []


def safe(text):
    """Remove the database address and password from any text before it is shown, so the output can be copied and shared."""
    text = str(text)
    for secret in sorted({x for x in _SECRETS if x}, key=len, reverse=True):
        text = text.replace(secret, "***")
    return re.sub(r"postgres(?:ql)?://\S+", "postgresql://***", text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--url", default="", help="the database address; leave it out to be asked for it (hidden, so it never appears on screen or in a command)")
    ap.add_argument("--hide", action="store_true", help="hide the address as it is pasted (some terminals cannot paste into a hidden prompt)")
    ap.add_argument("--only", default="", help="keep only the organization with this database name")
    ap.add_argument("--clear-questions", action="store_true")
    a = ap.parse_args()
    if not a.url:
        if a.hide:
            a.url = getpass.getpass("Paste the database's public address (nothing will show as you paste), then press Enter: ")
        else:
            print("Paste the database's public address and press Enter. It will be visible on your screen: do not share the screen or copy it into chat.")
            a.url = input("Address: ")
    a.url = a.url.strip().strip("\"'").strip()          # a stray space or quote from copying should not break the address
    if "${{" in a.url or "}}" in a.url:
        sys.exit("That is Railway's recipe for the address (it contains ${{ }}), not the finished address. In Railway open the database service, press Connect, "
                 "choose the Public Network tab, and copy the connection URL shown there.")
    if not a.url:
        sys.exit("No address was entered.")
    if not re.match(r"^postgres(ql)?://", a.url):
        known = [k for k in ("psql", "railway", "PGPASSWORD", "DATABASE_PUBLIC_URL", "DATABASE_URL", "http://", "https://", "export ", "set ") if k.lower() in a.url.lower()]
        print("What was pasted is not a database address: it does not start with postgresql://")
        print(f"  Its shape: {len(a.url)} characters; contains '://': {'://' in a.url}; contains '@': {'@' in a.url}; contains spaces: {' ' in a.url}; "
              f"contains a line break: {chr(10) in a.url or chr(13) in a.url}.")
        if known:
            print("  It mentions:", ", ".join(known))
        if " " not in a.url and "@" not in a.url and "://" not in a.url:
            print("  It looks like a single word or value (for example just the password or just the host name), not the whole address.")
        if "psql" in a.url.lower() or " " in a.url:
            print("  It looks like a command or several words. Copy the 'Connection URL' itself, not a psql command.")
        print("  In Railway: database service > Connect > Public Network > the URL that begins postgresql://postgres:...")
        sys.exit(1)
    try:
        pw = urlparse(a.url).password or ""
    except ValueError:
        pw = ""
    _SECRETS.extend([a.url, a.url.strip(), pw, unquote(pw)])
    if pg_bin() is None:
        sys.exit("Could not find the PostgreSQL tools folder (set PG_BIN in .env).")
    if not Path(a.dump).is_file():
        sys.exit("That dump file does not exist.")
    try:
        with connect(a.url, connect_timeout=20) as conn:
            has_papers = conn.execute("SELECT to_regclass('public.papers') IS NOT NULL").fetchone()[0]
            n = conn.execute("SELECT count(*) FROM papers").fetchone()[0] if has_papers else 0
    except Exception as e:
        try:
            u = urlparse(a.url)
            host, port = u.hostname, u.port
        except ValueError:
            print("Could not connect: the address is not in the right form (for example, the port number is not valid). Copy DATABASE_PUBLIC_URL again, whole.")
            sys.exit(1)
        why = safe(e)
        hints = []
        if u.scheme not in ("postgresql", "postgres"):
            hints.append("The address should start with postgresql:// (check that you copied the whole value, without extra quotes or spaces).")
        if (u.hostname or "").endswith(".railway.internal"):
            hints.append("This is Railway's INTERNAL address. Use DATABASE_PUBLIC_URL instead (it works from outside Railway).")
        if not u.hostname:
            hints.append("No host name was found in the address.")
        dbname = u.path.lstrip("/") if u.scheme in ("postgresql", "postgres") else "(not shown)"
        print(safe(f"Could not connect to the database. Tried host {u.hostname!r}, port {u.port!r}, database {dbname!r}."))
        print("Reason given:", why.strip().splitlines()[0][:300] if why.strip() else type(e).__name__)
        for h in hints:
            print("Hint:", h)
        sys.exit(1)
    if n:
        sys.exit(f"The database already holds {n} papers. Refusing to overwrite it.")
    print("Loading the dump (a few minutes for a large library)...")
    # No --exit-on-error: a dump made by Postgres 18 contains settings an older server does not know; those lines fail harmlessly. The row counts
    # below are what shows whether the data really arrived.
    r = subprocess.run([str(pg_bin() / "pg_restore.exe"), "--no-owner", "--no-privileges", "-d", a.url, a.dump], capture_output=True, text=True)
    errors = [ln for ln in r.stderr.splitlines() if "error:" in ln.lower()]
    if r.returncode > 1:
        sys.exit("pg_restore failed:\n" + safe(r.stderr.strip()[-800:]))
    if errors:
        print(f"{len(errors)} messages from the restore (usually harmless version differences); the first ones:")
        for ln in errors[:4]:
            print("  ", safe(ln)[:200])
    with connect(a.url, connect_timeout=20) as conn:
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
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"Stopped: {type(e).__name__}: {safe(e)[:400]}")
        sys.exit(1)
