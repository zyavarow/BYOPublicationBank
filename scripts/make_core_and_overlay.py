"""Seeds the shared CORE template (core/extraction_core.xlsx) and the DADA2 OVERLAY (orgs/dada2/overlay.xlsx).

The core is disease-neutral. The DADA2 overlay is computed as the exact difference between the core and the complete
single-file DADA2 template (scripts/make_template.py), so merging core + overlay reproduces that template. That also
shows precisely what is disease-specific. After this seeding, the two Excel files are the source of truth.
  .venv\\Scripts\\python.exe scripts\\make_core_and_overlay.py            # refuses to overwrite existing files
  .venv\\Scripts\\python.exe scripts\\make_core_and_overlay.py --force
"""
import argparse
import copy
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_template as base  # noqa: E402  (the complete DADA2 template content)
from pubbank.template import CAT_KEYS, CATEGORY_TEXT_FIELDS, FIELD_KEYS  # noqa: E402

DISEASE = "deficiency of adenosine deaminase 2 (DADA2, also called ADA2 deficiency; the gene is ADA2, formerly CECR1)"

CORE_CATEGORY_TEXT = {
    "study_type": {
        "interpret": "If a paper does several things, pick the primary one (the one the title and abstract emphasize). Reviews, guidelines and consensus statements summarize other work. Use 'Other/off-topic' when the paper is not about the disease or genes named in the Context rule (for example an unrelated disease, or a paper that only uses a tool or name that happens to match); say so in the rationale."},
    "gene_focus": {
        "counts": "Which gene or protein the paper is about, chosen from the options defined for this organization.",
        "interpret": "Choose the option naming the paper's main gene or protein. Beware of similar names (see the Context rule and this organization's options); use 'Both' only when the paper substantially studies both. A general guideline or review that mentions the disease only briefly is 'Other/none'.",
        "confidence_guide": "High when the title or abstract names the gene or protein; Medium when only inferable from context."},
    "techniques": {
        "interpret": "Use the listed technique name that fits (for example 'Western blot', 'Flow cytometry'). Name anything else under 'Other' with detail."},
    "clinical_features": {
        "counts": "Clinical features (phenotypes) observed in this paper's own patients.",
        "interpret": "A feature that the paper says a patient did NOT have is not a finding. Record each distinct feature as its own entry, as a short medical term in the paper's own words (for example 'vasculitis', 'ischemic stroke', 'hepatosplenomegaly'), not a whole sentence; put severity, timing or context in the detail field. Features are later mapped to Human Phenotype Ontology terms."},
}

# Search-filter groups for the DADA2 overlay. Each group is a set of exact HPO labels (a term plus all of its more specific
# child terms); labels were checked against HPO release 2026-09-01.
DADA2_DISPLAY_GROUPS = [
    ("clinical_features", "Vasculitis / arteritis", "Vasculitis; Arteritis"),
    ("clinical_features", "Stroke / cerebral ischemia", "Stroke; Cerebral ischemia; Cerebral hemorrhage"),
    ("clinical_features", "Cytopenia / marrow failure", "Pancytopenia; Anemia; Thrombocytopenia; Aplastic anemia; Pure red cell aplasia; Bone marrow hypocellularity"),
    ("clinical_features", "Immunodeficiency / infections", "Immunodeficiency; Recurrent infections"),
    ("clinical_features", "Livedo", "Livedo reticularis"),
    ("clinical_features", "Fever", "Fever"),
    ("clinical_features", "Lymphoproliferation", "Lymphoproliferative disorder; Lymphadenopathy; Lymphoma"),
    ("clinical_features", "Hepatosplenomegaly / portal hypertension", "Hepatosplenomegaly; Splenomegaly; Hepatomegaly; Portal hypertension"),
]
CORE_FIELD_CHANGES = {
    ("gene_focus", "value"): dict(values=["Target gene or protein", "Related gene or protein", "Both", "Other/none"],
                                  description="The gene or protein the paper is about."),
    ("mutations", "gene"): dict(description="Gene symbol, for example BRCA1."),
    ("reagents", "name"): dict(description="The reagent, for example 'anti-[target] antibody' or a specific clone."),
    ("techniques", "technique"): dict(remove=["ADA2 enzyme activity assay"]),
    ("treatments", "treatment"): dict(values=["Small-molecule drug", "Biologic/antibody", "Enzyme replacement", "Gene therapy",
                                              "Cell or stem-cell transplant", "Surgery/procedure", "Supportive care", "Other"]),
    ("clinical_features", "feature"): dict(kind="text", values=[],
                                           description="The clinical feature, as the paper words it."),
    ("clinical_features", "detail"): dict(description="Optional extra detail (for example severity or age at onset); empty string if none."),
}

HEADER_SETTINGS = ["Category", "Field", "Action (replace / append)", "Text"]
HEADER_CHANGES = ["Category", "Field", "Action (add / remove / replace / describe)", "Values (separate with ;)",
                  "Insert before (optional, for add)", "Kind (pick-list / text, optional)", "Description (for describe)"]


def cat_dicts():
    out = []
    for row in base.CATS:
        d = dict(zip(CAT_KEYS, list(row) + [base.CALL_GROUP_OF[row[0]]]))
        d["fields"] = []
        out.append(d)
    by_id = {d["id"]: d for d in out}
    for r in base.FIELDS:
        f = dict(zip(FIELD_KEYS, r))
        f["values"] = [x.strip() for x in f["values"].split(";") if x.strip()]
        by_id[f["category"]]["fields"].append(f)
    return out


def style_sheet(ws, widths):
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2B6E5E")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[0].font = Font(bold=True)
    ws.freeze_panes = "B2"


def write_how_to(wb, lines):
    wh = wb.create_sheet("How to edit", 0)
    for line in lines:
        wh.append([line])
    wh.column_dimensions["A"].width = 150
    wh["A1"].font = Font(bold=True, size=14)
    for row in wh.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")


def join(vals):
    return "; ".join(vals)


def list_ops(core_vals, target_vals):
    """Return ('add', [(value, insert_before)]) when target = core plus extras in the same order, else ('replace', target)."""
    removed = [v for v in core_vals if v not in target_vals]
    kept = [v for v in target_vals if v in core_vals]
    if not removed and kept == core_vals:
        adds = []
        for i, v in enumerate(target_vals):
            if v not in core_vals:
                nxt = next((x for x in target_vals[i + 1:] if x in core_vals), "")
                adds.append((v, nxt))
        return "add", adds
    return "replace", target_vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    core_path = ROOT / "core" / "extraction_core.xlsx"
    overlay_path = ROOT / "orgs" / "dada2" / "overlay.xlsx"
    for p in (core_path, overlay_path):
        if p.exists() and not args.force:
            sys.exit(f"{p} already exists; it is the source of truth now. Use --force to overwrite.")

    target = cat_dicts()                       # the complete DADA2 template
    # Intentional change from the original DADA2 template: phenotypes are extracted as free text (in the paper's own
    # words) and mapped to HPO afterwards, so DADA2 uses the core's free-text field instead of its own pick-list.
    for t_ in target:
        if t_["id"] == "clinical_features":
            t_["interpret"] = CORE_CATEGORY_TEXT["clinical_features"]["interpret"]
            for f_ in t_["fields"]:
                ch_ = CORE_FIELD_CHANGES[("clinical_features", f_["name"])]
                f_["kind"], f_["values"], f_["description"] = ch_.get("kind", f_["kind"]), ch_.get("values", f_["values"]), ch_["description"]
    core = copy.deepcopy(target)
    for c in core:
        for k, v in CORE_CATEGORY_TEXT.get(c["id"], {}).items():
            c[k] = v
        for f in c["fields"]:
            ch = CORE_FIELD_CHANGES.get((c["id"], f["name"]))
            if not ch:
                continue
            if "kind" in ch:
                f["kind"] = ch["kind"]
            if "values" in ch:
                f["values"] = ch["values"]
            if "remove" in ch:
                f["values"] = [v for v in f["values"] if v not in ch["remove"]]
            if "description" in ch:
                f["description"] = ch["description"]
    rules_target = list(base.GLOBAL_RULES)
    ctx_text = dict(rules_target)["Context"]
    assert DISEASE in ctx_text, "disease string not found in the DADA2 Context rule"
    rules_core = [(n, t.replace(DISEASE, "{disease}") if n == "Context" else t) for n, t in rules_target]

    # ---------------- core workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Categories"
    ws.append(base.HEADER_CATS + ["Call group (1-3)"])
    for c in core:
        ws.append([c["id"], c["label"], c["include"], c["answer_type"], c["applies_to"], c["counts"], c["not_counts"],
                   c["interpret"], c["na_means"], c["nf_means"], c["found_in"], c["confidence_guide"], c["filter_chip"],
                   c["call_group"]])
    style_sheet(ws, [22, 24, 12, 12, 34, 44, 40, 62, 36, 40, 24, 40, 16, 12])
    wf = wb.create_sheet("Entry fields")
    wf.append(base.HEADER_FIELDS)
    for c in core:
        for f in c["fields"]:
            wf.append([f["category"], f["name"], f["kind"], join(f["values"]), f["description"]])
    style_sheet(wf, [22, 18, 18, 80, 60])
    wg = wb.create_sheet("Global rules")
    wg.append(["Rule", "Text"])
    for n, t in rules_core:
        wg.append([n, t])
    style_sheet(wg, [26, 150])
    wc = wb.create_sheet("Call groups")
    wc.append(base.HEADER_GROUPS)
    for r in base.GROUPS:
        wc.append(r)
    style_sheet(wc, [12, 62, 60, 40, 40])
    write_how_to(wb, [
        "SHARED CORE TEMPLATE (used by every organization)",
        "",
        "These are the disease-neutral extraction categories, their rules, and the call groups. Organizations do not edit this file;",
        "each organization has an overlay (orgs/<name>/overlay.xlsx) that adds its disease context, changes pick-lists, adds rules or",
        "custom categories, and switches core categories on or off. Changing the core changes the extraction for ALL organizations,",
        "so test it first (scripts/check_template.py).",
        "",
        "The Context rule contains {disease}, which each overlay fills in on its Context tab.",
    ])
    core_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(core_path)

    # ---------------- overlay workbook (computed as the exact difference)
    ow = Workbook()
    wctx = ow.active
    wctx.title = "Context"
    wctx.append(["Key", "Value"])
    wctx.append(["disease", DISEASE])
    style_sheet(wctx, [20, 150])
    wset = ow.create_sheet("Category settings")
    wset.append(HEADER_SETTINGS)
    wchg = ow.create_sheet("Entry field changes")
    wchg.append(HEADER_CHANGES)
    core_by = {c["id"]: c for c in core}
    n_settings = n_changes = 0
    for t in target:
        c = core_by[t["id"]]
        for k in sorted(CATEGORY_TEXT_FIELDS - {"call_group"}, key=list(CAT_KEYS).index):
            if t[k] != c[k]:
                wset.append([t["id"], k, "replace", t[k]]); n_settings += 1
        for k in ("include", "filter_chip"):
            if t[k] != c[k]:
                wset.append([t["id"], k, "replace", t[k]]); n_settings += 1
        if t["applies_to"] != c["applies_to"]:
            wset.append([t["id"], "applies_to", "replace", t["applies_to"]]); n_settings += 1
        for tf, cf in zip(t["fields"], c["fields"]):
            if tf["values"] != cf["values"] or tf["kind"] != cf["kind"]:
                op, payload = list_ops(cf["values"], tf["values"]) if tf["kind"] == cf["kind"] else ("replace", tf["values"])
                if op == "add":
                    for v, before in payload:
                        wchg.append([t["id"], tf["name"], "add", v, before, "", ""]); n_changes += 1
                else:
                    wchg.append([t["id"], tf["name"], "replace", join(payload), "", tf["kind"], ""]); n_changes += 1
            if tf["description"] != cf["description"]:
                wchg.append([t["id"], tf["name"], "describe", "", "", "", tf["description"]]); n_changes += 1
    style_sheet(wset, [22, 20, 22, 150])
    style_sheet(wchg, [22, 18, 22, 80, 26, 20, 80])
    wcc = ow.create_sheet("Custom categories")
    wcc.append(base.HEADER_CATS + ["Call group (1-3)"])
    style_sheet(wcc, [22, 24, 12, 12, 34, 44, 40, 62, 36, 40, 24, 40, 16, 12])
    wce = ow.create_sheet("Custom entry fields")
    wce.append(base.HEADER_FIELDS)
    style_sheet(wce, [22, 18, 18, 80, 60])
    wra = ow.create_sheet("Rule additions")
    wra.append(["Rule", "Text appended to the global rules"])
    style_sheet(wra, [26, 150])
    wdg = ow.create_sheet("Display groups")
    wdg.append(["Category", "Group label shown on search chips", "Match (values, or ontology ids such as HP:0002633)"])
    for r in DADA2_DISPLAY_GROUPS:
        wdg.append(list(r))
    style_sheet(wdg, [22, 40, 100])
    wgn = ow.create_sheet("Genes")
    wgn.append(["Symbol", "Aliases (separate with ;)", "Notes"])
    wgn.append(["ADA2", "CECR1", "The gene mutated in DADA2 (formerly CECR1)."])
    wgn.append(["ADA", "ADA1", "A different enzyme (ADA-SCID, PEG-ADA); not DADA2."])
    style_sheet(wgn, [14, 30, 90])
    write_how_to(ow, [
        "ORGANIZATION OVERLAY: DADA2",
        "",
        "This file adapts the shared core template (core/extraction_core.xlsx) to one organization. Everything the core already does",
        "stays; only the differences are listed here. This is also what an AI proposes when a new organization is onboarded;",
        "a person reviews and edits it, then it is frozen and versioned.",
        "",
        "Context tab: 'disease' is required; it is inserted into the core's Context rule.",
        "Category settings: replace or append a category's text (counts, not_counts, interpret, na_means, nf_means, found_in,",
        "  confidence_guide, label), or set include / filter_chip / applies_to.",
        "Entry field changes: add / remove / replace pick-list values (add can say 'insert before'), switch a field between pick-list and",
        "  text, or describe it.",
        "Custom categories + Custom entry fields: new categories for this organization (same columns as the core).",
        "Rule additions: extra global rules. Display groups: how values are grouped on search chips (used by the app).",
        "Genes: the organization's genes with aliases (for example CECR1 = ADA2); used to standardize gene symbols in variant calls.",
        "",
        f"The rows below were generated automatically so that core + this overlay equals the original DADA2 template "
        f"({n_settings} category-setting rows, {n_changes} entry-field rows): every row is something specific to DADA2.",
    ])
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    ow.save(overlay_path)
    print(f"wrote {core_path}\nwrote {overlay_path}  ({n_settings} category-setting rows, {n_changes} entry-field rows)")


if __name__ == "__main__":
    main()
