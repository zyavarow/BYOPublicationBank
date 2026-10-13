-- Accounts: people who can sign in, which organizations they belong to, their sessions, invite / reset links and an audit trail.
CREATE TABLE IF NOT EXISTS users (
    id serial PRIMARY KEY,
    email text NOT NULL,
    name text NOT NULL DEFAULT '',
    password_hash text NOT NULL,
    is_super boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower ON users (lower(email));

CREATE TABLE IF NOT EXISTS memberships (
    user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_folder text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'member')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, org_folder)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash text PRIMARY KEY,
    user_id int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    last_seen timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invites (
    token_hash text PRIMARY KEY,
    email text NOT NULL,
    org_folder text,
    role text,
    purpose text NOT NULL DEFAULT 'invite',
    created_by int,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at timestamptz
);

CREATE TABLE IF NOT EXISTS audit_log (
    id bigserial PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    user_id int,
    action text NOT NULL,
    detail text
);

-- who asked each question (so an organization's admins can see its question log)
ALTER TABLE ask_log ADD COLUMN IF NOT EXISTS user_id int;
