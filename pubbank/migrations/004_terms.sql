-- Who accepted which version of which legal document, and when (Member Terms by everyone, Organization Agreement by whoever creates a bank).
-- No foreign key on purpose: the record must survive if an account is later removed, so the email is kept here too.
CREATE TABLE IF NOT EXISTS terms_acceptances (
    id bigserial PRIMARY KEY,
    user_id int NOT NULL,
    email text NOT NULL,
    doc text NOT NULL,
    version text NOT NULL,
    org_id int,
    org_folder text,
    accepted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS terms_acceptances_user ON terms_acceptances (user_id, doc, version);
