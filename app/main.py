"""Research Portal Setup Kit: local web app (setup wizard for an organization's portal). Runs on this computer only (127.0.0.1)."""
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi import Body, FastAPI, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from app.colors import logo_colors, pull_colors  # noqa: E402
from pubbank import bank, orgs, pdfs, review, samples, sources, topic  # noqa: E402
from pubbank.db import connect  # noqa: E402

APP = Path(__file__).resolve().parent
app = FastAPI(title="Research Portal Setup Kit", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=APP / "static"), name="static")
templates = Jinja2Templates(directory=APP / "templates")
templates.env.globals["money"] = lambda v: "under 1 cent" if v < 0.01 else f"${v:.2f}"

STEP_TITLES = {3: "Extraction Schema", 5: "Review & Launch"}
LOGO_MAX, GUIDE_MAX = 2 * 1024 * 1024, 20 * 1024 * 1024
REFERENCE_MAX = 25 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Refuse form posts that come from another website (this app changes files and the database)."""
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"error": "cross-site request refused"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _org_or_404(folder):
    if not orgs.valid_folder(folder):
        return None
    cfg = orgs.load(folder)
    cfg["initials"] = orgs.initials(cfg["name"])
    return cfg


def _page(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, msg: str = ""):
    cards = []
    with connect() as conn:
        for cfg in orgs.list_orgs():
            cfg["initials"] = orgs.initials(cfg["name"])
            cfg["received"], cfg["in_bank"] = orgs.bank_counts(conn, cfg["db_slug"]) if cfg["db_slug"] else (0, 0)
            cards.append(cfg)
    return _page(request, "home.html", orgs=cards, flash=msg)


@app.post("/orgs/new")
def new_org(name: str = Form(...)):
    name = name.strip()
    if not name:
        return RedirectResponse("/?msg=Please+enter+a+name", status_code=303)
    with connect() as conn:
        folder = orgs.create(name, conn)
    return RedirectResponse(f"/org/{folder}/step/1", status_code=303)


@app.get("/org/{folder}/step/1", response_class=HTMLResponse)
def step1(request: Request, folder: str, msg: str = "", err: str = ""):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    return _page(request, "step1.html", org=org, flash=err or msg, flash_error=bool(err))


def _save_upload(upload: UploadFile, dest: Path, max_bytes, kind):
    """Read an upload (size-limited and content-checked) and save it. Returns an error message or ''."""
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        return f"The {kind} is larger than {max_bytes // (1024 * 1024)} MB."
    if kind == "logo":
        if not (data.startswith(PNG_MAGIC) or (b"<svg" in data[:4096].lower())):
            return "The logo must be a PNG or an SVG image."
    elif not data.startswith(b"%PDF"):
        return "The brand guide must be a PDF."
    dest.write_bytes(data)
    return ""


@app.post("/org/{folder}/step/1")
async def save_step1(request: Request, folder: str, name: str = Form(...), description: str = Form(""), website: str = Form(""),
                     primary: str = Form("#2B6E5E"), accent: str = Form("#B5592E"), action: str = Form("continue"),
                     logo: UploadFile | None = None, brand_guide: UploadFile | None = None):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    problems = []
    if not orgs.HEX.match(primary) or not orgs.HEX.match(accent):
        problems.append("Colors must look like #2B6E5E.")
    if website and not re.match(r"^https?://", website.strip(), re.I):
        website = "https://" + website.strip()
    cfg = {k: org[k] for k in orgs.DEFAULTS}
    cfg.update(name=name.strip(), description=description.strip(), website=website.strip())
    if not problems:
        cfg.update(primary=primary.upper(), accent=accent.upper())
    d = orgs.ORGS_DIR / folder
    if logo and logo.filename:
        ext = "png" if logo.filename.lower().endswith(".png") else "svg" if logo.filename.lower().endswith(".svg") else ""
        err = "Logo: use a PNG or SVG file." if not ext else _save_upload(logo, d / f"logo.{ext}", LOGO_MAX, "logo")
        if err:
            problems.append(err)
        else:
            cfg["logo"] = f"logo.{ext}"
            for other in ("png", "svg"):
                if other != ext:
                    (d / f"logo.{other}").unlink(missing_ok=True)
    if brand_guide and brand_guide.filename:
        err = _save_upload(brand_guide, d / "brand_guide.pdf", GUIDE_MAX, "brand guide")
        if err:
            problems.append(err)
        else:
            cfg["brand_guide"] = "brand_guide.pdf"
    orgs.save(folder, cfg)
    if cfg["db_slug"]:
        with connect() as conn:
            conn.execute("UPDATE organizations SET name = %s WHERE slug = %s", (cfg["name"], cfg["db_slug"]))
            conn.commit()
    if problems:
        return RedirectResponse(f"/org/{folder}/step/1?err=" + quote_plus(" ".join(problems)), status_code=303)
    return RedirectResponse("/?msg=Saved" if action == "exit" else f"/org/{folder}/step/2", status_code=303)


@app.get("/org/{folder}/logo")
def logo(folder: str):
    org = _org_or_404(folder)
    if org is None or not org["logo"] or not re.match(r"^logo\.(png|svg)$", org["logo"]):
        return HTMLResponse("No logo", status_code=404)
    path = orgs.ORGS_DIR / folder / org["logo"]
    if not path.exists():
        return HTMLResponse("No logo", status_code=404)
    # sandbox + no-sniff: an uploaded SVG is shown as a picture only and can never run scripts
    return FileResponse(path, media_type="image/png" if path.suffix == ".png" else "image/svg+xml",
                        headers={"Content-Security-Policy": "sandbox", "Cache-Control": "no-cache"})


@app.get("/org/{folder}/logo-colors")
def saved_logo_colors(folder: str):
    org = _org_or_404(folder)
    if org is None or not re.match(r"^logo\.(png|svg)$", org["logo"] or ""):
        return JSONResponse({"candidates": []})
    path = orgs.ORGS_DIR / folder / org["logo"]
    if not path.exists():
        return JSONResponse({"candidates": []})
    return JSONResponse({"candidates": logo_colors(path.read_bytes(), path.suffix[1:])})


@app.post("/logo-colors")
def new_logo_colors(logo: UploadFile):
    data = logo.file.read(LOGO_MAX + 1)
    ext = "png" if data.startswith(PNG_MAGIC) else "svg" if b"<svg" in data[:4096].lower() else ""
    if not ext or len(data) > LOGO_MAX:
        return JSONResponse({"candidates": []})
    return JSONResponse({"candidates": logo_colors(data, ext)})


@app.post("/pull-colors")
def pull_colors_route(body: dict = Body(...)):
    # a plain (not async) function: FastAPI runs it in a worker thread, so a slow website cannot freeze the app
    return JSONResponse(pull_colors(str(body.get("url", "")).strip()))


def _org_id(conn, org):
    """Database id of the organization, creating its row if the folder was made by hand."""
    slug = org["db_slug"] or org["name"]
    conn.execute("INSERT INTO organizations (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING", (slug, org["name"]))
    conn.commit()
    return conn.execute("SELECT id FROM organizations WHERE slug = %s", (slug,)).fetchone()[0]


@app.get("/org/{folder}/step/2", response_class=HTMLResponse)
def step2(request: Request, folder: str, q: str = "", status: str = "", p: int = 1):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        sources.ensure_columns(conn)
        oid = _org_id(conn, org)
        counts = bank.counts(conn, oid)
        items, total, pages, p = bank.page(conn, oid, q.strip(), status, p)
    return _page(request, "step2.html", org=org, counts=counts, items=items, total=total, pages=pages, p=p, q=q, status=status,
                 statuses=bank.STATUS_LABELS)


@app.post("/org/{folder}/bank/pmids")
def add_pmids(folder: str, ids: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    good, bad = sources.parse_pmids(ids)
    if not good:
        return JSONResponse({"error": "No PubMed IDs found. Paste numbers such as 38291045, one per line or separated by commas."},
                            status_code=400)
    if len(good) > 3000:
        return JSONResponse({"error": "That is more than 3,000 IDs at once; please add them in smaller batches."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
    if sources.busy(oid):
        return JSONResponse({"error": "Another import for this organization is still running. Wait for it to finish."}, status_code=409)
    jid = sources.new_job("pmid", f"{len(good)} PubMed IDs", oid)
    sources.start(sources.run_pmid_job, jid, oid, good, bad)
    return {"job": jid, "count": len(good)}


@app.post("/org/{folder}/bank/reference")
def add_reference(folder: str, file: UploadFile):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    data = file.file.read(REFERENCE_MAX + 1)
    if len(data) > REFERENCE_MAX:
        return JSONResponse({"error": "That file is larger than 25 MB."}, status_code=400)
    try:
        records, fmt = sources.parse_reference_file(file.filename or "", data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if len(records) > 3000:
        return JSONResponse({"error": f"That file has {len(records)} references; please split it into files of up to 3,000."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
    if sources.busy(oid):
        return JSONResponse({"error": "Another import for this organization is still running. Wait for it to finish."}, status_code=409)
    jid = sources.new_job("reference", f"{len(records)} references ({fmt})", oid)
    sources.start(sources.run_reference_job, jid, oid, records)
    return {"job": jid, "count": len(records), "format": fmt}


def _pdf_slug_problem(org):
    slug = org["db_slug"] or org["name"]
    if pdfs.raw_dir(slug).name != slug:
        return ("The organization's name has characters that cannot be used in a folder name. "
                "Change the name on step 1 to letters, numbers and spaces.")
    return ""


@app.post("/org/{folder}/bank/pdfs/upload")
def upload_pdfs(folder: str, files: list[UploadFile]):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if (problem := _pdf_slug_problem(org)):
        return JSONResponse({"error": problem}, status_code=400)
    saved, skipped = 0, []
    for f in files:
        data = f.file.read(pdfs.MAX_PDF_BYTES + 1)
        result = pdfs.save_upload(org["db_slug"] or org["name"], f.filename or "", data)
        if result == "saved":
            saved += 1
        else:
            skipped.append(f"{Path((f.filename or '').replace(chr(92), '/')).name}: {result}")
    return {"saved": saved, "skipped": skipped}


def _start_pdf_job(org, copy_files, label):
    with connect() as conn:
        oid = _org_id(conn, org)
    if sources.busy(oid):
        return JSONResponse({"error": "Another import for this organization is still running. Wait for it to finish."}, status_code=409)
    jid = sources.new_job("pdf", label, oid)
    sources.start(pdfs.run_job, jid, oid, org["db_slug"] or org["name"], copy_files)
    return {"job": jid}


@app.post("/org/{folder}/bank/pdfs/process")
def process_pdfs(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if (problem := _pdf_slug_problem(org)):
        return JSONResponse({"error": problem}, status_code=400)
    return _start_pdf_job(org, None, "uploaded PDFs")


@app.post("/org/{folder}/bank/pdfs/folder-check")
def folder_check(folder: str, path: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    try:
        files, size = pdfs.scan_folder(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    dest = pdfs.raw_dir(org["db_slug"] or org["name"])
    new = sum(1 for _f, rel in files if not (dest / rel).exists())
    return {"count": len(files), "megabytes": round(size / 1048576), "new": new}


@app.post("/org/{folder}/bank/pdfs/folder-add")
def folder_add(folder: str, path: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if (problem := _pdf_slug_problem(org)):
        return JSONResponse({"error": problem}, status_code=400)
    try:
        files, _size = pdfs.scan_folder(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return _start_pdf_job(org, files, f"{len(files)} PDFs from a folder")


@app.get("/org/{folder}/step/2/review", response_class=HTMLResponse)
def review_page(request: Request, folder: str, g: str = "", p: int = 1):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        sources.ensure_columns(conn)
        oid = _org_id(conn, org)
        counts = review.counts(conn, oid)
        if g not in review.GROUPS:       # open on the first group that has something in it
            g = next((k for k in review.GROUPS if counts[k]), "nonpaper")
        items, total, pages, p = review.page(conn, oid, g, p)
        bank_counts = bank.counts(conn, oid)
        estimate = topic.estimate_ai(conn, oid, topic_disease(org))
    terms = topic.get_terms(org)
    return _page(request, "review.html", org=org, counts=counts, groups=review.GROUPS, g=g, items=items, total=total, pages=pages, p=p,
                 bank=bank_counts, terms="; ".join(terms), estimate=estimate, has_key=bool(os.environ.get("ANTHROPIC_API_KEY")))


@app.post("/org/{folder}/bank/decision")
def set_decision(folder: str, paper_id: int = Form(...), decision: str = Form("")):
    org = _org_or_404(folder)
    if org is None or decision not in ("Keep", "Exclude", ""):
        return JSONResponse({"error": "Bad request"}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        if not review.set_decision(conn, oid, paper_id, decision):
            return JSONResponse({"error": "Unknown paper"}, status_code=404)
        c = bank.counts(conn, oid)
        row = conn.execute("SELECT in_bank FROM papers WHERE id = %s", (paper_id,)).fetchone()
    return {"in_bank": bool(row[0]), "bank_total": c["inbank"] + c["abstract"]}


@app.get("/org/{folder}/bank/set-aside.xlsx")
def download_set_aside(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    with connect() as conn:
        data = review.export_xlsx(conn, _org_id(conn, org), org["name"])
    name = re.sub(r"[^A-Za-z0-9]+", "_", org["name"]).strip("_") or "organization"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="set_aside_{name}.xlsx"'})


@app.post("/org/{folder}/bank/decisions-upload")
def upload_decisions(folder: str, file: UploadFile):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    data = file.file.read(REFERENCE_MAX + 1)
    if len(data) > REFERENCE_MAX or not data.startswith(b"PK"):
        return JSONResponse({"error": "Please upload the Excel file you downloaded from this screen (.xlsx)."}, status_code=400)
    try:
        with connect() as conn:
            applied, ignored = review.import_xlsx(conn, _org_id(conn, org), data)
    except Exception:
        return JSONResponse({"error": "That file could not be read as the set-aside spreadsheet."}, status_code=400)
    return {"applied": applied, "ignored": ignored}


def _clean_terms(text):
    out, seen = [], set()
    for t in re.split(r"[;\n]+", text):
        t = t.strip()
        if 2 <= len(t) <= 80 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:60]


@app.post("/org/{folder}/bank/topic/keywords")
def topic_keywords(folder: str, terms: str = Form(""), apply: str = Form("no")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    clean = _clean_terms(terms)
    if not clean:
        return JSONResponse({"error": "Add at least one disease term (separate several with semicolons)."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        flagged, checked = topic.keyword_check(conn, oid, clean, apply=apply == "yes")
    if apply == "yes":
        cfg = {k: org[k] for k in orgs.DEFAULTS}
        cfg["topic_terms"] = "; ".join(clean)
        orgs.save(folder, cfg)
    return {"checked": checked, "flagged": len(flagged), "applied": apply == "yes",
            "examples": [{"title": t, "reason": r} for _pid, t, r in flagged[:8]]}


@app.post("/org/{folder}/bank/topic/ai")
def topic_ai(folder: str, confirm: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if confirm != "yes":
        return JSONResponse({"error": "Please confirm the cost first."}, status_code=400)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse({"error": "No Anthropic API key is set on this computer (see the .env file)."}, status_code=400)
    disease = topic_disease(org)
    with connect() as conn:
        oid = _org_id(conn, org)
        est = topic.estimate_ai(conn, oid, disease)
    if not est["papers"]:
        return JSONResponse({"error": "There are no papers left to check."}, status_code=400)
    if sources.busy(oid):
        return JSONResponse({"error": "Another job for this organization is still running."}, status_code=409)
    jid = sources.new_job("topic", f"AI topic check of {est['papers']} papers", oid)
    sources.start(topic.run_ai_job, jid, oid, disease, est["high"])
    return {"job": jid, "estimate": est}


def topic_disease(org):
    """The disease description given to the AI: the template's if there is one, otherwise the organization's own words."""
    overlay = orgs.ORGS_DIR / org["folder"] / "overlay.xlsx"
    if overlay.exists():
        from pubbank.template import load_template
        d = (load_template(overlay).get("context") or {}).get("disease")
        if d:
            return d
    return " ".join(x for x in (org["name"], org["description"]) if x)


@app.get("/org/{folder}/step/4", response_class=HTMLResponse)
def step4(request: Request, folder: str, paper: int = 0):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Unknown organization", status_code=404)
    path = samples.template_path(folder)
    ctx_ = {"org": org, "state": "no_template", "step": 4}
    if path:
        from pubbank.template import load_context
        with connect() as conn:
            oid = _org_id(conn, org)
            smp = samples.latest(conn, oid)
            ctx_["pool"] = len([c for c in samples.candidates(conn, oid, topic.get_terms(org)) if c["good"]])
            if smp is None:
                ctx_.update(state="none", default_size=samples.DEFAULT_SIZE, max_size=samples.MAX_SIZE)
            elif sources.running_job(oid, "sample"):
                ctx_.update(state="running", smp=smp)
            elif smp["status"] == "running":      # the app was restarted while the read was going: offer to run it again
                conn.execute("UPDATE samples SET status = 'draft' WHERE id = %s", (smp["id"],))
                conn.commit()
                return RedirectResponse(f"/org/{folder}/step/4", status_code=303)
            elif smp["status"] == "draft":
                ids = smp["paper_ids"]
                ctx_.update(state="draft", smp=smp, papers=samples.papers_info(conn, oid, ids), est=samples.estimate(conn, folder, ids),
                            has_key=bool(os.environ.get("ANTHROPIC_API_KEY")), max_size=samples.MAX_SIZE)
            else:
                ctx = load_context(path)
                plist = samples.papers_info(conn, oid, smp["paper_ids"])
                idx = min(max(0, paper), max(0, len(plist) - 1))
                done_rows = dict(conn.execute("SELECT paper_id, count(*) FROM sample_verdicts WHERE sample_id = %s AND verdict IS NOT NULL "
                                              "GROUP BY 1", (smp["id"],)).fetchall())
                cur = plist[idx] if plist else None
                ctx_.update(state="done", smp=smp, papers=plist, idx=idx, cur=cur, done_rows=done_rows, verdicts=samples.VERDICTS,
                            answers=samples.paper_answers(conn, smp["id"], cur["id"], ctx) if cur else None,
                            score=samples.scorecard(conn, smp["id"], ctx), stale=smp["template_version"] != ctx.version,
                            answers_total=len([c for c in ctx.template["categories"] if c["include"]]))
    return _page(request, "step4.html", **ctx_)


@app.post("/org/{folder}/sample/pick")
def sample_pick(folder: str, n: int = Form(15)):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        if len(samples.candidates(conn, oid)) < 1:
            return JSONResponse({"error": "There are no papers in the bank to sample yet. Add some on step 2."}, status_code=400)
        sid = samples.create(conn, oid, n, topic.get_terms(org))
    return {"sample": sid}


@app.post("/org/{folder}/sample/swap")
def sample_swap(folder: str, sample_id: int = Form(...), paper_id: int = Form(...)):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        ok = samples.replace_paper(conn, _org_id(conn, org), sample_id, paper_id, topic.get_terms(org))
    return {"ok": ok}


@app.post("/org/{folder}/sample/run")
def sample_run(folder: str, sample_id: int = Form(...), confirm: str = Form("")):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    if confirm != "yes":
        return JSONResponse({"error": "Please confirm the cost first."}, status_code=400)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse({"error": "No Anthropic API key is set on this computer (see the .env file)."}, status_code=400)
    with connect() as conn:
        oid = _org_id(conn, org)
        smp = samples.latest(conn, oid)
        if not smp or smp["id"] != sample_id or smp["status"] != "draft":
            return JSONResponse({"error": "That sample is not ready to run."}, status_code=400)
        est = samples.estimate(conn, folder, smp["paper_ids"])
    if sources.busy(oid):
        return JSONResponse({"error": "Another job for this organization is still running."}, status_code=409)
    jid = sources.new_job("sample", f"extraction of {est['papers']} sample papers", oid)
    sources.start(samples.run_job, jid, oid, folder, sample_id, est["high"])
    return {"job": jid, "estimate": est}


@app.post("/org/{folder}/sample/verdict")
def sample_verdict(folder: str, sample_id: int = Form(...), paper_id: int = Form(...), category: str = Form(...),
                   verdict: str | None = Form(None), note: str | None = Form(None)):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    from pubbank.template import load_context
    with connect() as conn:
        oid = _org_id(conn, org)
        try:
            def val(v, cap=None):      # an empty form value arrives as "not sent", so clearing is sent as __none__
                return samples.UNCHANGED if v is None else ("" if v == "__none__" else (v[:cap] if cap else v))
            ok = samples.save_verdict(conn, oid, sample_id, paper_id, category[:60], val(verdict), val(note, 2000))
        except ValueError:
            return JSONResponse({"error": "Unknown verdict"}, status_code=400)
        if not ok:
            return JSONResponse({"error": "Unknown sample"}, status_code=404)
        return {"score": samples.scorecard(conn, sample_id, load_context(samples.template_path(folder)))}


@app.get("/org/{folder}/sample/{sample_id}/check.xlsx")
def sample_export(folder: str, sample_id: int):
    org = _org_or_404(folder)
    if org is None or not samples.template_path(folder):
        return HTMLResponse("Not found", status_code=404)
    from pubbank.template import load_context
    with connect() as conn:
        data = samples.export_xlsx(conn, _org_id(conn, org), sample_id, load_context(samples.template_path(folder)), org["name"])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="sample_check.xlsx"'})


@app.post("/org/{folder}/sample/{sample_id}/import")
def sample_import(folder: str, sample_id: int, file: UploadFile):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    data = file.file.read(REFERENCE_MAX + 1)
    if len(data) > REFERENCE_MAX or not data.startswith(b"PK"):
        return JSONResponse({"error": "Please upload the Excel file you downloaded from this screen (.xlsx)."}, status_code=400)
    try:
        with connect() as conn:
            n = samples.import_xlsx(conn, _org_id(conn, org), sample_id, data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"applied": n}


@app.get("/org/{folder}/sample/status")
def sample_status(folder: str):
    org = _org_or_404(folder)
    if org is None:
        return JSONResponse({"error": "Unknown organization"}, status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
    return {"job": sources.running_job(oid, "sample")}


@app.get("/org/{folder}/paper/{paper_id}/pdf")
def paper_pdf(folder: str, paper_id: int):
    org = _org_or_404(folder)
    if org is None:
        return HTMLResponse("Not found", status_code=404)
    with connect() as conn:
        oid = _org_id(conn, org)
        row = conn.execute("SELECT relpath, coalesce(source, 'pdf') FROM papers WHERE id = %s AND org_id = %s", (paper_id, oid)).fetchone()
    if not row or row[1] != "pdf":
        return HTMLResponse("No PDF for this paper", status_code=404)
    base = pdfs.raw_dir(org["db_slug"] or org["name"]).resolve()
    path = (base / row[0]).resolve()
    if base not in path.parents or not path.is_file():
        return HTMLResponse("PDF file not found on this computer", status_code=404)
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline", "Content-Security-Policy": "sandbox"})


@app.get("/org/{folder}/bank/jobs/{job_id}")
def job_status(folder: str, job_id: str):
    job = sources.get_job(job_id)
    if not job or not orgs.valid_folder(folder):
        return JSONResponse({"error": "Unknown job"}, status_code=404)
    return {k: job[k] for k in ("stage", "done", "total", "finished", "error", "result", "label")}


@app.get("/org/{folder}/step/{n}", response_class=HTMLResponse)
def other_steps(request: Request, folder: str, n: int):
    org = _org_or_404(folder)
    if org is None or n not in STEP_TITLES:
        return HTMLResponse("Not found", status_code=404)
    return _page(request, "stub.html", org=org, step=n, title=STEP_TITLES[n])
