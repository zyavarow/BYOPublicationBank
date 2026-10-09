"""Checks that every supporting quote Claude returned really appears in the paper text it was given.

Statuses: verified (exact match after normalizing whitespace, case, dashes, quote marks, markdown),
approximate (>= 92% fuzzy match, e.g. odd hyphenation), not_found, too_short (< 8 characters).
"""
import re
import unicodedata

from rapidfuzz import fuzz

_DASHES = {ord(c): "-" for c in "‐‑‒–—―−"}
_QUOTES = {**{ord(c): "'" for c in "‘’‚‛′"}, **{ord(c): '"' for c in "“”„‟″"}}


def normalize(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("­", "")
    s = s.translate(_DASHES).translate(_QUOTES)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # markdown links: keep the link text
    # only real tags like <i> or </sup>; a stray "<" in "p<0.05" must not swallow text up to the next ">"
    s = re.sub(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]{0,80})?>", " ", s)
    s = re.sub(r"[*_`#>|]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def check_quote(quote, normalized_text):
    q = normalize(quote)
    if len(q) < 8:
        return "too_short"
    if q in normalized_text:
        return "verified"
    return "approximate" if fuzz.partial_ratio(q, normalized_text, score_cutoff=92) else "not_found"


def verify_extraction(data, paper_text):
    """Annotate every evidence dict (anything with quote/section/page) in `data` with a 'verification'
    status, in place. Returns counts per status."""
    haystack = normalize(paper_text)
    stats = {"total": 0, "verified": 0, "approximate": 0, "not_found": 0, "too_short": 0}

    def walk(node):
        if isinstance(node, dict):
            if {"quote", "section", "page"} <= node.keys():
                status = check_quote(node["quote"], haystack)
                node["verification"] = status
                stats["total"] += 1
                stats[status] += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return stats
