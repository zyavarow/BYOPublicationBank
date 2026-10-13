"""Numbered database migrations, run once each at start-up (so every installation ends up with the same structure).

Files in this folder are applied in name order: 001_baseline.sql, 002_accounts.sql, ... The first one creates the whole pilot structure in a
brand-new database; in a database that already has the papers table (the pilot's) it is only recorded, not run. To change the structure
later, add a new numbered .sql file: never edit one that has already been applied anywhere.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = 7654321      # one installation process migrates at a time


def run(conn):
    """Apply every migration that has not been applied yet. Returns the list of versions applied now."""
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
    applied = []
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        conn.commit()
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        existing = bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'papers'").fetchone())
        for f in sorted(HERE.glob("[0-9][0-9][0-9]_*.sql")):
            if f.stem in done:
                continue
            if not (f.stem.endswith("_baseline") and existing):
                conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.stem,))
            conn.commit()
            applied.append(f.stem)
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
        conn.commit()
    return applied
