"""Hybrid search over an organization's passages: meaning-based (vectors) + word-based (Postgres full text) + exact terms (variants, gene
symbols, catalog numbers), merged by reciprocal rank fusion. Everything runs on this computer.

Why three kinds: vectors find passages that mean the same thing in different words; the word index finds rare exact terms; exact matching
catches things neither handles well (p.R155H, Arg155His, ab189236, GENE1A vs GENE1).
"""
import re
import threading

import numpy as np

from pubbank import embeddings

RRF_K = 60
POOL = 120                  # candidates taken from each kind of search before merging
STOP = set("a an the of in on at to for from by with and or not is are was were be been this that these those it its as which what who whom how "
           "why when where does do did can could would should may might than then there their about into over under between among after before "
           "during using used use via per vs".split())
VARIANT = re.compile(r"^(?:p\.)?\(?([A-Z])(\d{2,4})([A-Z*])\)?$|^(?:p\.)?\(?([A-Z][a-z]{2})(\d{2,4})([A-Z][a-z]{2}|Ter)\)?$")
AA = {"A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "Q": "Gln", "E": "Glu", "G": "Gly", "H": "His", "I": "Ile", "L": "Leu",
      "K": "Lys", "M": "Met", "F": "Phe", "P": "Pro", "S": "Ser", "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val", "*": "Ter"}
AA1 = {v: k for k, v in AA.items()}
CATALOG = re.compile(r"^(?:ab|sc-?|cst-?|#)?\d{4,}[A-Za-z]{0,3}$", re.I)

_cache = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------------------------- the query
def words(text):
    return [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text) if w.lower() not in STOP]


def variant_forms(token):
    """Other ways of writing the same protein change: R155H -> p.R155H, Arg155His, p.Arg155His. Empty list if the token is not a variant."""
    m = VARIANT.match(token.strip(".,;:"))
    if not m:
        return []
    if m.group(1):
        ref, pos, alt = m.group(1), m.group(2), m.group(3)
        r3, a3 = AA.get(ref), AA.get(alt)
    else:
        r3, pos, a3 = m.group(4), m.group(5), m.group(6)
        ref, alt = AA1.get(r3), AA1.get(a3) or ("*" if a3 == "Ter" else None)
    if not (ref and alt and r3 and a3):
        return []
    return [f"{ref}{pos}{alt}", f"p.{ref}{pos}{alt}", f"{r3}{pos}{a3}", f"p.{r3}{pos}{a3}"]


def expand(query, synonyms=None):
    """(terms for the word index, exact strings to look for). `synonyms` is a list of name groups, e.g. [['GENE1', 'GENE1A']]."""
    terms = [w for w in words(query)]
    exact = []
    for w in list(terms):
        f = variant_forms(w)
        if f:
            exact += f
        if CATALOG.match(w) and re.search(r"\d", w):
            exact.append(w)
    low = {t.lower() for t in terms}
    for group in synonyms or []:                     # other names of a gene help the word index, but are far too common to use as exact matches
        if low & {g.lower() for g in group}:
            terms += [g for g in group if re.fullmatch(r"[A-Za-z0-9\-]{2,}", g)]
    return list(dict.fromkeys(terms)), list(dict.fromkeys(exact))


# ---------------------------------------------------------------------------------------------- the three searches
def _vectors(conn, org_id):
    """All passage vectors of an organization as one matrix, kept in memory (reloaded when the number of vectors changes)."""
    n = conn.execute("SELECT count(embedding) FROM chunks WHERE org_id = %s", (org_id,)).fetchone()[0]
    with _lock:
        hit = _cache.get(org_id)
        if hit and hit[0] == n:
            return hit[1], hit[2]
    rows = conn.execute("SELECT id, embedding FROM chunks WHERE org_id = %s AND embedding IS NOT NULL ORDER BY id", (org_id,)).fetchall()
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    mat = np.array([r[1] for r in rows], dtype=np.float32) if rows else np.zeros((0, embeddings.DIM), dtype=np.float32)
    with _lock:
        _cache[org_id] = (n, ids, mat)
    return ids, mat


def vector_search(conn, org_id, query, allowed=None, k=POOL):
    ids, mat = _vectors(conn, org_id)
    if not len(ids):
        return []
    q = embeddings.embed([query], query=True)[0]
    sims = mat @ q
    if allowed is not None:
        sims = np.where(np.isin(ids, allowed), sims, -1.0)
    top = np.argsort(-sims)[:k]
    return [(int(ids[i]), float(sims[i])) for i in top if sims[i] > 0]


def word_search(conn, org_id, terms, allowed=None, k=POOL):
    toks = [re.sub(r"[^A-Za-z0-9]", "", t) for t in terms]
    toks = [t for t in toks if len(t) >= 2]
    if not toks:
        return []
    q = " | ".join(dict.fromkeys(toks))
    sql = ("SELECT id, ts_rank_cd(tsv, to_tsquery('english', %s), 32) AS r FROM chunks WHERE org_id = %s AND tsv @@ to_tsquery('english', %s)"
           + (" AND id = ANY(%s)" if allowed is not None else "") + " ORDER BY r DESC LIMIT %s")
    args = [q, org_id, q] + ([list(map(int, allowed))] if allowed is not None else []) + [k]
    return [(r[0], float(r[1])) for r in conn.execute(sql, args)]


def strict_search(conn, org_id, query, allowed=None, k=POOL):
    """Passages containing ALL the question's words (a stricter reading of the same question)."""
    q = " ".join(words(query))
    if not q:
        return []
    sql = ("SELECT id, ts_rank_cd(tsv, plainto_tsquery('english', %s), 32) AS r FROM chunks WHERE org_id = %s AND tsv @@ plainto_tsquery('english', %s)"
           + (" AND id = ANY(%s)" if allowed is not None else "") + " ORDER BY r DESC LIMIT %s")
    args = [q, org_id, q] + ([list(map(int, allowed))] if allowed is not None else []) + [k]
    return [(r[0], float(r[1])) for r in conn.execute(sql, args)]


def exact_search(conn, org_id, exact, allowed=None, k=POOL):
    """Passages containing any of the exact strings (case-sensitive for symbols and variants)."""
    if not exact:
        return []
    like = [f"%{e}%" for e in exact]
    sql = ("SELECT id, (SELECT count(*) FROM unnest(%s::text[]) pat WHERE text LIKE pat) AS hits FROM chunks WHERE org_id = %s AND text LIKE ANY(%s)"
           + (" AND id = ANY(%s)" if allowed is not None else "") + " ORDER BY hits DESC, n_chars DESC LIMIT %s")
    args = [like, org_id, like] + ([list(map(int, allowed))] if allowed is not None else []) + [k]
    return [(r[0], float(r[1])) for r in conn.execute(sql, args)]


def fuse(*ranked):
    """Reciprocal rank fusion: a passage ranked high by several searches rises to the top."""
    score, why = {}, {}
    for name, lst in ranked:
        for rank, (cid, s) in enumerate(lst):
            score[cid] = score.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            why.setdefault(cid, {})[name] = (rank + 1, round(s, 3))
    return sorted(score.items(), key=lambda x: -x[1]), why


# ---------------------------------------------------------------------------------------------- the entry point
def search(conn, org_id, query, k=20, per_paper=3, mode="hybrid", paper_ids=None, synonyms=None):
    """The best `k` passages for a question, at most `per_paper` from any one paper. mode: hybrid | meaning | words."""
    allowed = None
    if paper_ids is not None:
        allowed = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE org_id = %s AND paper_id = ANY(%s)", (org_id, list(paper_ids)))]
    terms, exact = expand(query, synonyms)
    lists = []
    if mode in ("hybrid", "meaning"):
        lists.append(("meaning", vector_search(conn, org_id, query, allowed)))
    if mode in ("hybrid", "words"):
        lists.append(("words", word_search(conn, org_id, terms, allowed)))
        lists.append(("all words", strict_search(conn, org_id, query, allowed)))
        lists.append(("exact", exact_search(conn, org_id, exact, allowed)))
    fused, why = fuse(*lists)
    if not fused:
        return []
    ids = [c for c, _ in fused[: k * 6]]
    meta = {r[0]: r for r in conn.execute("SELECT id, paper_id FROM chunks WHERE id = ANY(%s)", (ids,))}
    out, used = [], {}
    for cid, sc in fused:
        pid = meta.get(cid, (None, None))[1]
        if pid is None or used.get(pid, 0) >= per_paper:
            continue
        used[pid] = used.get(pid, 0) + 1
        out.append({"chunk_id": cid, "paper_id": pid, "score": round(sc, 5), "why": why[cid]})
        if len(out) >= k:
            break
    return out


def passages(conn, hits):
    """Add the passage text and the paper's details to search hits."""
    if not hits:
        return []
    rows = {r[0]: r for r in conn.execute(
        """SELECT c.id, c.heading, c.page, c.text, c.section_kind, p.title, p.extracted_title, p.authors, p.journal, p.year, p.pmid, p.doi,
                  coalesce(p.source, 'pdf'), coalesce(p.text_level, 'full_text'), p.filename
           FROM chunks c JOIN papers p ON p.id = c.paper_id WHERE c.id = ANY(%s)""", ([h["chunk_id"] for h in hits],))}
    out = []
    for h in hits:
        r = rows.get(h["chunk_id"])
        if not r:
            continue
        authors = r[7] or []
        out.append({**h, "heading": r[1], "page": r[2], "text": r[3], "section_kind": r[4], "title": re.sub(r"<[^>]+>", "", r[5] or r[6] or r[14] or ""),
                    "authors": authors, "journal": r[8], "year": r[9], "pmid": r[10], "doi": r[11], "source": r[12], "text_level": r[13]})
    return out
