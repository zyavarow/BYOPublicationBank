"""Human Phenotype Ontology (HPO) matching.

Loads a local copy of hp.json (obographs format; download it from the HPO GitHub releases, cite Gargano et al. 2024 and
keep the release date) and maps free-text phenotypes to HPO ids by exact label / synonym match, then fuzzy match.
An embedding-similarity pass for low scores is added later (it reuses the search embedder).

HPO content must not be altered; this module only reads it.
"""
import json
import re
from pathlib import Path

from rapidfuzz import fuzz, process

PHENOTYPE_ROOT = "HP:0000118"          # "Phenotypic abnormality"
_SYN_PRIORITY = {"hasExactSynonym": 0.98, "hasBroadSynonym": 0.90, "hasNarrowSynonym": 0.90, "hasRelatedSynonym": 0.88}


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split())


def _hp_id(uri):
    m = re.search(r"HP_(\d+)$", uri or "")
    return f"HP:{m.group(1)}" if m else None


class Hpo:
    def __init__(self, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        graph = data["graphs"][0]
        self.version = (re.search(r"releases/([0-9\-]+)/", (graph.get("meta") or {}).get("version", "")) or [None, None])[1]
        self.terms, self.parents, self.children = {}, {}, {}
        for n in graph["nodes"]:
            hid = _hp_id(n.get("id"))
            if not hid or n.get("type") != "CLASS":
                continue
            meta = n.get("meta") or {}
            if meta.get("deprecated"):
                continue
            syns = [(s["val"], s.get("pred", "hasRelatedSynonym")) for s in meta.get("synonyms", []) if s.get("val")]
            self.terms[hid] = {"label": n.get("lbl", ""), "synonyms": syns,
                               "definition": ((meta.get("definition") or {}).get("val") or "")}
        for e in graph.get("edges", []):
            if e.get("pred") in ("is_a", "http://www.w3.org/2000/01/rdf-schema#subClassOf"):
                sub, obj = _hp_id(e["sub"]), _hp_id(e["obj"])
                if sub in self.terms and obj in self.terms:
                    self.parents.setdefault(sub, set()).add(obj)
                    self.children.setdefault(obj, set()).add(sub)
        self._anc_cache = {}
        self.exact = {}                      # normalized string -> list of (id, score, kind)
        for hid, t in self.terms.items():
            self.exact.setdefault(_norm(t["label"]), []).append((hid, 1.0, "label"))
            for val, pred in t["synonyms"]:
                self.exact.setdefault(_norm(val), []).append((hid, _SYN_PRIORITY.get(pred, 0.88), "synonym"))
        self.strings = list(self.exact)
        self._phenotype_ids = self.descendants(PHENOTYPE_ROOT) | {PHENOTYPE_ROOT} if PHENOTYPE_ROOT in self.terms else None

    def label(self, hid):
        return self.terms.get(hid, {}).get("label")

    def ancestors(self, hid):
        if hid in self._anc_cache:
            return self._anc_cache[hid]
        out, stack = set(), list(self.parents.get(hid, ()))
        while stack:
            p = stack.pop()
            if p not in out:
                out.add(p)
                stack.extend(self.parents.get(p, ()))
        self._anc_cache[hid] = out
        return out

    def descendants(self, hid):
        out, stack = set(), list(self.children.get(hid, ()))
        while stack:
            c = stack.pop()
            if c not in out:
                out.add(c)
                stack.extend(self.children.get(c, ()))
        return out

    def is_under(self, hid, root):
        return hid == root or root in self.ancestors(hid)

    def resolve(self, ref):
        """An HPO id ('HP:0002633') or an exact label ('Vasculitis') -> HPO id, else None."""
        if ref in self.terms:
            return ref
        for hid, score, kind in self.exact.get(_norm(ref), []):
            if kind == "label":
                return hid
        return None

    def match(self, text, limit=3, min_score=0.88, phenotypes_only=True):
        """Best HPO candidates for a phrase: [{'id','label','score','method','matched'}], best first."""
        q = _norm(text)
        if not q:
            return []
        cands = {}

        def add(hid, score, method, matched):
            if phenotypes_only and self._phenotype_ids is not None and hid not in self._phenotype_ids:
                return
            if hid not in cands or score > cands[hid]["score"]:
                cands[hid] = {"id": hid, "label": self.label(hid), "score": round(score, 3), "method": method, "matched": matched}

        for hid, score, kind in self.exact.get(q, []):
            add(hid, score, f"exact-{kind}", q)
        if not cands:
            for s, sc, _ in process.extract(q, self.strings, scorer=fuzz.token_sort_ratio, limit=8, score_cutoff=min_score * 100):
                for hid, base, kind in self.exact[s]:
                    add(hid, (sc / 100.0) * base * 0.97, f"fuzzy-{kind}", s)
        if not cands:
            # a known term appears inside a longer phrase ("congenital aplastic anemia" contains "aplastic anemia")
            padded = f" {q} "
            hits = [s for s in self.strings if len(s) >= 5 and f" {s} " in padded]
            for s in sorted(hits, key=len, reverse=True)[:6]:
                coverage = len(s) / len(q)
                for hid, base, kind in self.exact[s]:
                    add(hid, (0.78 + 0.12 * coverage) * base, f"contains-{kind}", s)
        return sorted(cands.values(), key=lambda c: (-c["score"], len(c["matched"]) * -1))[:limit]

    def match_phrase(self, text, **kw):
        """Match the whole phrase; if nothing matches, try its parts split on commas, semicolons, slashes and 'and'."""
        whole = self.match(text, **kw)
        if whole and not whole[0]["method"].startswith("contains"):
            return [whole[0] | {"part": text}]
        pieces = [p for p in re.split(r",|;|/|\band\b|\bwith\b", text or "") if _norm(p)]
        parts = []
        if len(pieces) > 1:                       # a list of features: map each one
            for part in pieces:
                m = self.match(part, **kw)
                if m:
                    parts.append(m[0] | {"part": part.strip()})
        if len(parts) > 1:
            return parts
        if whole:                                 # one feature with extra words around it
            return [whole[0] | {"part": text}]
        return parts


def group_roots(hpo, match_text):
    """Display-group definition ('Vasculitis; HP:0001297') -> list of HPO ids (unresolved items are returned separately)."""
    ids, missing = [], []
    for item in (p.strip() for p in (match_text or "").split(";") if p.strip()):
        hid = hpo.resolve(item)
        (ids if hid else missing).append(hid or item)
    return ids, missing
