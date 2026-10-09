"""Unit checks for the HPO matcher using a tiny synthetic ontology (free, offline; no real HPO file needed).
  .venv\\Scripts\\python.exe scripts\\test_hpo.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pubbank.normalize.hpo import Hpo, group_roots  # noqa: E402

U = "http://purl.obolibrary.org/obo/HP_"


def node(num, label, syns=(), deprecated=False):
    meta = {"synonyms": [{"pred": p, "val": v} for p, v in syns]}
    if deprecated:
        meta["deprecated"] = True
    return {"id": f"{U}{num}", "lbl": label, "type": "CLASS", "meta": meta}


def edge(sub, obj):
    return {"sub": f"{U}{sub}", "pred": "is_a", "obj": f"{U}{obj}"}


DATA = {"graphs": [{
    "meta": {"version": "http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json"},
    "nodes": [
        node("0000001", "All"), node("0000118", "Phenotypic abnormality"), node("0000005", "Mode of inheritance"),
        node("0002633", "Vasculitis", [("hasExactSynonym", "Angiitis")]),
        node("0001297", "Stroke", [("hasExactSynonym", "Cerebrovascular accident")]),
        node("0002326", "Transient ischemic attack", [("hasRelatedSynonym", "TIA")]),
        node("0001903", "Anemia"), node("0001873", "Thrombocytopenia"),
        node("0000999", "Old term", deprecated=True),
    ],
    "edges": [edge("0000118", "0000001"), edge("0000005", "0000001"), edge("0002633", "0000118"), edge("0001297", "0000118"),
              edge("0002326", "0001297"), edge("0001903", "0000118"), edge("0001873", "0000118")],
}]}
fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label} {detail if not cond else ''}")
    if not cond:
        fails.append(label)


with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "hp.json"
    p.write_text(json.dumps(DATA), encoding="utf-8")
    h = Hpo(p)

check("release date parsed", h.version == "2026-09-01", h.version)
check("deprecated terms are dropped", "HP:0000999" not in h.terms)
m = h.match("Vasculitis")
check("exact label", m and m[0]["id"] == "HP:0002633" and m[0]["method"] == "exact-label", m)
m = h.match("angiitis")
check("exact synonym", m and m[0]["id"] == "HP:0002633" and m[0]["method"] == "exact-synonym", m)
m = h.match("Cerebrovascular Accident")
check("case-insensitive synonym", m and m[0]["id"] == "HP:0001297", m)
m = h.match("vasculits")
check("typo found by fuzzy match", m and m[0]["id"] == "HP:0002633" and m[0]["method"].startswith("fuzzy"), m)
m = h.match("congenital anemia in childhood")
check("known term inside a longer phrase", m and m[0]["id"] == "HP:0001903" and m[0]["method"].startswith("contains"), m)
check("nonsense text gives no match", h.match("zzz qqq") == [])
check("modes of inheritance are not phenotypes", h.match("Mode of inheritance") == [])
check("TIA is under Stroke", h.is_under("HP:0002326", "HP:0001297") and "HP:0002326" in h.descendants("HP:0001297"))
parts = h.match_phrase("anemia and thrombocytopenia")
check("phrase split into parts", {x["id"] for x in parts} == {"HP:0001903", "HP:0001873"}, parts)
ids, missing = group_roots(h, "Vasculitis; HP:0001297; Nonexistent thing")
check("display group roots resolved", ids == ["HP:0002633", "HP:0001297"] and missing == ["Nonexistent thing"], (ids, missing))

print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
