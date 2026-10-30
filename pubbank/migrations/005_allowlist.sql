-- Who may create a publication bank and become its administrator during the beta (exact email addresses, lower case),
-- and the requests for access that people send from the "invite-only" screen.
CREATE TABLE IF NOT EXISTS admin_allowlist (
    email text PRIMARY KEY,
    source text NOT NULL DEFAULT 'added by owner',
    added_at timestamptz NOT NULL DEFAULT now(),
    added_by int
);

-- everyone who is already an administrator of an organization starts on the list
INSERT INTO admin_allowlist (email, source)
SELECT DISTINCT lower(u.email), 'existing administrator'
FROM users u JOIN memberships m ON m.user_id = u.id
WHERE m.role = 'admin' AND u.active
ON CONFLICT (email) DO NOTHING;

CREATE TABLE IF NOT EXISTS access_requests (
    id bigserial PRIMARY KEY,
    email text NOT NULL,
    name text NOT NULL DEFAULT '',
    note text NOT NULL DEFAULT '',
    requested_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'approved', 'dismissed'))
);
