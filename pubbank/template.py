"""Extraction template: reads orgs/<org>/extraction_template.xlsx and builds from it
  - one Pydantic model per call group (every category carries status / confidence / rationale / entries),
  - one system prompt per call group (global rules + one block per category in the group),
  - the post-processing that guarantees no blank category and computes the final confidence.

Design notes
  * The API compiles the schema into a grammar and rejects it as "too large" above roughly 9-10k characters, so a
    paper is read in up to three requests ("call groups": front matter; methods; findings), each seeing only the
    relevant sections. Group 1 holds Study type, which decides which categories of groups 2 and 3 apply; when none
    apply (reviews, guidelines, off-topic papers) those requests are skipped.
  * No nullable fields anywhere (they blow up the grammar): "not stated" is an empty string / empty list.
  * Applicability is decided by code from the study type (template column 'Applies to study types'); the AI is told
    the same rule, and the code overrides it if the two disagree (recording a flag).
"""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook
from pydantic import Field, create_model

from .extraction_schema import Evidence, derived_fields  # noqa: F401  (derived_fields re-exported)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORE = ROOT / "core" / "extraction_core.xlsx"
LEGACY_SINGLE_TEMPLATE = ROOT / "orgs" / "exd" / "extraction_template.xlsx"   # complete single-file template (regression baseline)
DEFAULT_TEMPLATE = ROOT / "orgs" / "exd" / "overlay.xlsx"                      # core + this overlay
SCHEMA_VERSION = "exd-v2"

STATUSES = ("Found", "Not found", "Not applicable")
CONFIDENCES = ("High", "Medium", "Low")
RANK = {"Low": 0, "Medium": 1, "High": 2}
SINGLE_PICK = {"Pick one"}
VALID_KINDS = {"front", "abstract", "introduction", "methods", "results", "discussion", "conclusion",
               "acknowledgments", "case"}

CAT_KEYS = ["id", "label", "include", "answer_type", "applies_to", "counts", "not_counts", "interpret",
            "na_means", "nf_means", "found_in", "confidence_guide", "filter_chip", "call_group"]
FIELD_KEYS = ["category", "name", "kind", "values", "description"]


def _s(x):
    return "" if x is None else str(x).strip()


def _split(x):
    return [p.strip() for p in _s(x).split(";") if p.strip()]


def _int(x, default=0):
    try:
        return int(float(_s(x)))
    except ValueError:
        return default


def _read_categories(ws):
    cats = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not _s(row[0]):
            continue
        d = {k: _s(v) for k, v in zip(CAT_KEYS, row)}
        d["include"] = d["include"].lower() != "no"
        d["filter_chip"] = d["filter_chip"].lower() == "yes"
        d["applies_to"] = "All" if d["applies_to"].lower() in ("", "all") else _split(d["applies_to"])
        d["fields"] = []
        cats.append(d)
    return cats


def _read_fields(ws, by_id, tab):
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not _s(row[0]):
            continue
        f = {k: _s(v) for k, v in zip(FIELD_KEYS, row)}
        f["values"] = _split(f["values"])
        if f["category"] not in by_id:
            raise ValueError(f"{tab} tab: category '{f['category']}' is not on the Categories tab.")
        by_id[f["category"]]["fields"].append(f)


def _group_from_row(r):
    policy = _s(r[5]).lower() if len(r) > 5 else ""
    return {"id": _s(r[0]), "label": _s(r[1]), "kinds": _split(r[2]), "min_chars": _int(r[3]), "page_max": _int(r[4]),
            "recheck": "never" if policy.startswith("never") else "key_missing" if policy.startswith("only") else "always",
            "key_kinds": _split(r[6]) if len(r) > 6 else []}


def _read_complete_template(wb, path):
    """A workbook with Categories, Entry fields, Global rules and Call groups tabs (the core, or a complete
    single-file template). Not yet validated or versioned."""
    cats = _read_categories(wb["Categories"])
    _read_fields(wb["Entry fields"], {c["id"]: c for c in cats}, "Entry fields")
    rules = [(_s(r[0]), _s(r[1])) for r in wb["Global rules"].iter_rows(min_row=2, values_only=True) if _s(r[0])]
    groups = {}
    for r in wb["Call groups"].iter_rows(min_row=2, values_only=True):
        if _s(r[0]):
            groups[_s(r[0])] = _group_from_row(r)
    return {"categories": cats, "global_rules": rules, "groups": dict(sorted(groups.items())), "path": str(path),
            "display_groups": [], "sources": {"template": str(path)}}


CATEGORY_TEXT_FIELDS = {"label", "answer_type", "counts", "not_counts", "interpret", "na_means", "nf_means",
                        "found_in", "confidence_guide", "call_group"}


def _apply_overlay(t, wb, overlay_path):
    """Merge an organization overlay into the core template `t` (in place)."""
    by_id = {c["id"]: c for c in t["categories"]}

    def rows(sheet):
        return [r for r in wb[sheet].iter_rows(min_row=2, values_only=True) if _s(r[0])] if sheet in wb.sheetnames else []

    # 1) Context: {placeholders} in the core's global rules
    ctx = {_s(r[0]): _s(r[1]) for r in rows("Context")}
    if not ctx.get("disease"):
        raise ValueError("Overlay 'Context' tab must define 'disease' (the disease or topic the bank is about).")
    t["global_rules"] = [(n, _replace_placeholders(x, ctx)) for n, x in t["global_rules"]]
    t["context"] = ctx

    # 2) Category settings: replace / append a text, or set include / filter chip / applies-to
    for cid, field, action, text in ((_s(r[0]), _s(r[1]), _s(r[2]).lower(), _s(r[3])) for r in rows("Category settings")):
        if cid not in by_id:
            raise ValueError(f"Overlay 'Category settings': unknown category '{cid}'.")
        c = by_id[cid]
        if field == "include":
            c["include"] = text.lower() != "no"
        elif field == "filter_chip":
            c["filter_chip"] = text.lower() == "yes"
        elif field == "applies_to":
            c["applies_to"] = "All" if text.lower() in ("", "all") else _split(text)
        elif field in CATEGORY_TEXT_FIELDS:
            c[field] = text if action != "append" else (c[field].rstrip() + " " + text).strip()
        else:
            raise ValueError(f"Overlay 'Category settings': unknown field '{field}' for category '{cid}'.")

    # 3) Entry field changes: pick-list values (add / remove / replace), kind, description
    for r in rows("Entry field changes"):
        cid, fname, action = _s(r[0]), _s(r[1]), _s(r[2]).lower()
        values, before, kind, desc = _split(r[3]), _s(r[4]), _s(r[5]), _s(r[6])
        if cid not in by_id:
            raise ValueError(f"Overlay 'Entry field changes': unknown category '{cid}'.")
        f = next((f for f in by_id[cid]["fields"] if f["name"] == fname), None)
        if f is None:
            raise ValueError(f"Overlay 'Entry field changes': category '{cid}' has no field '{fname}'.")
        if action == "add":
            for v in values:
                if v in f["values"]:
                    continue
                if before and before in f["values"]:
                    f["values"].insert(f["values"].index(before), v)
                else:
                    f["values"].append(v)
        elif action == "remove":
            f["values"] = [v for v in f["values"] if v not in values]
        elif action == "replace":
            f["values"] = values
            if kind:
                f["kind"] = kind
        elif action == "describe":
            f["description"] = desc
        else:
            raise ValueError(f"Overlay 'Entry field changes': unknown action '{action}' (use add, remove, replace, describe).")
        if action != "replace" and kind:
            f["kind"] = kind

    # 4a) Extra call groups (needed when custom categories do not fit the core groups' size limit)
    for r in rows("Custom call groups"):
        gid = _s(r[0])
        if gid in t["groups"]:
            raise ValueError(f"Overlay 'Custom call groups': group '{gid}' already exists in the core.")
        t["groups"][gid] = _group_from_row(r)
    t["groups"] = dict(sorted(t["groups"].items()))

    # 4b) Custom categories and their entry fields
    if "Custom categories" in wb.sheetnames:
        custom = _read_categories(wb["Custom categories"])
        for c in custom:
            if c["id"] in by_id:
                raise ValueError(f"Overlay 'Custom categories': id '{c['id']}' already exists in the core.")
        by_custom = {c["id"]: c for c in custom}
        if "Custom entry fields" in wb.sheetnames:
            _read_fields(wb["Custom entry fields"], by_custom, "Custom entry fields")
        t["categories"].extend(custom)

    # 5) Extra global rules, and display groups (used later by search filters)
    t["global_rules"].extend((_s(r[0]), _s(r[1])) for r in rows("Rule additions"))
    t["display_groups"] = [{"category": _s(r[0]), "label": _s(r[1]), "match": _s(r[2])} for r in rows("Display groups")]
    t["genes"] = [{"symbol": _s(r[0]), "aliases": _split(r[1]), "notes": _s(r[2])} for r in rows("Genes")]
    t["sources"]["overlay"] = str(overlay_path)
    return t


def _replace_placeholders(text, ctx):
    for k, v in ctx.items():
        text = text.replace("{" + k + "}", v)
    left = re.findall(r"\{([a-z_]+)\}", text)
    if left:
        raise ValueError(f"Overlay 'Context' tab is missing a value for: {', '.join(sorted(set(left)))}")
    return text


def _finish(t):
    _validate(t)
    blob = json.dumps({k: t[k] for k in ("categories", "global_rules", "groups")}, sort_keys=True, ensure_ascii=False)
    t["version"] = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]
    return t


def load_template(path=None, core=None):
    """Load a template. `path` is either an organization overlay workbook (has a 'Context' tab; merged onto the shared
    core at `core`, default core/extraction_core.xlsx) or a complete single-file template (has a 'Categories' tab)."""
    path = Path(path or DEFAULT_TEMPLATE)
    wb = load_workbook(path, data_only=True)
    if "Categories" in wb.sheetnames:
        return _finish(_read_complete_template(wb, path))
    core_path = Path(core or DEFAULT_CORE)
    t = _read_complete_template(load_workbook(core_path, data_only=True), core_path)
    t["sources"] = {"core": str(core_path)}
    return _finish(_apply_overlay(t, wb, path))


def _validate(t):
    ids = [c["id"] for c in t["categories"]]
    for must in ("study_type", "gene_focus"):
        if must not in ids:
            raise ValueError(f"The template must keep the '{must}' category.")
    study_cat = next(c for c in t["categories"] if c["id"] == "study_type")
    study_values = study_cat["fields"][0]["values"]
    first_group = next(iter(t["groups"]))
    if study_cat["call_group"] != first_group:
        raise ValueError(f"'study_type' must be in the first call group ({first_group}); it decides which other categories apply.")
    for c in t["categories"]:
        if not c["fields"]:
            raise ValueError(f"Category '{c['id']}' has no rows on the Entry fields tab.")
        for f in c["fields"]:
            if f["kind"] == "pick-list" and not f["values"]:
                raise ValueError(f"{c['id']}.{f['name']} is a pick-list with no allowed values.")
        if c["applies_to"] != "All":
            bad = [x for x in c["applies_to"] if x not in study_values]
            if bad:
                raise ValueError(f"Category '{c['id']}': 'Applies to study types' has names not in the study type list: {bad}")
        if c["include"] and c["call_group"] not in t["groups"]:
            raise ValueError(f"Category '{c['id']}': call group '{c['call_group']}' is not on the Call groups tab.")
    for g in t["groups"].values():
        bad = [k for k in g["kinds"] + g.get("key_kinds", []) if k not in VALID_KINDS]
        if bad:
            raise ValueError(f"Call group {g['id']}: unknown section names {bad}; allowed: {sorted(VALID_KINDS)}")
        if g.get("recheck") == "key_missing" and not g.get("key_kinds"):
            raise ValueError(f"Call group {g['id']}: 'Only if key sections are missing' needs key sections to be listed.")
    if not any(n == "Context" for n, _ in t["global_rules"]):
        raise ValueError("Global rules tab needs a 'Context' rule.")


def _pascal(s):
    return "".join(p.capitalize() for p in s.split("_"))


def active_categories(t):
    return [c for c in t["categories"] if c["include"]]


def group_categories(t, gid):
    return [c for c in active_categories(t) if c["call_group"] == gid]


def first_group_id(t):
    return next(iter(t["groups"]))


def applies(c, study):
    return c["applies_to"] == "All" or study is None or study in c["applies_to"]


def build_model(t, gid):
    first = gid == first_group_id(t)
    top = {}
    for c in group_categories(t, gid):
        entry_fields = {}
        for f in c["fields"]:
            typ = Literal[tuple(f["values"])] if f["kind"] == "pick-list" else str
            entry_fields[f["name"]] = (typ, Field(description=f["description"] or f["name"]))
        entry_fields["evidence"] = (Evidence, Field(description="Verbatim quote, section and page supporting this entry."))
        Entry = create_model(f"{_pascal(c['id'])}Entry", **entry_fields)
        Cat = create_model(
            f"{_pascal(c['id'])}Result",
            status=(Literal[STATUSES], Field(description="Found, Not found, or Not applicable (see the category rules).")),
            confidence=(Literal[CONFIDENCES], Field(description="High, Medium or Low (see the Confidence rule).")),
            rationale=(str, Field(description="One short plain-language sentence on how you read this category and why you chose the status. Required unless Found with High confidence.")),
            entries=(list[Entry], Field(description=(
                "The entries. Exactly one for a 'Pick one' category when Found; empty when the status is Not found or Not applicable."))),
        )
        top[c["id"]] = (Cat, Field(description=f"{c['label']}: {c['counts']}"))
    if first:
        top["all_authors_from_title_page"] = (list[str], Field(description=(
            "Every author name in the order printed on the title page (names only, no affiliations).")))
    top["extraction_notes"] = (str, Field(description=(
        "Short note for the human reviewer about anything unusual; empty string if nothing to note.")))
    return create_model(f"PaperExtractionGroup{gid}", **top)


def build_system_prompt(t, gid):
    rules = dict(t["global_rules"])
    g = t["groups"][gid]
    first = gid == first_group_id(t)
    out = [rules["Context"], "", "GENERAL RULES"]
    for name, text in t["global_rules"]:
        if name != "Context":
            out.append(f"- {name}: {text}")
    out += ["", f"THIS REQUEST: {g['label']}. Other categories are handled in separate requests.",
            ("Decide the study type first: it determines which other categories apply." if first else
             "The user message states the study type already determined and lists any categories of this request that do "
             "not apply to it; return status 'Not applicable' and no entries for those.")]
    out.append("Fill in every category below, always with a status.")
    for c in group_categories(t, gid):
        applies_txt = "all study types" if c["applies_to"] == "All" else "; ".join(c["applies_to"])
        out += ["", f"### {c['id']} ({c['label']}); answer type: {c['answer_type']}",
                f"Applies to study types: {applies_txt}",
                f"What counts: {c['counts']}",
                f"What does NOT count: {c['not_counts']}",
                f"How to interpret it: {c['interpret']}",
                f"Not applicable means: {c['na_means']}",
                f"Not found means: {c['nf_means']}",
                f"Usually found in: {c['found_in']}",
                f"Confidence: {c['confidence_guide']}"]
    out += ["", ("Also fill all_authors_from_title_page and extraction_notes. " if first else "Also fill extraction_notes. ")
            + "Return only the structured result."]
    return "\n".join(out)


@dataclass
class TemplateContext:
    template: dict
    models: dict
    prompts: dict

    @property
    def version(self):
        return self.template["version"]


def load_context(path=None):
    t = load_template(path)
    gids = [g for g in t["groups"] if group_categories(t, g)]
    return TemplateContext(t, {g: build_model(t, g) for g in gids}, {g: build_system_prompt(t, g) for g in gids})


def _evidence_check(entries):
    states = [e["evidence"].get("verification") for e in entries]
    if not states:
        return "n/a"
    if any(s in ("not_found", "too_short") for s in states):
        return "unverified"
    if any(s == "approximate" for s in states):
        return "approximate"
    return "verified"


def study_type_of(data):
    entries = (data.get("study_type") or {}).get("entries") or []
    return entries[0].get("value") if entries else None


def finalize_result(t, data):
    """Guarantee no blank category and compute the final confidence. `data` is the merged model output after quote
    verification (categories whose request was skipped are simply absent). Adds to every category:
    confidence_final, evidence_check, rule_based, flags."""
    study = study_type_of(data)
    for c in active_categories(t):
        flags = []
        rule_based = False
        cat = data.get(c["id"])
        applicable = applies(c, study)
        if cat is None:
            if applicable:
                flags.append("missing from the model output")
                cat = {"status": "Not found", "confidence": "Low", "entries": [],
                       "rationale": "Not found: this category was not returned by the extraction."}
            else:
                cat = {"status": "Not applicable", "confidence": "High", "entries": [], "rationale": ""}
        if c["answer_type"] in SINGLE_PICK and len(cat["entries"]) > 1:
            flags.append(f"AI returned {len(cat['entries'])} entries for a pick-one category; kept the first")
            cat["entries"] = cat["entries"][:1]
        if not applicable:
            if cat["status"] != "Not applicable" or cat["entries"]:
                flags.append(f"AI said '{cat['status']}' with {len(cat['entries'])} entries, but study type '{study}' "
                             f"is outside this category's scope; set to Not applicable")
            cat["status"], cat["entries"], rule_based = "Not applicable", [], True
            cat["rationale"] = f"Not applicable: the study type is '{study}', which this category does not cover."
            cat["confidence"] = "High"
        else:
            if cat["status"] == "Not applicable":
                flags.append("AI said Not applicable, but this category applies to this study type; set to Not found")
                cat["status"] = "Not found"
            if cat["status"] == "Found" and not cat["entries"]:
                flags.append("AI said Found but returned no entries; set to Not found")
                cat["status"] = "Not found"
            if cat["status"] == "Not found" and cat["entries"]:
                flags.append("AI said Not found but returned entries; set to Found")
                cat["status"] = "Found"
            if cat["status"] == "Not found" and not (cat.get("rationale") or "").strip():
                cat["rationale"] = "Not found: the paper does not state it (no reason was given)."
        ev = _evidence_check(cat["entries"])
        conf = cat["confidence"]
        if ev == "unverified":
            conf = "Low"
        elif ev == "approximate" and RANK[conf] > RANK["Medium"]:
            conf = "Medium"
        cat.update(confidence_final=conf, evidence_check=ev, rule_based=rule_based, flags=flags)
        data[c["id"]] = cat
    return data
