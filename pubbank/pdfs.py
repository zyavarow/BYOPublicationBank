"""PDF intake for the setup wizard: save uploaded PDFs (or copy a folder from this computer) into data/raw/<organization>/ and run
the ingestion script on them in the background. Parsing PDFs is heavy, so it runs in its own process, never inside the web app."""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pubbank import bank, sources
from pubbank.db import connect

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(os.environ.get("PUBBANK_HOME") or ROOT) / "data" / "raw"
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_FILES_FROM_FOLDER = 5000


def raw_dir(db_slug):
    """The organization's PDF folder. The slug is only used as a folder name, never as a path."""
    name = re.sub(r"[^\w .()-]+", "_", db_slug).strip(" .") or "organization"
    return RAW / name


def safe_name(filename):
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f<>:\"|?*]+", "_", name).strip(" .")
    if not name.lower().endswith(".pdf"):
        return None
    return name[:180]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def save_upload(db_slug, filename, data):
    """Save one uploaded PDF. Returns 'saved', 'same file already there', or a short reason it was refused."""
    name = safe_name(filename)
    if not name:
        return "not a PDF file name"
    if not data.startswith(b"%PDF"):
        return "not a PDF (wrong file contents)"
    if len(data) > MAX_PDF_BYTES:
        return "larger than 200 MB"
    folder = raw_dir(db_slug)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    n = 2
    while dest.exists():
        if _sha(dest.read_bytes()) == _sha(data):
            return "same file already there"
        dest = folder / f"{dest.stem.rsplit(' (', 1)[0]} ({n}).pdf"
        n += 1
    dest.write_bytes(data)
    return "saved"


def scan_folder(path):
    """PDFs inside a folder on this computer: (list of (full path, relative path), total bytes). Raises ValueError if unusable."""
    p = Path(path.strip().strip('"')).expanduser()
    if not p.is_dir():
        raise ValueError("That folder was not found on this computer. Paste its full path, for example C:\\Users\\you\\Documents\\Papers.")
    files = []
    for f in sorted(p.rglob("*")):
        if f.suffix.lower() == ".pdf" and f.is_file():
            files.append((f, f.relative_to(p)))
            if len(files) > MAX_FILES_FROM_FOLDER:
                raise ValueError(f"That folder has more than {MAX_FILES_FROM_FOLDER} PDFs; please choose a smaller folder.")
    if not files:
        raise ValueError("No PDF files were found in that folder (subfolders are included).")
    return files, sum(f.stat().st_size for f, _ in files)


def run_job(jid, org_id, db_slug, copy_files=None):
    """Optionally copy files into the organization's folder, then ingest every PDF there that has not been read yet."""
    try:
        folder = raw_dir(db_slug)
        folder.mkdir(parents=True, exist_ok=True)
        with connect() as conn:
            before = bank.counts(conn, org_id)["total"]
        if copy_files:
            sources._set(jid, stage="Copying PDFs", done=0, total=len(copy_files))
            for i, (src, rel) in enumerate(copy_files, 1):
                dest = folder / rel
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                sources._set(jid, done=i)
        sources._set(jid, stage="Reading PDFs (about a minute each)", done=0, total=0)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "ingest.py"), "--org", folder.name], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        tail = []
        for line in proc.stdout:
            tail = (tail + [line.rstrip()])[-12:]
            m = re.match(r"^\[(\d+)/(\d+)\]", line)
            if m:
                sources._set(jid, done=int(m.group(1)), total=int(m.group(2)))
                continue
            m = re.search(r"processing (\d+)", line)
            if m and "PDFs in folder" in line:
                sources._set(jid, total=int(m.group(1)))
        code = proc.wait()
        if code != 0:
            raise RuntimeError("The PDF reader stopped with an error: " + " | ".join(tail[-4:]))
        sources._set(jid, stage="Updating the bank")
        with connect() as conn:
            sources.ensure_columns(conn)
            bank.apply_rules(conn, org_id)
            after = bank.counts(conn, org_id)
        added = after["total"] - before
        sources._set(jid, finished=True, stage="Done", result={
            "added": added, "kind": "pdf", "in_bank": after["inbank"] + after["abstract"], "ocr": after["ocr"],
            "duplicate": after["duplicate"], "setaside": after["setaside"], "failed": after["failed"]})
    except Exception as e:
        sources._set(jid, finished=True, stage="Failed", error=f"{type(e).__name__}: {e}")
