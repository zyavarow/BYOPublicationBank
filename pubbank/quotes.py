"""Keeps quoting from papers short: answers may show a sentence or two per citation, always with a link to the original.

Pure text helpers (no database, no network) so they can be checked on their own.
"""
import re

MAX_EXCERPT = 450   # characters shown from one passage (about two sentences)
MAX_QUOTED = 260    # longest run of words an answer may put inside quotation marks

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
_QUOTED = re.compile(r"[\"“]([^\"“”]{%d,})[\"”]" % (MAX_QUOTED + 1))
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "and", "of", "in", "to", "a", "is", "was", "were", "with", "for", "that", "by", "on", "as", "are", "or", "from", "at", "an", "be", "this", "these"}


def _words(text):
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _clip(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def excerpt(text, hint="", limit=MAX_EXCERPT):
    """The one or two sentences of a passage that best match `hint` (the question or the answer text citing it), never longer than `limit`."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    sents = _SENT.split(text) or [text]
    want = _words(hint)
    best = 0
    if want:
        scores = [len(want & _words(s)) for s in sents]
        best = max(range(len(sents)), key=lambda i: (scores[i], -i))
    out = sents[best]
    if len(out) < limit * 0.5 and best + 1 < len(sents):
        out += " " + sents[best + 1]
    lead = "… " if best > 0 else ""
    return lead + _clip(out, limit)


def cap_quoted(text, limit=MAX_QUOTED):
    """Shorten any text inside quotation marks that runs longer than `limit` characters (the model was asked to quote only a sentence or two)."""
    def shrink(m):
        q = m.group(1)
        return "“" + _clip(q, limit) + "”"
    return _QUOTED.sub(shrink, text or "")
