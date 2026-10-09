"""Writes the SEED of the extraction template to orgs/<org>/extraction_template.xlsx.
Run once to start; after that the Excel file itself is the source of truth (edit it directly).
Re-running overwrites the file, so it refuses to if the file already exists unless --force is given.
  .venv\\Scripts\\python.exe scripts\\make_template.py --org dada2
"""
import argparse
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parent.parent

STUDY_TYPES = [
    "Case report/series", "Cohort/registry", "Clinical trial or treatment study", "Animal study",
    "In vitro (cells/biochemistry)", "Computational/structural", "Review", "Guideline/consensus", "Other/off-topic",
]
WITH_OWN_WORK = STUDY_TYPES[:6]
CLINICAL = STUDY_TYPES[:3]


def v(*values):
    return "; ".join(values)


GLOBAL_RULES = [
    ("Context", "You extract structured data from one scientific paper for a research database about deficiency of adenosine deaminase 2 (DADA2, also called ADA2 deficiency; the gene is ADA2, formerly CECR1). The database serves a patient advocacy organization, so accuracy matters more than completeness. Some papers in the library are off-topic or only loosely related."),
    ("Source of truth", "Use only what the paper text says. Never use outside knowledge to fill a category. The text was machine-extracted from a PDF, so expect artifacts (broken hyphenation, odd spacing, merged columns); the reference list has been removed. Each section starts with a marker such as '## [p.3] Methods' giving the page on which it starts."),
    ("Status: Found", "The paper states it. Return the entries, each with evidence."),
    ("Status: Not found", "The category applies to this kind of paper, but the paper does not state it (or the relevant text looks missing or garbled). Return no entries and give a one-sentence rationale saying what you looked at."),
    ("Status: Not applicable", "The category does not apply to this kind of paper (see 'Applies to study types'). Return no entries and a one-sentence rationale. Never leave a category empty without one of the three statuses."),
    ("Evidence", "Every entry needs evidence: a quote copied EXACTLY, character for character, from the paper text (at most about 300 characters; the single most informative phrase), the heading of the section it is in, and the page number from that section's marker (0 if unknown). Never paraphrase, never fix typos, never join text from different places."),
    ("This paper's own work", "Record only what THIS paper itself did, studied or observed. Do not record items the paper merely cites from other studies, mentions as background, or discusses in a review of other work."),
    ("One entry per item", "One entry per distinct item, not one per mention. If an item appears several times, use the clearest single quote."),
    ("Pick-lists", "For categories with a list of allowed values, use the listed value that fits. Use 'Other' only when nothing fits, and then always name the specific item in the detail field."),
    ("Confidence", "High = stated explicitly and unambiguously. Medium = you had to interpret, combine information, or the statement is partial. Low = weak, ambiguous, or the relevant text may be incomplete. For Not found, High means you checked the relevant sections and there is nothing; Medium or Low means the relevant text may be missing or hard to read."),
    ("Rationale", "One short, plain-language sentence a patient-organization reviewer can understand: how you read the category for this paper and why you chose the status. Required whenever the status is not Found; optional (may be an empty string) when Found with High confidence."),
    ("Reviewer note", "Use extraction_notes for anything unusual a human reviewer should know (text looks truncated, only an abstract is available, the paper is off-topic, ambiguous naming). Empty string if nothing to note."),
]

HEADER_CATS = ["ID", "Label", "Include? (Yes / No)", "Answer type", "Applies to study types", "What counts",
               "What does NOT count", "How to interpret it", "'Not applicable' means", "'Not found' means",
               "Usually found in", "Confidence guide", "Show as a search filter? (Yes / No)"]

CATS = [
    ["study_type", "Study type", "Yes", "Pick one", "All",
     "What this paper itself did: its primary design.",
     "Studies the paper only cites or reviews.",
     "If a paper does several things, pick the primary one (the one the title and abstract emphasize). Reviews, guidelines and consensus statements summarize other work. Use 'Other/off-topic' when the paper is not about ADA or ADA2 biology or medicine (for example DNA-sequencing software, or an unrelated disease); say so in the rationale.",
     "Never used for this category.", "Almost never. If the design truly cannot be determined, pick 'Other/off-topic' and explain.",
     "Title; Abstract; Methods", "High when the abstract or methods state the design plainly; Medium when you inferred it.", "Yes"],
    ["gene_focus", "Gene / protein focus", "Yes", "Pick one", "All",
     "Which gene or enzyme the paper is about: ADA2 (CECR1), the gene mutated in DADA2; ADA1 (ADA), a different enzyme (ADA-SCID, PEG-ADA); both; or neither.",
     "Genes that are only mentioned in passing.",
     "ADA and ADA2 are different enzymes: never assume 'ADA' means ADA2. Use 'Both' only when the paper substantially studies both. A general guideline or review that mentions DADA2 only briefly is 'Other/none'.",
     "Never used for this category.", "Almost never.", "Title; Abstract; Introduction",
     "High when the title or abstract names the enzyme; Medium when only inferable from context.", "Yes"],
    ["mutations", "Mutations studied", "Yes", "Entries", v(*WITH_OWN_WORK),
     "Gene variants found in this paper's own patients or families, or engineered or tested in its own experiments.",
     "Variants cited from other studies, listed as background, or in tables of previously published variants.",
     "Write each variant in one-letter protein notation (p.R169Q). Normalize 'Arg169Gln', 'p.Arg169Gln' and 'R169Q' to the same form, and keep the paper's own wording in 'as written'. If only a DNA-level change is given, use that (c.506G>A). Engineered variants count; say 'engineered' in the rationale.",
     "Reviews, guidelines and off-topic papers (they discuss variants only as background).",
     "A patient or experimental paper that names no variants (for example a treatment study that does not report genotypes).",
     "Abstract; Results; Methods", "High when the variant is written out for this paper's own subjects; Medium when it is only implied.", "Yes"],
    ["model_systems", "Model system", "Yes", "Entries", v(*WITH_OWN_WORK),
     "What this paper's own experiments or subjects were: human patients, donors, primary cells, cell lines, mice, zebrafish, recombinant protein, and so on.",
     "Models used in studies the paper only cites.",
     "A clinical paper's model system is 'Human patients'. Put cell line names, mouse strains and similar specifics in the detail field.",
     "Reviews, guidelines and off-topic papers.", "Almost never for a paper with its own work; if it happens, say what was unclear.",
     "Methods; Abstract", "High when stated in Methods; Medium when inferred from Results.", "Yes"],
    ["techniques", "Techniques", "Yes", "Entries", v(*WITH_OWN_WORK),
     "Laboratory, clinical or computational methods this paper itself performed.",
     "Methods the paper only cites, or methods used by other studies it reviews.",
     "Use the listed technique name that fits (for example 'ADA2 enzyme activity assay', 'Western blot'). Name anything else under 'Other' with detail.",
     "Reviews, guidelines and off-topic papers.", "A paper with own work whose methods are not described in the text.",
     "Methods; Results", "High when named in Methods; Medium when inferred from Results.", "Yes"],
    ["sample_sources", "Sample source", "Yes", "Entries", v(*STUDY_TYPES[:5]),
     "Where the samples analysed in this paper came from: serum, plasma, PBMC, bone marrow, skin biopsy, fibroblasts, CSF, urine, and so on.",
     "Sample types used in cited work. Purely computational data.",
     "Record the sample type, not the individual patients. Use the detail field for context such as 'patient vs. control'.",
     "Reviews, guidelines, computational papers and off-topic papers.", "A paper with own samples that never says what they were.",
     "Methods; Results", "High when stated; Medium when inferred.", "Yes"],
    ["reagents", "Reagents (incl. catalog #)", "Yes", "Entries", v(*STUDY_TYPES[:5]),
     "Antibodies, kits, recombinant proteins, and drugs used in this paper's own experiments, with vendor and catalog number when printed.",
     "Reagents mentioned in cited work, or drugs given to patients as treatment (those go under Treatments).",
     "Copy catalog numbers exactly as printed (for example 'ab12345' or '#4691'). Leave the catalog number empty when none is printed; never guess one. Reagents matter because antibody differences can explain contradictory results between papers.",
     "Reviews, guidelines, computational papers and off-topic papers.",
     "A paper with its own lab or clinical assays that does not name its reagents or kits (common in clinical case reports).",
     "Methods", "High when the reagent is named with vendor/catalog; Medium when only a generic name is given.", "Yes"],
    ["corresponding_authors", "Corresponding author(s)", "Yes", "Entries", "All",
     "Author(s) the paper itself labels as corresponding author, usually on the first page or in a footnote.",
     "The first or last author, unless the paper labels them as corresponding. Email addresses.",
     "Look for 'Correspondence to', 'Corresponding author', 'Address correspondence to', 'To whom correspondence should be addressed', or an asterisk/envelope footnote. There can be several: list every one. Give the institution if stated. If the paper labels none, do not guess.",
     "Never used for this category.",
     "The paper labels no corresponding author (common when only email addresses appear in a footnote).",
     "Title page; footnotes; end of article", "High when explicitly labelled; Medium when inferred from an email footnote.", "No"],
    ["treatments", "Treatments / interventions", "Yes", "Entries", v(*WITH_OWN_WORK),
     "Treatments given to this paper's own patients, or tested in its own experiments or animal models.",
     "Treatments only mentioned in the background, in a review of the literature, or recommended by cited guidelines.",
     "Use the listed treatment class and name the specific drug or regimen in the detail field (for example 'adalimumab').",
     "Reviews, guidelines and off-topic papers; also computational/structural papers.",
     "A paper with own patients or experiments that reports no treatment.",
     "Abstract; Results; Methods", "High when the treatment and its use are stated; Medium when only implied.", "Yes"],
    ["clinical_features", "Clinical features reported", "Yes", "Entries", v(*CLINICAL),
     "Clinical features observed in this paper's own patients (vasculitis, stroke, cytopenia, immunodeficiency, livedo, fever, and so on).",
     "Features described in other studies, or features explicitly absent in this paper's patients.",
     "A feature that the paper says a patient did NOT have is not a finding. Put anything not in the list under 'Other' with detail.",
     "Reviews, guidelines, laboratory-only, computational and off-topic papers (no patients described).",
     "A clinical paper that does not describe the patients' features.",
     "Abstract; Results", "High when listed explicitly; Medium when inferred.", "Yes"],
]

CALL_GROUP_OF = {
    "study_type": "1", "gene_focus": "1", "corresponding_authors": "1",
    "model_systems": "2", "techniques": "2", "sample_sources": "2", "reagents": "2",
    "mutations": "3", "treatments": "3", "clinical_features": "3",
}
HEADER_GROUPS = ["Call group", "Label", "Sections sent to the AI (separate with ;)",
                 "If those sections hold fewer than this many characters, send the whole paper instead",
                 "Also send every section that starts on this page or earlier (0 = none)"]
GROUPS = [
    ["1", "Front matter: study type, gene focus, corresponding authors",
     "front; abstract; introduction; conclusion; acknowledgments", 3000, 1],
    ["2", "Methods: model system, techniques, sample source, reagents", "abstract; methods; case", 3000, 0],
    ["3", "Findings: variants, treatments, clinical features", "abstract; results; case; discussion; conclusion", 3000, 0],
]

HEADER_FIELDS = ["Category ID", "Field", "Kind (pick-list / text)", "Allowed values (separate with ;)", "Description"]
FIELDS = [
    ["study_type", "value", "pick-list", v(*STUDY_TYPES), "The study type."],
    ["gene_focus", "value", "pick-list", v("ADA2 (CECR1)", "ADA1 (ADA)", "Both", "Other/none"), "The gene or enzyme the paper is about."],
    ["mutations", "gene", "text", "", "Gene symbol, for example ADA2 (also called CECR1) or ADA."],
    ["mutations", "variant", "text", "", "Variant in one-letter protein form (p.R169Q), or the DNA-level change if no protein change is given."],
    ["mutations", "as_written", "text", "", "The variant exactly as the paper writes it."],
    ["model_systems", "system", "pick-list", v("Human patients", "Healthy donors", "Primary human cells", "Human cell line", "Mouse", "Zebrafish", "Other animal", "Yeast/fungus", "Recombinant protein", "Other"), "The model system."],
    ["model_systems", "detail", "text", "", "Specifics such as cell line name or mouse strain; required if 'Other'; empty string if none."],
    ["techniques", "technique", "pick-list", v("ADA2 enzyme activity assay", "Sanger sequencing", "Whole-exome sequencing", "Gene panel/NGS", "Western blot", "Flow cytometry", "qPCR", "ELISA", "RNA-seq", "Single-cell RNA-seq", "Immunohistochemistry", "Mass spectrometry", "Cytokine/interferon signature", "Imaging (MRI/MRA)", "Biopsy/histology", "CRISPR", "Crystallography/structure", "Other"), "The technique."],
    ["techniques", "detail", "text", "", "Required if 'Other' (name the technique); otherwise optional; empty string if none."],
    ["sample_sources", "source", "pick-list", v("Serum", "Plasma", "PBMC", "Whole blood", "Bone marrow", "Skin biopsy", "Fibroblasts", "CSF", "Urine", "Other tissue", "Other"), "The sample type."],
    ["sample_sources", "detail", "text", "", "Required if 'Other'; otherwise optional (for example patient vs. control); empty string if none."],
    ["reagents", "name", "text", "", "The reagent, for example 'anti-ADA2 antibody' or a specific clone."],
    ["reagents", "vendor", "text", "", "Supplier if stated; otherwise an empty string."],
    ["reagents", "catalog_number", "text", "", "Catalog number EXACTLY as printed; empty string if not reported. Never guess."],
    ["reagents", "target_or_use", "text", "", "What it targets or is used for (for example 'Western blot'); empty string if not stated."],
    ["corresponding_authors", "name", "text", "", "Author name as printed."],
    ["corresponding_authors", "institution", "text", "", "Institution given for this author; empty string if not stated. No email addresses."],
    ["treatments", "treatment", "pick-list", v("Anti-TNF", "IVIG", "Stem-cell transplant (HSCT)", "Corticosteroids", "Gene therapy", "Enzyme replacement", "JAK inhibitor", "Plasma infusion (FFP)", "Other"), "The treatment class."],
    ["treatments", "detail", "text", "", "Specific drug or regimen (for example 'etanercept'); required if 'Other'; empty string if none."],
    ["clinical_features", "feature", "pick-list", v("Vasculitis/PAN", "Stroke", "Cytopenia/marrow failure", "Immunodeficiency", "Livedo", "Fever", "Lymphoproliferation", "Hepatosplenomegaly/portal hypertension", "Other"), "The clinical feature."],
    ["clinical_features", "detail", "text", "", "Required if 'Other'; otherwise optional; empty string if none."],
]

HOW_TO = [
    "HOW TO EDIT THIS TEMPLATE",
    "",
    "This workbook tells the AI exactly how to read each category, and it also becomes the 'Field guide' that reviewers see next to the results.",
    "",
    "Categories tab: one row per category. Plain-language columns: what counts, what does not, how to interpret it, and what 'Not applicable' and 'Not found' mean for that category.",
    "  - 'Applies to study types': 'All', or a list separated by ;  A category is automatically marked Not applicable when a paper's study type is not in the list.",
    "  - 'Include?': set to No to drop a category from future extractions without deleting its row.",
    "Entry fields tab: the pieces of information recorded for each entry (for example a reagent's name, vendor and catalog number), and the allowed values for pick-lists.",
    "Call groups tab: each paper is read in up to three short requests, each seeing only the relevant sections (front matter; methods; findings). 'Call group' on the Categories tab says which request a category belongs to. Group 1 must contain Study type, because it decides which categories apply and therefore whether groups 2 and 3 run at all.",
    "  - Section names that can be used: front, abstract, introduction, methods, results, discussion, conclusion, acknowledgments, case.",
    "Global rules tab: rules that apply to every category (what Found / Not found / Not applicable mean, evidence, confidence).",
    "",
    "After editing: save the file, tell Claude, and the next extraction run uses the new rules. Every result records the template version it was produced with.",
    "Keep study type names in 'Applies to study types' identical to the values on the Entry fields tab (study_type, value).",
]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="dada2")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = ROOT / "orgs" / args.org / "extraction_template.xlsx"
    if out.exists() and not args.force:
        sys.exit(f"{out} already exists; it is the source of truth now. Use --force to overwrite it.")
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Categories"
    ws.append(HEADER_CATS + ["Call group (1-3)"])
    for r in CATS:
        ws.append(r + [CALL_GROUP_OF[r[0]]])
    style_sheet(ws, [22, 24, 12, 12, 34, 44, 40, 62, 36, 40, 24, 40, 16, 12])
    for col in ("C", "M"):
        dv = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}60")
    wc = wb.create_sheet("Call groups")
    wc.append(HEADER_GROUPS)
    for r in GROUPS:
        wc.append(r)
    style_sheet(wc, [12, 62, 60, 40, 40])
    wf = wb.create_sheet("Entry fields")
    wf.append(HEADER_FIELDS)
    for r in FIELDS:
        wf.append(r)
    style_sheet(wf, [22, 18, 18, 80, 60])
    wg = wb.create_sheet("Global rules")
    wg.append(["Rule", "Text"])
    for r in GLOBAL_RULES:
        wg.append(list(r))
    style_sheet(wg, [26, 150])
    wh = wb.create_sheet("How to edit", 0)
    for line in HOW_TO:
        wh.append([line])
    wh.column_dimensions["A"].width = 150
    wh["A1"].font = Font(bold=True, size=14)
    for row in wh.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
