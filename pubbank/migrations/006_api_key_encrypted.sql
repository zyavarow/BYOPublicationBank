-- Each organization's own Anthropic key, stored encrypted (Fernet; the secret is the PUBBANK_KEY_SECRET host setting, never in the database).
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS api_key_enc text;

-- Where each paper's text may be used from (groundwork for license tracking; filled in by a later step).
ALTER TABLE papers ADD COLUMN IF NOT EXISTS license text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS license_source text;
