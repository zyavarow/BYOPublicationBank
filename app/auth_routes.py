"""Sign-in, first-run, invitations, account and people pages."""
import os
import secrets

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from pubbank import auth, orgs
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
        return page(request, "first_run.html", need_code=bool(os.environ.get("PUBBANK_SETUP_CODE")), problem=_first_run_problem(request), err="", form={})

    @app.post("/first-run")
    def first_run_post(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""), password2: str = Form(""), code: str = Form("")):
        form = {"name": name, "email": email}
        need_code = bool(os.environ.get("PUBBANK_SETUP_CODE"))

        def fail(msg, status=400):
            return page(request, "first_run.html", need_code=need_code, problem=_first_run_problem(request), err=msg, form=form, status_code=status)
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
            if password != password2:
                return fail("The two passwords are different.")
            bad = auth.password_problem(password, e)
            if bad:
                return fail(bad)
            uid = auth.create_user(conn, e, name, password, is_super=True)
            auth.audit(conn, uid, "first_run", "installation owner created")
            token = auth.new_session(conn, uid)
            conn.commit()
        r = RedirectResponse("/", status_code=303)
        _set_cookie(r, token)
        return r

    # ------------------------------------------------------------------------------------------ sign in / out
    @app.get("/login", response_class=HTMLResponse)
    def login(request: Request, next: str = "", msg: str = ""):
        with connect() as conn:
            if not auth.has_users(conn):
                return RedirectResponse("/first-run", status_code=303)
            if auth.user_for_token(conn, request.cookies.get(auth.COOKIE)):
                return RedirectResponse(_safe_next(next), status_code=303)
        return page(request, "login.html", next=_safe_next(next), err="", info=msg, email="")

    @app.post("/login")
    def login_post(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("")):
        with connect() as conn:
            uid, err = auth.authenticate(conn, email, password, _client_ip(request))
            if not uid:
                auth.audit(conn, None, "login_failed", auth.clean_email(email)[:120])
                conn.commit()
                return page(request, "login.html", next=_safe_next(next), err=err, info="", email=email, status_code=429 if err.startswith("Too many") else 400)
            token = auth.new_session(conn, uid)
            auth.audit(conn, uid, "login")
            conn.commit()
        r = RedirectResponse(_safe_next(next), status_code=303)
        _set_cookie(r, token)
        return r

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
        return page(request, "account.html", flash=msg or err, flash_error=bool(err))

    @app.post("/account/password")
    def account_password(request: Request, current: str = Form(""), password: str = Form(""), password2: str = Form("")):
        user = request.state.user
        with connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],)).fetchone()
            if not auth.verify_password(current, row[0]):
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
            existing = auth.user_by_email(conn, link["email"]) if link else None
            user = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
        org_name = orgs.load(link["folder"])["name"] if link and link["folder"] and orgs.valid_folder(link["folder"]) else ""
        return page(request, "invite.html", link=link, existing=existing, org_name=org_name, token=token, err=err, signed_in=user,
                    status_code=status)

    @app.get("/invite/{token}", response_class=HTMLResponse)
    def invite(request: Request, token: str):
        return _link_page(request, token)

    @app.post("/invite/{token}")
    def invite_post(request: Request, token: str, name: str = Form(""), password: str = Form(""), password2: str = Form("")):
        with connect() as conn:
            link = auth.get_link(conn, token)
            if not link:
                return _link_page(request, token, status=410)
            existing = auth.user_by_email(conn, link["email"])
            if existing and link["purpose"] != "reset":
                return _link_page(request, token, "This email already has an account. Sign in to accept the invitation.", 400)
            if link["purpose"] == "reset" and not existing:
                return _link_page(request, token, "There is no account for this email.", 400)
            if password != password2:
                return _link_page(request, token, "The two passwords are different.", 400)
            bad = auth.password_problem(password, link["email"])
            if bad:
                return _link_page(request, token, bad, 400)
            if existing:
                uid = existing["id"]
                auth.set_password(conn, uid, password)
                auth.audit(conn, uid, "password_reset_by_link")
            else:
                uid = auth.create_user(conn, link["email"], name, password)
                auth.audit(conn, uid, "account_created_by_invite", link["folder"] or "")
            if link["folder"] and link["role"] in auth.ROLES and orgs.valid_folder(link["folder"]):
                auth.add_member(conn, uid, link["folder"], link["role"])
            auth.use_link(conn, token)
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
