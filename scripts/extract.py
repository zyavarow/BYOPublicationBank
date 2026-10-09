"""Phase 2: extract the categories of the extraction template from parsed papers with Claude; store results in Postgres.

SPENDS API MONEY unless --estimate is used (counting tokens is free). Safety rails: --max-cost (hard cap for this run,
default $1.00), only papers you name (--only / --list), only papers marked in_bank, and the run stops after repeated errors.
  .venv\\Scripts\\python.exe scripts\\extract.py --org "DADA2 Papers" --list data\\slices\\test15.txt --estimate
  .venv\\Scripts\\python.exe scripts\\extract.py --org "DADA2 Papers" --list data\\slices\\test15.txt --run-label v2test --max-cost 2
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank.db import psycopg  # noqa: E402  (must come before any other psycopg import)
import anthropic  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

load_dotenv(ROOT / ".env")

from pubbank.extraction import MODEL, PRICES, extract_paper, plan_calls  # noqa: E402
from pubbank.template import SCHEMA_VERSION, load_context  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS extractions (
    id serial PRIMARY KEY,
    paper_id int NOT NULL REFERENCES papers(id),
    org_id int NOT NULL,
    schema_version text NOT NULL,
    model text NOT NULL,
    run_label text NOT NULL DEFAULT 'default',
    status text NOT NULL,
    error text,
    result jsonb,
    derived jsonb,
    quote_stats jsonb,
    stop_reason text,
    refusal_category text,
    input_tokens int, output_tokens int, cache_write_tokens int, cache_read_tokens int,
    cost_usd numeric(10,5),
    seconds real,
    created_at timestamptz DEFAULT now(),
    UNIQUE (paper_id, schema_version, model, run_label)
);
ALTER TABLE extractions ADD COLUMN IF NOT EXISTS template_version text;
"""

PAPER_COLS = ["id", "filename", "title", "extracted_title", "journal", "year", "authors", "sections", "full_text",
              "parse_status", "in_bank"]

# Tokens the response schema adds to each request (measured on the API, per call group). The schema and the system
# prompt are cached after the first request, so they are billed at the cache-read rate afterwards.
SCHEMA_TOKENS = {"1": 2900, "2": 4000, "3": 3000}
EXPECTED_OUT, HIGH_OUT = 3000, 5000   # output tokens per paper (all groups), expected and high case


def estimate(client, conn, ctx, papers, model, org_id):
    first_calls = {}
    tot_text = tot_cached = 0
    rows = []
    n_calls = 0
    for p in papers:
        r = conn.execute(
            "SELECT e.result->'study_type'->>'value', e.result->'study_type'->'entries'->0->>'value' FROM extractions e "
            "WHERE e.paper_id=%s AND e.status='ok' ORDER BY e.created_at DESC LIMIT 1", (p["id"],)).fetchone()
        study = (r[1] or r[0]) if r else None
        plan = plan_calls(ctx, p, study)
        if study is None:
            plan = plan_calls(ctx, p, "Case report/series")  # unknown study type: assume everything may apply (upper bound)
        paper_text_tokens = 0
        for c in plan:
            sys_tokens = client.messages.count_tokens(model=model, system=ctx.prompts[c["group"]],
                                                      messages=[{"role": "user", "content": "x"}]).input_tokens
            total = client.messages.count_tokens(model=model, system=ctx.prompts[c["group"]],
                                                 messages=[{"role": "user", "content": c["text"]}]).input_tokens
            text_tokens = total - sys_tokens
            cached = sys_tokens + SCHEMA_TOKENS[c["group"]]
            first_calls.setdefault(c["group"], cached)
            paper_text_tokens += text_tokens
            tot_text += text_tokens
            tot_cached += cached
            n_calls += 1
        rows.append((p["filename"], study or "(unknown: assumed all groups)", len(plan), paper_text_tokens))
    writes = sum(first_calls.values())
    inp = (tot_text * PRICES["input"] + (tot_cached - writes) * PRICES["cache_read"] + writes * PRICES["cache_write"]) / 1e6
    for fn, st, n, tk in rows:
        print(f"  {tk:>7,} text tokens  {n} request(s)  {st[:34]:<34} {fn[:46]}")
    print(f"\n{len(papers)} papers, {n_calls} requests, {tot_text:,} paper-text tokens, "
          f"{tot_cached:,} cached instruction+schema tokens")
    for label, out_each in (("expected", EXPECTED_OUT), ("high", HIGH_OUT)):
        out = len(papers) * out_each * PRICES["output"] / 1e6
        print(f"  {label:<9} input ${inp:.2f} + output ${out:.2f} = ${inp + out:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--list", help="text file with one PDF filename per line")
    ap.add_argument("--only", action="append", default=[], help="filename contains this text (repeatable)")
    ap.add_argument("--run-label", default="default")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--template", help="path to the extraction template (default orgs/dada2/extraction_template.xlsx)")
    ap.add_argument("--max-cost", type=float, default=1.00, help="stop launching new papers once this run has cost this many USD")
    ap.add_argument("--force", action="store_true", help="redo papers that already have a successful extraction")
    ap.add_argument("--estimate", action="store_true", help="count tokens and print the expected cost; spends nothing")
    args = ap.parse_args()
    if not (args.list or args.only):
        sys.exit("Name the papers explicitly with --only or --list (this script spends money).")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is missing from .env")

    ctx = load_context(args.template)
    client = anthropic.Anthropic()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(DDL)
        conn.commit()
        org_id = conn.execute("SELECT id FROM organizations WHERE slug=%s", (args.org,)).fetchone()[0]

        wanted = []
        if args.list:
            wanted += [ln.strip() for ln in Path(args.list).read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
        rows = conn.execute(f"SELECT {', '.join(PAPER_COLS)} FROM papers WHERE org_id=%s ORDER BY filename", (org_id,)).fetchall()
        papers = [dict(zip(PAPER_COLS, r)) for r in rows]
        chosen = [p for p in papers if p["filename"] in wanted or any(o.lower() in p["filename"].lower() for o in args.only)]
        eligible = []
        for p in chosen:
            if p["parse_status"] != "ok" or not p["in_bank"]:
                print(f"skipping (not in the bank: excluded/duplicate/unparsed/needs OCR): {p['filename']}")
            else:
                eligible.append(p)

        done = set()
        if not args.force and not args.estimate:
            done = {r[0] for r in conn.execute(
                "SELECT paper_id FROM extractions WHERE schema_version=%s AND model=%s AND run_label=%s AND status='ok'",
                (SCHEMA_VERSION, args.model, args.run_label))}
        todo = [p for p in eligible if p["id"] not in done]
        print(f"{len(chosen)} matched, {len(eligible)} eligible, {len(todo)} to extract "
              f"(model {args.model}, schema {SCHEMA_VERSION}, template {ctx.version}, cost cap ${args.max_cost:.2f})", flush=True)
        if args.estimate:
            estimate(client, conn, ctx, todo, args.model, org_id)
            return

        spent, consecutive_errors = 0.0, 0
        for i, p in enumerate(todo, 1):
            if spent >= args.max_cost:
                print(f"STOPPED: cost cap reached (${spent:.3f} >= ${args.max_cost:.2f}). {len(todo) - i + 1} papers not run.")
                break
            res = extract_paper(client, p, ctx, args.model)
            spent += res["cost_usd"]
            u, qs = res["usage"], res["quote_stats"] or {}
            conn.execute(
                """INSERT INTO extractions (paper_id, org_id, schema_version, model, run_label, status, error, result,
                       derived, quote_stats, stop_reason, refusal_category, input_tokens, output_tokens,
                       cache_write_tokens, cache_read_tokens, cost_usd, seconds, template_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (paper_id, schema_version, model, run_label) DO UPDATE SET
                       status=EXCLUDED.status, error=EXCLUDED.error, result=EXCLUDED.result, derived=EXCLUDED.derived,
                       quote_stats=EXCLUDED.quote_stats, stop_reason=EXCLUDED.stop_reason,
                       refusal_category=EXCLUDED.refusal_category, input_tokens=EXCLUDED.input_tokens,
                       output_tokens=EXCLUDED.output_tokens, cache_write_tokens=EXCLUDED.cache_write_tokens,
                       cache_read_tokens=EXCLUDED.cache_read_tokens, cost_usd=EXCLUDED.cost_usd,
                       seconds=EXCLUDED.seconds, template_version=EXCLUDED.template_version, created_at=now()""",
                (p["id"], org_id, SCHEMA_VERSION, args.model, args.run_label, res["status"], res["error"],
                 Jsonb(res["result"]) if res["result"] else None, Jsonb(res["derived"]) if res["derived"] else None,
                 Jsonb(res["quote_stats"]) if res["quote_stats"] else None, res["stop_reason"], res["refusal_category"],
                 u["input_tokens"], u["output_tokens"], u["cache_write_tokens"], u["cache_read_tokens"],
                 round(res["cost_usd"], 5), res["seconds"], res["template_version"]))
            conn.commit()
            quotes = f"quotes verified {qs.get('verified', 0)}/{qs.get('total', 0)}" if qs else ""
            mix = ""
            if res["result"]:
                cats = [v for k, v in res["result"].items() if isinstance(v, dict) and "status" in v]
                mix = " found {}/ not found {}/ N/A {}".format(*[sum(1 for c in cats if c["status"] == s)
                                                                  for s in ("Found", "Not found", "Not applicable")])
                n_req = sum(1 for c in res["result"]["_meta"]["calls"] if "skipped" not in c)
                mix += f" ({n_req} req)"
            print(f"[{i}/{len(todo)}] {res['status']:<9} in {u['input_tokens'] + u['cache_read_tokens'] + u['cache_write_tokens']:>6} "
                  f"out {u['output_tokens']:>5} tok  ${res['cost_usd']:.4f}  {res['seconds']:.0f}s {quotes}{mix}  {p['filename'][:40]}"
                  + (f"   !! {res['error'][:120]}" if res["error"] else ""), flush=True)
            consecutive_errors = consecutive_errors + 1 if res["status"] in ("error", "invalid") else 0
            if consecutive_errors >= 2:
                print("STOPPED: two errors in a row, something systematic is wrong. Nothing more will be spent.")
                break
        print(f"\nRun cost: ${spent:.4f}")


if __name__ == "__main__":
    main()
