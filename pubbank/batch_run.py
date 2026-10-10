"""Read a whole library with Anthropic's Message Batches service (half price, results within about a day, often within the hour).

A paper normally takes up to three requests that depend on each other (study type first, then the other fields, then a whole-paper re-read for
anything not found). With batches this becomes three rounds, each one batch for ALL papers:
  round 1: the first reading pass (study type, gene focus, authors)           -> decides which other fields apply to each paper
  round 2: the other reading passes                                           -> methods, findings, extra fields
  round 3: whole-paper re-reads for fields still 'Not found' where the template allows it
Progress is stored in the database, so the run survives closing the app: `advance(run_id)` checks the batch in flight and moves on when it has ended.
"""
import json
import threading
import time

import anthropic

from pubbank.db import Jsonb, connect
from pubbank.extraction import MODEL, PRICES, _key_kinds, _wants_recheck, build_paper_text, cost_usd
from pubbank.extraction_schema import derived_fields
from pubbank.quote_check import verify_extraction
from pubbank.samples import PAPER_COLS, load_papers, save_extraction, template_path
from pubbank.template import applies, finalize_result, first_group_id, group_categories, load_context, study_type_of

BATCH_FACTOR = 0.5
DDL = """
CREATE TABLE IF NOT EXISTS extraction_runs (
    id serial PRIMARY KEY, org_id int NOT NULL, label text, status text NOT NULL DEFAULT 'running', round int NOT NULL DEFAULT 1,
    paper_ids jsonb NOT NULL, state jsonb NOT NULL DEFAULT '{}', batch_id text, limit_usd numeric(10,2), cost_usd numeric(10,4) DEFAULT 0,
    template_version text, message text, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
"""
_lock = threading.Lock()


def ensure_table(conn):
    if not conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'extraction_runs'").fetchone():
        conn.execute(DDL)
    conn.commit()


# ---------------------------------------------------------------------------------------------- price
def estimate(conn, folder, ids):
    """Price of reading these papers with the Batch service. The 'at most' figure assumes no prompt caching at all (batches only cache
    on a best-effort basis), so it is the number to hold the spending limit against."""
    from pubbank.samples import CHARS_PER_TOKEN, OUT_EXPECTED, SCHEMA_TOKEN_PER_CHAR
    from pubbank.extraction import plan_calls
    ctx = load_context(template_path(folder))
    papers = load_papers(conn, ids)
    text_t = fixed_t = 0.0
    n_calls = 0
    first = {}
    for p in papers:
        for c in plan_calls(ctx, p, "Case report/series"):
            text_t += len(c["text"]) / CHARS_PER_TOKEN
            fixed = len(ctx.prompts[c["group"]]) / CHARS_PER_TOKEN + len(json.dumps(ctx.models[c["group"]].model_json_schema())) * SCHEMA_TOKEN_PER_CHAR
            fixed_t += fixed
            first.setdefault(c["group"], fixed)
            n_calls += 1
    out_t = len(papers) * OUT_EXPECTED
    cached = (text_t * PRICES["input"] + (fixed_t - sum(first.values())) * PRICES["cache_read"] + sum(first.values()) * PRICES["cache_write"]
              + out_t * PRICES["output"]) / 1e6
    nocache = ((text_t + fixed_t) * PRICES["input"] + out_t * PRICES["output"]) / 1e6
    rechecks = 0.12 * cached                    # whole-paper re-reads (a rough allowance)
    expected = (cached * 1.1 + rechecks) * BATCH_FACTOR
    high = (nocache * 1.1 + rechecks * 2) * BATCH_FACTOR * 1.15
    return {"papers": len(papers), "requests": n_calls, "expected": round(expected, 2), "high": round(high, 2),
            "standard_expected": round(cached * 1.1 + rechecks, 2)}


# ---------------------------------------------------------------------------------------------- requests
def _schema(model_cls):
    return anthropic.transform_schema(model_cls)


def _request(custom_id, ctx, gid, user_text):
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    return Request(custom_id=custom_id, params=MessageCreateParamsNonStreaming(
        model=MODEL, max_tokens=16000,
        system=[{"type": "text", "text": ctx.prompts[gid], "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_text}],
        output_config={"format": {"type": "json_schema", "schema": _schema(ctx.models[gid])}, "effort": "low"}))


def _prefix(study, na_ids, first):
    if first:
        return ""
    p = f"Study type already determined for this paper: {study}.\n"
    if na_ids:
        p += ("These categories do NOT apply to this study type; return status 'Not applicable' with no entries for them: " + ", ".join(na_ids) + ".\n")
    return p + "\n"


def _parse(result, model_cls):
    """(parsed dict or None, usage dict, problem text) from one batch result."""
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}
    if result.result.type != "succeeded":
        return None, usage, f"request {result.result.type}"
    msg = result.result.message
    u = msg.usage
    usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
             "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0, "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
    if msg.stop_reason == "refusal":
        return None, usage, "the model declined to answer this paper"
    if msg.stop_reason == "max_tokens":
        return None, usage, "answer was cut off"
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return model_cls.model_validate_json(text).model_dump(), usage, ""
    except Exception as e:
        return None, usage, f"answer did not match the schema: {str(e)[:160]}"


def _add_usage(total, u):
    for k, v in u.items():
        total[k] = total.get(k, 0) + v


# ---------------------------------------------------------------------------------------------- the run
def create(conn, org_id, folder, ids, limit_usd):
    ensure_table(conn)
    run_id = conn.execute("INSERT INTO extraction_runs (org_id, paper_ids, limit_usd, status, message) VALUES (%s, %s, %s, 'running', 'Starting') RETURNING id",
                          (org_id, Jsonb(list(ids)), limit_usd)).fetchone()[0]
    conn.execute("UPDATE extraction_runs SET label = %s WHERE id = %s", (f"run-{run_id}", run_id))
    conn.commit()
    return run_id


def active(conn, org_id):
    ensure_table(conn)
    r = conn.execute("SELECT id FROM extraction_runs WHERE org_id = %s AND status = 'running' ORDER BY id DESC LIMIT 1", (org_id,)).fetchone()
    return r[0] if r else None


def latest(conn, org_id):
    ensure_table(conn)
    r = conn.execute("SELECT id, status, round, message, cost_usd, limit_usd, jsonb_array_length(paper_ids), created_at, batch_id "
                     "FROM extraction_runs WHERE org_id = %s ORDER BY id DESC LIMIT 1", (org_id,)).fetchone()
    if not r:
        return None
    return {"id": r[0], "status": r[1], "round": r[2], "message": r[3], "cost": float(r[4] or 0), "limit": float(r[5] or 0), "papers": r[6],
            "started": r[7].isoformat(), "batch_id": r[8]}


def _submit(conn, client, run_id, requests, round_no, message):
    if not requests:
        return False
    batch = client.messages.batches.create(requests=requests)
    conn.execute("UPDATE extraction_runs SET batch_id = %s, round = %s, message = %s, updated_at = now() WHERE id = %s", (batch.id, round_no, message, run_id))
    conn.commit()
    return True


def start(folder, run_id):
    """Build and submit round 1."""
    client = anthropic.Anthropic()
    conn = connect()
    try:
        ctx = load_context(template_path(folder))
        row = conn.execute("SELECT paper_ids FROM extraction_runs WHERE id = %s", (run_id,)).fetchone()
        papers = load_papers(conn, row[0])
        first = first_group_id(ctx.template)
        g = ctx.template["groups"][first]
        reqs, state = [], {}
        for p in papers:
            text, scope = build_paper_text(p, g["kinds"], g["min_chars"], g["page_max"], _key_kinds(g))
            reqs.append(_request(f"{p['id']}-{first}-p", ctx, first, text))
            state[str(p["id"])] = {"scopes": {first: scope}, "parsed": {}, "usage": {}, "problem": "", "calls": []}
        conn.execute("UPDATE extraction_runs SET state = %s, template_version = %s WHERE id = %s", (Jsonb(state), ctx.version, run_id))
        conn.commit()
        _submit_chunks(conn, client, run_id, reqs, 1, "Round 1 of 3: study type, gene focus and authors for every paper")
    except Exception as e:
        _fail(conn, run_id, e)
    finally:
        conn.close()


def _submit_chunks(conn, client, run_id, reqs, round_no, message):
    """One batch holds up to 100,000 requests or 256 MB; a library is far below that, but split defensively by size."""
    if not reqs:
        return False
    size = sum(len(json.dumps(r["params"]["messages"])) for r in reqs)
    if size > 200 * 1024 * 1024:
        raise RuntimeError("This library is too large for one batch; split it into smaller runs.")
    return _submit(conn, client, run_id, reqs, round_no, message)


def _fail(conn, run_id, e):
    conn.execute("UPDATE extraction_runs SET status = 'failed', message = %s, updated_at = now() WHERE id = %s", (f"{type(e).__name__}: {e}"[:400], run_id))
    conn.commit()


def advance(run_id):
    """Check the batch in flight; when it has ended, collect results and submit the next round (or finish). Safe to call often."""
    if not _lock.acquire(blocking=False):
        return
    conn = connect()
    try:
        # only one process may move a run along at a time (two would each submit the next round, and every round costs money)
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (7000 + run_id,)).fetchone()[0]:
            return
        r = conn.execute("SELECT org_id, status, round, batch_id, state, paper_ids, template_version, limit_usd FROM extraction_runs WHERE id = %s", (run_id,)).fetchone()
        if not r or r[1] != "running" or not r[3]:
            return
        org_id, _st, rnd, batch_id, state, paper_ids, _ver, limit = r
        client = anthropic.Anthropic()
        batch = client.messages.batches.retrieve(batch_id)
        c = batch.request_counts
        done = c.succeeded + c.errored + c.canceled + c.expired
        total = done + c.processing
        if batch.processing_status != "ended":
            conn.execute("UPDATE extraction_runs SET message = %s, updated_at = now() WHERE id = %s",
                         (f"Round {rnd} of 3 is running at Anthropic: {done} of {total} requests finished", run_id))
            conn.commit()
            return
        folder = _folder_of(conn, org_id)
        ctx = load_context(template_path(folder))
        if _ver and ctx.version != _ver:
            conn.execute("UPDATE extraction_runs SET status = 'failed', message = %s WHERE id = %s",
                         ("The extraction schema was changed while the run was going, so the pieces no longer fit together. Start a new run.", run_id))
            conn.commit()
            return
        _collect(conn, client, run_id, ctx, state, batch_id, rnd)
        spent = _spent(state)
        if spent > float(limit) * 1.0:
            conn.execute("UPDATE extraction_runs SET state = %s, cost_usd = %s, status = 'stopped', message = %s WHERE id = %s",
                         (Jsonb(state), round(spent, 4), f"Stopped: the cost so far (${spent:.2f}) reached your limit of ${float(limit):.2f}. Nothing more was submitted.", run_id))
            conn.commit()
            return
        papers = {p["id"]: p for p in load_papers(conn, paper_ids)}
        nxt = _next_round(ctx, state, papers, rnd)
        done_round = rnd
        if not nxt and rnd == 1:                       # nothing for round 2: go straight to the re-reads
            nxt = _next_round(ctx, state, papers, 2)
            done_round = 2
        conn.execute("UPDATE extraction_runs SET state = %s, cost_usd = %s, updated_at = now() WHERE id = %s", (Jsonb(state), round(spent, 4), run_id))
        conn.commit()
        if nxt:
            _submit(conn, client, run_id, nxt, done_round + 1, f"Round {done_round + 1} of 3: " + ("the remaining fields for every paper" if done_round == 1 else "re-reading whole papers for fields not found"))
        else:
            _finish(conn, run_id, org_id, ctx, state, papers)
    except Exception as e:
        _fail(conn, run_id, e)
    finally:
        conn.close()
        _lock.release()


def _folder_of(conn, org_id):
    from pubbank import orgs
    slug = conn.execute("SELECT slug FROM organizations WHERE id = %s", (org_id,)).fetchone()[0]
    for cfg in orgs.list_orgs():
        if cfg["db_slug"] == slug:
            return cfg["folder"]
    raise RuntimeError("The organization's folder was not found.")


def _spent(state):
    return sum(cost_usd(s["usage"] or {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}) for s in state.values()) * BATCH_FACTOR


def _collect(conn, client, run_id, ctx, state, batch_id, rnd):
    for res in client.messages.batches.results(batch_id):
        pid, gid, kind = res.custom_id.split("-", 2)
        s = state.get(pid)
        if s is None:
            continue
        parsed, usage, problem = _parse(res, ctx.models[gid])
        _add_usage(s.setdefault("usage", {}), usage)
        s["calls"].append({"group": gid, "kind": kind, "problem": problem})
        if parsed is None:
            s["problem"] = f"group {gid}: {problem}"
            continue
        if kind == "p":
            s["parsed"][gid] = parsed
        else:
            s.setdefault("recheck_parsed", {})[gid] = parsed


def _next_round(ctx, state, papers, rnd):
    t = ctx.template
    first = first_group_id(t)
    reqs = []
    if rnd == 1:
        for pid, s in state.items():
            p = papers[int(pid)]
            if first not in s["parsed"]:
                continue
            study = study_type_of(s["parsed"][first])
            s["study"] = study
            for gid, g in t["groups"].items():
                cats = group_categories(t, gid)
                if gid == first or not cats or not [c for c in cats if applies(c, study)]:
                    continue
                na_ids = [c["id"] for c in cats if not applies(c, study)]
                text, scope = build_paper_text(p, g["kinds"], g["min_chars"], g["page_max"], _key_kinds(g))
                s["scopes"][gid] = scope
                s.setdefault("na", {})[gid] = na_ids
                reqs.append(_request(f"{pid}-{gid}-p", ctx, gid, _prefix(study, na_ids, False) + text))
        return reqs
    if rnd == 2:
        for pid, s in state.items():
            p = papers[int(pid)]
            for gid, parsed in s["parsed"].items():
                g = t["groups"][gid]
                if s["scopes"].get(gid) == "whole paper":
                    continue
                na_ids = s.get("na", {}).get(gid, [])
                missing = [c["id"] for c in group_categories(t, gid) if c["id"] not in na_ids and applies(c, s.get("study"))
                           and (parsed[c["id"]]["status"] == "Not found" or (parsed[c["id"]]["status"] == "Found" and not parsed[c["id"]]["entries"]))]
                if not missing:
                    continue
                if not _wants_recheck(g, p):
                    s.setdefault("skipped", {})[gid] = missing
                    continue
                s.setdefault("missing", {})[gid] = missing
                full, _scope = build_paper_text(p)
                prefix = _prefix(s.get("study"), na_ids, gid == first) + (
                    "A first pass over selected sections did not find: " + ", ".join(missing) +
                    ". This request contains the WHOLE paper: look for these categories everywhere in it. Answer every category again.\n\n")
                reqs.append(_request(f"{pid}-{gid}-r", ctx, gid, prefix + full))
        return reqs
    return []


def _finish(conn, run_id, org_id, ctx, state, papers):
    """Merge every paper's pieces exactly as the one-paper-at-a-time reader does, verify quotes, and save the results."""
    t = ctx.template
    label = f"run-{run_id}"
    ok = bad = 0
    for pid, s in state.items():
        p = papers[int(pid)]
        usage = s["usage"] or {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}
        res = {"status": "error", "error": s["problem"] or "no result", "result": None, "derived": None, "quote_stats": None, "stop_reason": None,
               "refusal_category": None, "usage": usage, "cost_usd": cost_usd(usage) * BATCH_FACTOR, "seconds": 0, "template_version": t["version"]}
        if s["parsed"] and not s["problem"]:
            merged, notes = {}, []
            for gid in t["groups"]:
                parsed = s["parsed"].get(gid)
                if not parsed:
                    continue
                parsed = json.loads(json.dumps(parsed))
                re_ = (s.get("recheck_parsed") or {}).get(gid)
                if re_:
                    for cid in s.get("missing", {}).get(gid, []):
                        new = re_[cid]
                        if new["status"] == "Not found":
                            new["rationale"] = (new["rationale"].rstrip() + " [Checked on the whole paper.]").strip()
                        parsed[cid] = new
                    if re_.get("extraction_notes", "").strip():
                        parsed["extraction_notes"] = (parsed.get("extraction_notes", "") + " " + re_["extraction_notes"]).strip()
                for cid in s.get("skipped", {}).get(gid, []):
                    if parsed[cid]["status"] == "Not found":
                        parsed[cid]["rationale"] = (parsed[cid]["rationale"].rstrip() + " [The relevant section was read in full.]").strip()
                for k, v in parsed.items():
                    if k == "extraction_notes":
                        if v.strip():
                            notes.append(v.strip())
                    else:
                        merged[k] = v
            merged.setdefault("all_authors_from_title_page", [])
            merged["extraction_notes"] = " | ".join(notes)
            full_text, _ = build_paper_text(p)
            res["quote_stats"] = verify_extraction(merged, full_text)
            result = finalize_result(t, merged)
            result["_meta"] = {"template_version": t["version"], "schema_version": "batch", "calls": s["calls"], "batch_run": run_id}
            res.update(status="ok", error=None, result=result, derived=derived_fields(p.get("authors"), p.get("year"), result["all_authors_from_title_page"]))
        c2 = connect()
        try:
            save_extraction(c2, p["id"], org_id, label, res)
        finally:
            c2.close()
        ok += res["status"] == "ok"
        bad += res["status"] != "ok"
    spent = _spent(state)
    conn.execute("UPDATE extraction_runs SET status = 'done', cost_usd = %s, message = %s, updated_at = now() WHERE id = %s",
                 (round(spent, 4), f"Finished: {ok} papers read, {bad} could not be read. Cost ${spent:.2f}.", run_id))
    conn.commit()


def cancel(run_id):
    conn = connect()
    try:
        r = conn.execute("SELECT batch_id, status FROM extraction_runs WHERE id = %s", (run_id,)).fetchone()
        if r and r[1] == "running":
            if r[0]:
                try:
                    anthropic.Anthropic().messages.batches.cancel(r[0])
                except Exception:
                    pass
            conn.execute("UPDATE extraction_runs SET status = 'cancelled', message = 'Cancelled. Anything already processed is still charged.', updated_at = now() WHERE id = %s", (run_id,))
            conn.commit()
    finally:
        conn.close()


def watch_forever(interval=60):
    """Background thread started with the app: moves every running run along."""
    while True:
        try:
            conn = connect()
            ids = [r[0] for r in conn.execute("SELECT id FROM extraction_runs WHERE status = 'running' AND batch_id IS NOT NULL")] \
                if conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'extraction_runs'").fetchone() else []
            conn.close()
            for i in ids:
                advance(i)
        except Exception:
            pass
        time.sleep(interval)
