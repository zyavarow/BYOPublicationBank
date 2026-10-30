"""The legal documents (versioned Markdown files in terms/) and the record of who accepted which version.

A document is terms/<name>-v<major>.<minor>.md; the highest version is the current one. Publishing a new version is just adding a new file.
An optional first line  <!-- changes: what is different -->  is shown to people who accepted an earlier version.
"""
import csv
import html
import io
import re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "terms"
# key -> (file name, title, address under /legal/)
DOCS = {
    "member_terms": ("member-terms", "Member Terms", "member-terms"),
    "organization_agreement": ("organization-agreement", "Organization Agreement", "organization-agreement"),
    "privacy": ("privacy-notice", "Privacy Notice", "privacy-notice"),
    "providers": ("service-providers", "Service Providers", "service-providers"),
}
EXEMPT = {"/terms", "/terms/accept", "/logout"}      # reachable before the Member Terms are accepted
CHANGES = re.compile(r"^\s*<!--\s*changes:(.*?)-->\s*", re.S)


def _version_key(v):
    return tuple(int(x) for x in v.split("."))


def _render(md):
    """A small, safe Markdown subset: ## headings, - lists, **bold**, paragraphs. Everything else is shown as plain text."""
    out, para, items = [], [], []

    def inline(s):
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(s))

    def flush():
        nonlocal para, items
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
        para, items = [], []

    for line in md.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            level = min(len(s) - len(s.lstrip("#")), 3) + 1      # ## becomes h3 on the page (the page title is the h1)
            out.append(f"<h{level}>{html.escape(s.lstrip('#').strip())}</h{level}>")
        elif s.startswith("- "):
            if para:
                flush()
            items.append(s[2:])
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


@lru_cache(maxsize=None)
def current(key):
    """{key, title, slug, version, html, changes} for the newest version of a document (files only change when the site is redeployed)."""
    name, title, slug = DOCS[key]
    found = []
    for f in DIR.glob(f"{name}-v*.md"):
        m = re.fullmatch(rf"{re.escape(name)}-v(\d+\.\d+)", f.stem)
        if m:
            found.append((_version_key(m.group(1)), m.group(1), f))
    if not found:
        raise FileNotFoundError(f"No file terms/{name}-v1.0.md")
    _, version, f = max(found)
    text = f.read_text(encoding="utf-8")
    m = CHANGES.match(text)
    return {"key": key, "title": title, "slug": slug, "version": version, "html": _render(text[m.end():] if m else text),
            "changes": (m.group(1).strip() if m else "")}


def by_slug(slug):
    for key, (_, _, s) in DOCS.items():
        if s == slug:
            return current(key)
    return None


def accepted(conn, user_id, key):
    """True when this person has accepted the current version of the document."""
    return conn.execute("SELECT 1 FROM terms_acceptances WHERE user_id = %s AND doc = %s AND version = %s LIMIT 1",
                        (user_id, key, current(key)["version"])).fetchone() is not None


def member_terms_due(conn, user_id):
    return not accepted(conn, user_id, "member_terms")


def earlier_version(conn, user_id, key):
    """The version this person accepted before (the newest), or None if they never accepted this document."""
    r = conn.execute("SELECT version FROM terms_acceptances WHERE user_id = %s AND doc = %s ORDER BY accepted_at DESC LIMIT 1", (user_id, key)).fetchone()
    return r[0] if r else None


def record(conn, user, key, org_folder=None, org_id=None):
    """Write down that this signed-in person accepted the current version of a document."""
    conn.execute("""INSERT INTO terms_acceptances (user_id, email, doc, version, org_id, org_folder) VALUES (%s, %s, %s, %s, %s, %s)""",
                 (user["id"], user["email"], key, current(key)["version"], org_id, org_folder))


def org_id_for(conn, db_slug):
    r = conn.execute("SELECT id FROM organizations WHERE slug = %s", (db_slug,)).fetchone() if db_slug else None
    return r[0] if r else None


def export_csv(conn):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["accepted_at_utc", "user_id", "email", "document", "version", "organization_id", "organization_folder"])
    for r in conn.execute("""SELECT to_char(accepted_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'), user_id, email, doc, version, org_id, org_folder
                             FROM terms_acceptances ORDER BY accepted_at, id"""):
        w.writerow(["" if v is None else ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for v in r])      # a leading ' stops a spreadsheet running an email as a formula
    return buf.getvalue()
