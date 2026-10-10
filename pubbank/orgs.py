"""Organizations: one folder per organization under orgs/<folder>/ holding its settings (config.toml), logo, template overlay.

config.toml holds what the setup wizard's first screen collects (name, description, website, colors, logo). `db_slug` links the folder to the
organization's rows in the database (organizations.slug), which is also the folder name under data/raw/.
"""
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORGS_DIR = ROOT / "orgs"

DEFAULTS = {
    "name": "", "description": "", "website": "", "topic_terms": "", "answer_format": "plain", "search_depth": "standard", "allow_depth_choice": "yes", "daily_cap": "5.00", "schema_approved": "",
    "primary": "#2B6E5E", "accent": "#B5592E", "logo": "", "brand_guide": "", "db_slug": "",
}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
FOLDER = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40].strip("-") or "organization"


def valid_folder(folder):
    return bool(FOLDER.match(folder or "")) and (ORGS_DIR / folder).is_dir()


def load(folder):
    cfg = dict(DEFAULTS)
    path = ORGS_DIR / folder / "config.toml"
    if path.exists():
        with open(path, "rb") as f:
            cfg.update({k: v for k, v in tomllib.load(f).items() if k in DEFAULTS})
    cfg["folder"] = folder
    if not cfg["name"]:
        cfg["name"] = folder.upper()
    return cfg


def save(folder, cfg):
    lines = ["# Organization settings (written by the setup kit; safe to edit by hand)."]
    for k in DEFAULTS:
        lines.append(f"{k} = {json.dumps(str(cfg.get(k, DEFAULTS[k])), ensure_ascii=False)}")
    (ORGS_DIR / folder / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def list_orgs():
    return [load(p.name) for p in sorted(ORGS_DIR.iterdir()) if p.is_dir() and FOLDER.match(p.name)]


def initials(name):
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def create(name, conn):
    """Create a new organization: a folder, a config file and a database row. Returns the folder name."""
    base = slugify(name)
    folder, n = base, 2
    while (ORGS_DIR / folder).exists():
        folder, n = f"{base}-{n}", n + 1
    (ORGS_DIR / folder).mkdir(parents=True)
    cfg = dict(DEFAULTS, name=name.strip(), db_slug=name.strip())
    conn.execute("INSERT INTO organizations (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
                 (cfg["db_slug"], cfg["name"]))
    conn.commit()
    save(folder, cfg)
    return folder


def bank_counts(conn, db_slug):
    """(papers received, papers in the bank) for an organization, or (0, 0) if it has none."""
    row = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE p.in_bank) FROM papers p JOIN organizations o ON o.id = p.org_id "
        "WHERE o.slug = %s", (db_slug,)).fetchone()
    return row[0], row[1]
