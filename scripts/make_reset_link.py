"""Make a one-time password-reset link for ANY account on an installation (owner, administrator or member), from your own computer.

Use it when someone is locked out and there is no other way in: an administrator cannot reset an owner from the website, and emailed reset
needs an email service. The link opens the website's normal "Choose a new password" page; the person picks a new password and is signed in.

  .venv/Scripts/python.exe scripts/make_reset_link.py                 # the live site: asks for the database's public address, then the email
  .venv/Scripts/python.exe scripts/make_reset_link.py --list          # show which accounts exist (to remember which email you used)
  .venv/Scripts/python.exe scripts/make_reset_link.py --local         # the copy on this laptop (uses DATABASE_URL from .env)
  .venv/Scripts/python.exe scripts/make_reset_link.py --hide          # hide the address as you paste it (some terminals cannot paste into a hidden prompt)

The address is the host's PUBLIC database address (Railway: the Postgres service -> Variables -> DATABASE_PUBLIC_URL, or Connect -> Public Network).
It is used only for this command: it is not saved anywhere and not printed. The link is printed as a path; put your site's address in front of it.
Nothing about the website changes, so nothing needs to be deployed.
"""
import argparse
import getpass
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from pubbank import auth  # noqa: E402
from pubbank.db import connect  # noqa: E402

_SECRETS = []


def safe(text):
    """Remove the database address and password from any text before it is shown, so the output can be copied and shared."""
    text = str(text)
    for secret in sorted({x for x in _SECRETS if x}, key=len, reverse=True):
        text = text.replace(secret, "***")
    return re.sub(r"postgres(?:ql)?://\S+", "postgresql://***", text)


def database_address(a):
    """The address to connect to, checked for the usual copy-and-paste mistakes."""
    if a.local:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            sys.exit("No DATABASE_URL was found in .env, so the local copy cannot be reached.")
        _SECRETS.append(url)
        return url
    url = a.url
    if not url:
        if a.hide:
            url = getpass.getpass("Paste the database's public address (nothing will show as you paste), then press Enter: ")
        else:
            print("Paste the database's public address and press Enter. It will be visible on your screen: do not share the screen or copy it into chat.")
            url = input("Address: ")
    url = url.strip().strip("\"'").strip()
    if "${{" in url or "}}" in url:
        sys.exit("That is Railway's recipe for the address (it contains ${{ }}), not the finished address. In Railway open the Postgres service, press Connect, "
                 "choose the Public Network tab, and copy the connection URL shown there.")
    if not url:
        sys.exit("No address was entered.")
    if not re.match(r"^postgres(ql)?://", url):
        print("What was pasted is not a database address: it does not start with postgresql://")
        if "psql" in url.lower() or " " in url:
            print("  It looks like a command or several words. Copy the 'Connection URL' itself, not a psql command.")
        print("  In Railway: Postgres service > Variables > DATABASE_PUBLIC_URL (or Connect > Public Network).")
        sys.exit(1)
    try:
        u = urlparse(url)
        pw = u.password or ""
        host = u.hostname or ""
    except ValueError:
        sys.exit("The address is not in the right form (for example, the port number is not valid). Copy DATABASE_PUBLIC_URL again, whole.")
    _SECRETS.extend([url, pw, unquote(pw)])
    if host.endswith(".railway.internal"):
        sys.exit("That is Railway's INTERNAL address, which only works inside Railway. Use DATABASE_PUBLIC_URL instead.")
    return url


def role_text(conn, uid, is_super):
    if is_super:
        return "owner (every organization)"
    rows = conn.execute("SELECT org_folder, role FROM memberships WHERE user_id = %s ORDER BY org_folder", (uid,)).fetchall()
    return ", ".join(f"{'administrator' if r == 'admin' else 'member'} of {f}" for f, r in rows) or "no organization"


def main():
    ap = argparse.ArgumentParser(description="Make a one-time password-reset link for any account.")
    ap.add_argument("--url", default="", help="the database address; leave it out to be asked for it (so it never appears in a command)")
    ap.add_argument("--hide", action="store_true", help="hide the address as it is pasted")
    ap.add_argument("--local", action="store_true", help="use the copy on this computer (DATABASE_URL from .env)")
    ap.add_argument("--list", action="store_true", help="list the accounts on this installation instead of making a link")
    ap.add_argument("--email", default="", help="the account's email; leave it out to be asked")
    ap.add_argument("--hours", type=int, default=1, help="how long the link works, 1 to 24 hours (default 1)")
    a = ap.parse_args()
    if not 1 <= a.hours <= 24:
        sys.exit("--hours must be between 1 and 24.")
    url = database_address(a)
    try:
        conn = connect(url, connect_timeout=20)
    except Exception as e:
        why = safe(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
        sys.exit(safe(f"Could not connect to the database. Reason given: {why}\nCheck that you copied DATABASE_PUBLIC_URL, whole, from the Postgres service."))
    with conn:
        if not conn.execute("SELECT to_regclass('public.users') IS NOT NULL").fetchone()[0]:
            sys.exit("This database has no accounts table. The site has not started on it yet, or the address points at the wrong database.")
        if a.list:
            rows = conn.execute("SELECT id, email, name, is_super, active, last_login FROM users ORDER BY lower(email)").fetchall()
            if not rows:
                sys.exit("There are no accounts yet. Open the site: it will ask you to create the first (owner) account.")
            print(f"{len(rows)} account{'' if len(rows) == 1 else 's'}:")
            for uid, email, name, is_super, active, last in rows:
                seen = last.strftime("%b %d, %Y") if last else "never signed in"
                print(f"  {email}  ({name or 'no name'}): {role_text(conn, uid, is_super)}; {'active' if active else 'NOT active'}; last sign-in {seen}")
            return
        email = auth.clean_email(a.email or input("The account's email address: "))
        if not email:
            sys.exit("That is not a valid email address.")
        user = auth.user_by_email(conn, email)
        if not user:
            sys.exit("There is no account with that email on this installation. Run again with --list to see which accounts exist.")
        if not user["active"]:
            sys.exit("That account has been deactivated (removed by an administrator), so no link was made.")
        token = auth.make_link(conn, email, None, None, None, purpose="reset", hours=a.hours)
        auth.audit(conn, user["id"], "reset_link_created_by_host_script", email)
        who = role_text(conn, user["id"], user["is_super"])
        conn.commit()
    hours = f"{a.hours} hour{'' if a.hours == 1 else 's'}"
    print(f"\nA password-reset link was made for {email} ({who}).")
    print(f"It works once and stops working after {hours}. Open this in your browser, with your site's address in front:\n")
    print(f"    /invite/{token}\n")
    print("For example:  https://your-site-address/invite/...   (use the same address you use to sign in).")
    print("Choose a new password on that page and you are signed in. Anyone who has the link can use it once, so keep it to yourself.")


if __name__ == "__main__":
    main()
