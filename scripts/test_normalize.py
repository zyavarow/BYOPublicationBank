"""Unit checks for the normalizers (free, offline).  .venv\\Scripts\\python.exe scripts\\test_normalize.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pubbank.normalize.hgvs import canonical_gene, normalize_variant  # noqa: E402

GENES = [{"symbol": "GENE1", "aliases": ["GENE1A"]}, {"symbol": "GENE2", "aliases": ["GENE2A"]}]
fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label} {detail if not cond else ''}")
    if not cond:
        fails.append(label)


print("same variant, many spellings -> one canonical protein change")
for s in ("p.R169Q", "R169Q", "Arg169Gln", "p.Arg169Gln", "p.(Arg169Gln)", "ARG169GLN", "p.arg169gln", "(R169Q)"):
    r = normalize_variant(s)
    check(f"{s!r}", r["status"] == "valid" and r["hgvs_p"] == "p.Arg169Gln" and r["one_letter"] == "R169Q", r)

print("cDNA, accession, and combined forms")
r = normalize_variant("c.506G>A (p.Arg169Gln)")
check("cDNA + protein together", r["hgvs_c"] == "c.506G>A" and r["hgvs_p"] == "p.Arg169Gln", r)
r = normalize_variant("NM_001282225.2:c.506G>A")
check("accession + cDNA", r["reference"] == "NM_001282225.2" and r["hgvs_c"] == "c.506G>A" and r["hgvs_p"] is None, r)
r = normalize_variant("c.506G>A")
check("cDNA alone is NOT misread as a protein change", r["hgvs_p"] is None and r["status"] == "valid", r)
r = normalize_variant("c.506 G → A")
check("spaced arrow", r["hgvs_c"] == "c.506G>A", r)
r = normalize_variant("c.139_141del")
check("cDNA deletion", r["hgvs_c"] == "c.139_141del" and r["status"] == "valid", r)

print("nonsense, deletion, frameshift")
for s, want in (("p.R169*", "p.Arg169Ter"), ("Arg169X", "p.Arg169Ter"), ("p.Arg169Ter", "p.Arg169Ter"),
                ("p.Glu328del", "p.Glu328del"), ("E328del", "p.Glu328del"),
                ("p.Leu351fs", "p.Leu351fs"), ("p.L351Pfs*12", "p.Leu351Profs*12")):
    r = normalize_variant(s)
    check(f"{s!r} -> {want}", r["hgvs_p"] == want and r["status"] == "valid", r)

print("things that must NOT be accepted")
for s in ("mutation in exon 3", "Z169Q", "Foo12Bar", "", "homozygous"):
    r = normalize_variant(s)
    check(f"{s!r} is invalid", r["status"] == "invalid", r)
r = normalize_variant("R169Q and G47R")
check("two variants -> ambiguous", r["status"] == "ambiguous", r)
r = normalize_variant("rs775440641")
check("rsID only -> ambiguous, id kept", r["status"] == "ambiguous" and r["dbsnp"] == "rs775440641", r)

print("gene aliases")
check("GENE1A -> GENE1", canonical_gene("GENE1A", GENES) == "GENE1")
check("'GENE1 (GENE1A)' -> GENE1", canonical_gene("GENE1 (GENE1A)", GENES) == "GENE1")
check("GENE2A -> GENE2", canonical_gene("GENE2A", GENES) == "GENE2")
check("unknown gene -> None", canonical_gene("TP53", GENES) is None)
r = normalize_variant("p.R169Q", gene="TP53", genes=GENES)
check("unknown gene is noted", any("not in this organization" in n for n in r["notes"]), r)

print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
