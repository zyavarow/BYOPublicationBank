-- Magic-link sign-in: an account may have no password (it signs in with an emailed link instead). No existing data changes.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
