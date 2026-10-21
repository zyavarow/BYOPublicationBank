"""Sign-in, first-run, invitations, account and people pages."""
import os
import secrets
import threading

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from pubbank import auth, mailer, orgs
from pubbank.db import connect


def _safe_next(url):
    """Only addresses on this site (never another website)."""
    return url if url and url.startswith("/") and not url.startswith("//") and "\\" not in url else "/"


def _client_ip(request):
    return request.client.host if request.client else ""


def _set_cookie(response, token):
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=auth.HOSTED, path="/")


def _local_request(request):
    host = (request.headers.get("host") or "").split(":")[0]
    return host in ("localhost", "127.0.0.1", "[::1]")


def register(app, page):
    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ------------------------------------------------------------------------------------------ first account
    def _first_run_problem(request):
        code = os.environ.get("PUBBANK_SETUP_CODE", "")
        if not code and not _local_request(request):
            return ("No setup code is set for this installation. Add PUBBANK_SETUP_CODE (any long random text) in the host's settings, restart, "
                    "and come back to this page.")
        return ""

    @app.get("/first-run", response_class=HTMLResponse)
    def first_run(request: Request):
        with connect() as conn:
            if auth.has_users(conn):
                return RedirectResponse("/login", status_code=303)
        return page(request, "first_run.html", need_code=bool(os.environ.get("PUBBANK_SETUP_CODE")), problem=_first_run_problem(request), err="", form={}, mode=auth.login_mode())

    @app.post("/first-run")
    def first_run_post(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""), password2: str = Form(""), code: str = Form("")):
        form = {"name": name, "email": email}
        need_code = bool(os.environ.get("PUBBANK_SETUP_CODE"))

        def fail(msg, status=400):
            return page(request, "first_run.html", need_code=need_code, problem=_first_run_problem(request), err=msg, form=form, mode=auth.login_mode(), status_code=status)
        with connect() as conn:
            if auth.has_users(conn):
                return RedirectResponse("/login", status_code=303)
            problem = _first_run_problem(request)
            if problem:
                return fail(problem, 403)
            if need_code and not secrets.compare_digest(code.strip(), os.environ["PUBBANK_SETUP_CODE"]):
                return fail("The setup code is not right.", 403)
            e = auth.clean_email(email)
            if not e:
                return fail("Enter a valid email address.")
            if auth.passwords_allowed():
                if password != password2:
                    return fail("The two passwords are different.")
                bad = auth.password_problem(password, e)
                if bad:
                    return fail(bad)
            uid = auth.create_user(conn, e, name, password if auth.passwords_allowed() else None, is_super=True)
            auth.audit(conn, uid, "first_run", "installation owner created")
            token = auth.new_session(conn, uid)
            conn.commit()
        r = RedirectResponse("/", status_code=303)
        _set_cookie(r, token)
        return r

    def _login_page(request, status_code=200, **kw):
        ctx = dict(next="/", err="", info="", email="", mail_ready=mailer.configured(), mode=auth.login_mode())
        ctx.update(kw)
        return page(request, "login.html", status_code=status_code, **ctx)

    # ------------------------------------------------------------------------------------------ sign in / out
    @app.get("/login", response_class=HTMLResponse)
    def login(request: Request, next: str = "", msg: str = ""):
        with connect() as conn:
            if not auth.has_users(conn):
                return RedirectResponse("/first-run", status_code=303)
            if auth.user_for_token(conn, request.cookies.get(auth.COOKIE)):
                return RedirectResponse(_safe_next(next), status_code=303)
        return _login_page(request, next=_safe_next(next), info=msg)

    @app.post("/login")
    def login_post(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("")):
        if not auth.passwords_allowed():
            return _login_page(request, status_code=400, next=_safe_next(next), err="Passwords are turned off here. Use the emailed sign-in link.", email=email)
        with connect() as conn:
            uid, err = auth.authenticate(conn, email, password, _client_ip(request))
            if not uid:
                auth.audit(conn, None, "login_failed", auth.clean_email(email)[:120])
                conn.commit()
                return _login_page(request, status_code=429 if err.startswith("Too many") else 400, next=_safe_next(next), err=err, email=email)
            token = auth.new_session(conn, uid)
            auth.audit(conn, uid, "login")
            conn.commit()
        r = RedirectResponse(_safe_next(next), status_code=303)
        _set_cookie(r, token)
        return r

    # ------------------------------------------------------------------------------------------ sign in with an emailed link (PUBBANK_LOGIN = both or link)
    LOGIN_MAIL = (
        "Hello,\n\nHere is your sign-in link for BYO Publication Bank. It works once and expires in {minutes} minutes:\n{url}\n\n"
        "If you did not ask to sign in, you can ignore this email: nobody can sign in without this link.\n")

    def _login_send(to, url):
        try:
            mailer.send(to, "Your BYO Publication Bank sign-in link", LOGIN_MAIL.format(url=url, minutes=auth.LOGIN_LINK_MINUTES))
        except Exception as e:   # never shown to the visitor (it would reveal which addresses have accounts); the host log shows it
            print(f"[mail] could not send a sign-in link: {type(e).__name__}: {e}", flush=True)

    def _recent(conn, action, like):
        return conn.execute("SELECT count(*) FROM audit_log WHERE action = %s AND detail LIKE %s AND at > now() - interval '1 hour'", (action, like)).fetchone()[0]

    @app.post("/login/link", response_class=HTMLResponse)
    def login_link(request: Request, email: str = Form(""), next: str = Form("")):
        if not auth.links_allowed():
            return RedirectResponse("/login", status_code=303)
        e = auth.clean_email(email)
        if not e:
            return _login_page(request, status_code=400, next=_safe_next(next), err="Enter the email address you sign in with.", email=email)
        ip = _client_ip(request)
        local_link = ""
        with connect() as conn:
            # a limit per address and per visitor per hour (see auth.link_limits), so this cannot be used to flood someone's inbox
            max_email, max_ip = auth.link_limits()
            per_email = _recent(conn, "login_link_requested", f"%|{e}")
            per_ip = _recent(conn, "login_link_requested", f"{ip}|%")
            auth.audit(conn, None, "login_link_requested", f"{ip}|{e}"[:300])
            user = auth.user_by_email(conn, e)
            # the visitor sees the same page whatever happens; the host log says which reason applied (never the address)
            if not user or not user["active"]:
                print("[signin] link not sent: no active account for that address", flush=True)
            elif per_email >= max_email:
                print(f"[signin] link not sent: this address already asked {per_email} times in the last hour (limit {max_email})", flush=True)
            elif per_ip >= max_ip:
                print(f"[signin] link not sent: this visitor already asked {per_ip} times in the last hour (limit {max_ip})", flush=True)
            if user and user["active"] and per_email < max_email and per_ip < max_ip:
                token = auth.make_link(conn, e, None, None, None, purpose="login", minutes=auth.LOGIN_LINK_MINUTES)
                url = mailer.base_url(request) + "/signin/" + token
                if mailer.configured():
                    threading.Thread(target=_login_send, args=(e, url), daemon=True).start()
                elif not auth.HOSTED and _local_request(request):
                    local_link = url          # a local laptop copy with no email set up: show the link instead (never done on a hosted site)
            conn.commit()
        return page(request, "login_sent.html", email=e, mail_ready=mailer.configured(), local_link=local_link, minutes=auth.LOGIN_LINK_MINUTES)

    @app.get("/signin/{token}", response_class=HTMLResponse)
    def signin(request: Request, token: str):
        """Opening the link only SHOWS a button. It never signs anyone in by itself: email systems often open links to scan them."""
        with connect() as conn:
            link = auth.get_link(conn, token)
            user = auth.user_by_email(conn, link["email"]) if link else None
        if not (auth.links_allowed() and link and link["purpose"] == "login" and user and user["active"]):
            return page(request, "signin.html", link=None, token="", status_code=410)
        return page(request, "signin.html", link=link, token=token)

    @app.post("/signin/{token}")
    def signin_post(request: Request, token: str):
        with connect() as conn:
            link = auth.get_link(conn, token)
            user = auth.user_by_email(conn, link["email"]) if link else None
            if not (auth.links_allowed() and link and link["purpose"] == "login" and user and user["active"]) or not auth.consume_link(conn, token):
                return page(request, "signin.html", link=None, token="", status_code=410)
            session = auth.new_session(conn, user["id"])
            auth.audit(conn, user["id"], "login_by_link")
            conn.commit()
        r = RedirectResponse("/", status_code=303)
        _set_cookie(r, session)
        return r

    # ------------------------------------------------------------------------------------------ the owner's way back in when email is not working
    @app.get("/recover", response_class=HTMLResponse)
    def recover(request: Request):
        if not auth.links_allowed():
            return RedirectResponse("/login", status_code=303)
        return page(request, "recover.html", err="", email="", has_code=bool(os.environ.get("PUBBANK_SETUP_CODE")))

    @app.post("/recover")
    def recover_post(request: Request, email: str = Form(""), code: str = Form("")):
        """The owner enters the host's setup code (a secret only whoever runs the host knows) instead of waiting for an email."""
        if not auth.links_allowed():
            return RedirectResponse("/login", status_code=303)
        want = os.environ.get("PUBBANK_SETUP_CODE", "")

        def fail(msg, status=400):
            return page(request, "recover.html", err=msg, email=email, has_code=bool(want), status_code=status)
        if not want:
            return fail("This installation has no setup code (PUBBANK_SETUP_CODE) in its host settings, so this page cannot be used.", 403)
        e = auth.clean_email(email)
        ip = _client_ip(request)
        with connect() as conn:
            too_many = _recent(conn, "recover_attempt", f"{ip}|%") >= 5 or _recent(conn, "recover_attempt", f"%|{e}") >= 5
            auth.audit(conn, None, "recover_attempt", f"{ip}|{e}"[:300])
            conn.commit()
            if too_many:
                return fail("Too many attempts. Please wait an hour and try again.", 429)
            user = auth.user_by_email(conn, e) if e else None
            code_ok = secrets.compare_digest(code.strip().encode("utf-8"), want.encode("utf-8"))
            if not (code_ok and user and user["active"] and user["is_super"]):
                return fail("That email or setup code is not right.")         # one message for every kind of failure
            session = auth.new_session(conn, user["id"])
            auth.audit(conn, user["id"], "recover_signin", "owner signed in with the setup code")
            conn.commit()
        r = RedirectResponse("/", status_code=303)
        _set_cookie(r, session)
        return r

    # ------------------------------------------------------------------------------------------ "Forgot your password?" by email
    FORGOT_MAIL = (
        "Hello,\n\nSomeone (hopefully you) asked to reset the password for this address on BYO Publication Bank.\n\n"
        "Choose a new password here (the link works once and expires in 2 hours):\n{url}\n\n"
        "If you did not ask for this, you can ignore this email: your password has not changed.\n")

    def _forgot_send(to, url):
        try:
            mailer.send(to, "Reset your BYO Publication Bank password", FORGOT_MAIL.format(url=url))
        except Exception as e:   # never shown to the visitor (it would reveal which addresses have accounts); the owner sees it in the host log
            print(f"[mail] could not send a reset email: {type(e).__name__}", flush=True)

    @app.get("/forgot", response_class=HTMLResponse)
    def forgot(request: Request):
        if not mailer.configured() or not auth.passwords_allowed():
            return RedirectResponse("/login", status_code=303)
        return page(request, "forgot.html", sent=False, err="", email="")

    @app.post("/forgot", response_class=HTMLResponse)
    def forgot_post(request: Request, email: str = Form("")):
        if not mailer.configured() or not auth.passwords_allowed():
            return RedirectResponse("/login", status_code=303)
        e = auth.clean_email(email)
        if not e:
            return page(request, "forgot.html", sent=False, err="Enter the email address you sign in with.", email=email, status_code=400)
        ip = _client_ip(request)
        with connect() as conn:
            # at most 3 requests per address and 10 per visitor per hour, so this cannot be used to flood someone's inbox
            per_email = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'forgot_requested' AND detail LIKE %s AND at > now() - interval '1 hour'", (f"%|{e}",)).fetchone()[0]
            per_ip = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'forgot_requested' AND detail LIKE %s AND at > now() - interval '1 hour'", (f"{ip}|%",)).fetchone()[0]
            auth.audit(conn, None, "forgot_requested", f"{ip}|{e}"[:300])
            user = auth.user_by_email(conn, e)
            if user and user["active"] and per_email < 3 and per_ip < 10:
                token = auth.make_link(conn, e, None, None, None, purpose="reset", hours=2)
                url = mailer.base_url(request) + "/invite/" + token
                threading.Thread(target=_forgot_send, args=(e, url), daemon=True).start()
            conn.commit()
        return page(request, "forgot.html", sent=True, err="", email=e)

    @app.post("/logout")
    def logout(request: Request):
        with connect() as conn:
            user = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
            auth.end_session(conn, request.cookies.get(auth.COOKIE))
            if user:
                auth.audit(conn, user["id"], "logout")
            conn.commit()
        r = RedirectResponse("/login?msg=You+have+been+signed+out.", status_code=303)
        r.delete_cookie(auth.COOKIE, path="/")
        return r

    # ------------------------------------------------------------------------------------------ own account
    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request, msg: str = "", err: str = ""):
        with connect() as conn:
            has_password = bool(conn.execute("SELECT password_hash FROM users WHERE id = %s", (request.state.user["id"],)).fetchone()[0])
        return page(request, "account.html", flash=msg or err, flash_error=bool(err), has_password=has_password, mode=auth.login_mode())

    @app.post("/account/password")
    def account_password(request: Request, current: str = Form(""), password: str = Form(""), password2: str = Form("")):
        user = request.state.user
        if not auth.passwords_allowed():
            return RedirectResponse("/account?err=Passwords+are+turned+off+on+this+installation.", status_code=303)
        with connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],)).fetchone()
            if row[0] and not auth.verify_password(current, row[0]):          # an account with no password yet can set one without a current password
                return RedirectResponse("/account?err=The+current+password+is+not+right.", status_code=303)
            if password != password2:
                return RedirectResponse("/account?err=The+two+new+passwords+are+different.", status_code=303)
            bad = auth.password_problem(password, user["email"])
            if bad:
                return RedirectResponse("/account?err=" + bad.replace(" ", "+"), status_code=303)
            auth.set_password(conn, user["id"], password)
            auth.audit(conn, user["id"], "password_changed")
            token = auth.new_session(conn, user["id"])
            conn.commit()
        r = RedirectResponse("/account?msg=Password+changed.", status_code=303)
        _set_cookie(r, token)
        return r

    # ------------------------------------------------------------------------------------------ invitation and reset links
    def _link_page(request, token, err="", status=200):
        with connect() as conn:
            link = auth.get_link(conn, token)
            if link and link["purpose"] not in ("invite", "reset"):
                link = None                   # a sign-in link opens at /signin, never here
            existing = auth.user_by_email(conn, link["email"]) if link else None
            user = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
        org_name = orgs.load(link["folder"])["name"] if link and link["folder"] and orgs.valid_folder(link["folder"]) else ""
        return page(request, "invite.html", link=link, existing=existing, org_name=org_name, token=token, err=err, signed_in=user,
                    mode=auth.login_mode(), status_code=status)

    @app.get("/invite/{token}", response_class=HTMLResponse)
    def invite(request: Request, token: str):
        return _link_page(request, token)

    @app.post("/invite/{token}")
    def invite_post(request: Request, token: str, name: str = Form(""), password: str = Form(""), password2: str = Form("")):
        with connect() as conn:
            link = auth.get_link(conn, token)
            if not link or link["purpose"] not in ("invite", "reset"):
                return _link_page(request, token, status=410)
            no_password = not auth.passwords_allowed()          # links are the only way in: nobody chooses a password
            existing = auth.user_by_email(conn, link["email"])
            if existing and link["purpose"] != "reset":
                return _link_page(request, token, "This email already has an account. Sign in to accept the invitation.", 400)
            if link["purpose"] == "reset" and not existing:
                return _link_page(request, token, "There is no account for this email.", 400)
            if not no_password:
                if password != password2:
                    return _link_page(request, token, "The two passwords are different.", 400)
                bad = auth.password_problem(password, link["email"])
                if bad:
                    return _link_page(request, token, bad, 400)
            if existing and not existing["active"]:
                return _link_page(request, token, "This account is not active.", 400)
            if not auth.consume_link(conn, token):
                return _link_page(request, token, status=410)
            if existing:
                uid = existing["id"]
                if no_password:
                    auth.audit(conn, uid, "login_by_reset_link")
                else:
                    auth.set_password(conn, uid, password)
                    auth.audit(conn, uid, "password_reset_by_link")
            else:
                uid = auth.create_user(conn, link["email"], name, None if no_password else password)
                auth.audit(conn, uid, "account_created_by_invite", link["folder"] or "")
            if link["folder"] and link["role"] in auth.ROLES and orgs.valid_folder(link["folder"]):
                auth.add_member(conn, uid, link["folder"], link["role"])
            session = auth.new_session(conn, uid)
            conn.commit()
        r = RedirectResponse("/", status_code=303)
        _set_cookie(r, session)
        return r

    @app.post("/invite/{token}/join")
    def invite_join(request: Request, token: str):
        """An existing account accepts an invitation to another organization (it must be signed in as that email)."""
        with connect() as conn:
            user = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
            link = auth.get_link(conn, token)
            if not link or not user or user["email"].lower() != link["email"] or link["purpose"] != "invite":
                return _link_page(request, token, "Sign in with the invited email address first.", 400)
            if link["folder"] and link["role"] in auth.ROLES and orgs.valid_folder(link["folder"]):
                auth.add_member(conn, user["id"], link["folder"], link["role"])
                auth.audit(conn, user["id"], "joined_org", link["folder"])
            auth.use_link(conn, token)
            conn.commit()
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------------------------------ people of one organization (admins)
    def _people_page(request, folder, link="", link_note="", err="", status=200):
        org = orgs.load(folder)
        org["initials"] = orgs.initials(org["name"])
        with connect() as conn:
            rows = auth.people(conn, folder)
            admin = request.state.user
            for p in rows:
                p["can_reset"] = p["id"] != admin["id"] and auth.can_reset(conn, admin, p["id"])
        return page(request, "people.html", org=org, rows=rows, link=link, link_note=link_note, err=err, status_code=status)

    @app.get("/org/{folder}/people", response_class=HTMLResponse)
    def people(request: Request, folder: str):
        if not orgs.valid_folder(folder):
            return HTMLResponse("Unknown organization", status_code=404)
        return _people_page(request, folder)

    @app.post("/org/{folder}/people/invite")
    def people_invite(request: Request, folder: str, email: str = Form(""), role: str = Form("member")):
        if not orgs.valid_folder(folder):
            return HTMLResponse("Unknown organization", status_code=404)
        e = auth.clean_email(email)
        if not e or role not in auth.ROLES:
            return _people_page(request, folder, err="Enter a valid email address and choose a role.", status=400)
        with connect() as conn:
            token = auth.make_link(conn, e, folder, role, request.state.user["id"])
            auth.audit(conn, request.state.user["id"], "invite_created", f"{folder} {role} {e}")
            conn.commit()
        url = str(request.base_url).rstrip("/") + "/invite/" + token
        return _people_page(request, folder, link=url, link_note=f"Invitation for {e} ({'administrator' if role == 'admin' else 'member'}). The link works once and expires in {auth.INVITE_HOURS} hours.")

    @app.post("/org/{folder}/people/reset")
    def people_reset(request: Request, folder: str, user_id: int = Form(...)):
        if not orgs.valid_folder(folder):
            return HTMLResponse("Unknown organization", status_code=404)
        with connect() as conn:
            admin = request.state.user
            target = conn.execute("""SELECT u.email FROM users u JOIN memberships m ON m.user_id = u.id WHERE u.id = %s AND m.org_folder = %s""", (user_id, folder)).fetchone()
            if not target or not auth.can_reset(conn, admin, user_id):
                return _people_page(request, folder, err="A reset link cannot be made for this person (they may also belong to another organization).", status=400)
            token = auth.make_link(conn, target[0], None, None, admin["id"], purpose="reset", hours=24)
            auth.audit(conn, admin["id"], "reset_link_created", target[0])
            conn.commit()
        url = str(request.base_url).rstrip("/") + "/invite/" + token
        return _people_page(request, folder, link=url, link_note=f"Password reset link for {target[0]}. It works once and expires in 24 hours.")

    @app.post("/org/{folder}/people/signin-link")
    def people_signin_link(request: Request, folder: str, user_id: int = Form(...)):
        """A sign-in link an administrator sends by hand: the way in for someone whose email is not arriving (same limits as a reset link)."""
        if not orgs.valid_folder(folder):
            return HTMLResponse("Unknown organization", status_code=404)
        if not auth.links_allowed():
            return _people_page(request, folder, err="Sign-in links are not turned on for this installation.", status=400)
        with connect() as conn:
            admin = request.state.user
            target = conn.execute("""SELECT u.email FROM users u JOIN memberships m ON m.user_id = u.id WHERE u.id = %s AND m.org_folder = %s""", (user_id, folder)).fetchone()
            if not target or not auth.can_reset(conn, admin, user_id):
                return _people_page(request, folder, err="A sign-in link cannot be made for this person (they may also belong to another organization).", status=400)
            token = auth.make_link(conn, target[0], None, None, admin["id"], purpose="login", hours=auth.MANUAL_LINK_HOURS)
            auth.audit(conn, admin["id"], "signin_link_created", target[0])
            conn.commit()
        url = str(request.base_url).rstrip("/") + "/signin/" + token
        return _people_page(request, folder, link=url, link_note=f"Sign-in link for {target[0]}. It works once and expires in {auth.MANUAL_LINK_HOURS} hours.")

    @app.post("/org/{folder}/people/remove")
    def people_remove(request: Request, folder: str, user_id: int = Form(...)):
        if not orgs.valid_folder(folder):
            return HTMLResponse("Unknown organization", status_code=404)
        admin = request.state.user
        with connect() as conn:
            if user_id == admin["id"]:
                return _people_page(request, folder, err="You cannot remove yourself.", status=400)
            n = conn.execute("DELETE FROM memberships WHERE user_id = %s AND org_folder = %s", (user_id, folder)).rowcount
            if n:
                auth.audit(conn, admin["id"], "member_removed", f"{folder} user {user_id}")
            conn.commit()
        return RedirectResponse(f"/org/{folder}/people", status_code=303)
