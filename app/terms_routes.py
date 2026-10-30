"""The legal pages (open to everyone), the Member Terms acceptance screen, and the owner's download of acceptance records."""
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.auth_routes import _safe_next
from pubbank import auth, orgs, terms
from pubbank.db import connect


def terms_context(conn, user, key):
    """What the acceptance screen says: a welcome the first time, or 'we've updated…' (with what changed) for someone who accepted an earlier version."""
    doc = terms.current(key)
    before = terms.earlier_version(conn, user["id"], key)
    return {"doc": doc, "updated": before is not None, "before": before}


def register(app, page):
    @app.get("/legal/{slug}", response_class=HTMLResponse)
    def legal(request: Request, slug: str):
        doc = terms.by_slug(slug)
        if doc is None:
            return HTMLResponse("Unknown page", status_code=404)
        return page(request, "legal.html", doc=doc)

    @app.get("/terms", response_class=HTMLResponse)
    def terms_screen(request: Request, next: str = ""):
        user = request.state.user
        nxt = _safe_next(next)
        with connect() as conn:
            if terms.accepted(conn, user["id"], "member_terms"):
                return RedirectResponse(nxt, status_code=303)
            ctx = terms_context(conn, user, "member_terms")
        return page(request, "terms.html", err="", action="/terms/accept", hidden={"next": nxt}, button="Continue", cancel=None, **ctx)

    @app.post("/terms/accept")
    def terms_accept(request: Request, agree: str = Form(""), version: str = Form(""), next: str = Form("")):
        user = request.state.user
        nxt = _safe_next(next)
        with connect() as conn:
            if terms.accepted(conn, user["id"], "member_terms"):
                return RedirectResponse(nxt, status_code=303)
            current = terms.current("member_terms")
            if agree != "on" or version != current["version"]:
                ctx = terms_context(conn, user, "member_terms")
                msg = "Please tick the box to continue." if agree != "on" else "The terms were just updated. Please read the new version and agree again."
                return page(request, "terms.html", status_code=400, err=msg, action="/terms/accept", hidden={"next": nxt}, button="Continue", cancel=None, **ctx)
            folders = [f for f in user["orgs"]]
            folder = folders[0] if len(folders) == 1 and not user["is_super"] else None      # the organization is known only when there is exactly one
            org_id = None
            if folder:
                org_id = terms.org_id_for(conn, orgs.load(folder).get("db_slug"))
            terms.record(conn, user, "member_terms", folder, org_id)
            auth.audit(conn, user["id"], "terms_accepted", f"member_terms {current['version']}")
            conn.commit()
        return RedirectResponse(nxt, status_code=303)

    @app.get("/admin/terms-acceptances.csv")
    def acceptances_csv(request: Request):
        """Every acceptance on record (installation owner only: this address is not in the access table, so it defaults to owner-only)."""
        with connect() as conn:
            body = terms.export_csv(conn)
            auth.audit(conn, request.state.user["id"], "terms_exported", "")
            conn.commit()
        return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="terms_acceptances.csv"'})
