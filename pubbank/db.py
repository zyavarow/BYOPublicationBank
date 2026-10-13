"""Database access for every script.

Windows Smart App Control blocks the libpq library bundled inside `psycopg-binary` (unsigned), so psycopg is
switched to its pure-Python mode and pointed at the signed `libpq.dll` that ships with the PostgreSQL install.
Always import psycopg through this module:  from pubbank.db import psycopg, connect
"""
import os
from pathlib import Path

_CANDIDATES = [os.environ.get("PG_BIN")] + [rf"C:\Program Files\PostgreSQL\{v}\bin" for v in (18, 17, 16, 15)]


def pg_bin():
    """Folder holding the signed PostgreSQL tools (libpq.dll, pg_dump.exe, ...)."""
    for c in _CANDIDATES:
        if c and (Path(c) / "libpq.dll").exists():
            return Path(c)
    return None


def _prepare():
    """Point psycopg at the one signed file it needs (libpq.dll) WITHOUT touching the search path.
    Putting the whole Postgres folder on PATH / add_dll_directory makes other packages (SciPy, the PDF parser, lxml)
    load Postgres's copies of zlib, libxml2, libzstd... and fail, so only the single libpq.dll path is exposed."""
    if os.name != "nt":
        return          # only Windows Smart App Control needs this workaround; elsewhere psycopg finds its own libpq
    os.environ["PSYCOPG_IMPL"] = "python"
    folder = pg_bin()
    if folder:
        import ctypes.util
        libpq = str(folder / "libpq.dll")
        original = ctypes.util.find_library

        def find_library(name, _orig=original, _libpq=libpq):
            return _libpq if name in ("pq", "libpq", "libpq.dll") else _orig(name)

        ctypes.util.find_library = find_library


_prepare()

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402,F401
from psycopg.types.json import Jsonb  # noqa: E402,F401


def connect(url=None, **kwargs):
    return psycopg.connect(url or os.environ["DATABASE_URL"], **kwargs)
