"""The beta allowlist: which email addresses may create a publication bank (and so become an administrator).

Exact addresses only, compared in lower case after trimming spaces. The installation owner always may. Members are not on this list:
they join only by invitation from an administrator of their organization.
"""
import re

from pubbank import auth

SPLIT = re.compile(r"[,;\s]+")


def parse(text):
    """(valid addresses without repeats, things that were not addresses) from pasted text: commas, semicolons, spaces or one per line."""
    good, bad = [], []
    for piece in SPLIT.split(text or ""):
        piece = piece.strip().strip("<>\"'")
        if not piece:
            continue
        e = auth.clean_email(piece)
        if e:
            if e not in good:
                good.append(e)
        else:
            bad.append(piece[:60])
    return good, bad


def is_allowed(conn, email):
    return conn.execute("SELECT 1 FROM admin_allowlist WHERE email = %s", (auth.clean_email(email),)).fetchone() is not None


def may_create_banks(conn, user):
    return bool(user) and (user["is_super"] or is_allowed(conn, user["email"]))


def add(conn, email, source="added by owner", by=None):
    """True when the address was new to the list."""
    return conn.execute("INSERT INTO admin_allowlist (email, source, added_by) VALUES (%s, %s, %s) ON CONFLICT (email) DO NOTHING RETURNING 1",
                        (auth.clean_email(email), source[:60], by)).fetchone() is not None


def remove(conn, email):
    conn.execute("DELETE FROM admin_allowlist WHERE email = %s", (auth.clean_email(email),))


def rows(conn):
    return [{"email": r[0], "source": r[1], "added_at": r[2]} for r in conn.execute("SELECT email, source, added_at FROM admin_allowlist ORDER BY email")]


# ---------------------------------------------------------------------------------------------- requests for access
def add_request(conn, email, name, note):
    conn.execute("INSERT INTO access_requests (email, name, note) VALUES (%s, %s, %s)", (email, name.strip()[:120], note.strip()[:1000]))


def open_requests(conn):
    return [{"id": r[0], "email": r[1], "name": r[2], "note": r[3], "at": r[4]} for r in conn.execute(
        "SELECT id, email, name, note, requested_at FROM access_requests WHERE status = 'new' ORDER BY requested_at")]


def set_request(conn, request_id, status):
    conn.execute("UPDATE access_requests SET status = %s WHERE id = %s", (status, request_id))


# ---------------------------------------------------------------------------------------------- blocked attempts
BLOCKED = {"signup_blocked": "tried to create an account", "admin_invite_blocked": "invited as an administrator"}


def blocked(conn, limit=60):
    """The latest refused attempts: [{email, what, at}]. (The audit detail is 'where|address'; only the address is shown.)"""
    out = []
    for action, detail, at in conn.execute("SELECT action, detail, at FROM audit_log WHERE action = ANY(%s) ORDER BY at DESC LIMIT %s", (list(BLOCKED), limit)):
        out.append({"email": (detail or "").split("|", 1)[-1], "what": BLOCKED[action], "at": at})
    return out
