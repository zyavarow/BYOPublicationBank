"""Offline normalization of variant descriptions to HGVS-style notation.

Handles protein substitutions, nonsense, deletions and frameshifts written with one- or three-letter amino acid
codes ("R169Q", "Arg169Gln", "p.(Arg169Gln)"), and cDNA changes ("c.506G>A", optionally with a RefSeq accession).
It canonicalizes syntax only: it does NOT check the change against a reference sequence (that needs an online
service or a local sequence database; see the plan). Status is
  valid     at least one component was recognized and is syntactically correct
  ambiguous more than one distinct variant in the text, or only an rsID
  invalid   nothing recognizable as a variant description
"""
import re

AA3 = {"A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "Q": "Gln", "E": "Glu", "G": "Gly", "H": "His",
       "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe", "P": "Pro", "S": "Ser", "T": "Thr", "W": "Trp",
       "Y": "Tyr", "V": "Val", "*": "Ter"}
AA1 = {v.lower(): k for k, v in AA3.items()}
AA1.update({"stop": "*", "x": "*", "xaa": None})

_ACC = r"(?:(?P<ref>(?:NM|NR|NP|ENST|NC|NG|LRG)_?[0-9]+(?:\.\d+)?)\s*(?:\([A-Za-z0-9\-]+\))?\s*:\s*)?"
CDNA = re.compile(_ACC + r"c\.\s*(?P<pos>[-*]?\d+(?:[+-]\d+)?(?:_[-*]?\d+(?:[+-]\d+)?)?)\s*"
                  r"(?P<chg>[ACGT]>[ACGT]|delins[ACGT]+|del[ACGT]*|dup[ACGT]*|ins[ACGT]+|inv)", re.I)
_TOKEN = r"(?:[A-Za-z]{3}|[A-Z*])"
PROT_FS = re.compile(rf"(?<![A-Za-z0-9])(?:p\.)?\(?(?P<ref>{_TOKEN})(?P<pos>[1-9]\d*)(?P<alt>[A-Za-z]{{3}}|[A-Z])?fs(?:\*|Ter|X)?(?P<n>\d+)?\)?", re.I)
PROT_DEL = re.compile(rf"(?<![A-Za-z0-9])(?:p\.)?\(?(?P<ref>{_TOKEN})(?P<pos>[1-9]\d*)(?:_(?P<ref2>{_TOKEN})(?P<pos2>[1-9]\d*))?del\)?", re.I)
PROT_SUB = re.compile(rf"(?<![A-Za-z0-9])(?:p\.)?\(?(?P<ref>{_TOKEN})(?P<pos>[1-9]\d*)(?P<alt>{_TOKEN}|=)\)?(?![A-Za-z0-9])")
RSID = re.compile(r"\brs\d+\b", re.I)


def _aa(token):
    """Return the one-letter code for a 1- or 3-letter amino acid token, or None if it is not an amino acid."""
    if len(token) == 1:
        return token if token in AA3 else ("*" if token == "X" else None)
    return AA1.get(token.lower())


def _three(one):
    return AA3[one]


def _prot_candidates(text):
    """Yield (span, canonical_three_letter, one_letter) for protein changes found in `text`."""
    found = []
    for m in PROT_FS.finditer(text):
        ref = _aa(m["ref"])
        alt = _aa(m["alt"]) if m["alt"] else None
        if ref and ref != "*" and (not m["alt"] or alt):
            tail = (_three(alt) if alt else "") + "fs" + (f"*{m['n']}" if m["n"] else "")
            found.append((m.span(), f"p.{_three(ref)}{m['pos']}{tail}", f"{ref}{m['pos']}{(alt or '')}fs"))
    for m in PROT_DEL.finditer(text):
        ref = _aa(m["ref"])
        if ref and ref != "*":
            if m["ref2"]:
                ref2 = _aa(m["ref2"])
                if not ref2:
                    continue
                found.append((m.span(), f"p.{_three(ref)}{m['pos']}_{_three(ref2)}{m['pos2']}del", f"{ref}{m['pos']}_{ref2}{m['pos2']}del"))
            else:
                found.append((m.span(), f"p.{_three(ref)}{m['pos']}del", f"{ref}{m['pos']}del"))
    taken = [s for s, _, _ in found]
    for m in PROT_SUB.finditer(text):
        if any(m.start() < e and s < m.end() for s, e in taken):
            continue
        ref = _aa(m["ref"])
        if m["alt"] == "=":
            continue
        alt = _aa(m["alt"])
        if ref and alt and ref != "*":
            found.append((m.span(), f"p.{_three(ref)}{m['pos']}{_three(alt)}", f"{ref}{m['pos']}{alt}"))
    return found


def canonical_gene(symbol, genes):
    """Map a symbol or alias to the canonical symbol using the overlay's gene table, else None.
    `genes` = [{'symbol': 'ADA2', 'aliases': ['CECR1']}, ...]"""
    if not symbol:
        return None
    for token in re.findall(r"[A-Za-z0-9\-]+", symbol.upper()):
        for g in genes or []:
            names = {g["symbol"].upper(), *(a.upper() for a in g.get("aliases", []))}
            if token in names:
                return g["symbol"]
    return None


def normalize_variant(text, gene=None, genes=None):
    """Normalize one variant description. Returns a JSON-friendly dict."""
    raw = (text or "").replace("→", ">").replace("−", "-")
    raw = re.sub(r"\s*>\s*", ">", raw)
    out = {"status": "invalid", "hgvs_c": None, "hgvs_p": None, "one_letter": None, "reference": None,
           "dbsnp": None, "gene": canonical_gene(gene, genes), "notes": []}
    cdna = list(CDNA.finditer(raw))
    rest = CDNA.sub(" ", raw)
    prots = _prot_candidates(rest)
    distinct_c = {f"c.{m['pos']}{m['chg'].upper() if '>' in m['chg'] else m['chg'].lower()}" for m in cdna}
    distinct_p = {p for _, p, _ in prots}
    rs = RSID.search(raw)
    if rs:
        out["dbsnp"] = rs.group(0).lower()
    if len(distinct_c) > 1 or len(distinct_p) > 1:
        out["status"] = "ambiguous"
        out["notes"].append("more than one variant in the text; split into separate entries")
    if cdna:
        m = cdna[0]
        chg = m["chg"].upper() if ">" in m["chg"] else re.sub(r"[ACGT]+$", lambda x: x.group(0).upper(), m["chg"].lower())
        out["hgvs_c"] = f"c.{m['pos']}{chg}"
        out["reference"] = m["ref"]
    if prots:
        _, p3, p1 = prots[0]
        out["hgvs_p"], out["one_letter"] = p3, p1
    if out["status"] != "ambiguous":
        if out["hgvs_c"] or out["hgvs_p"]:
            out["status"] = "valid"
        elif out["dbsnp"]:
            out["status"] = "ambiguous"
            out["notes"].append("only an rsID was given; no change description")
        else:
            out["notes"].append("no HGVS-style change recognized")
    if gene and not out["gene"]:
        out["notes"].append(f"gene '{gene}' is not in this organization's gene list")
    return out
