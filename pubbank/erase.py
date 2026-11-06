"""Deleting one person's information on request (the owner's tool; see the Privacy Notice, "How long we keep it").

What goes: the account, memberships and sign-in sessions, every question the person asked (and the ratings given to those answers), sign-in and
invite records that carry their email address, their entry on the approved-addresses list and any access request they sent.
What stays: the record of which terms they accepted (the notice says it is kept as proof of what was agreed).
The deletion itself is written to the audit log with counts only: never the email address, name or anything else that was deleted.
"""


def _like(email):
    return email.strip().lower()


def report(conn, email):
    """What is held about this email address, or None when nothing is. Counts only, plus the account's name and organizations."""
    e = _like(email)
    u = conn.execute("SELECT id, email, name, is_super, active, last_login FROM users WHERE lower(email) = %s", (e,)).fetchone()
    uid = u[0] if u else None
    one = lambda q, *a: conn.execute(q, a).fetchone()[0]          # noqa: E731
    r = {
        "email": e,
        "user": {"id": u[0], "email": u[1], "name": u[2], "is_super": u[3], "active": u[4], "last_login": u[5]} if u else None,
        "memberships": [{"folder": m[0], "role": m[1]} for m in conn.execute("SELECT org_folder, role FROM memberships WHERE user_id = %s ORDER BY org_folder", (uid,))] if u else [],
        "questions": one("SELECT count(*) FROM ask_log WHERE user_id = %s", uid) if u else 0,
        "ratings": one("SELECT count(*) FROM answer_feedback WHERE user_id = %s OR ask_id IN (SELECT id FROM ask_log WHERE user_id = %s)", uid, uid) if u else 0,
        "sessions": one("SELECT count(*) FROM sessions WHERE user_id = %s", uid) if u else 0,
        "invites": one("SELECT count(*) FROM invites WHERE lower(email) = %s", e),
        "audit": one("SELECT count(*) FROM audit_log WHERE user_id = %s OR position(%s in lower(coalesce(detail, ''))) > 0", uid, e),
        "approved": one("SELECT count(*) FROM admin_allowlist WHERE lower(email) = %s", e),
        "requests": one("SELECT count(*) FROM access_requests WHERE lower(email) = %s", e),
        "terms_kept": one("SELECT count(*) FROM terms_acceptances WHERE lower(email) = %s", e),
    }
    held = sum(r[k] for k in ("questions", "ratings", "sessions", "invites", "audit", "approved", "requests")) + (1 if u else 0)
    return r if held or r["terms_kept"] else None


def erase(conn, email, by_user_id):
    """Delete everything report() lists, except the terms record. Returns the counts that were deleted. Raises ValueError for the owner's account."""
    e = _like(email)
    u = conn.execute("SELECT id, is_super FROM users WHERE lower(email) = %s", (e,)).fetchone()
    if u and u[1]:
        raise ValueError("This is the installation owner's account. It cannot be deleted here.")
    uid = u[0] if u else None
    done = {}

    def run(name, query, *args):
        done[name] = conn.execute(query, args).rowcount

    if uid is not None:
        run("ratings", "DELETE FROM answer_feedback WHERE user_id = %s OR ask_id IN (SELECT id FROM ask_log WHERE user_id = %s)", uid, uid)
        run("questions", "DELETE FROM ask_log WHERE user_id = %s", uid)
        conn.execute("UPDATE invites SET created_by = NULL WHERE created_by = %s", (uid,))
        conn.execute("UPDATE admin_allowlist SET added_by = NULL WHERE added_by = %s", (uid,))
    run("invites", "DELETE FROM invites WHERE lower(email) = %s", e)
    run("audit", "DELETE FROM audit_log WHERE user_id IS NOT DISTINCT FROM %s OR position(%s in lower(coalesce(detail, ''))) > 0", uid if uid is not None else -1, e)
    run("approved", "DELETE FROM admin_allowlist WHERE lower(email) = %s", e)
    run("requests", "DELETE FROM access_requests WHERE lower(email) = %s", e)
    if uid is not None:
        run("account", "DELETE FROM users WHERE id = %s", uid)          # memberships and sign-in sessions go with it
    conn.execute("INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'person_erased', %s)",
                 (by_user_id, ", ".join(f"{k} {v}" for k, v in done.items() if v) or "nothing found"))
    conn.commit()
    return done


def recent(conn, limit=10):
    """The latest deletions (when, and the counts): nothing that identifies the person."""
    return [{"at": r[0], "what": r[1]} for r in conn.execute("SELECT at, detail FROM audit_log WHERE action = 'person_erased' ORDER BY at DESC LIMIT %s", (limit,))]
