-- A rating (and optional comment) that the person who asked gives an answer. One per answer; changing your mind replaces it.
-- No foreign key on purpose, like the terms record: a removed question or account is deleted by the owner's deletion step, not by a cascade.
CREATE TABLE IF NOT EXISTS answer_feedback (
    id bigserial PRIMARY KEY,
    ask_id int NOT NULL UNIQUE,
    org_id int NOT NULL,
    user_id int,
    rating text NOT NULL CHECK (rating IN ('up', 'down')),
    comment text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS answer_feedback_org ON answer_feedback (org_id, created_at);
