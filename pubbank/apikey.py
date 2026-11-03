"""Which Anthropic API key an organization's model calls are billed to.

An organization can enter its own key. It is stored encrypted in the database (organizations.api_key_enc); the encryption secret is the
host setting PUBBANK_KEY_SECRET and is never stored with the data. Until an organization enters its own key, calls use the shared default
key in the .env file, so the pilot keeps working unchanged. The key is never sent to the browser or written to logs: the page only ever
sees whether a key is set and its last four characters.

Older installations kept the key as plain text in orgs/<folder>/api_key.txt; the first time it is read it is encrypted into the database and
the file is deleted.
"""
import base64
import hashlib
import os
import re

import anthropic

from pubbank import db, orgs

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # the Windows laptop may block the compiled library; hosted installations always have it
    Fernet = None
    InvalidToken = Exception

KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$")
_KEY_IN_TEXT = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")

SECRET_MESSAGE = ("This installation has no encryption secret (PUBBANK_KEY_SECRET), so it cannot store an organization's key safely. "
                  "Ask whoever runs the installation to set it.")


def scrub(text):
    """Hide anything that looks like an Anthropic key in text that may be shown or logged."""
    return _KEY_IN_TEXT.sub("[hidden]", str(text))


def can_encrypt():
    return bool(Fernet and os.environ.get("PUBBANK_KEY_SECRET", "").strip())


def _fernet():
    secret = os.environ.get("PUBBANK_KEY_SECRET", "").strip()
    if not (Fernet and secret):
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def _file(folder):
    return orgs.ORGS_DIR / folder / "api_key.txt"


def _slug(folder):
    return orgs.load(folder).get("db_slug", "")


def _read_db(folder):
    """The stored token ("" when none), or None when the organization has no row or the database is unreachable."""
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT api_key_enc FROM organizations WHERE slug = %s", (_slug(folder),)).fetchone()
        return (row[0] or "") if row else None
    except Exception:
        return None


def _write_db(folder, token):
    with db.connect() as conn:
        n = conn.execute("UPDATE organizations SET api_key_enc = %s WHERE slug = %s", (token or None, _slug(folder))).rowcount
    return n > 0


def own_key(folder):
    f = _fernet()
    token = _read_db(folder)
    if token and f:
        try:
            return f.decrypt(token.encode("ascii")).decode("utf-8").strip()
        except (InvalidToken, ValueError):
            return ""
    path = _file(folder)
    try:
        legacy = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    # an old plain-text file: move it into the database now (encrypted) and delete it
    if legacy and f and token == "":
        try:
            if _write_db(folder, f.encrypt(legacy.encode("utf-8")).decode("ascii")):
                path.unlink()
        except Exception:
            pass
    return legacy


def shared_key(folder):
    """The host's shared default key, unless this organization was created by someone other than the owner: those must bring their own."""
    if orgs.load(folder).get("own_key_required"):
        return ""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def key_for(folder):
    """The key this organization's calls use: its own, else the shared default from .env (not for organizations that must bring their own), else empty."""
    return own_key(folder) or shared_key(folder)


def has_key(folder):
    return bool(key_for(folder))


def status(folder):
    """What the page may show: where the key comes from, and for an organization's own key only the last four characters."""
    own = own_key(folder)
    if own:
        return {"source": "own", "last4": own[-4:]}
    return {"source": "default" if shared_key(folder) else "none", "last4": ""}


def client(folder):
    key = key_for(folder)
    if not key:
        raise RuntimeError("No Anthropic API key is set for this organization.")
    return anthropic.Anthropic(api_key=key)


def folder_of(conn, org_id):
    slug = conn.execute("SELECT slug FROM organizations WHERE id = %s", (org_id,)).fetchone()[0]
    for cfg in orgs.list_orgs():
        if cfg["db_slug"] == slug:
            return cfg["folder"]
    raise RuntimeError("The organization's folder was not found.")


def client_for_org(conn, org_id):
    return client(folder_of(conn, org_id))


def test(key):
    """Ask Anthropic whether the key works. Listing models uses no tokens, so this costs nothing. Returns (ok, message)."""
    try:
        anthropic.Anthropic(api_key=key, max_retries=1, timeout=20).models.list(limit=1)
        return True, "The key works."
    except anthropic.AuthenticationError:
        return False, "Anthropic did not accept this key. Check that it was copied completely."
    except anthropic.PermissionDeniedError:
        return False, "Anthropic accepted the key but it does not have permission to use the API."
    except anthropic.APIConnectionError:
        return False, "Could not reach Anthropic to check the key. Check the internet connection and try again."
    except anthropic.APIStatusError as e:
        return False, f"Anthropic returned an error ({e.status_code}) while checking the key."


def save(folder, key):
    """Check the key and, if it works, store it (encrypted) for this organization. Returns (ok, message)."""
    key = (key or "").strip()
    if not KEY_RE.match(key):
        return False, "That does not look like an Anthropic key. Keys start with sk-ant- and have no spaces."
    f = _fernet()
    if not f:
        return False, SECRET_MESSAGE
    ok, msg = test(key)
    if not ok:
        return False, msg
    if not _write_db(folder, f.encrypt(key.encode("utf-8")).decode("ascii")):
        return False, "The organization was not found, so the key was not saved."
    try:
        _file(folder).unlink()
    except OSError:
        pass
    return True, "Saved. The key works."


def remove(folder):
    try:
        _write_db(folder, None)
    except Exception:
        pass
    try:
        _file(folder).unlink()
    except FileNotFoundError:
        pass
