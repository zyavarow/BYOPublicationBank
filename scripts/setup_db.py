"""Create the publication_bank database if missing and verify the connection."""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pubbank.db import psycopg, sql  # noqa: E402  (must come before any other psycopg import)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
url = os.environ.get("DATABASE_URL", "")
if not url or "YOUR_POSTGRES_PASSWORD" in url:
    sys.exit("DATABASE_URL in .env is missing or still has the placeholder password.")

parsed = urlparse(url)
dbname = parsed.path.lstrip("/")
admin_url = parsed._replace(path="/postgres").geturl()

try:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if exists:
            print(f"Database '{dbname}' already exists.")
        else:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            print(f"Created database '{dbname}'.")
except psycopg.OperationalError as e:
    msg = str(e).lower()
    if "password authentication failed" in msg:
        sys.exit("Connected to Postgres, but the password was rejected. Check DATABASE_URL in .env.")
    sys.exit(f"Could not connect to Postgres: {type(e).__name__}. Is the service running and the port/host correct?")

with psycopg.connect(url) as conn:
    version = conn.execute("SHOW server_version").fetchone()[0]
    print(f"Connected to '{dbname}' on Postgres {version}. Setup OK.")
