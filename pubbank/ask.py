"""Answer a question from an organization's library (retrieval-augmented generation).

  1. find the best passages (hybrid search; for Standard and Deep the question is first rewritten or split into parts);
  2. give Claude only those numbered passages, with what was extracted about each paper (study type, models, techniques, reagents);
  3. get back a synthesized answer where every statement cites passage numbers, plus a confidence rating, what the library does not cover, and
     where sources disagree;
  4. check the citations, set the final confidence from the evidence (number of independent papers, kinds of evidence, agreement), and log it.
The three depths trade cost for thoroughness. The organization's API key is only ever used here, on the server.
"""
import json
import re
import time
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from pubbank import apikey, search
from pubbank.bank import clean_title
from pubbank.db import Jsonb, connect

HAIKU, SONNET = "claude-haiku-4-5", "claude-sonnet-5-5"
PRICE = {HAIKU: (1.0, 5.0), SONNET: (2.0, 10.0)}               # dollars per million input / output tokens (Anthropic price list)
DEPTHS = {
    "quick": dict(label="Quick", model=HAIKU, passages=8, per_paper=2, rewrite=False, split=False),
    "standard": dict(label="Standard", model=SONNET, passages=20, per_paper=3, rewrite=True, split=False),
    "deep": dict(label="Deep", model=SONNET, passages=34, per_paper=3, rewrite=False, split=True),
}
ROLES = {
    "patient": ("Patient / Caregiver", "The reader is a patient or family member. Use warm, plain words and short sentences; explain every medical term the first time; "
                "avoid statistics unless they help; say what the evidence means for a person living with the condition; never give medical advice, and "
                "suggest discussing specifics with the care team."),
    "clinician": ("Clinician", "The reader is a clinician. Be concise and precise: name study types and sizes, outcomes, doses where reported, and limits of the evidence."),
    "scientist": ("Scientist / Researcher", "The reader is a researcher. Use proper terminology; say which models, methods, variants and reagents (including antibodies) "
                  "the findings came from, and point out methodological differences that could explain conflicting results."),
    "advocate": ("Advocacy Leader", "The reader leads a patient organization. Give the big picture: what is well supported, what is uncertain, and what is missing "
                 "from the research, in plain language suitable for planning and communication."),
}
FORMATS = {"plain": "Write in plain language with minimal jargon.", "technical": "Use full technical terminology and be precise."}


class Para(BaseModel):
    text: str = Field(description="One paragraph of the answer. Every factual statement must be followed by the passage numbers that support it, like [1] or [2][5].")
    cites: list[int] = Field(description="All passage numbers used in this paragraph.")


class Answer(BaseModel):
    paragraphs: list[Para] = Field(description="The answer, synthesized across papers: compare and combine them, do not summarize one paper at a time.")
    confidence: Literal["High", "Medium", "Low"] = Field(description="How well the passages support the answer: High needs several independent papers that agree.")
    confidence_reason: str = Field(description="One plain sentence on why.")
    not_covered: list[str] = Field(description="Parts of the question the passages do not address, each as a short sentence. Empty list if everything is covered.")
    disagreements: list[str] = Field(description="Places where papers disagree or could be compared, with the likely reason when the passages give one "
                                                 "(different antibody, model, patients). Empty list if none.")


class Rewrites(BaseModel):
    queries: list[str] = Field(description="Up to three different ways to search for the answer: synonyms, gene or variant name variants, key technical terms.")


class SubQuestions(BaseModel):
    parts: list[str] = Field(description="Up to four self-contained sub-questions that together answer the question.")


DDL = """
CREATE TABLE IF NOT EXISTS ask_log (
    id serial PRIMARY KEY, org_id int NOT NULL, question text NOT NULL, role text, depth text, format text, answer jsonb, sources jsonb,
    model text, input_tokens int, output_tokens int, cost_usd numeric(10,5), seconds real, created_at timestamptz DEFAULT now());
"""


def ensure_table(conn):
    if not conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'ask_log'").fetchone():
        conn.execute(DDL)
    conn.commit()


def cost(model, tin, tout):
    pi, po = PRICE[model]
    return (tin * pi + tout * po) / 1e6


def spent_today(conn, org_id):
    ensure_table(conn)
    return float(conn.execute("SELECT coalesce(sum(cost_usd), 0) FROM ask_log WHERE org_id = %s AND created_at >= date_trunc('day', now())", (org_id,)).fetchone()[0])


def estimate(depth):
    """Planning figure shown next to the depth choice (dollars per question)."""
    d = DEPTHS[depth]
    tin = d["passages"] * 420 + 1400 + (900 if d["rewrite"] else 0) + (1500 if d["split"] else 0)
    tout = 700 + (200 if d["rewrite"] else 0) + (300 if d["split"] else 0)
    return round(cost(d["model"], tin, tout), 3)


# ---------------------------------------------------------------------------------------------- calls
class Meter:
    def __init__(self):
        self.tin = self.tout = 0
        self.dollars = 0.0

    def add(self, model, usage):
        self.tin += usage.input_tokens or 0
        self.tout += usage.output_tokens or 0
        self.dollars += cost(model, usage.input_tokens or 0, usage.output_tokens or 0)


def _parse(client, meter, model, system, user, cls, max_tokens=4000, effort=None):
    kw = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}], output_format=cls)
    if effort and model != HAIKU:                  # Haiku 4.5 has no effort setting
        kw["output_config"] = {"effort": effort}
    resp = client.messages.parse(**kw)
    meter.add(model, resp.usage)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("The model could not answer this question.")
    return resp.parsed_output


def _retrieve(conn, org_id, question, d, client, meter, synonyms, paper_ids):
    queries = [question]
    if d["rewrite"]:
        try:
            r = _parse(client, meter, HAIKU, "You rewrite search questions for a library of scientific papers on one rare disease. Keep the meaning; vary the wording.",
                       question, Rewrites, 400)
            queries += [q for q in r.queries if q.strip()][:3]
        except Exception:
            pass
    if d["split"]:
        try:
            r = _parse(client, meter, SONNET, "Split a research question about a rare disease into at most four self-contained sub-questions that together cover it. "
                       "If the question is already simple, return it alone.", question, SubQuestions, 500, "low")
            queries += [q for q in r.parts if q.strip()][:4]
        except Exception:
            pass
    per_query = max(8, d["passages"] // max(1, len(queries)) + 4) if len(queries) > 1 else d["passages"]
    lists = []
    for q in queries:
        hits = search.search(conn, org_id, q, k=per_query * 2, per_paper=d["per_paper"], synonyms=synonyms, paper_ids=paper_ids)
        lists.append(("q%d" % len(lists), [(h["chunk_id"], h["score"]) for h in hits]))
    fused, why = search.fuse(*lists)
    ids = [c for c, _ in fused[: d["passages"] * 4]]
    pid_of = {r[0]: r[1] for r in conn.execute("SELECT id, paper_id FROM chunks WHERE id = ANY(%s)", (ids,))}
    out, used = [], {}
    for cid, sc in fused:
        pid = pid_of.get(cid)
        if pid is None or used.get(pid, 0) >= d["per_paper"]:
            continue
        used[pid] = used.get(pid, 0) + 1
        out.append({"chunk_id": cid, "paper_id": pid, "score": round(sc, 5), "why": why[cid]})
        if len(out) >= d["passages"]:
            break
    return out, queries


def _extracted(conn, org_id, paper_ids):
    """What the extraction found for each paper (latest successful run), as a short readable line."""
    rows = conn.execute(
        """SELECT DISTINCT ON (paper_id) paper_id, result FROM extractions WHERE org_id = %s AND status = 'ok' AND paper_id = ANY(%s)
           ORDER BY paper_id, created_at DESC""", (org_id, list(paper_ids))).fetchall()
    out = {}
    for pid, res in rows:
        bits = []
        for key, label in (("study_type", "Study"), ("model_systems", "Models"), ("techniques", "Techniques"), ("reagents", "Reagents"), ("treatments", "Treatments")):
            cat = res.get(key) or {}
            vals = []
            for e in cat.get("entries", [])[:6]:
                v = " ".join(str(x) for k, x in e.items() if k not in ("evidence",) and x)
                if v:
                    vals.append(v[:70])
            if vals:
                bits.append(f"{label}: " + "; ".join(vals))
        out[pid] = bits
    return out


def _evidence_block(passages, extracted):
    lines = []
    for i, p in enumerate(passages, 1):
        meta = ", ".join(str(x) for x in (p["journal"], p["year"]) if x)
        level = " [ABSTRACT ONLY: full text was not available]" if p["text_level"] == "abstract_only" else ""
        ex = ("\n   Extracted data for this paper: " + " | ".join(extracted[p["paper_id"]])) if extracted.get(p["paper_id"]) else ""
        page = f", p.{p['page']}" if p.get("page") else ""
        lines.append(f"[{i}] {p['title']} ({meta}){level}\n   Section: {p['heading'] or p['section_kind']}{page}{ex}\n   Passage: {p['text']}")
    return "\n\n".join(lines)


def _system(role, fmt, disease):
    return (f"You answer questions for a patient organization about: {disease}\n\n"
            "Use ONLY the numbered passages below, taken from the organization's own library of scientific papers. Rules:\n"
            "- Combine evidence from several papers into one synthesized answer; compare them. Do not go paper by paper.\n"
            "- Cite every factual statement with the passage numbers in square brackets, like [3] or [2][7]. Never cite a number that is not listed.\n"
            "- If the passages do not answer part of the question, say so in not_covered. Do not fill gaps from outside knowledge.\n"
            "- If a passage is marked ABSTRACT ONLY, treat its details as limited and say so where it matters.\n"
            "- Say where papers disagree and, if the passages give a reason (different antibody, model, patient group, method), name it.\n"
            "- Distinguish what was seen in patients from what was seen in cell or animal models.\n"
            f"- {ROLES[role][1]}\n- {FORMATS[fmt]}\n"
            "- confidence: High only if several independent papers agree and the evidence fits the question; Low if it rests on one paper or on indirect evidence.")


# ---------------------------------------------------------------------------------------------- the entry point
def ask(conn, org_id, question, role="scientist", depth="standard", fmt="plain", disease="", synonyms=None, paper_ids=None, daily_cap=5.0, user_id=None):
    """Returns a dict ready for the screen. Raises ValueError for problems the person can fix (too long, cap reached)."""
    question = question.strip()
    if not question or len(question) > 1200:
        raise ValueError("Type a question of up to about 1,000 characters.")
    if role not in ROLES or depth not in DEPTHS or fmt not in FORMATS:
        raise ValueError("Unknown role, depth or format.")
    if spent_today(conn, org_id) + estimate(depth) > daily_cap:
        raise ValueError(f"The daily spending limit of ${daily_cap:.2f} for questions has been reached. It resets tomorrow, or an administrator can raise it.")
    t0 = time.time()
    d = DEPTHS[depth]
    client = apikey.client_for_org(conn, org_id)
    meter = Meter()
    hits, queries = _retrieve(conn, org_id, question, d, client, meter, synonyms, paper_ids)
    passages = search.passages(conn, hits)
    if not passages:
        result = {"paragraphs": [], "confidence": "Low", "confidence_reason": "Nothing in the library matched this question.",
                  "not_covered": [question], "disagreements": []}
        sources = []
    else:
        extracted = _extracted(conn, org_id, {p["paper_id"] for p in passages})
        ans = _parse(client, meter, d["model"], _system(role, fmt, disease or "a rare disease"),
                     f"QUESTION: {question}\n\nPASSAGES:\n\n{_evidence_block(passages, extracted)}", Answer, 4000, "low" if depth != "deep" else "medium")
        result, sources = _finish(ans.model_dump(), passages, extracted)
    seconds = time.time() - t0
    result.update(depth=depth, role=role, format=fmt, queries=queries, seconds=round(seconds, 1), cost=round(meter.dollars, 4), model=d["model"])
    ensure_table(conn)
    log_id = conn.execute(
        """INSERT INTO ask_log (org_id, question, role, depth, format, answer, sources, model, input_tokens, output_tokens, cost_usd, seconds, user_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (org_id, question, role, depth, fmt, Jsonb(result), Jsonb(sources), d["model"], meter.tin, meter.tout, round(meter.dollars, 5), seconds, user_id)).fetchone()[0]
    conn.commit()
    return {"id": log_id, "question": question, "answer": result, "sources": sources}


EVIDENCE_KIND = [("clinical", re.compile(r"case report|cohort|registry|clinical|patients?", re.I)), ("laboratory", re.compile(r"mouse|animal|cell|in vitro|structur|biochem", re.I))]


def _finish(ans, passages, extracted):
    """Check the citations, group the cited passages by paper (numbered in order of first citation) and set the final confidence."""
    n = len(passages)
    paper_no, order = {}, []
    for para in ans["paragraphs"]:
        para["cites"] = sorted({c for c in para["cites"] if 1 <= c <= n})
        for c in re.findall(r"\[(\d+)\]", para["text"]):
            if 1 <= int(c) <= n:
                para["cites"] = sorted(set(para["cites"]) | {int(c)})
        for c in para["cites"]:
            pid = passages[c - 1]["paper_id"]
            if pid not in paper_no:
                paper_no[pid] = len(paper_no) + 1
                order.append(pid)
    # rewrite passage numbers in the text as paper numbers, so the reader sees [1], [2] for papers and can open the exact passages
    for para in ans["paragraphs"]:
        para["text"] = re.sub(r"\[(\d+)\]", lambda m: f"[{paper_no[passages[int(m.group(1)) - 1]['paper_id']]}]" if 1 <= int(m.group(1)) <= n else "", para["text"])
        para["papers"] = sorted({paper_no[passages[c - 1]["paper_id"]] for c in para["cites"]})
        para["text"] = re.sub(r"(\[\d+\])(?=\1)", "", para["text"])
    sources = []
    for pid in order:
        ps = [p for i, p in enumerate(passages, 1) if p["paper_id"] == pid and any(i in para["cites"] for para in ans["paragraphs"])]
        first = ps[0]
        ex = extracted.get(pid, [])
        sources.append({"no": paper_no[pid], "paper_id": pid, "title": first["title"], "authors": first["authors"][:3], "journal": first["journal"],
                        "year": first["year"], "pmid": first["pmid"], "doi": first["doi"], "text_level": first["text_level"], "extracted": ex,
                        "passages": [{"heading": p["heading"], "page": p["page"], "text": p["text"][:1400]} for p in ps]})
    # final confidence: never higher than the evidence allows, whatever the model said
    k = len(sources)
    cap = "High" if k >= 3 else "Medium" if k == 2 else "Low"
    if ans["disagreements"] and cap == "High":
        cap = "Medium"
    abstract_only = sum(1 for s in sources if s["text_level"] == "abstract_only")
    if k and abstract_only == k and cap == "High":
        cap = "Medium"
    rank = {"Low": 0, "Medium": 1, "High": 2}
    final = ans["confidence"] if rank[ans["confidence"]] <= rank[cap] else cap
    if not k:
        final = "Low"
    ans["confidence"] = final
    ans["confidence_basis"] = (f"{k} paper{'' if k == 1 else 's'} cited" + (f", {abstract_only} abstract-only" if abstract_only else "")
                               + ("; sources disagree in places" if ans["disagreements"] else "") + ".")
    ans["papers_cited"] = k
    return ans, sources
