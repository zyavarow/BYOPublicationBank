"""Creating a publication bank during the beta: the "Create a publication bank" page for approved addresses, the request-access form for everyone
else, and the owner's page for managing the allowlist."""
import threading
from urllib.parse import quote_plus

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth_routes import _client_ip, _local_request
from pubbank import allowlist, auth, mailer
from pubbank.db import connect

SIGNUP_MINUTES = 60
SIGNUP_MAIL = ("Hello,\n\nHere is your link to create your BYO Publication Bank account. It works once and expires in {minutes} minutes:\n{url}\n\n"
               "If you did not ask for this, you can ignore this email: nobody can create an account without this link.\n")
LOGIN_MAIL = ("Hello,\n\nYou asked to create a publication bank, and this address already has an account. Here is your sign-in link. "
              "It works once and expires in {minutes} minutes:\n{url}\n\nIf you did not ask for this, you can ignore this email.\n")


def _send(to, subject, text):
    try:
        mailer.send(to, subject, text)
    except Exception as e:      # never shown to the visitor (it would reveal which addresses are approved); the host log has it
        print(f"[mail] could not send a sign-up link: {type(e).__name__}: {e}", flush=True)


def _recent(conn, action, like):
    return conn.execute("SELECT count(*) FROM audit_log WHERE action = %s AND detail LIKE %s AND at > now() - interval '1 hour'", (action, like)).fetchone()[0]


def register(app, page):
    def _start_page(request, state="form", status_code=200, **kw):
        ctx = dict(state=state, email="", err="", rname="", note="", local_link="", minutes=SIGNUP_MINUTES)
        ctx.update(kw)
        return page(request, "start.html", status_code=status_code, **ctx)

    def _can_email(request):
        return mailer.configured() or (not auth.HOSTED and _local_request(request))

    @app.get("/start", response_class=HTMLResponse)
    def start(request: Request):
        return _start_page(request, "form" if _can_email(request) else "unavailable")

    @app.post("/start", response_class=HTMLResponse)
    def start_post(request: Request, email: str = Form("")):
        if not _can_email(request):
            return _start_page(request, "unavailable")
        e = auth.clean_email(email)
        if not e:
            return _start_page(request, "form", status_code=400, err="Enter a valid email address.", email=email)
        ip = _client_ip(request)
        local_link = ""
        with connect() as conn:
            per_email = _recent(conn, "signup_requested", f"%|{e}")
            per_ip = _recent(conn, "signup_requested", f"{ip}|%")
            auth.audit(conn, None, "signup_requested", f"{ip}|{e}"[:300])
            user = auth.user_by_email(conn, e)
            if per_email >= 3 or per_ip >= 10:
                print("[signup] link not sent: too many requests from this address or visitor in the last hour", flush=True)
                conn.commit()
                return _start_page(request, "sent", email=e)
            if user and user["active"]:
                # already has an account: they get an ordinary sign-in link instead of a second account
                if not auth.links_allowed():
                    conn.commit()
                    return _start_page(request, "sent", email=e)
                token = auth.make_link(conn, e, None, None, None, purpose="login", minutes=auth.LOGIN_LINK_MINUTES)
                url, subject, text = mailer.base_url(request) + "/signin/" + token, "Your BYO Publication Bank sign-in link", LOGIN_MAIL.format(minutes=auth.LOGIN_LINK_MINUTES, url="{url}")
            elif allowlist.is_allowed(conn, e):
                token = auth.make_link(conn, e, None, None, None, purpose="signup", minutes=SIGNUP_MINUTES)
                url, subject, text = mailer.base_url(request) + "/invite/" + token, "Create your BYO Publication Bank account", SIGNUP_MAIL.format(minutes=SIGNUP_MINUTES, url="{url}")
            else:
                auth.audit(conn, None, "signup_blocked", f"{ip}|{e}"[:300])
                conn.commit()
                return _start_page(request, "blocked", email=e)
            conn.commit()
        text = text.replace("{url}", url)
        if mailer.configured():
            threading.Thread(target=_send, args=(e, subject, text), daemon=True).start()
        else:
            local_link = url      # a local laptop copy with no email set up: show the link instead (never done on a hosted site)
        return _start_page(request, "sent", email=e, local_link=local_link)

    @app.post("/start/request", response_class=HTMLResponse)
    def start_request(request: Request, email: str = Form(""), name: str = Form(""), note: str = Form("")):
        e = auth.clean_email(email)
        if not e:
            return _start_page(request, "blocked", status_code=400, err="Enter a valid email address.", email=email, rname=name, note=note)
        ip = _client_ip(request)
        with connect() as conn:
            if _recent(conn, "access_requested", f"%|{e}") >= 2 or _recent(conn, "access_requested", f"{ip}|%") >= 5:
                return _start_page(request, "blocked", status_code=429, err="Too many requests. Please try again in an hour.", email=e, rname=name, note=note)
            auth.audit(conn, None, "access_requested", f"{ip}|{e}"[:300])
            if not conn.execute("SELECT 1 FROM access_requests WHERE email = %s AND status = 'new'", (e,)).fetchone():
                allowlist.add_request(conn, e, name, note)
            conn.commit()
        return _start_page(request, "requested", email=e)

    # ------------------------------------------------------------------------------------------ the owner's allowlist page
    def _list_page(request, msg="", err="", status_code=200):
        with connect() as conn:
            ctx = dict(approved=allowlist.rows(conn), requests=allowlist.open_requests(conn), blocked=allowlist.blocked(conn))
        return page(request, "allowlist.html", flash=err or msg, flash_error=bool(err), status_code=status_code, **ctx)

    def _back(msg="", err=""):
        return RedirectResponse("/admin/allowlist?" + ("err=" if err else "msg=") + quote_plus(err or msg), status_code=303)

    @app.get("/admin/allowlist", response_class=HTMLResponse)
    def allowlist_page(request: Request, msg: str = "", err: str = ""):
        return _list_page(request, msg, err)

    @app.post("/admin/allowlist/add")
    def allowlist_add(request: Request, emails: str = Form("")):
        good, bad = allowlist.parse(emails)
        if not good and not bad:
            return _back(err="Paste at least one email address.")
        new = 0
        with connect() as conn:
            for e in good:
                if allowlist.add(conn, e, "added by owner", request.state.user["id"]):
                    new += 1
                    auth.audit(conn, request.state.user["id"], "allowlist_added", e)
                conn.execute("UPDATE access_requests SET status = 'approved' WHERE email = %s AND status = 'new'", (e,))
            conn.commit()
        msg = f"Added {new} address{'' if new == 1 else 'es'}" + (f"; {len(good) - new} already on the list" if len(good) > new else "") + "."
        if bad:
            msg += f" Not an email address, so skipped: {', '.join(bad[:8])}{'…' if len(bad) > 8 else ''}."
        return _back(err=msg) if bad and not new else _back(msg=msg)

    @app.post("/admin/allowlist/remove")
    def allowlist_remove(request: Request, email: str = Form("")):
        with connect() as conn:
            allowlist.remove(conn, email)
            auth.audit(conn, request.state.user["id"], "allowlist_removed", auth.clean_email(email))
            conn.commit()
        return _back(msg=f"Removed {auth.clean_email(email)}. They keep any bank they already have, but cannot create new ones.")

    @app.post("/admin/allowlist/request")
    def allowlist_request(request: Request, request_id: int = Form(...), action: str = Form("")):
        with connect() as conn:
            row = conn.execute("SELECT email FROM access_requests WHERE id = %s AND status = 'new'", (request_id,)).fetchone()
            if not row:
                return _back(err="That request was already handled.")
            if action == "approve":
                allowlist.add(conn, row[0], "approved request", request.state.user["id"])
                allowlist.set_request(conn, request_id, "approved")
                auth.audit(conn, request.state.user["id"], "allowlist_added", row[0])
                msg = f"Approved {row[0]}. They can now create an account from the Create a publication bank page."
            else:
                allowlist.set_request(conn, request_id, "dismissed")
                msg = "Request dismissed."
            conn.commit()
        return _back(msg=msg)
