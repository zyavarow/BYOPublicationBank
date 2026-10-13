"""What an organization's administrators see about its workspace: the question log, gaps in the library, and usage by person.

Everything reads the ask_log table (one row per answered question). A "gap" is a question the library could not fully answer: the answer
listed parts that were not covered, or its confidence was Low.
"""
import csv
import io

GAP_SQL = "(a.answer->>'confidence' = 'Low' OR jsonb_array_length(coalesce(a.answer->'not_covered', '[]'::jsonb)) > 0)"
PER_PAGE = 25
PERIODS = {7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days", 0: "All time"}


def _where(org_id, days=30, who="", depth="", gaps=False, q=""):
    cond, args = ["a.org_id = %s"], [org_id]
    if days:
        cond.append("a.created_at >= now() - make_interval(days => %s)")
        args.append(days)
    if who == "none":
        cond.append("a.user_id IS NULL")
    elif who.isdigit():
        cond.append("a.user_id = %s")
        args.append(int(who))
    if depth in ("quick", "standard", "deep"):
        cond.append("a.depth = %s")
        args.append(depth)
    if gaps:
        cond.append(GAP_SQL)
    if q.strip():
        cond.append("a.question ILIKE %s")
        args.append("%" + q.strip().replace("%", "").replace("_", " ") + "%")
    return " AND ".join(cond), args


def summary(conn, org_id, days=30):
    w, args = _where(org_id, days)
    r = conn.execute(f"""SELECT count(*), count(DISTINCT a.user_id), coalesce(sum(a.cost_usd), 0),
                                count(*) FILTER (WHERE {GAP_SQL}), count(*) FILTER (WHERE a.answer->>'confidence' = 'Low')
                         FROM ask_log a WHERE {w}""", args).fetchone()
    return {"questions": r[0], "people": r[1], "spend": float(r[2]), "gaps": r[3], "low": r[4]}


def people(conn, org_id, days=30):
    """Questions and spend per person (people without an account, e.g. questions asked before accounts existed, are one row)."""
    w, args = _where(org_id, days)
    rows = conn.execute(f"""SELECT a.user_id, coalesce(nullif(u.name, ''), u.email), count(*), coalesce(sum(a.cost_usd), 0), max(a.created_at)
                            FROM ask_log a LEFT JOIN users u ON u.id = a.user_id WHERE {w}
                            GROUP BY a.user_id, u.name, u.email ORDER BY count(*) DESC""", args).fetchall()
    return [{"id": r[0], "name": r[1] or "Before accounts were added", "n": r[2], "spend": float(r[3]), "last": r[4]} for r in rows]


def gaps(conn, org_id, days=30, limit=10):
    """Questions the library could not fully answer, with what was missing: Low-confidence ones first, then the most recent."""
    w, args = _where(org_id, days, gaps=True)
    rows = conn.execute(f"""SELECT a.id, a.created_at, a.question, a.answer->>'confidence', a.answer->'not_covered' FROM ask_log a
                            WHERE {w} ORDER BY (a.answer->>'confidence' = 'Low') DESC, a.created_at DESC LIMIT %s""", [*args, limit]).fetchall()
    return [{"id": r[0], "at": r[1], "question": r[2], "confidence": r[3], "missing": r[4] or []} for r in rows]


def log_page(conn, org_id, page=1, **filters):
    w, args = _where(org_id, **filters)
    total = conn.execute(f"SELECT count(*) FROM ask_log a WHERE {w}", args).fetchone()[0]
    pages = max(1, -(-total // PER_PAGE))
    page = min(max(1, page), pages)
    rows = conn.execute(f"""SELECT a.id, a.created_at, a.user_id, coalesce(nullif(u.name, ''), u.email), a.question, a.role, a.depth, a.cost_usd,
                                   a.answer, a.sources
                            FROM ask_log a LEFT JOIN users u ON u.id = a.user_id WHERE {w}
                            ORDER BY a.created_at DESC LIMIT %s OFFSET %s""", [*args, PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    items = []
    for r in rows:
        ans = r[8] or {}
        items.append({"id": r[0], "at": r[1], "who": r[3] or "Before accounts were added", "question": r[4], "role": ans.get("role") or r[5], "depth": r[6],
                      "cost": float(r[7] or 0), "confidence": ans.get("confidence", ""), "papers": ans.get("papers_cited", 0),
                      "paragraphs": ans.get("paragraphs", []), "not_covered": ans.get("not_covered", []), "disagreements": ans.get("disagreements", []),
                      "sources": [{"no": s.get("no"), "title": s.get("title"), "year": s.get("year")} for s in (r[9] or [])]})
    return items, total, pages, page


def _safe(cell):
    """Spreadsheet programs run text that starts with = + - @ as a formula: put a quote in front."""
    s = "" if cell is None else str(cell)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(conn, org_id, **filters):
    w, args = _where(org_id, **filters)
    rows = conn.execute(f"""SELECT a.created_at, coalesce(nullif(u.name, ''), u.email), a.question, a.role, a.depth, a.answer->>'confidence',
                                   a.answer->>'papers_cited', a.answer->'not_covered', a.cost_usd
                            FROM ask_log a LEFT JOIN users u ON u.id = a.user_id WHERE {w} ORDER BY a.created_at DESC""", args).fetchall()
    out = io.StringIO()
    wr = csv.writer(out)
    wr.writerow(["When (UTC)", "Who", "Question", "Answering as", "Depth", "Confidence", "Papers cited", "Not covered by the library", "Cost (USD)"])
    for r in rows:
        wr.writerow([r[0].strftime("%Y-%m-%d %H:%M"), _safe(r[1] or "Before accounts were added"), _safe(r[2]), _safe(r[3]), _safe(r[4]), _safe(r[5]), r[6] or 0,
                     _safe("; ".join(r[7] or [])), f"{float(r[8] or 0):.4f}"])
    return out.getvalue()
