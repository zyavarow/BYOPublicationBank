"""Check that the sign-in rules hold on a running installation: walks EVERY address the app defines and expects it to refuse a visitor who is
not signed in (except the few public pages); with --member / --admin cookies it also checks what each role can and cannot open.
  .venv/Scripts/python.exe scripts/check_access.py http://127.0.0.1:8000
  .venv/Scripts/python.exe scripts/check_access.py http://127.0.0.1:8000 --member <cookie> --admin <cookie> --folder exd
The cookie value is the pb_session cookie of a signed-in member or administrator (browser developer tools -> Application -> Cookies).
Never sends anything that changes data: POSTs are sent empty, so an address that is wrongly open fails on missing input, not by doing something.
"""
import argparse
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PUBLIC = {"/login", "/logout", "/first-run", "/healthz"}


def routes():
    from app.main import app
    seen = set()
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path or path == "/static":
            continue
        for m in getattr(r, "methods", None) or []:
            if m in ("GET", "POST"):
                seen.add((m, path))
    return sorted(seen)


def fill(path, folder):
    path = path.replace("{folder}", folder)
    return re.sub(r"\{[^}]+\}", "1", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--folder", default="exd")
    ap.add_argument("--member", default="")
    ap.add_argument("--admin", default="")
    a = ap.parse_args()
    bad = 0
    n = 0
    s = requests.Session()

    def call(method, path, cookie=""):
        h = {"Accept": "text/html"} if method == "GET" else {}
        if cookie:
            h["Cookie"] = "pb_session=" + cookie
        return s.request(method, a.base + path, headers=h, allow_redirects=False, timeout=30, data={} if method == "POST" else None)

    for method, raw in routes():
        path = fill(raw, a.folder)
        public = raw in PUBLIC or raw.startswith("/invite/") or raw.startswith("/static")
        r = call(method, path)
        n += 1
        ok = (r.status_code in (200, 303, 307, 400, 404, 405, 410, 422, 429) if public else
              (r.status_code in (401, 403) or (r.status_code in (303, 307) and any(x in r.headers.get("location", "") for x in ("/login", "/first-run")))))
        if public and r.status_code >= 500:
            ok = False
        if not ok:
            bad += 1
            print(f"OPEN?  {method} {raw} -> {r.status_code} {r.headers.get('location', '')}")
    print(f"visitor not signed in: {n} addresses checked, {bad} problems")

    def expect(label, method, path, cookie, allowed):
        nonlocal bad
        r = call(method, path, cookie)
        ok = r.status_code in allowed
        bad += not ok
        print(("ok     " if ok else "WRONG  ") + f"{label}: {method} {path} -> {r.status_code}")

    f = a.folder
    if a.member:
        expect("member can open the workspace", "GET", f"/org/{f}/workspace", a.member, (200,))
        expect("member cannot open setup", "GET", f"/org/{f}/step/1", a.member, (403,))
        expect("member cannot change the API key", "POST", f"/org/{f}/apikey/test", a.member, (403,))
        expect("member cannot see people", "GET", f"/org/{f}/people", a.member, (403,))
        expect("member cannot get the registry", "GET", "/admin/schema-registry.xlsx", a.member, (403,))
        expect("member cannot make an organization", "POST", "/orgs/new", a.member, (403,))
    if a.admin:
        expect("admin can open setup", "GET", f"/org/{f}/step/1", a.admin, (200,))
        expect("admin can see people", "GET", f"/org/{f}/people", a.admin, (200,))
        expect("admin cannot make an organization", "POST", "/orgs/new", a.admin, (403,))
        expect("admin cannot get the registry", "GET", "/admin/schema-registry.xlsx", a.admin, (403,))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
