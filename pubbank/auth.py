"""Accounts for one installation: sign-in with email and password, sessions, invite and reset links, and who may see which organization.

Roles (per organization):  admin = runs setup and sees the question log;  member = uses the workspace.
A "super" user (the person who set the installation up) is an admin of every organization on it.
Passwords are stored only as salted scrypt hashes (Python's standard library, nothing to install). Session cookies hold a random token;
only its hash is stored, so a copy of the database cannot be used to sign in.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

COOKIE = "pb_session"
SESSION_DAYS = 14
INVITE_HOURS = 72
MIN_PASSWORD = 10
HOSTED = os.environ.get("PUBBANK_HOSTED") == "1"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES = ("admin", "member")

_N, _R, _P = 2 ** 14, 8, 1


# ---------------------------------------------------------------------------------------------- passwords
def hash_password(password):
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(h).decode())


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, h = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password("not-a-real-password")      # checked when the email is unknown, so timing does not reveal which emails have accounts


def password_problem(password, email=""):
    """Why this password is not acceptable, or '' if it is."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters. A few words in a row make a good password."
    if len(set(password)) < 4:
        return "That password is too repetitive."
    if email and password.lower() in (email.lower(), email.split("@")[0].lower()):
        return "The password cannot be the same as the email address."
    return ""


def clean_email(email):
    e = (email or "").strip().lower()
    return e if EMAIL.match(e) and len(e) <= 200 else ""


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- throttling (one server process)
_fails = {}
_lock = threading.Lock()
WINDOW = 15 * 60
LIMITS = {"e": 8, "i": 60}      # failed sign-ins per 15 minutes: per email (strict), per network address (loose, so people sharing one address are not locked out)


def _blocked(keys):
    now = time.time()
    with _lock:
        for k in keys:
            _fails[k] = [t for t in _fails.get(k, []) if now - t < WINDOW]
        return any(len(_fails[k]) >= LIMITS[k[0]] for k in keys)


def _fail(keys):
    with _lock:
        for k in keys:
            _fails.setdefault(k, []).append(time.time())


# ---------------------------------------------------------------------------------------------- users
def has_users(conn):
    return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def audit(conn, user_id, action, detail=""):
    conn.execute("INSERT INTO audit_log (user_id, action, detail) VALUES (%s, %s, %s)", (user_id, action, detail[:500]))


def create_user(conn, email, name, password, is_super=False):
    uid = conn.execute("INSERT INTO users (email, name, password_hash, is_super) VALUES (%s, %s, %s, %s) RETURNING id",
                       (email, name.strip()[:120], hash_password(password), is_super)).fetchone()[0]
    return uid


def set_password(conn, user_id, password):
    conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(password), user_id))
    conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))      # everywhere else is signed out


def authenticate(conn, email, password, ip=""):
    """(user id, '') when the email and password are right, else (None, message). Repeated failures are slowed down."""
    email = clean_email(email)
    keys = [f"e:{email}", f"i:{ip}"]
    if _blocked(keys):
        return None, "Too many attempts. Please wait 15 minutes and try again."
    row = conn.execute("SELECT id, password_hash, active FROM users WHERE lower(email) = %s", (email,)).fetchone() if email else None
    ok = verify_password(password or "", row[1] if row else _DUMMY)
    if not (row and ok and row[2]):
        _fail(keys)
        return None, "That email and password do not match an active account."
    with _lock:
        _fails.pop(keys[0], None)
    return row[0], ""


def new_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires_at < now()")
    conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + make_interval(days => %s))", (_digest(token), user_id, SESSION_DAYS))
    conn.execute("UPDATE users SET last_login = now() WHERE id = %s", (user_id,))
    return token


def end_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_digest(token),))


def user_for_token(conn, token):
    """The signed-in user for a session cookie: {id, email, name, is_super, orgs: {folder: role}}, or None."""
    if not token:
        return None
    row = conn.execute("""SELECT u.id, u.email, u.name, u.is_super FROM sessions s JOIN users u ON u.id = s.user_id
                          WHERE s.token_hash = %s AND s.expires_at > now() AND u.active""", (_digest(token),)).fetchone()
    if not row:
        return None
    orgs = {f: r for f, r in conn.execute("SELECT org_folder, role FROM memberships WHERE user_id = %s", (row[0],))}
    return {"id": row[0], "email": row[1], "name": row[2], "is_super": row[3], "orgs": orgs}


# ---------------------------------------------------------------------------------------------- who may do what
def role_in(user, folder):
    """'admin', 'member' or None for this user in this organization."""
    if not user:
        return None
    return "admin" if user["is_super"] else user["orgs"].get(folder)


def allows(user, folder, need):
    r = role_in(user, folder)
    return r == "admin" or (r == "member" and need == "member")


def is_any_admin(user):
    return bool(user) and (user["is_super"] or "admin" in user["orgs"].values())


def visible_folders(user, folders):
    return [f for f in folders if role_in(user, f)]


# ---------------------------------------------------------------------------------------------- invitations and reset links
def make_link(conn, email, folder, role, created_by, purpose="invite", hours=INVITE_HOURS):
    """A one-time link token (shown once to whoever made it)."""
    token = secrets.token_urlsafe(32)
    conn.execute("""INSERT INTO invites (token_hash, email, org_folder, role, purpose, created_by, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now() + make_interval(hours => %s))""", (_digest(token), email, folder, role, purpose, created_by, hours))
    return token


def get_link(conn, token):
    r = conn.execute("""SELECT email, org_folder, role, purpose FROM invites WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()""", (_digest(token or ""),)).fetchone()
    return {"email": r[0], "folder": r[1], "role": r[2], "purpose": r[3]} if r else None


def use_link(conn, token):
    conn.execute("UPDATE invites SET used_at = now() WHERE token_hash = %s", (_digest(token),))


def user_by_email(conn, email):
    r = conn.execute("SELECT id, email, name, is_super, active FROM users WHERE lower(email) = %s", (email,)).fetchone()
    return {"id": r[0], "email": r[1], "name": r[2], "is_super": r[3], "active": r[4]} if r else None


def add_member(conn, user_id, folder, role):
    conn.execute("""INSERT INTO memberships (user_id, org_folder, role) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, org_folder) DO UPDATE SET role = EXCLUDED.role""", (user_id, folder, role))


def people(conn, folder):
    """Everyone with access to this organization (not counting super users, who can reach all of them)."""
    return [{"id": r[0], "email": r[1], "name": r[2], "role": r[3], "active": r[4], "last_login": r[5]} for r in conn.execute(
        """SELECT u.id, u.email, u.name, m.role, u.active, u.last_login FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE m.org_folder = %s ORDER BY m.role, lower(u.email)""", (folder,))]


def can_reset(conn, admin, target_id):
    """An organization admin may reset a password only for people who belong to no other organization (otherwise they could take over
    that person's access elsewhere). A super user may reset anyone's."""
    if admin["is_super"]:
        return True
    t = conn.execute("SELECT is_super FROM users WHERE id = %s", (target_id,)).fetchone()
    if not t or t[0]:
        return False
    theirs = {r[0] for r in conn.execute("SELECT org_folder FROM memberships WHERE user_id = %s", (target_id,))}
    mine = {f for f, r in admin["orgs"].items() if r == "admin"}
    return bool(theirs) and theirs <= mine
