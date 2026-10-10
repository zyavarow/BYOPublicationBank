"""AI proposal for an organization's extraction schema: Claude reads the onboarding transcripts and notes (plus a few abstracts) and drafts
changes to the standard template. A person reviews every item before anything is applied; nothing here edits the overlay by itself."""
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from pubbank import apikey, orgs
from pubbank.extraction import MODEL, PRICES, cost_usd
from pubbank.overlay_edit import Overlay, join
from pubbank.schema_edit import LOCKED, add_field, load_template, overlay_path

MAX_UPLOAD = 5 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 90_000
ALLOWED = {".txt", ".md", ".docx", ".pdf"}


# ---------------------------------------------------------------------------------------------- reading uploaded files
def extract_text(filename, data):
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError("Upload .txt, .md, .docx or .pdf files.")
    if ext in (".txt", ".md"):
        return data.decode("utf-8-sig", errors="replace")
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                root = ET.fromstring(z.read("word/document.xml"))
        except Exception as e:
            raise ValueError("That Word file could not be read.") from e
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paras = ["".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p")]
        return "\n".join(paras)
    from pypdf import PdfReader
    try:
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
    except Exception as e:
        raise ValueError("That PDF could not be read.") from e


def uploads_dir(folder):
    d = orgs.ORGS_DIR / folder / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_stem(name):
    return re.sub(r"[^\w .()-]+", "_", Path(name.replace("\\", "/")).stem).strip(" .")[:80] or "document"


def save_upload(folder, filename, data):
    if len(data) > MAX_UPLOAD:
        raise ValueError("That file is larger than 5 MB.")
    text = extract_text(filename, data)
    if len(text.strip()) < 40:
        raise ValueError("No readable text was found in that file.")
    stem = safe_stem(filename)
    (uploads_dir(folder) / f"{stem}.txt").write_text(text, encoding="utf-8")
    return stem, len(text)


def list_uploads(folder):
    d = orgs.ORGS_DIR / folder / "uploads"
    if not d.exists():
        return []
    return [{"name": p.stem, "chars": p.stat().st_size} for p in sorted(d.glob("*.txt"))]


def delete_upload(folder, name):
    (uploads_dir(folder) / f"{safe_stem(name)}.txt").unlink(missing_ok=True)


def notes_path(folder):
    return orgs.ORGS_DIR / folder / "uploads" / "_notes.md"


def get_notes(folder):
    p = notes_path(folder)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def set_notes(folder, text):
    uploads_dir(folder)
    notes_path(folder).write_text(text.strip()[:20_000], encoding="utf-8")


# ---------------------------------------------------------------------------------------------- the proposal
GENE_ROLES = ["disease gene", "cofactor or partner", "gene of a related disease", "model-organism version", "other"]


class GeneItem(BaseModel):
    symbol: str = Field(description="Official gene symbol.")
    role: Literal["disease gene", "cofactor or partner", "gene of a related disease", "model-organism version", "other"] = Field(
        description="What this gene is to the disease: the disease gene itself, a protein that works with it, the gene of a related disease, or the name used in a model organism.")
    aliases: list[str] = Field(description="Other names for the gene or protein, including orthologs. Empty list if none.")
    note: str = Field(description="One short note, for example what it is. Empty string if nothing to add.")


class CoreChange(BaseModel):
    id: str = Field(description="The id of an existing field in the standard template (for example 'techniques').")
    action: Literal["keep", "edit", "remove"] = Field(description="keep: no change. edit: add options or a sentence. remove: not useful for this organization.")
    why: str = Field(description="Plain-language reason, citing what the organization said or what the abstracts show.")
    add_options: list[str] = Field(description="New allowed values to add to this field's list. Empty list if none.")
    counts_addition: str = Field(description="One sentence to add to 'what counts' for this field. Empty string if none.")


class NewField(BaseModel):
    label: str = Field(description="Short field name a patient organization would recognize.")
    kind: Literal["pick_one", "pick_many", "text_list"] = Field(description="pick_one: exactly one option. pick_many: several options may apply. text_list: free-text items.")
    options: list[str] = Field(description="Concrete options for pick_one / pick_many (empty list for text_list).")
    counts: str = Field(description="What the AI should record, written as an instruction.")
    not_counts: str = Field(description="What it should ignore.")
    why: str = Field(description="Plain-language reason, citing what the organization said.")


class Proposal(BaseModel):
    summary: str = Field(description="Three or four plain-language sentences: what this organization needs most from its library.")
    disease: str = Field(description="One paragraph naming the disease, its acronym(s) in brackets, the gene(s) and aliases, the topics that are in scope, and what is NOT the subject.")
    topic_terms: list[str] = Field(description="Words that a paper about this topic would contain: names, acronyms, gene symbols and aliases.")
    genes: list[GeneItem] = Field(description="Genes and proteins of this disease and the ones people confuse with them.")
    gene_focus_options: list[str] = Field(description="4 to 6 options for 'which gene or protein is the paper about', ending with 'Other/none'.")
    core_changes: list[CoreChange] = Field(description="One entry for each standard field.")
    new_fields: list[NewField] = Field(description="At most five extra fields this organization asked for or clearly needs. Empty list if none.")


SYSTEM = """You help a patient organization set up a searchable library of scientific papers. The system reads each paper and fills in a standard set of fields (study type, gene focus, variants, models, techniques, samples, reagents, corresponding authors, treatments, clinical features). Your job is to propose how to tailor that standard template to ONE organization, using only what its own words and its papers show.

Rules:
- Base every suggestion on the transcripts, notes or abstracts you are given. In each 'why', say where the idea came from in plain words ("In the interview, they said...", "The notes say...", "The abstracts show...") and say what the change is for. Never refer to "the owner" or to a role the reader has not been told about. Never invent needs.
- Never change the options of the 'study_type' field: they are the same for every organization. Keep standard fields unless the organization plainly does not need them. Prefer adding concrete options to a field over creating a new field.
- Propose a new field only when the organization asked for it or it is clearly central to what they want to compare across papers. At most five. When the person's feedback asks for a new field, always include it in new_fields, even if an existing field could partly cover it: their instruction outranks the preference for adding options.
- Options must be short, concrete and non-overlapping, using the vocabulary the organization and the abstracts use.
- Gene names: list the disease gene, the proteins that work with it (cofactors and partners), genes of related diseases, and the different names used for the same gene in other species. Give each a role.
- Write for a non-technical reader. If the material is thin on something, say what is missing in the summary instead of guessing.
- For core_changes, return one entry for every standard field listed, with action 'keep' when nothing should change."""


def template_summary(folder):
    t = load_template(overlay_path(folder))
    custom = Overlay(overlay_path(folder)).custom_ids()
    lines = []
    for c in t["categories"]:
        opt = next((f for f in c["fields"] if f["kind"] == "pick-list"), None)
        tag = " [ALREADY ADDED for this organization, not a standard field]" if c["id"] in custom else ""
        lines.append(f"- {c['id']} ({c['label']}){tag}: {c['counts'].split('. ')[0]}" + (f" Options: {join(opt['values'][:14])}." if opt else ""))
    return "\n".join(lines)


def build_input(conn, org_id, folder, cfg, previous=None, feedback=""):
    docs = []
    for u in list_uploads(folder):
        docs.append(f"### Transcript or document: {u['name']}\n" + (uploads_dir(folder) / f"{u['name']}.txt").read_text(encoding="utf-8"))
    transcripts = "\n\n".join(docs)[:MAX_TRANSCRIPT_CHARS]
    notes = get_notes(folder)
    rows = conn.execute(
        """SELECT coalesce(title, extracted_title, filename), left(coalesce(abstract, ''), 700) FROM papers
           WHERE org_id = %s AND in_bank AND coalesce(abstract, '') <> '' ORDER BY md5(id::text) LIMIT 12""", (org_id,)).fetchall()
    abstracts = "\n".join(f"- {t}: {a}" for t, a in rows)
    parts = [f"ORGANIZATION: {cfg['name']}. {cfg.get('description', '')}", "STANDARD TEMPLATE FIELDS:\n" + template_summary(folder)]
    if transcripts:
        parts.append("WHAT THE ORGANIZATION SAID (transcripts and documents):\n" + transcripts)
    if notes:
        parts.append("EXTRA NOTES FROM THE PERSON SETTING THIS UP:\n" + notes)
    if abstracts:
        parts.append(f"A SAMPLE OF {len(rows)} PAPERS ALREADY IN THEIR LIBRARY (title: abstract start):\n" + abstracts)
    if previous and feedback.strip():
        keep = {k: v for k, v in previous.items() if not k.startswith("_")}
        parts.append("YOUR PREVIOUS PROPOSAL, AS THE PERSON HAS EDITED IT (keep their edits unless their feedback says otherwise):\n"
                     + json.dumps(keep, ensure_ascii=False))
        parts.append("THE PERSON'S FEEDBACK ON THAT PROPOSAL (follow it):\n" + feedback.strip()[:4000])
    return "\n\n".join(parts)


def estimate(conn, org_id, folder, cfg, revising=False):
    text = build_input(conn, org_id, folder, cfg) + ("x" * 9000 if revising else "")
    tokens_in = len(SYSTEM) / 3.8 + len(text) / 3.8 + 1200       # plus the response format
    tokens_out = 3500
    cost = (tokens_in * PRICES["input"] + tokens_out * PRICES["output"]) / 1e6
    return {"input_tokens": int(tokens_in), "expected": round(cost, 2), "high": round(cost * 1.5 + 0.05, 2), "has_material": bool(list_uploads(folder) or get_notes(folder))}


def proposal_path(folder):
    return orgs.ORGS_DIR / folder / "proposal.json"


def load_proposal(folder):
    p = proposal_path(folder)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    for g in data.get("genes", []):          # proposals saved before gene roles existed
        g.setdefault("role", "other")
    return data


def generate(conn, org_id, folder, cfg, previous=None, feedback=""):
    """One request to Claude; saves and returns the proposal plus what it cost. With `previous` and `feedback` it revises."""
    client = apikey.client(folder)
    text = build_input(conn, org_id, folder, cfg, previous, feedback)
    resp = client.messages.parse(
        model=MODEL, max_tokens=12000, system=SYSTEM, messages=[{"role": "user", "content": text}], output_format=Proposal,
        output_config={"effort": "medium"})
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("The model could not produce a proposal from this material.")
    u = resp.usage
    cost = cost_usd({"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0, "cache_write_tokens": 0, "cache_read_tokens": 0})
    data = resp.parsed_output.model_dump()
    data["_meta"] = {"cost": round(cost, 4), "documents": [u_["name"] for u_ in list_uploads(folder)], "model": MODEL}
    proposal_path(folder).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data, cost


# ---------------------------------------------------------------------------------------------- applying accepted items
def save_edited(folder, edited):
    """Store the proposal as the person edited it (checked against the proposal format) so edits survive a revision."""
    old = load_proposal(folder) or {}
    for g in edited.get("genes", []):
        g.setdefault("role", "other")
    try:
        clean = Proposal.model_validate({k: v for k, v in edited.items() if not k.startswith("_")}).model_dump()
    except Exception as e:
        raise ValueError("The edited proposal has a problem: " + str(e)[:200]) from e
    clean["_meta"] = old.get("_meta", {})
    proposal_path(folder).write_text(json.dumps(clean, indent=1, ensure_ascii=False), encoding="utf-8")
    return clean


def apply(folder, accept, edited=None):
    """accept: {'disease': bool, 'terms': bool, 'genes': bool, 'gene_focus': bool, 'core': [ids], 'new': [indexes]}. `edited` is the proposal
    after the person's edits. Returns a list of what changed."""
    prop = save_edited(folder, edited) if edited else load_proposal(folder)
    if not prop:
        raise ValueError("There is no proposal to apply.")
    path = overlay_path(folder)
    done = []
    ov = Overlay(path)
    if accept.get("disease") and prop["disease"].strip():
        ov.set_context("disease", prop["disease"].strip())
        done.append("Disease description")
    if accept.get("genes"):
        ws = ov.wb["Genes"]
        for r in reversed(list(ws.iter_rows(min_row=2))):
            ws.delete_rows(r[0].row)
        for g in prop["genes"]:
            ws.append([g["symbol"], join(g["aliases"]), f"[{g['role']}] {g['note']}".strip()])
        done.append(f"{len(prop['genes'])} genes and aliases")
    if accept.get("gene_focus") and len(prop["gene_focus_options"]) >= 2:
        ov.set_options("gene_focus", "value", [o.strip() for o in prop["gene_focus_options"] if o.strip()])
        done.append("Gene focus options")
    t = load_template(path)
    by = {c["id"]: c for c in t["categories"]}
    custom = ov.custom_ids()
    for ch in prop["core_changes"]:
        if ch["id"] not in accept.get("core", []) or ch["id"] not in by or ch["action"] == "keep":
            continue
        cur = by[ch["id"]]
        is_custom = ch["id"] in custom        # fields this organization added earlier are edited in their own rows
        if ch["action"] == "remove" and ch["id"] not in LOCKED:
            ov.remove_custom(ch["id"]) if is_custom else ov.set_setting(ch["id"], "include", "No")
            done.append(f"Removed {cur['label']}")
        elif ch["action"] == "edit":
            opt = next((f for f in cur["fields"] if f["kind"] == "pick-list"), None)
            adds = [o.strip() for o in ch["add_options"] if o.strip() and o.strip() not in (opt["values"] if opt else [])] if ch["id"] != "study_type" else []
            if opt and adds:
                values = [v for v in opt["values"] if v != "Other"] + adds + (["Other"] if "Other" in opt["values"] else [])
                ov.set_custom_options(ch["id"], opt["name"], values) if is_custom else ov.set_options(ch["id"], opt["name"], values)
            if ch["counts_addition"].strip():
                if is_custom:
                    ov.set_custom_text(ch["id"], "counts", (cur["counts"].rstrip() + " " + ch["counts_addition"].strip()).strip())
                else:
                    ov.set_setting(ch["id"], "counts", ch["counts_addition"].strip(), "append")
            done.append(f"Updated {cur['label']}")
    ov.save()
    if accept.get("terms"):
        cfg = {k: orgs.load(folder)[k] for k in orgs.DEFAULTS}
        cfg["topic_terms"] = "; ".join(dict.fromkeys(t_.strip() for t_ in prop["topic_terms"] if t_.strip()))
        orgs.save(folder, cfg)
        done.append("Topic words for the off-topic check")
    for i in accept.get("new", []):
        if 0 <= i < len(prop["new_fields"]):
            f = prop["new_fields"][i]
            add_field(folder, f["label"], f["kind"], f["options"], f["counts"], f["not_counts"], False)
            done.append(f"Added field “{f['label']}”")
    return done


def run_job(jid, org_id, folder, cfg, feedback=""):
    """Background job wrapper used by the web app."""
    from pubbank import sources
    from pubbank.db import connect
    conn = connect()
    try:
        sources._set(jid, stage="Reading the transcripts, notes and abstracts", done=0, total=1)
        data, cost = generate(conn, org_id, folder, cfg, load_proposal(folder) if feedback.strip() else None, feedback)
        sources._set(jid, done=1, finished=True, stage="Done", result={"kind": "schema", "cost": round(cost, 3),
                                                                      "new_fields": len(data["new_fields"]), "changes": sum(1 for c in data["core_changes"] if c["action"] != "keep")})
    except Exception as e:
        sources._set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()
