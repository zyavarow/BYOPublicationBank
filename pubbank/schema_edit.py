"""What the Extraction Schema screen shows and does: the organization's template as field cards, and the edits made to them."""
import re

from pubbank import orgs
from pubbank.overlay_edit import CAT_KEYS, Overlay, join, new_overlay, slug_id
from pubbank.template import load_template

TEXT_KEYS = ["counts", "not_counts", "interpret", "na_means", "nf_means"]
LOCKED = {"study_type", "gene_focus"}
OPTIONS_LOCKED = {"study_type"}          # study types stay identical everywhere so papers can always be compared by design          # the template cannot work without these
KIND_LABELS = {"pick_one": "Pick one", "pick_many": "Pick several", "text_list": "Free text"}


def overlay_path(folder):
    p = orgs.ORGS_DIR / folder / "overlay.xlsx"
    return p if p.exists() else None


def create_overlay(folder, disease):
    new_overlay(orgs.ORGS_DIR / folder / "overlay.xlsx", disease)


def _first_sentence(text, limit=190):
    s = re.split(r"(?<=[.!?])\s", (text or "").strip(), maxsplit=1)[0]
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _options_field(c):
    return next((f for f in c["fields"] if f["kind"] == "pick-list"), None)


def view(folder):
    """The template as cards for the screen. Returns None if the organization has no overlay yet."""
    path = overlay_path(folder)
    if path is None:
        return None
    t = load_template(path)
    ov = Overlay(path)
    custom = ov.custom_ids()
    changed = {}              # what the organization changed in each standard field
    for r in ov._rows("Category settings"):
        changed.setdefault(str(r[0].value), set()).add("wording" if str(r[1].value) not in ("include", "filter_chip") else "settings")
    for r in ov._rows("Entry field changes"):
        changed.setdefault(str(r[0].value), set()).add("options" if str(r[2].value).lower() in ("add", "remove", "replace") else "wording")
    cards = []
    for c in t["categories"]:
        opt = _options_field(c)
        if c["answer_type"].lower().startswith("pick one"):
            chip = "Pick one"
        elif opt:
            chip = "Pick several"
        else:
            chip = "Free text"
        cards.append({
            "id": c["id"], "label": c["label"], "include": c["include"], "chip": chip, "custom": c["id"] in custom, "locked": c["id"] in LOCKED,
            "why": _first_sentence(c["counts"]), "options": opt["values"] if opt else [], "options_field": opt["name"] if opt else "",
            "example": (c["fields"][0]["description"] if c["fields"] else ""), "filter_chip": bool(c["filter_chip"]), "options_locked": c["id"] == "study_type",
            "text": {k: c[k] for k in TEXT_KEYS}, "kind": "custom" if c["id"] in custom else ("customized" if changed.get(c["id"]) else "standard"),
            "changes": sorted(changed.get(c["id"], [])), "applies_to": "all papers" if c["applies_to"] == "All" else "papers reporting the paper's own work",
        })
    return {"version": t["version"], "disease": (t.get("context") or {}).get("disease", ""), "cards": cards,
            "genes": t.get("genes", []), "groups": len(t["groups"])}


def save_category(folder, cid, data):
    """Apply edits to one card. `data` may hold label, counts, not_counts, interpret, na_means, nf_means, filter_chip (bool), options (list)."""
    path = overlay_path(folder)
    t = load_template(path)
    cur = next((c for c in t["categories"] if c["id"] == cid), None)
    if cur is None:
        raise ValueError("Unknown field.")
    ov = Overlay(path)
    is_custom = cid in ov.custom_ids()
    for key in ["label"] + TEXT_KEYS:
        if key in data and data[key].strip() != cur[key].strip():
            text = data[key].strip()
            if not text:
                raise ValueError("Please leave no box empty.")
            ov.set_custom_text(cid, key, text) if is_custom else ov.set_setting(cid, key, text)
    if "filter_chip" in data and bool(data["filter_chip"]) != bool(cur["filter_chip"]):
        flag = "Yes" if data["filter_chip"] else "No"
        ov.set_custom_text(cid, "filter_chip", flag) if is_custom else ov.set_setting(cid, "filter_chip", flag)
    opt = _options_field(cur)
    if "options" in data and opt is not None and cid not in OPTIONS_LOCKED:
        values = [v.strip() for v in data["options"] if v.strip()]
        if not values:
            raise ValueError("A list needs at least one option.")
        if values != opt["values"]:
            ov.set_custom_options(cid, opt["name"], values) if is_custom else ov.set_options(cid, opt["name"], values)
    ov.save()


def set_included(folder, cid, include):
    path = overlay_path(folder)
    ov = Overlay(path)
    if cid in ov.custom_ids():
        if include:
            raise ValueError("Add the field again with the form below.")
        ov.remove_custom(cid)
    else:
        if not include and cid in LOCKED:
            raise ValueError("This field is needed by every template and cannot be removed.")
        ov.set_setting(cid, "include", "Yes" if include else "No")
    ov.save()


def add_field(folder, label, kind, options, counts, not_counts, all_papers):
    """Add a custom field. kind is pick_one / pick_many / text_list."""
    path = overlay_path(folder)
    t = load_template(path)
    label = label.strip()
    if not label or not counts.strip():
        raise ValueError("Give the field a name and say what it should capture.")
    if kind not in KIND_LABELS:
        raise ValueError("Choose what kind of answer this is.")
    options = [o.strip() for o in options if o.strip()]
    if kind != "text_list" and len(options) < 2:
        raise ValueError("A list field needs at least two options (one per line).")
    cid = slug_id(label, {c["id"] for c in t["categories"]})
    primary = next((c["applies_to"] for c in t["categories"] if c["id"] == "model_systems"), "All")
    applies = "All" if all_papers or primary == "All" else join(primary)
    values = [o for o in options if o.lower() not in ("other", "none")] + (["Other"] if kind != "text_list" else [])
    if kind == "pick_one":
        fields = [dict(name="value", kind="pick-list", values=[o for o in options], description=f"The {label.lower()}.")]
        interpret = "Choose the one option that best fits this paper's own work. If none fits, say so in the rationale."
        answer = "Pick one"
    elif kind == "pick_many":
        fields = [dict(name="value", kind="pick-list", values=values, description=f"The {label.lower()}."),
                  dict(name="detail", kind="text", values=[], description="Specifics; required if 'Other'; otherwise optional (empty string if none).")]
        interpret = "Use the listed option that fits; name anything else under 'Other' with detail. One entry per distinct item."
        answer = "Entries"
    else:
        fields = [dict(name="value", kind="text", values=[], description=f"The {label.lower()}, as the paper words it."),
                  dict(name="detail", kind="text", values=[], description="Optional extra detail; empty string if none.")]
        interpret = "Record each distinct item as its own entry, in the paper's own words, with a short detail if useful."
        answer = "Entries"
    cat = dict(id=cid, label=label, include="Yes", answer_type=answer, applies_to=applies, counts=counts.strip(),
               not_counts=not_counts.strip() or "Items only cited from other studies, mentioned as background, or discussed in a review of other work.",
               interpret=interpret, na_means="Reviews, guidelines and off-topic papers.",
               nf_means="A paper reporting its own work that does not state this.", found_in="Abstract; Methods; Results",
               confidence_guide="High when stated explicitly; Medium when inferred.", filter_chip="Yes", call_group="")
    ov = Overlay(path)
    ov.add_custom(cat, fields)
    ov.save()
    return cid


# Search depth choices shown on the screen. Prices per million tokens (input / output) come from Anthropic's price list; the token counts
# are planning assumptions, not measurements, because the search layer is not built yet.
DEPTHS = [
    ("quick", "Quick", "One search, about 8 passages, a small fast model.", 6800, 500, 1.0, 5.0),
    ("standard", "Standard", "Rewrites the question, reads about 20 passages, ranks them, then answers.", 17000, 900, 2.0, 10.0),
    ("deep", "Deep", "Breaks the question into parts, searches several times, reads whole sections, and notes disagreements.", 126000, 7000, 2.0, 10.0),
]


def depth_options():
    out = []
    for key, label, text, tin, tout, pin, pout in DEPTHS:
        cost = (tin * pin + tout * pout) / 1e6
        out.append({"key": key, "label": label, "text": text, "tokens": tin + tout, "cost": cost})
    return out
