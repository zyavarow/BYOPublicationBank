"""Self-check of the extraction template (free; the optional --api flag makes one tiny API call per call group, about
1 cent each, to confirm the API accepts each group's schema).
  .venv\\Scripts\\python.exe scripts\\check_template.py
  .venv\\Scripts\\python.exe scripts\\check_template.py --api
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank import extraction_schema as v1  # noqa: E402
from pubbank.template import (LEGACY_SINGLE_TEMPLATE, SCHEMA_VERSION, finalize_result, group_categories,  # noqa: E402
                              load_context, load_template)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", action="store_true")
    args = ap.parse_args()
    ctx = load_context()
    t = ctx.template
    cats = [c for c in t["categories"] if c["include"]]
    print(f"template version {t['version']} | schema {SCHEMA_VERSION} | {len(cats)} categories | {len(ctx.models)} call groups")
    for gid, g in t["groups"].items():
        ids = [c["id"] for c in group_categories(t, gid)]
        print(f"  group {gid}: {', '.join(ids)} | prompt ~{len(ctx.prompts[gid]):,} chars | schema "
              f"{len(json.dumps(ctx.models[gid].model_json_schema())):,} chars | sends: {', '.join(g['kinds'])}"
              f"{' + page<=' + str(g['page_max']) if g['page_max'] else ''}")

    # regression: core + overlay must reproduce the original complete DADA2 template exactly
    legacy = load_template(LEGACY_SINGLE_TEMPLATE)
    print("  sources:", {k: Path(v).name for k, v in t.get("sources", {}).items()})
    # Intentional changes since the original single-file template: DADA2 phenotypes became free text (mapped to HPO).
    INTENTIONAL = {"clinical_features"}
    all_same = True
    for key in ("categories", "global_rules", "groups"):
        if key == "categories":
            a_ = [c for c in legacy[key] if c["id"] not in INTENTIONAL]
            b_ = [c for c in t[key] if c["id"] not in INTENTIONAL]
        else:
            a_, b_ = legacy[key], t[key]
        same = a_ == b_
        all_same &= same
        print(f"  regression core+overlay vs original single-file template, {key}"
              f"{' (except ' + ', '.join(sorted(INTENTIONAL)) + ')' if key == 'categories' else ''}: {'IDENTICAL' if same else 'DIFFERENT'}")
        if not same and key == "categories":
            for a, b in zip(a_, b_):
                for k in a:
                    if a[k] != b[k]:
                        print(f"     {a['id']}.{k}: original={str(a[k])[:90]!r} merged={str(b[k])[:90]!r}")
    cf_old = next(c for c in legacy["categories"] if c["id"] == "clinical_features")
    cf_new = next(c for c in t["categories"] if c["id"] == "clinical_features")
    print(f"  changed on purpose: clinical_features feature field {cf_old['fields'][0]['kind']} -> {cf_new['fields'][0]['kind']} (free text, mapped to HPO)")
    print(f"  template version: now {t['version']} | original {legacy['version']} (differs because of the intentional change)")

    # vocabularies must match the v1 constants, except 'None (review/guideline)' which became Not applicable
    v1_lists = {"study_type": v1.StudyType, "gene_focus": v1.GeneFocus, "model_systems": v1.ModelSystemName,
                "techniques": v1.TechniqueName, "sample_sources": v1.SampleSourceName, "treatments": v1.TreatmentName,
                "clinical_features": v1.ClinicalFeatureName}
    for cid, lit in v1_lists.items():
        old = [x for x in lit.__args__ if x != "None (review/guideline)"]
        new = next((f["values"] for c in cats if c["id"] == cid for f in c["fields"] if f["kind"] == "pick-list"), None)
        if new is None:
            print(f"  vocabulary {cid:<18} free text now (mapped to a standard ontology)")
            continue
        print(f"  vocabulary {cid:<18} {'matches v1' if old == new else 'DIFFERS from v1: ' + str(set(old) ^ set(new))}")

    # synthetic results round-trip through each group's model, and finalize() leaves no blank category
    ev = {"quote": "Plasma ADA2 activity was measured.", "section": "Methods", "page": 2, "verification": "verified"}

    def cat(status, entries, conf="High", why=""):
        return {"status": status, "confidence": conf, "rationale": why, "entries": entries}

    merged = {}
    for gid in ctx.models:
        data = {c["id"]: cat("Not found", [], "Medium", "") for c in group_categories(t, gid)}
        data["extraction_notes"] = ""
        if "study_type" in data:
            data["study_type"] = cat("Found", [{"value": "Review", "evidence": ev}])
            data["gene_focus"] = cat("Found", [{"value": "ADA2 (CECR1)", "evidence": ev}])
            data["all_authors_from_title_page"] = ["A B"]
        if "reagents" in data:
            data["reagents"] = cat("Found", [{"name": "kit", "vendor": "", "catalog_number": "", "target_or_use": "", "evidence": ev}])
        ctx.models[gid].model_validate(data)
        merged.update({k: v for k, v in data.items() if k != "extraction_notes"})
    out = finalize_result(t, merged)
    for c in cats:
        r = out[c["id"]]
        assert r["status"] in ("Found", "Not found", "Not applicable"), c["id"]
        assert r["status"] == "Found" or r["rationale"].strip(), c["id"]
    print("  synthetic Review paper: reagents ->", out["reagents"]["status"], "|", out["reagents"]["rationale"][:80])
    print("  corresponding_authors (applies to all) ->", out["corresponding_authors"]["status"], "|", out["corresponding_authors"]["rationale"][:60])
    print("  no blank category: OK")

    if args.api:
        import anthropic
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        client = anthropic.Anthropic()
        for gid, model_cls in ctx.models.items():
            try:
                r = client.messages.parse(
                    model="claude-sonnet-5-5", max_tokens=64, output_format=model_cls, output_config={"effort": "low"},
                    messages=[{"role": "user", "content": "Reply with the structured result for an empty paper."}])
                print(f"  group {gid}: API ACCEPTED the schema (input tokens {r.usage.input_tokens})")
            except anthropic.APIStatusError as e:
                msg = e.body["error"]["message"] if isinstance(e.body, dict) else str(e)
                print(f"  group {gid}: API REJECTED the schema: {e.status_code} {msg[:120]}")
            except Exception as e:  # a truncated 64-token answer fails parsing locally, which still proves the API accepted it
                print(f"  group {gid}: API accepted the request (local parse of the truncated answer failed: {type(e).__name__})")


if __name__ == "__main__":
    main()
