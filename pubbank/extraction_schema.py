"""Extraction schema for the DADA2 publication bank (pilot), version dada2-v1.

Claude fills `PaperExtraction` for each paper. The field descriptions below are sent to the model with the
schema, so they double as the extraction rules. Every extracted value carries `Evidence` (verbatim quote,
section, page). Publication year, the full author list and the last author are NOT asked of Claude when
PubMed/Crossref metadata exists: see `derived_fields`.

Design note: there are no nullable (Optional) fields on purpose. The API compiles this schema into a
grammar and rejects it as "too large" when many fields are nullable, so "not stated" is an empty string
(or page 0) instead.
"""
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "dada2-v1"

StudyType = Literal[
    "Case report/series", "Cohort/registry", "Clinical trial or treatment study", "Animal study",
    "In vitro (cells/biochemistry)", "Computational/structural", "Review", "Guideline/consensus",
    "Other/off-topic",
]
GeneFocus = Literal["ADA2 (CECR1)", "ADA1 (ADA)", "Both", "Other/none"]
ModelSystemName = Literal[
    "Human patients", "Healthy donors", "Primary human cells", "Human cell line", "Mouse", "Zebrafish",
    "Other animal", "Yeast/fungus", "Recombinant protein", "None (review/guideline)", "Other",
]
TechniqueName = Literal[
    "ADA2 enzyme activity assay", "Sanger sequencing", "Whole-exome sequencing", "Gene panel/NGS",
    "Western blot", "Flow cytometry", "qPCR", "ELISA", "RNA-seq", "Single-cell RNA-seq",
    "Immunohistochemistry", "Mass spectrometry", "Cytokine/interferon signature", "Imaging (MRI/MRA)",
    "Biopsy/histology", "CRISPR", "Crystallography/structure", "Other",
]
SampleSourceName = Literal[
    "Serum", "Plasma", "PBMC", "Whole blood", "Bone marrow", "Skin biopsy", "Fibroblasts", "CSF", "Urine",
    "Other tissue", "Other",
]
TreatmentName = Literal[
    "Anti-TNF", "IVIG", "Stem-cell transplant (HSCT)", "Corticosteroids", "Gene therapy",
    "Enzyme replacement", "JAK inhibitor", "Plasma infusion (FFP)", "Other",
]
ClinicalFeatureName = Literal[
    "Vasculitis/PAN", "Stroke", "Cytopenia/marrow failure", "Immunodeficiency", "Livedo", "Fever",
    "Lymphoproliferation", "Hepatosplenomegaly/portal hypertension", "Other",
]


class Evidence(BaseModel):
    quote: str = Field(description=(
        "Text copied EXACTLY, character for character, from the paper that supports this value "
        "(at most about 300 characters). Never paraphrase or fix typos."))
    section: str = Field(description=(
        "Heading of the section the quote appears in, e.g. 'Methods', 'Results', or 'Title page'."))
    page: int = Field(description=(
        "Page number given in the section marker, e.g. [p.3] means 3. Use 0 if unknown."))


class StudyTypeValue(BaseModel):
    value: StudyType = Field(description=(
        "What THIS paper itself did. If it did several things, pick the primary one. Use "
        "'Other/off-topic' if the paper is not about ADA or ADA2 deficiency biology or medicine."))
    evidence: Evidence


class GeneFocusValue(BaseModel):
    value: GeneFocus = Field(description=(
        "Which gene/enzyme the paper is about. Do NOT assume 'ADA' means ADA2: ADA1 (ADA, SCID, PEG-ADA) "
        "is a different enzyme."))
    evidence: Evidence


class MutationEntry(BaseModel):
    gene: str = Field(description="Gene symbol, e.g. 'ADA2' (also called CECR1) or 'ADA'.")
    variant: str = Field(description=(
        "Variant in standard one-letter protein form, e.g. 'p.R169Q'. Normalize 'Arg169Gln' and "
        "'p.Arg169Gln' to 'p.R169Q'. If only a DNA change is given, use it, e.g. 'c.506G>A'."))
    as_written: str = Field(description="The variant exactly as written in the paper.")
    evidence: Evidence


class ModelSystemEntry(BaseModel):
    system: ModelSystemName
    detail: str = Field(description="Specifics, e.g. cell line name or mouse strain; required if 'Other'. Empty string if none.")
    evidence: Evidence


class TechniqueEntry(BaseModel):
    technique: TechniqueName
    detail: str = Field(description="Required if 'Other' (name the technique); otherwise optional. Empty string if none.")
    evidence: Evidence


class SampleSourceEntry(BaseModel):
    source: SampleSourceName
    detail: str = Field(description="Required if 'Other'; otherwise optional (e.g. patient vs control). Empty string if none.")
    evidence: Evidence


class ReagentEntry(BaseModel):
    name: str = Field(description="Reagent, e.g. 'anti-ADA2 antibody' or a specific clone name.")
    vendor: str = Field(description="Supplier if stated, otherwise an empty string.")
    catalog_number: str = Field(description="Catalog number EXACTLY as printed. Empty string if not reported; never guess.")
    target_or_use: str = Field(description="What it targets or is used for, e.g. 'Western blot'. Empty string if not stated.")
    evidence: Evidence


class CorrespondingAuthorEntry(BaseModel):
    name: str
    institution: str = Field(description="Institution given for this author, otherwise an empty string. No email addresses.")
    evidence: Evidence


class TreatmentEntry(BaseModel):
    treatment: TreatmentName
    detail: str = Field(description="Specific drug/regimen, e.g. 'etanercept'; required if 'Other'. Empty string if none.")
    evidence: Evidence


class ClinicalFeatureEntry(BaseModel):
    feature: ClinicalFeatureName
    detail: str = Field(description="Required if 'Other'; otherwise optional. Empty string if none.")
    evidence: Evidence


class PaperExtraction(BaseModel):
    study_type: StudyTypeValue
    gene_focus: GeneFocusValue
    mutations: list[MutationEntry] = Field(description=(
        "Only variants in THIS paper's own patients or experiments. Do NOT list variants that are merely "
        "cited from other studies or listed as background. Empty list if none."))
    model_systems: list[ModelSystemEntry] = Field(description=(
        "What this paper's own experiments or subjects used. Reviews and guidelines: one entry "
        "'None (review/guideline)'."))
    techniques: list[TechniqueEntry] = Field(description=(
        "Methods this paper itself performed, not ones it only cites. Empty list if none."))
    sample_sources: list[SampleSourceEntry] = Field(description=(
        "Where the samples analysed in this paper came from. Empty list if none."))
    reagents: list[ReagentEntry] = Field(description=(
        "Reagents actually used in this paper's own methods (antibodies, kits, recombinant proteins, drugs "
        "used in experiments). Catalog numbers matter: capture them exactly when printed. Empty list if none."))
    corresponding_authors: list[CorrespondingAuthorEntry] = Field(description=(
        "Author(s) the paper labels as corresponding author / correspondence / to whom correspondence should "
        "be addressed, usually on the first page or in a footnote. There can be several: list all. If the "
        "paper does not label any, return an empty list. Do NOT guess the last author."))
    all_authors_from_title_page: list[str] = Field(description=(
        "Every author name in the order printed on the title page (names only, no affiliations)."))
    treatments: list[TreatmentEntry] = Field(description=(
        "Treatments given to THIS paper's patients or tested in its experiments, not ones merely mentioned "
        "in the background. Empty list if none."))
    clinical_features: list[ClinicalFeatureEntry] = Field(description=(
        "Clinical features observed in THIS paper's patients. Empty list if none."))
    extraction_notes: str = Field(description=(
        "Short note for the human reviewer about anything uncertain or odd (e.g. text appears truncated, paper "
        "is off-topic, only an abstract is available). Empty string if nothing to note."))


def derived_fields(metadata_authors, publication_year, title_page_authors):
    """Fields that come from PubMed/Crossref metadata, not from Claude. Falls back to the title-page
    authors read by Claude when the paper had no metadata match."""
    all_authors = list(metadata_authors or []) or list(title_page_authors or [])
    return {
        "publication_year": publication_year,
        "all_authors": all_authors,
        "last_author": all_authors[-1] if all_authors else None,
        "author_source": "metadata" if metadata_authors else ("title page" if title_page_authors else None),
    }
