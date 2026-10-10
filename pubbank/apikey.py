"""Which Anthropic API key an organization's model calls are billed to.

An organization can enter its own key (stored on the server in orgs/<folder>/api_key.txt, which git ignores). Until it does, calls use the
shared default key in the .env file, so the pilot keeps working unchanged. The key is never sent to the browser: the page only ever sees
whether a key is set and its last four characters.
"""
import os
import re

import anthropic

from pubbank import orgs

KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$")


def _file(folder):
    return orgs.ORGS_DIR / folder / "api_key.txt"


def own_key(folder):
    try:
        return _file(folder).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def key_for(folder):
    """The key this organization's calls use: its own, else the shared default from .env, else empty."""
    return own_key(folder) or os.environ.get("ANTHROPIC_API_KEY", "").strip()


def has_key(folder):
    return bool(key_for(folder))


def status(folder):
    """What the page may show: where the key comes from, and for an organization's own key only the last four characters."""
    own = own_key(folder)
    if own:
        return {"source": "own", "last4": own[-4:]}
    return {"source": "default" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else "none", "last4": ""}


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
    """Check the key and, if it works, store it for this organization. Returns (ok, message)."""
    key = (key or "").strip()
    if not KEY_RE.match(key):
        return False, "That does not look like an Anthropic key. Keys start with sk-ant- and have no spaces."
    ok, msg = test(key)
    if not ok:
        return False, msg
    path = _file(folder)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(key + "\n", encoding="utf-8")
    tmp.replace(path)
    return True, "Saved. The key works."


def remove(folder):
    try:
        _file(folder).unlink()
    except FileNotFoundError:
        pass
