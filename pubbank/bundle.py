"""Move an organization's settings between installations (laptop to host, test copy to the organization's own copy) as one zip file.

The bundle holds what the setup wizard saved in orgs/<folder>/: settings, logo, brand guide, template workbooks and proposal. It never contains
the organization's Anthropic key, the papers or the database (those travel separately).
"""
import io
import re
import zipfile

from pubbank import orgs

ALLOWED = re.compile(r"^(config\.toml|overlay\.xlsx|extraction_template\.xlsx|proposal\.json|logo\.[a-z]{2,4}|brand_guide\.[a-z]{2,4}|uploads/[^/\\\x00]{1,200})$")
MAX_BYTES = 150 * 1024 * 1024


def export(folder):
    """The organization's settings files as zip bytes."""
    base = orgs.ORGS_DIR / folder
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(base).as_posix()
            if p.is_file() and ALLOWED.match(rel):
                z.write(p, rel)
    return buf.getvalue()


def import_bundle(folder, data):
    """Write a bundle into orgs/<folder>/ (creating it). Returns the list of files written; raises ValueError for anything unexpected."""
    if not orgs.FOLDER.match(folder):
        raise ValueError("The folder name must be lower-case letters, digits and hyphens.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That is not a zip file.")
    infos = [i for i in z.infolist() if not i.is_dir()]
    if not infos:
        raise ValueError("The zip file is empty.")
    if sum(i.file_size for i in infos) > MAX_BYTES:
        raise ValueError("The bundle is too large.")
    bad = [i.filename for i in infos if not ALLOWED.match(i.filename)]
    if bad:
        raise ValueError("The bundle contains files that are not organization settings: " + ", ".join(bad[:3]))
    if "config.toml" not in {i.filename for i in infos}:
        raise ValueError("The bundle has no config.toml, so it is not an organization bundle.")
    base = orgs.ORGS_DIR / folder
    written = []
    for i in infos:
        dest = base / i.filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(i))
        written.append(i.filename)
    return written
