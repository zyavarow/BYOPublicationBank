"""Fill a TEST installation's database with a small sample of the DADA2 library and a few test accounts. Never run it against the live site.

What it copies from this laptop's database: the chosen papers, their search passages (with vectors already computed) and any finished
extractions. No Anthropic key is used and nothing is spent. What it does NOT copy: questions, accounts, other papers, the organization's
settings files (those go in on the site: Home > Move an organization > Import settings, as in docs/DEPLOY.md).

  .venv/Scripts/python.exe scripts/seed_test.py --ids data/test_sample_ids.json --owner you+owner@example.com --admin you+admin@example.com --member you+member@example.com

It asks for the TEST database's public address (Railway: the Postgres service in the *test* environment > Variables > DATABASE_PUBLIC_URL).
The address is not printed or saved. It refuses to run if the target database already holds papers, or if it looks like the live database
(it holds more than 100 papers or any question log entries). Accounts sign in by emailed link, so use addresses that are in the Test site's
PUBBANK_TEST_EMAIL_ALLOW list.
"""
import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from pubbank import auth  # noqa: E402
from pubbank.db import connect  # noqa: E402

ORG_SLUG = "DADA2 Papers"
SKIP = {"papers": {"duplicate_of", "possible_duplicate_of"}}       # point at papers outside the sample
CAST = {"tsvector"}                                                 # copied as text, then cast back


def columns(conn, table):
    return conn.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                        (table,)).fetchall()


def copy_rows(src, dst, table, where, args, org_map=None, extra_select=""):
    """Copy matching rows of one table, keeping their ids. Returns the number copied."""
    cols = [(c, t) for c, t in columns(src, table) if c not in SKIP.get(table, ())]
    sel = ", ".join(f"{c}::text" if t == "tsvector" else c for c, t in cols)
    ins = ", ".join(c for c, _ in cols)
    marks = ", ".join("%s::tsvector" if t == "tsvector" else "%s" for _, t in cols)
    print(f"  {table}: reading from this laptop...", flush=True)
    batch = []
    for row in src.execute(f"SELECT {sel} FROM {table} WHERE {where}", args):
        row = list(row)
        if org_map:
            for i, (c, _) in enumerate(cols):
                if c == "org_id":
                    row[i] = org_map
        batch.append([json.dumps(v) if isinstance(v, (dict, list)) and t in ("jsonb", "json") else v for v, (_, t) in zip(row, cols)])
    sql = f"INSERT INTO {table} ({ins}) VALUES ({marks})"
    for start in range(0, len(batch), 100):             # a hundred rows per trip to the Test database: many small trips over the internet are very slow
        with dst.cursor() as cur:
            cur.executemany(sql, batch[start:start + 100])
        print(f"  {table}: {min(start + 100, len(batch))} of {len(batch)} sent", flush=True)
    return len(batch)


def fix_sequence(dst, table):
    dst.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), coalesce((SELECT max(id) FROM {table}), 1))")


def target_address(hide):
    url = getpass.getpass("Paste the TEST database's public address (nothing shows), then Enter: ") if hide else input("TEST database address: ")
    url = url.strip().strip("\"'").strip()
    if not re.match(r"^postgres(ql)?://", url):
        sys.exit("That is not a database address (it must start with postgresql://).")
    if url == os.environ.get("DATABASE_URL", ""):
        sys.exit("That is this laptop's own database. Paste the Test site's address.")
    return url


def main():
    ap = argparse.ArgumentParser(description="Seed a TEST database with a DADA2 sample and test accounts.")
    ap.add_argument("--ids", required=True, help="a JSON file holding the list of laptop paper ids to copy")
    ap.add_argument("--owner", required=True, help="email of the test owner account (sees every organization)")
    ap.add_argument("--admin", default="", help="email of a test administrator of the sample organization")
    ap.add_argument("--member", default="", help="email of a test member of the sample organization")
    ap.add_argument("--folder", default="dada2", help="the organization's folder name on the Test site (default dada2)")
    ap.add_argument("--hide", action="store_true", help="hide the address as it is pasted")
    a = ap.parse_args()
    ids = json.loads(Path(a.ids).read_text())
    emails = {r: auth.clean_email(e) for r, e in (("owner", a.owner), ("admin", a.admin), ("member", a.member)) if e}
    if not all(emails.values()):
        sys.exit("One of the email addresses is not valid.")
    if not os.environ.get("DATABASE_URL"):
        sys.exit("No DATABASE_URL in .env, so the laptop's database (the source) cannot be reached.")

    url = target_address(a.hide)
    src = connect()
    try:
        dst = connect(url, connect_timeout=20)
    except Exception:
        sys.exit("Could not connect to the Test database. Check that you copied DATABASE_PUBLIC_URL, whole, from the Postgres service of the TEST environment.")
    with src, dst:
        if not dst.execute("SELECT to_regclass('public.papers') IS NOT NULL").fetchone()[0]:
            sys.exit("The Test database has no tables yet. Let the Test site start once (it creates them), then run this again.")
        papers, asked = dst.execute("SELECT (SELECT count(*) FROM papers), (SELECT count(*) FROM ask_log)").fetchone()
        if papers or asked > 0:
            sys.exit("The target database already holds papers or questions. This script only fills an empty Test database.")
        if dst.execute("SELECT count(*) FROM users").fetchone()[0]:
            sys.exit("The target database already has accounts. This script only fills a fresh Test database.")

        src_org = src.execute("SELECT id FROM organizations WHERE slug = %s", (ORG_SLUG,)).fetchone()[0]
        dst.execute("INSERT INTO organizations (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING", (ORG_SLUG, "DADA2 (test sample)"))
        dst_org = dst.execute("SELECT id FROM organizations WHERE slug = %s", (ORG_SLUG,)).fetchone()[0]

        n = {}
        n["papers"] = copy_rows(src, dst, "papers", "org_id = %s AND id = ANY(%s)", (src_org, ids), org_map=dst_org)
        n["chunks"] = copy_rows(src, dst, "chunks", "org_id = %s AND paper_id = ANY(%s)", (src_org, ids), org_map=dst_org)
        n["extractions"] = copy_rows(src, dst, "extractions", "org_id = %s AND paper_id = ANY(%s)", (src_org, ids), org_map=dst_org)
        n["entry_terms"] = copy_rows(src, dst, "entry_terms", "extraction_id IN (SELECT id FROM extractions WHERE org_id = %s AND paper_id = ANY(%s))",
                                     (src_org, ids))
        for t in ("papers", "chunks", "extractions", "entry_terms"):
            fix_sequence(dst, t)

        for role, email in emails.items():
            uid = auth.create_user(dst, email, f"Test {role}", None, is_super=(role == "owner"))
            if role != "owner":
                auth.add_member(dst, uid, a.folder, role)
            auth.audit(dst, uid, "seeded_test_account", role)
        dst.commit()
    print("Copied:", ", ".join(f"{v} {k}" for k, v in n.items()))
    print("Accounts:", ", ".join(f"{r} ({e})" for r, e in emails.items()))
    print("Next: on the Test site, Home > Move an organization, folder name:", a.folder, "and import the DADA2 settings zip.")


if __name__ == "__main__":
    main()
