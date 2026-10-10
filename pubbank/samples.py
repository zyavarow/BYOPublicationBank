"""The 'Check a sample' step: pick a varied handful of papers, extract them with the organization's template, and let a person mark each
answer Correct / Correct but incomplete / Partly wrong / Wrong / Unsure. The scorecard shows where the template needs work before the
whole library is processed."""
import io
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor

import anthropic
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from pubbank import orgs, sources
from pubbank.bank import clean_title
from pubbank.db import Jsonb, connect
from pubbank.extraction import MODEL, PRICES, extract_paper, plan_calls
from pubbank.template import SCHEMA_VERSION, load_context

DEFAULT_SIZE, MAX_SIZE = 15, 30
VERDICTS = ["Correct", "Correct but incomplete", "Partly wrong", "Wrong", "Unsure"]
PAPER_COLS = ["id", "filename", "title", "extracted_title", "journal", "year", "authors", "sections", "full_text", "parse_status", "in_bank"]
CHARS_PER_TOKEN = 3.8
SCHEMA_TOKEN_PER_CHAR = 0.48
OUT_EXPECTED, HIGH_FACTOR = 3000, 1.6

DDL = """
CREATE TABLE IF NOT EXISTS samples (
    id serial PRIMARY KEY, org_id int NOT NULL, paper_ids jsonb NOT NULL, status text NOT NULL DEFAULT 'draft',
    template_version text, cost_usd numeric(10,4), created_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS sample_verdicts (
    sample_id int NOT NULL REFERENCES samples(id) ON DELETE CASCADE, paper_id int NOT NULL, category text NOT NULL,
    verdict text, note text, updated_at timestamptz DEFAULT now(), PRIMARY KEY (sample_id, paper_id, category));
"""


def ensure_tables(conn):
    exists = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_name IN ('samples', 'sample_verdicts')").fetchone()[0]
    if exists < 2:
        conn.execute(DDL)
    conn.commit()


def template_path(folder):
    p = orgs.ORGS_DIR / folder / "overlay.xlsx"
    return p if p.exists() else None


# ---------------------------------------------------------------------------------------------- choosing papers
def _bucket(pub_types, title):
    t = " ".join(pub_types or []).lower() + " " + (title or "").lower()
    if "case report" in t:
        return "case"
    if "review" in t or "guideline" in t or "consensus" in t:
        return "review"
    if "clinical trial" in t or "randomized" in t:
        return "trial"
    return "other"


def candidates(conn, org_id, terms=None):
    """Papers fit for a sample: in the bank, with real text to read (full text preferred), not enormous. 'good' marks papers that
    name the disease in the title or abstract and have a proper PubMed/Crossref record (preferred when choosing)."""
    import re
    pat = None
    if terms:
        pat = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")(?![A-Za-z0-9])", re.I)
    rows = conn.execute(
        """SELECT id, coalesce(title, extracted_title, filename), year, pub_types, coalesce(text_level, 'full_text'), char_count, coalesce(source, 'pdf'),
                  coalesce(abstract, ''), coalesce(meta_source, 'none') FROM papers
           WHERE org_id = %s AND in_bank AND parse_status = 'ok' AND coalesce(char_count, 0) BETWEEN 6000 AND 160000 ORDER BY id""", (org_id,)).fetchall()
    out = []
    for r in rows:
        on = bool(pat.search(r[1] or "") or pat.search(r[7])) if pat else True
        out.append({"id": r[0], "title": clean_title(r[1]), "year": r[2], "bucket": _bucket(r[3], r[1]), "level": r[4], "chars": r[5],
                    "source": r[6], "good": on and r[8] != "none"})
    return out


def pick(conn, org_id, n=DEFAULT_SIZE, exclude=(), terms=None):
    """A varied sample: full-text papers across kinds of paper (case reports, trials, reviews, others) and across years, preferring
    papers that clearly concern the disease."""
    allc = [c for c in candidates(conn, org_id, terms) if c["id"] not in exclude]
    pool = [c for c in allc if c["level"] == "full_text" and c["good"]]
    if len(pool) < n:       # not enough: allow abstract-only, then less clearly on-topic papers
        pool += [c for c in allc if c["level"] != "full_text" and c["good"]]
    if len(pool) < n:
        pool += [c for c in allc if not c["good"] and c not in pool]
    rng = random.Random(org_id * 7919)
    rng.shuffle(pool)
    quota = {"review": max(1, n // 7), "case": max(2, n // 4), "trial": max(1, n // 8)}
    chosen, counts = [], {"review": 0, "case": 0, "trial": 0, "other": 0}
    years = sorted({c["year"] for c in pool if c["year"]})
    by_year = {y: i * 3 // max(1, len(years)) for i, y in enumerate(years)}       # early / middle / recent thirds
    seen_third = {0: 0, 1: 0, 2: 0}
    for c in sorted(pool, key=lambda c: (seen_third.get(by_year.get(c["year"], 1), 0), rng.random())):
        if len(chosen) >= n:
            break
        if c["bucket"] in quota and counts[c["bucket"]] >= quota[c["bucket"]]:
            continue
        chosen.append(c)
        counts[c["bucket"]] += 1
        t = by_year.get(c["year"], 1)
        seen_third[t] = seen_third.get(t, 0) + 1
    for c in pool:                                  # top up if the quotas left gaps
        if len(chosen) >= n:
            break
        if c not in chosen:
            chosen.append(c)
    return [c["id"] for c in chosen[:n]]


def create(conn, org_id, n=DEFAULT_SIZE, terms=None):
    ensure_tables(conn)
    conn.execute("DELETE FROM samples WHERE org_id = %s AND status = 'draft'", (org_id,))
    ids = pick(conn, org_id, max(1, min(MAX_SIZE, n)), terms=terms)
    sid = conn.execute("INSERT INTO samples (org_id, paper_ids) VALUES (%s, %s) RETURNING id", (org_id, Jsonb(ids))).fetchone()[0]
    conn.commit()
    return sid


def latest(conn, org_id):
    ensure_tables(conn)
    r = conn.execute("SELECT id, paper_ids, status, template_version, cost_usd FROM samples WHERE org_id = %s ORDER BY id DESC LIMIT 1", (org_id,)).fetchone()
    return {"id": r[0], "paper_ids": r[1], "status": r[2], "template_version": r[3], "cost": float(r[4] or 0)} if r else None


def papers_info(conn, org_id, ids):
    if not ids:
        return []
    rows = {r[0]: r for r in conn.execute(
        """SELECT id, coalesce(title, extracted_title, filename), journal, year, coalesce(source, 'pdf'), coalesce(text_level, 'full_text'), char_count,
                  pmid, doi, pmcid, relpath FROM papers WHERE org_id = %s AND id = ANY(%s)""", (org_id, list(ids))).fetchall()}
    out = []
    for i in ids:
        r = rows.get(i)
        if r:
            out.append({"id": r[0], "title": clean_title(r[1]), "journal": r[2], "year": r[3], "source": r[4], "level": r[5], "chars": r[6],
                        "pmid": r[7], "doi": r[8], "pmcid": r[9], "relpath": r[10]})
    return out


def replace_paper(conn, org_id, sample_id, old_id, terms=None):
    """Swap one paper of a draft sample for another varied candidate."""
    s = conn.execute("SELECT paper_ids, status FROM samples WHERE id = %s AND org_id = %s", (sample_id, org_id)).fetchone()
    if not s or s[1] != "draft":
        return False
    ids = [i for i in s[0] if i != old_id]
    extra = pick(conn, org_id, 1, exclude=set(s[0]), terms=terms)
    conn.execute("UPDATE samples SET paper_ids = %s WHERE id = %s", (Jsonb(ids + extra), sample_id))
    conn.commit()
    return True


# ---------------------------------------------------------------------------------------------- price and running
def load_papers(conn, ids):
    rows = conn.execute(f"SELECT {', '.join(PAPER_COLS)} FROM papers WHERE id = ANY(%s)", (list(ids),)).fetchall()
    by = {r[0]: dict(zip(PAPER_COLS, r)) for r in rows}
    return [by[i] for i in ids if i in by]


def estimate(conn, folder, ids):
    """Free price estimate (no API call). Unknown study types are treated as 'everything applies', so this leans high."""
    path = template_path(folder)
    if not path:
        return None
    ctx = load_context(path)
    papers = load_papers(conn, ids)
    text_tokens = cached_tokens = n_calls = 0
    first = {}
    for p in papers:
        for c in plan_calls(ctx, p, "Case report/series"):
            text_tokens += len(c["text"]) / CHARS_PER_TOKEN
            fixed = len(ctx.prompts[c["group"]]) / CHARS_PER_TOKEN + len(json.dumps(ctx.models[c["group"]].model_json_schema())) * SCHEMA_TOKEN_PER_CHAR
            cached_tokens += fixed
            first.setdefault(c["group"], fixed)
            n_calls += 1
    writes = sum(first.values())
    inp = (text_tokens * PRICES["input"] + (cached_tokens - writes) * PRICES["cache_read"] + writes * PRICES["cache_write"]) / 1e6
    out = len(papers) * OUT_EXPECTED * PRICES["output"] / 1e6
    expected = inp + out
    return {"papers": len(papers), "requests": n_calls, "expected": round(expected, 2), "high": round(expected * HIGH_FACTOR + 0.1, 2),
            "minutes": max(1, -(-len(papers) * 40 // 180))}


def save_extraction(conn, paper_id, org_id, run_label, res):
    u, qs = res["usage"], res["quote_stats"]
    conn.execute(
        """INSERT INTO extractions (paper_id, org_id, schema_version, model, run_label, status, error, result, derived, quote_stats, stop_reason,
               refusal_category, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, cost_usd, seconds, template_version)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (paper_id, schema_version, model, run_label) DO UPDATE SET status=EXCLUDED.status, error=EXCLUDED.error,
               result=EXCLUDED.result, derived=EXCLUDED.derived, quote_stats=EXCLUDED.quote_stats, cost_usd=EXCLUDED.cost_usd,
               template_version=EXCLUDED.template_version, created_at=now()""",
        (paper_id, org_id, SCHEMA_VERSION, MODEL, run_label, res["status"], res["error"], Jsonb(res["result"]) if res["result"] else None,
         Jsonb(res["derived"]) if res["derived"] else None, Jsonb(res["quote_stats"]) if qs else None, res["stop_reason"],
         res["refusal_category"], u["input_tokens"], u["output_tokens"], u["cache_write_tokens"], u["cache_read_tokens"],
         round(res["cost_usd"], 5), res["seconds"], res["template_version"]))
    conn.commit()


def run_job(jid, org_id, folder, sample_id, max_cost):
    client = anthropic.Anthropic()
    conn = connect()
    try:
        ensure_tables(conn)
        s = conn.execute("SELECT paper_ids FROM samples WHERE id = %s AND org_id = %s", (sample_id, org_id)).fetchone()
        ctx = load_context(template_path(folder))
        papers = load_papers(conn, s[0])
        label = f"sample-{sample_id}"
        conn.execute("UPDATE samples SET status = 'running', template_version = %s WHERE id = %s", (ctx.version, sample_id))
        conn.commit()
        sources._set(jid, stage="Reading the sample papers", done=0, total=len(papers))
        lock, spent, done, failed = threading.Lock(), [0.0], [0], [0]

        def one(p):
            with lock:
                if spent[0] >= max_cost:
                    return
            res = extract_paper(client, p, ctx, MODEL)
            c2 = connect()
            try:
                save_extraction(c2, p["id"], org_id, label, res)
            finally:
                c2.close()
            with lock:
                spent[0] += res["cost_usd"]
                done[0] += 1
                failed[0] += res["status"] != "ok"
                sources._set(jid, done=done[0])
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(one, papers))
        stopped = spent[0] >= max_cost and done[0] < len(papers)
        conn.execute("UPDATE samples SET status = %s, cost_usd = %s WHERE id = %s", ("done" if done[0] else "draft", round(spent[0], 4), sample_id))
        conn.commit()
        sources._set(jid, finished=True, stage="Done", result={"kind": "sample", "done": done[0], "failed": failed[0], "cost": round(spent[0], 2),
                                                               "stopped_at_cap": stopped, "sample": sample_id})
    except Exception as e:
        sources._set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------- showing results
def entry_text(entry):
    parts = [str(v) for k, v in entry.items() if k != "evidence" and v not in ("", None)]
    return " / ".join(parts)


def paper_answers(conn, sample_id, paper_id, ctx):
    """The AI's answers for one paper as display-ready cards, in the template's order, with any saved verdicts."""
    r = conn.execute("SELECT result FROM extractions WHERE paper_id = %s AND run_label = %s AND status = 'ok'", (paper_id, f"sample-{sample_id}")).fetchone()
    if not r:
        return None
    result = r[0]
    verdicts = {v[0]: (v[1] or "", v[2] or "") for v in conn.execute(
        "SELECT category, verdict, note FROM sample_verdicts WHERE sample_id = %s AND paper_id = %s", (sample_id, paper_id))}
    cards = []
    for c in ctx.template["categories"]:
        if not c["include"] or c["id"] not in result:
            continue
        cat = result[c["id"]]
        entries = []
        for e in cat.get("entries", []):
            ev = e.get("evidence") or {}
            entries.append({"text": entry_text(e), "quote": ev.get("quote", ""), "section": ev.get("section", ""), "page": ev.get("page") or 0,
                            "check": ev.get("verification", "")})
        v, note = verdicts.get(c["id"], ("", ""))
        cards.append({"id": c["id"], "label": c["label"], "status": cat["status"], "confidence": cat.get("confidence_final") or cat.get("confidence"),
                      "rationale": cat.get("rationale", ""), "entries": entries, "verdict": v, "note": note})
    missed = verdicts.get("_missed", ("", ""))[1]
    return {"cards": cards, "missed": missed}


UNCHANGED = object()


def save_verdict(conn, org_id, sample_id, paper_id, category, verdict=UNCHANGED, note=UNCHANGED):
    """Save a verdict and/or a note for one answer. Whatever is left out stays as it was. Returns False for an unknown sample."""
    if verdict is not UNCHANGED and verdict and verdict not in VERDICTS:
        raise ValueError("unknown verdict")
    if not conn.execute("SELECT 1 FROM samples WHERE id = %s AND org_id = %s", (sample_id, org_id)).fetchone():
        return False
    sets, cols, vals = [], [], []
    if verdict is not UNCHANGED:
        cols.append("verdict"); vals.append(verdict or None); sets.append("verdict = EXCLUDED.verdict")
    if note is not UNCHANGED:
        cols.append("note"); vals.append(note or None); sets.append("note = EXCLUDED.note")
    if not cols:
        return True
    conn.execute(
        f"""INSERT INTO sample_verdicts (sample_id, paper_id, category, {', '.join(cols)}) VALUES (%s, %s, %s, {', '.join('%s' for _ in cols)})
            ON CONFLICT (sample_id, paper_id, category) DO UPDATE SET {', '.join(sets)}, updated_at = now()""",
        [sample_id, paper_id, category] + vals)
    conn.commit()
    return True


def scorecard(conn, sample_id, ctx):
    rows = conn.execute("SELECT category, verdict, count(*) FROM sample_verdicts WHERE sample_id = %s AND verdict IS NOT NULL AND category <> '_missed' "
                        "GROUP BY 1, 2", (sample_id,)).fetchall()
    labels = {c["id"]: c["label"] for c in ctx.template["categories"]}
    by = {}
    for cat, v, n in rows:
        by.setdefault(cat, {x: 0 for x in VERDICTS})[v] += n

    def line(name, d):
        total = sum(d.values())
        return {"name": name, "reviewed": total, **{v: d[v] for v in VERDICTS}, "pct": round(100 * d["Correct"] / total) if total else None}
    overall = {x: sum(d[x] for d in by.values()) for x in VERDICTS}
    return {"overall": line("All answers", overall), "categories": [line(labels.get(k, k), d) for k, d in by.items()]}


# ---------------------------------------------------------------------------------------------- spreadsheet round trip
def export_xlsx(conn, org_id, sample_id, ctx, org_name):
    s = conn.execute("SELECT paper_ids FROM samples WHERE id = %s AND org_id = %s", (sample_id, org_id)).fetchone()
    wb = Workbook()
    ws = wb.active
    ws.title = "Check each answer"
    ws.append(["Paper ID", "Category ID", "Paper", "Category", "AI status", "AI confidence", "What the AI answered", "Evidence (section, page, quote)",
               "AI's reason", "YOUR VERDICT", "Note"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2B6E5E")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for p in papers_info(conn, org_id, s[0]):
        a = paper_answers(conn, sample_id, p["id"], ctx)
        if not a:
            continue
        for card in a["cards"]:
            ws.append([p["id"], card["id"], p["title"], card["label"], card["status"], card["confidence"],
                       "\n".join(e["text"] for e in card["entries"]) or "(nothing)",
                       "\n".join(f"[{e['section']}, p.{e['page']}] “{e['quote']}”" for e in card["entries"]), card["rationale"],
                       card["verdict"], card["note"]])
    for i, w in enumerate([9, 14, 40, 24, 14, 12, 50, 70, 40, 24, 40], 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    dv = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"J2:J{max(ws.max_row, 2)}")
    ws.freeze_panes = "D2"
    g = wb.create_sheet("How to use", 0)
    for line in (f"{org_name}: check the AI's answers for a sample of papers", "",
                 "For each row choose a verdict in the YOUR VERDICT column: Correct, Correct but incomplete, Partly wrong, Wrong or Unsure.",
                 "Add a note where something is wrong or missing. Upload the file again on the Check a sample screen."):
        g.append([line])
    g.column_dimensions["A"].width = 120
    g["A1"].font = Font(bold=True, size=13)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_xlsx(conn, org_id, sample_id, data):
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    if "Check each answer" not in wb.sheetnames:
        raise ValueError("That is not the spreadsheet downloaded from this screen.")
    rows = wb["Check each answer"].iter_rows(values_only=True)
    header = [str(h or "") for h in next(rows, [])]
    if "YOUR VERDICT" not in header or header[0] != "Paper ID":
        raise ValueError("That is not the spreadsheet downloaded from this screen.")
    applied = 0
    for r in rows:
        v = str(r[header.index("YOUR VERDICT")] or "").strip()
        v = next((x for x in VERDICTS if x.lower() == v.lower()), "")
        note = str(r[header.index("Note")] or "").strip()
        if not v and not note:
            continue
        try:
            if save_verdict(conn, org_id, sample_id, int(r[0]), str(r[1]), v, note):
                applied += 1
        except (TypeError, ValueError):
            pass
    return applied
