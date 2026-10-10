"""Builds and updates the search index of an organization: passages (chunks) of every paper in the bank, with a word index and a vector.

Resumable: passages are stored first; vectors are added in batches, so an interrupted build continues where it stopped.
"""
import time

from pubbank import embeddings
from pubbank.chunking import chunk_paper
from pubbank.db import connect

DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    id bigserial PRIMARY KEY, org_id int NOT NULL, paper_id int NOT NULL REFERENCES papers(id) ON DELETE CASCADE, idx int NOT NULL,
    section_kind text, heading text, page int, text text NOT NULL, n_chars int, tsv tsvector, embedding real[], model text,
    created_at timestamptz DEFAULT now());
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin(tsv);
CREATE INDEX IF NOT EXISTS chunks_paper ON chunks(paper_id);
CREATE INDEX IF NOT EXISTS chunks_org ON chunks(org_id);
"""


def ensure_table(conn):
    if not conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'chunks'").fetchone():
        conn.execute(DDL)
    conn.commit()


def status(conn, org_id):
    """(papers in the bank, papers with passages, passages, passages with vectors)."""
    ensure_table(conn)
    papers = conn.execute("SELECT count(*) FROM papers WHERE org_id = %s AND in_bank AND parse_status = 'ok'", (org_id,)).fetchone()[0]
    r = conn.execute("SELECT count(DISTINCT paper_id), count(*), count(embedding) FROM chunks WHERE org_id = %s", (org_id,)).fetchone()
    return {"papers": papers, "papers_indexed": r[0], "chunks": r[1], "embedded": r[2]}


def embed_text(title, heading, text):
    """What the vector is computed from: the paper's title and the section heading give the passage its context."""
    head = " ".join(x for x in ((title or "")[:160], heading[:80]) if x)
    return (head + ". " + text)[:3000]


def chunk_new_papers(conn, org_id, rebuild=False, on_progress=None):
    """Create passages for in-bank papers that have none (or for all, when rebuilding). Returns the number of papers chunked."""
    ensure_table(conn)
    if rebuild:
        conn.execute("DELETE FROM chunks WHERE org_id = %s", (org_id,))
        conn.commit()
    # papers that left the bank no longer belong in the index
    conn.execute("DELETE FROM chunks WHERE org_id = %s AND paper_id NOT IN (SELECT id FROM papers WHERE org_id = %s AND in_bank)", (org_id, org_id))
    rows = conn.execute(
        """SELECT p.id, p.sections, p.abstract, p.full_text FROM papers p
           WHERE p.org_id = %s AND p.in_bank AND p.parse_status = 'ok' AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.paper_id = p.id) ORDER BY p.id""",
        (org_id,)).fetchall()
    for n, (pid, sections, abstract, full_text) in enumerate(rows, 1):
        parts = chunk_paper({"sections": sections, "abstract": abstract, "full_text": full_text})
        for c in parts:
            conn.execute(
                """INSERT INTO chunks (org_id, paper_id, idx, section_kind, heading, page, text, n_chars, tsv)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('english', coalesce(%s, '') || ' ' || %s))""",
                (org_id, pid, c["idx"], c["section_kind"], c["heading"], c["page"], c["text"], len(c["text"]), c["heading"], c["text"]))
        conn.commit()
        if on_progress:
            on_progress("Cutting papers into passages", n, len(rows))
    return len(rows)


def embed_pending(conn, org_id, batch=32, on_progress=None, stop=lambda: False, shard=None):
    """Add vectors to every passage that lacks one. Commits after each batch."""
    part, nparts = shard or (0, 1)                 # several workers can share the job: worker k takes passages whose id % n == k
    todo = conn.execute("SELECT count(*) FROM chunks WHERE org_id = %s AND embedding IS NULL AND id %% %s = %s", (org_id, nparts, part)).fetchone()[0]
    done = 0
    t0 = time.time()
    while not stop():
        rows = conn.execute(
            """SELECT c.id, p.title, p.extracted_title, c.heading, c.text FROM chunks c JOIN papers p ON p.id = c.paper_id
               WHERE c.org_id = %s AND c.embedding IS NULL AND c.id %% %s = %s ORDER BY c.id LIMIT %s""", (org_id, nparts, part, batch)).fetchall()
        if not rows:
            break
        vecs = embeddings.embed([embed_text(t or et, h, x) for _id, t, et, h, x in rows])
        for (cid, *_), v in zip(rows, vecs):
            conn.execute("UPDATE chunks SET embedding = %s, model = %s WHERE id = %s", (v.tolist(), embeddings.MODEL_NAME, cid))
        conn.commit()
        done += len(rows)
        if on_progress:
            on_progress("Reading passages for meaning-based search", done, todo)
    return done


def build(org_id, rebuild=False, on_progress=None, stop=lambda: False):
    conn = connect()
    try:
        chunk_new_papers(conn, org_id, rebuild, on_progress)
        embed_pending(conn, org_id, on_progress=on_progress, stop=stop)
        return status(conn, org_id)
    finally:
        conn.close()
