"""Which kind of person may open which address. One table, checked for every request before any page code runs.

Levels:  public (no sign-in) · any (signed in) · super (the installation owner) · admin_any (an admin of some organization)
         member (member or admin of the organization in the address) · admin (admin of that organization)
Anything not listed here needs the installation owner, so a forgotten new page is closed by default.
"""
import re

PUBLIC_PATHS = {"/login", "/login/link", "/logout", "/first-run", "/forgot", "/recover", "/healthz"}
ORG = re.compile(r"^/org/([^/]+)(/.*)?$")
MEMBER_PART = re.compile(r"^/(workspace(/.*)?|logo|bank/jobs/[^/]+)$")      # bank/jobs/<id>: progress of a question; the route refuses other kinds of job for members
ANY_PATHS = {"/", "/account", "/account/password", "/terms", "/terms/accept"}
ADMIN_ANY_PATHS = {"/logo-colors", "/guide-colors", "/pull-colors"}


def classify(path):
    """(level, organization folder or None)."""
    if path in PUBLIC_PATHS or path.startswith(("/static/", "/invite/", "/signin/", "/legal/")):
        return "public", None
    m = ORG.match(path)
    if m:
        rest = m.group(2) or "/"
        return ("member" if MEMBER_PART.match(rest) else "admin"), m.group(1)
    if path in ANY_PATHS:
        return "any", None
    if path in ADMIN_ANY_PATHS:
        return "admin_any", None
    return "super", None
