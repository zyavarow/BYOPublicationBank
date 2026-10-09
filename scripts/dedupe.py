"""Duplicate detection. Definite duplicates (same file / DOI / PMID) get duplicate_of set;
fuzzy matches (similar title, Zotero '-1' copies) are only flagged as possible_duplicate_of."""
import re

from rapidfuzz import fuzz


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split())


def dedupe_org(conn, org_id):
    conn.execute(
        "UPDATE papers SET duplicate_of=NULL, duplicate_reason=NULL, possible_duplicate_of=NULL, "
        "possible_duplicate_reason=NULL WHERE org_id=%s", (org_id,))
    # papers that "parsed" but produced almost no text are effectively scanned: flag them for OCR
    conn.execute("UPDATE papers SET needs_ocr=true WHERE org_id=%s AND parse_status='ok' AND page_count>=3 "
                 "AND char_count::float/page_count < 100", (org_id,))
    cols = ["id", "filename", "file_sha256", "doi", "pmid", "title", "extracted_title", "year",
            "parse_status", "char_count"]
    rows = [dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM papers WHERE org_id=%s AND parse_status IN ('ok','skipped_ocr','duplicate_skipped')", (org_id,))]
    by_id = {r["id"]: r for r in rows}

    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys = {}
    for r in rows:
        for kind, val in (("sha", r["file_sha256"]), ("doi", (r["doi"] or "").lower()), ("pmid", r["pmid"])):
            if val:
                keys.setdefault((kind, val), []).append(r["id"])
    for ids in keys.values():
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])

    groups = {}
    for r in rows:
        groups.setdefault(find(r["id"]), []).append(r)

    def rank(r):
        zotero_copy = bool(re.search(r"-\d+\.pdf$", r["filename"], re.I))
        return (r["parse_status"] != "ok", -(r["char_count"] or 0), zotero_copy, r["id"])

    canon = set()
    for members in groups.values():
        members.sort(key=rank)
        keep = members[0]
        canon.add(keep["id"])
        for dup in members[1:]:
            if dup["file_sha256"] and dup["file_sha256"] == keep["file_sha256"]:
                why = "identical file"
            elif dup["doi"] and (dup["doi"] or "").lower() == (keep["doi"] or "").lower():
                why = "same DOI"
            elif dup["pmid"] and dup["pmid"] == keep["pmid"]:
                why = "same PMID"
            else:
                why = "linked through another copy"
            conn.execute("UPDATE papers SET duplicate_of=%s, duplicate_reason=%s WHERE id=%s",
                         (keep["id"], f"{why} as '{keep['filename']}'", dup["id"]))

    # fuzzy / filename-pattern flags among the papers still considered unique
    uniq = sorted((by_id[i] for i in canon), key=lambda r: r["id"])
    for idx, a in enumerate(uniq):
        ta = _norm(a["title"] or a["extracted_title"])
        stem_a = re.sub(r"\.pdf$", "", a["filename"], flags=re.I)
        base_a = re.sub(r"-\d+$", "", stem_a)
        for b in uniq[:idx]:
            reason = None
            stem_b = re.sub(r"\.pdf$", "", b["filename"], flags=re.I)
            base_b = re.sub(r"-\d+$", "", stem_b)
            tb = _norm(b["title"] or b["extracted_title"])
            # token_sort_ratio, not token_set_ratio: a short title contained in a longer one must not match.
            sc = fuzz.token_sort_ratio(ta, tb) if (ta and tb) else None
            # Zotero numbers *different* papers by the same author+year with -1/-2, so a suffix alone proves nothing.
            if (base_a == base_b and base_a != "" and (stem_a != base_a or stem_b != base_b)
                    and (sc is None or sc >= 80)):
                reason = "filename differs only by a '-1' style suffix and titles are similar or missing (Zotero second copy?)"
            elif sc is not None and len(ta) >= 25 and len(tb) >= 25 and sc >= 92:
                ya, yb = a["year"], b["year"]
                if ya is None or yb is None or abs(ya - yb) <= 1:
                    reason = f"very similar title (score {sc:.0f})"
            if reason:
                conn.execute(
                    "UPDATE papers SET possible_duplicate_of=%s, possible_duplicate_reason=%s WHERE id=%s",
                    (b["id"], f"{reason}; compare with '{b['filename']}'", a["id"]))
                break
    conn.commit()
