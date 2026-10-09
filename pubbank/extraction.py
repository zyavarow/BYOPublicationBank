"""Extract the categories defined in the extraction template from one parsed paper with Claude (claude-sonnet-5-5).

A paper is read in up to three requests ("call groups", see pubbank/template.py): group 1 (front matter) decides the
study type; groups 2 and 3 run only when at least one of their categories applies to that study type. Each request is
sent only the sections it needs (falling back to the whole paper when those sections are missing).
"""
import re
import time

import anthropic
from pydantic import ValidationError

from .quote_check import verify_extraction
from .template import (SCHEMA_VERSION, applies, derived_fields, finalize_result, first_group_id, group_categories,
                       study_type_of)

MODEL = "claude-sonnet-5-5"
# USD per million tokens for claude-sonnet-5-5 (5-minute cache writes).
PRICES = {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20}
REFS_HEADING = re.compile(r"^\W*(\d+\W*)?(references?|bibliography|literature cited|reference list)\W*$", re.I)


def build_paper_text(paper, kinds=None, min_chars=0, page_max=0):
    """Text sent to Claude: metadata header + sections (reference list removed) with page markers.
    With `kinds`, only sections of those kinds (plus any section starting on a page <= page_max) are sent, unless that
    leaves fewer than `min_chars` characters, in which case the whole paper is sent."""
    header = [f"Title (from PubMed/Crossref, may be missing): {paper.get('title') or paper.get('extracted_title') or 'unknown'}"]
    if paper.get("journal"):
        header.append(f"Journal: {paper['journal']}")
    if paper.get("year"):
        header.append(f"Year: {paper['year']}")
    sections = [s for s in (paper.get("sections") or []) if not REFS_HEADING.match((s.get("heading") or "").strip())
                and ((s.get("text") or "").strip() or s.get("heading") != "(front matter)")]
    scope = "whole paper"
    chosen = sections
    if kinds is not None and sections:
        pick = [s for s in sections if s.get("kind") in kinds or (page_max and (s.get("page_start") or 999) <= page_max)]
        if sum(len(s.get("text") or "") for s in pick) >= min_chars:
            chosen, scope = pick, "selected sections: " + ", ".join(kinds)
    parts = [f"## [p.{s.get('page_start') or '?'}] {s.get('heading')}\n{s.get('text', '')}" for s in chosen]
    body = "\n\n".join(parts) if parts else (paper.get("full_text") or "")
    return ("\n".join(header) + f"\n\n=== PAPER TEXT (reference list removed; {scope}) ===\n\n" + body), scope


def cost_usd(u):
    return (u["input_tokens"] * PRICES["input"] + u["output_tokens"] * PRICES["output"]
            + u["cache_write_tokens"] * PRICES["cache_write"] + u["cache_read_tokens"] * PRICES["cache_read"]) / 1e6


def _empty_usage():
    return {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}


def _call(client, model, system_prompt, user_text, model_cls):
    """One request. Returns dict(status, parsed, usage, seconds, stop_reason, refusal_category, error)."""
    out = {"status": "error", "error": None, "parsed": None, "usage": _empty_usage(), "seconds": 0.0,
           "stop_reason": None, "refusal_category": None}
    t0 = time.time()
    try:
        resp = client.messages.parse(
            model=model, max_tokens=16000,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_text}],
            output_format=model_cls, output_config={"effort": "low"})
    except ValidationError as e:
        out.update(status="invalid", error=f"answer did not match the schema: {e.error_count()} problems: {str(e)[:300]}")
    except anthropic.APIStatusError as e:
        out.update(status="error", error=f"{e.status_code}: {str(e)[:400]}")
    except anthropic.APIConnectionError as e:
        out.update(status="error", error=f"connection problem: {type(e).__name__}")
    else:
        u = resp.usage
        out["usage"] = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                        "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                        "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
        out["stop_reason"] = resp.stop_reason
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            out.update(status="refused", refusal_category=getattr(details, "category", None),
                       error="the model declined to answer this paper")
        elif resp.stop_reason == "max_tokens" or resp.parsed_output is None:
            out.update(status="truncated", error="answer was cut off or empty")
        else:
            out.update(status="ok", parsed=resp.parsed_output.model_dump())
    out["seconds"] = time.time() - t0
    return out


def plan_calls(ctx, paper, study=None):
    """Which requests would run for this paper (used by the cost estimator too). Without a known study type,
    only group 1 can be planned."""
    t = ctx.template
    plan = []
    first = first_group_id(t)
    for gid, g in t["groups"].items():
        cats = group_categories(t, gid)
        if not cats:
            continue
        if gid != first:
            if study is None:
                continue
            todo = [c for c in cats if applies(c, study)]
            if not todo:
                continue
            na = [c["id"] for c in cats if not applies(c, study)]
        else:
            na = []
        text, scope = build_paper_text(paper, g["kinds"], g["min_chars"], g["page_max"])
        plan.append({"group": gid, "text": text, "scope": scope, "not_applicable": na})
    return plan


def extract_paper(client, paper, ctx, model=MODEL):
    """Returns a dict ready to store: status ('ok', 'refused', 'truncated', 'invalid', 'error'), result, derived,
    quote_stats, token usage, cost, per-request details."""
    t = ctx.template
    out = {"status": "error", "error": None, "result": None, "derived": None, "quote_stats": None,
           "stop_reason": None, "refusal_category": None, "usage": _empty_usage(), "cost_usd": 0.0, "seconds": 0.0,
           "template_version": t["version"]}
    full_text, _ = build_paper_text(paper)
    first = first_group_id(t)
    merged, notes, calls, study = {}, [], [], None

    def add_usage(r):
        for k, v in r["usage"].items():
            out["usage"][k] += v
        out["seconds"] += r["seconds"]

    for gid, g in t["groups"].items():
        cats = group_categories(t, gid)
        if not cats:
            continue
        na_ids = []
        if gid != first:
            if not [c for c in cats if applies(c, study)]:
                calls.append({"group": gid, "skipped": f"no category in this group applies to study type '{study}'"})
                continue
            na_ids = [c["id"] for c in cats if not applies(c, study)]
        text, scope = build_paper_text(paper, g["kinds"], g["min_chars"], g["page_max"])
        prefix = ""
        if gid != first:
            prefix = f"Study type already determined for this paper: {study}.\n"
            if na_ids:
                prefix += ("These categories do NOT apply to this study type; return status 'Not applicable' with no "
                           "entries for them: " + ", ".join(na_ids) + ".\n")
            prefix += "\n"
        r = _call(client, model, ctx.prompts[gid], prefix + text, ctx.models[gid])
        add_usage(r)
        calls.append({"group": gid, "scope": scope, "status": r["status"], **{k: r["usage"][k] for k in r["usage"]},
                      "seconds": round(r["seconds"], 1)})
        out["stop_reason"] = r["stop_reason"]
        if r["status"] != "ok":
            out.update(status=r["status"], error=f"group {gid}: {r['error']}", refusal_category=r["refusal_category"])
            out["cost_usd"] = cost_usd(out["usage"])
            return out

        # Safety net: the request saw only selected sections, so anything it did not find is re-checked on the whole paper.
        if scope != "whole paper":
            missing = [c["id"] for c in cats if c["id"] not in na_ids and applies(c, study) and
                       (r["parsed"][c["id"]]["status"] == "Not found" or
                        (r["parsed"][c["id"]]["status"] == "Found" and not r["parsed"][c["id"]]["entries"]))]
            if missing:
                full, full_scope = build_paper_text(paper)
                prefix2 = prefix + ("A first pass over selected sections did not find: " + ", ".join(missing) +
                                    ". This request contains the WHOLE paper: look for these categories everywhere in it. "
                                    "Answer every category again.\n\n")
                r2 = _call(client, model, ctx.prompts[gid], prefix2 + full, ctx.models[gid])
                add_usage(r2)
                calls.append({"group": gid, "scope": full_scope, "recheck": missing, "status": r2["status"],
                              **{k: r2["usage"][k] for k in r2["usage"]}, "seconds": round(r2["seconds"], 1)})
                if r2["status"] == "ok":
                    for cid in missing:
                        new = r2["parsed"][cid]
                        if new["status"] == "Not found":
                            new["rationale"] = (new["rationale"].rstrip() + " [Checked on the whole paper.]").strip()
                        r["parsed"][cid] = new
                    if r2["parsed"].get("extraction_notes", "").strip():
                        r["parsed"]["extraction_notes"] = (r["parsed"].get("extraction_notes", "") + " " +
                                                           r2["parsed"]["extraction_notes"]).strip()
        for k, v in r["parsed"].items():
            if k == "extraction_notes":
                if v.strip():
                    notes.append(v.strip())
            else:
                merged[k] = v
        if gid == first:
            study = study_type_of(merged)

    merged.setdefault("all_authors_from_title_page", [])
    merged["extraction_notes"] = " | ".join(notes)
    out["quote_stats"] = verify_extraction(merged, full_text)
    result = finalize_result(t, merged)
    result["_meta"] = {"template_version": t["version"], "schema_version": SCHEMA_VERSION, "calls": calls}
    out["result"] = result
    out["derived"] = derived_fields(paper.get("authors"), paper.get("year"), result["all_authors_from_title_page"])
    out["cost_usd"] = cost_usd(out["usage"])
    out["status"] = "ok"
    return out
