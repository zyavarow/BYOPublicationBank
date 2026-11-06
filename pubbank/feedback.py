"""Ratings people give to answers: "helpful" or "not helpful", with an optional comment. Only the person who asked can rate an answer; the
organization's administrators and the owner can read the ratings next to the question in the question log."""
MAX_COMMENT = 1000
RATINGS = ("up", "down")


def save(conn, org_id, ask_id, user_id, rating, comment=""):
    """Store (or replace) the rating for one answer. Raises ValueError for a rating we do not know."""
    if rating not in RATINGS:
        raise ValueError("Choose helpful or not helpful.")
    comment = (comment or "").strip()[:MAX_COMMENT]
    conn.execute(
        """INSERT INTO answer_feedback (ask_id, org_id, user_id, rating, comment) VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (ask_id) DO UPDATE SET rating = EXCLUDED.rating, comment = EXCLUDED.comment, updated_at = now()""",
        (ask_id, org_id, user_id, rating, comment))
    conn.commit()


def mine(conn, ask_id):
    """The rating already given to this answer, as the screen needs it, or None."""
    r = conn.execute("SELECT rating, comment FROM answer_feedback WHERE ask_id = %s", (ask_id,)).fetchone()
    return {"rating": r[0], "comment": r[1]} if r else None


def for_asks(conn, ask_ids):
    """{ask_id: {"rating", "comment", "at"}} for the answers on one page of the question log."""
    if not ask_ids:
        return {}
    rows = conn.execute("SELECT ask_id, rating, comment, updated_at FROM answer_feedback WHERE ask_id = ANY(%s)", (list(ask_ids),)).fetchall()
    return {r[0]: {"rating": r[1], "comment": r[2], "at": r[3]} for r in rows}


def counts(conn, org_id, days=30):
    """(helpful, not helpful) for the period shown on the Admin page."""
    where, args = ["org_id = %s"], [org_id]
    if days:
        where.append("created_at >= now() - make_interval(days => %s)")
        args.append(days)
    r = conn.execute(f"SELECT count(*) FILTER (WHERE rating = 'up'), count(*) FILTER (WHERE rating = 'down') FROM answer_feedback WHERE {' AND '.join(where)}", args).fetchone()
    return r[0], r[1]
