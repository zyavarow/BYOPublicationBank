"""Off-topic check for a publication bank: does each paper actually concern the organization's disease?

Two ways, both recorded in papers.topic_status ('on_topic', 'borderline', 'off_topic'):
  * keywords (free): the organization's disease terms must appear in the title or abstract, or several times in the text;
  * AI (costs money, estimated first): Claude reads each title and abstract against the disease description. This also catches
    look-alikes, such as the EXD sequencing software versus the EXD disease.
A person always has the last word: a paper marked Keep stays in the bank whatever the check says.
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from pubbank import apikey, orgs, sources
from pubbank.bank import apply_rules
from pubbank.db import connect
from pubbank.extraction import MODEL, PRICES, _call, cost_usd

COMMON_ACRONYMS = {"DNA", "RNA", "ATP", "NOT", "AAA", "PCR", "MRI", "CSF", "AND", "THE"}
MIN_MENTIONS = 3            # a paper that never names the disease in its title or abstract needs this many mentions in the text
SYSTEM_TOKENS = 330         # the fixed instructions, read from the cache after the first request
OUT_TOKENS = 60


# ---------------------------------------------------------------------------------------------- terms
def suggest_terms(folder):
    """Disease terms for the keyword check, from the organization's template (genes with aliases and the acronyms in the disease
    description). Returns a list; empty when the organization has no template yet."""
    overlay = orgs.ORGS_DIR / folder / "overlay.xlsx"
    if not overlay.exists():
        return []
    from pubbank.template import load_template
    t = load_template(overlay)
    terms = []
    for g in t.get("genes", []):
        if re.search(r"(?i)(^|[;.,]\s*)(a different|not |unrelated)", g.get("notes", "")):    # e.g. "A different enzyme; not <this disease>."
            continue
        terms += [g["symbol"]] + list(g.get("aliases", []))
    disease = (t.get("context") or {}).get("disease", "")
    for inside in re.findall(r"\(([^()]*)\)", disease):          # acronyms such as EXD or EXD3 are written in brackets
        terms += re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", inside)
    name = re.sub(r"(?i)^(deficiency|syndrome|disease)\s+of\s+", "", disease.split("(")[0]).strip(" ,;.")
    if 4 <= len(name) <= 60:
        terms += [name, re.sub(r"\s+\d+$", "", name)]            # "example enzyme 1" and "example enzyme"
    seen, out = set(), []
    for x in terms:
        x = x.strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def get_terms(cfg):
    saved = [x.strip() for x in cfg.get("topic_terms", "").split(";") if x.strip()]
    return saved or suggest_terms(cfg["folder"])


def _pattern(terms):
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")(?![A-Za-z0-9])", re.I)


# ---------------------------------------------------------------------------------------------- free keyword check
def candidates(conn, org_id):
    """Papers the check looks at: parsed, not duplicates, and not already decided by a person."""
    return conn.execute(
        """SELECT id, coalesce(title, extracted_title, filename), coalesce(abstract, ''), coalesce(full_text, ''), bank_decision
           FROM papers WHERE org_id = %s AND parse_status = 'ok' AND duplicate_of IS NULL AND NOT coalesce(needs_ocr, false)
             AND coalesce(text_level, 'full_text') <> 'citation_only' AND (in_bank OR topic_status IS NOT NULL) ORDER BY id""", (org_id,)).fetchall()


def keyword_check(conn, org_id, terms, apply=True):
    """Returns (flagged, checked): flagged = [(paper id, title, reason)]. With apply=True the result is saved."""
    if not terms:
        raise ValueError("There are no disease terms to look for. Add some below.")
    pat = _pattern(terms)
    flagged, checked, updates = [], 0, []
    for pid, title, abstract, text, decision in candidates(conn, org_id):
        if decision:
            continue
        checked += 1
        if pat.search(title) or pat.search(abstract):
            updates.append((pid, "on_topic", None))
            continue
        n = len(pat.findall(text))
        if n >= MIN_MENTIONS:
            updates.append((pid, "on_topic", None))
        else:
            reason = (f"none of the disease terms appear in the title or abstract, and only {n} time{'s' if n != 1 else ''} in the text"
                      if n else "none of the disease terms appear anywhere in the paper")
            flagged.append((pid, title, reason))
            updates.append((pid, "off_topic", reason))
    if apply:
        for pid, status, reason in updates:
            conn.execute("UPDATE papers SET topic_status = %s, topic_reason = %s, topic_method = 'keywords' WHERE id = %s", (status, reason, pid))
        apply_rules(conn, org_id)
    return flagged, checked


# ---------------------------------------------------------------------------------------------- AI check
class TopicVerdict(BaseModel):
    verdict: Literal["on_topic", "borderline", "off_topic"] = Field(description=(
        "on_topic: the paper is mainly about the disease or its gene, or is directly useful research on it. "
        "borderline: related field, or the disease is only one of several topics. "
        "off_topic: not about the disease (for example it shares a name or acronym with something unrelated)."))
    reason: str = Field(description="One short plain-language sentence (at most 20 words) explaining the verdict.")


def system_prompt(disease):
    return ("You help build a library of scientific papers for a patient organization. The library is about: " + disease + ".\n\n"
            "Read the title and abstract of one paper and decide whether it belongs in this library. Be generous with papers that are "
            "clearly part of the research field around the disease, including basic biology, models and treatments. Mark a paper "
            "off_topic only when it is plainly about something else, even if it shares a name, gene symbol or acronym with the "
            "disease. Use borderline when you are unsure. Answer in the required format.")


def _paper_text(title, abstract, text):
    body = abstract.strip() or text.strip()[:1500]
    return f"Title: {title}\n\nAbstract: {body[:2500]}"


def estimate_ai(conn, org_id, cfg_disease):
    """Free estimate (no API call): number of papers and expected cost of the AI check."""
    rows = [r for r in candidates(conn, org_id) if not r[4]]
    sys_tokens = SYSTEM_TOKENS + len(cfg_disease) // 4
    in_tokens = sum(len(_paper_text(t, a, x)) / 3.8 + 25 for _pid, t, a, x, _d in rows)
    out_tokens = OUT_TOKENS * len(rows)
    cost = (in_tokens * PRICES["input"] + len(rows) * sys_tokens * PRICES["cache_read"] + out_tokens * PRICES["output"]) / 1e6
    return {"papers": len(rows), "input_tokens": int(in_tokens + len(rows) * sys_tokens), "output_tokens": int(out_tokens),
            "expected": round(cost, 2), "high": round(cost * 1.5 + 0.05, 2)}


def run_ai_job(jid, org_id, disease, max_cost):
    """Background job: classify every candidate paper. Stops (and says so) if the cost cap is reached."""
    conn = connect()
    try:
        client = apikey.client_for_org(conn, org_id)
        sources.ensure_columns(conn)
        rows = [r for r in candidates(conn, org_id) if not r[4]]
        sources._set(jid, stage="Reading titles and abstracts", done=0, total=len(rows))
        sysmsg = system_prompt(disease)
        lock, spent, errors, done = threading.Lock(), [0.0], [0], [0]
        results = []

        def one(row):
            pid, title, abstract, text, _d = row
            with lock:
                if spent[0] >= max_cost:
                    return
            r = _call(client, MODEL, sysmsg, _paper_text(title, abstract, text), TopicVerdict)
            with lock:
                spent[0] += cost_usd(r["usage"])
                done[0] += 1
                if r["status"] == "ok":
                    results.append((pid, r["parsed"]["verdict"], r["parsed"]["reason"]))
                else:
                    errors[0] += 1
                sources._set(jid, done=done[0])
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(one, rows))
        for pid, verdict, reason in results:
            conn.execute("UPDATE papers SET topic_status = %s, topic_reason = %s, topic_method = 'ai' WHERE id = %s", (verdict, reason, pid))
        apply_rules(conn, org_id)
        stopped = spent[0] >= max_cost and done[0] < len(rows)
        sources._set(jid, finished=True, stage="Done", result={
            "kind": "topic", "checked": len(results), "off_topic": sum(1 for r in results if r[1] == "off_topic"),
            "borderline": sum(1 for r in results if r[1] == "borderline"), "errors": errors[0], "cost": round(spent[0], 3),
            "stopped_at_cap": stopped})
    except Exception as e:
        sources._set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()
