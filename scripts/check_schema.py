"""Quick self-check of the extraction schema: valid data passes, bad data fails loudly.
  .venv\\Scripts\\python.exe scripts\\check_schema.py
"""
import json
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pubbank.extraction_schema import SCHEMA_VERSION, PaperExtraction, derived_fields  # noqa: E402

ev = {"quote": "Patients carried the homozygous c.506G>A (p.Arg169Gln) variant.", "section": "Results", "page": 3}
good = {
    "study_type": {"value": "Case report/series", "evidence": ev},
    "gene_focus": {"value": "GENE1 (GENE1A)", "evidence": ev},
    "mutations": [{"gene": "GENE1", "variant": "p.R169Q", "as_written": "p.Arg169Gln", "evidence": ev}],
    "model_systems": [{"system": "Human patients", "detail": "", "evidence": ev}],
    "techniques": [{"technique": "Sanger sequencing", "detail": "", "evidence": ev}],
    "sample_sources": [{"source": "Plasma", "detail": "", "evidence": ev}],
    "reagents": [{"name": "anti-GENE1 antibody", "vendor": "Abcam", "catalog_number": "ab12345",
                  "target_or_use": "Western blot", "evidence": ev}],
    "corresponding_authors": [{"name": "Jane Smith", "institution": "NIH", "evidence": ev}],
    "all_authors_from_title_page": ["Jane Smith", "Kim Lee"],
    "treatments": [{"treatment": "Anti-TNF", "detail": "etanercept", "evidence": ev}],
    "clinical_features": [{"feature": "Stroke", "detail": "", "evidence": ev}],
    "extraction_notes": "",
}
p = PaperExtraction.model_validate(good)
print(f"schema {SCHEMA_VERSION}: valid example accepted ({len(PaperExtraction.model_fields)} fields)")

bad_cases = {
    "study type not in the allowed list": {**good, "study_type": {"value": "Mouse study", "evidence": ev}},
    "value with no supporting quote": {**good, "mutations": [{"gene": "GENE1", "variant": "p.R169Q", "as_written": "R169Q"}]},
    "field missing entirely": {k: v for k, v in good.items() if k != "reagents"},
}
for label, data in bad_cases.items():
    try:
        PaperExtraction.model_validate(data)
        print(f"  PROBLEM: '{label}' was accepted")
    except ValidationError as e:
        print(f"  correctly rejected: {label} ({e.error_count()} error)")

schema_text = json.dumps(PaperExtraction.model_json_schema())
print(f"JSON schema size: {len(schema_text):,} characters (this is sent to Claude with each request)")
print("derived fields example:", derived_fields(["A B", "C D", "E F"], 2024, []))
