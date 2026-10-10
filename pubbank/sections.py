"""Section labels shared by the PDF ingestion and the PubMed Central full-text import.

A heading is mapped to a section kind (abstract, introduction, methods, results, discussion, ...). A heading that matches nothing
inherits the kind of the section before it, so subheadings stay with their parent section."""
import re

SECTION_KINDS = [
    ("abstract", re.compile(r"^(abstr|summary$|synopsis)")),
    ("introduction", re.compile(r"^(introduction|background|overview$)")),
    ("methods", re.compile(r"^(methods?|methodology|materials?|onlinemethods|patientsand|subjectsand|participantsand|experimental|studydesign|studypopulation)")),
    ("results", re.compile(r"^(results|findings)")),
    ("discussion", re.compile(r"^discussion")),
    ("conclusion", re.compile(r"^(conclusion|concludingremarks)")),
    ("references", re.compile(r"^(references|bibliography|literaturecited)")),
    ("acknowledgments", re.compile(r"^(acknowledg|funding|conflict|disclosure|authorcontrib|declaration|competinginterest|dataavailab|ethic|supportinginformation|supplementary|appendix)")),
    ("case", re.compile(r"^(case|clinicalcase|clinicalpresentation|patientpresentation|patientdescription|patientinformation)")),
]
LEADING_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*[\.\)\|:\-]*|[ivx]+[\.\)\|:])+")


def section_kind(heading, prev_kind):
    key = re.sub(r"\s+", "", heading.lower())
    key = LEADING_NUMBERING.sub("", key).lstrip("|:.-")
    for kind, rx in SECTION_KINDS:
        if rx.match(key):
            # An "Ethics statement" inside the methods is part of the methods; it must not end them (otherwise every
            # later methods subheading, such as "Mice" or "Statistics", would be filed under acknowledgments).
            if kind == "acknowledgments" and prev_kind == "methods" and key.startswith("ethic"):
                return prev_kind
            return kind
    return prev_kind
