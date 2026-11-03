"""Self-check for the Phase 5 safeguards: encrypted API keys, key scrubbing, short quotes, and keys staying out of the settings zip.

Needs no database, no internet and no Anthropic spending (the key database and the key test are replaced by stand-ins).
Run:  .venv/Scripts/python.exe scripts/check_phase5.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")

from pubbank import apikey, bundle, orgs, quotes  # noqa: E402

problems = []


def check(name, ok):
    print(("ok     " if ok else "FAILED ") + name)
    if not ok:
        problems.append(name)


KEY = "sk-ant-api03-" + "A1b2C3d4" * 6

tmp = Path(tempfile.mkdtemp())
orgs.ORGS_DIR = tmp
(tmp / "demo").mkdir()
store = {}                                           # stands in for organizations.api_key_enc
apikey._read_db = lambda folder: store.get(folder, "")
apikey._write_db = lambda folder, token: (store.__setitem__(folder, token or "") or True)
apikey.test = lambda key: (True, "The key works.")
os.environ["ANTHROPIC_API_KEY"] = ""
orgs.load = lambda folder: {"db_slug": folder}

# 1. refuses to store anything without the encryption secret
os.environ.pop("PUBBANK_KEY_SECRET", None)
ok, msg = apikey.save("demo", KEY)
check("saving without PUBBANK_KEY_SECRET is refused", not ok and "PUBBANK_KEY_SECRET" in msg and not store.get("demo"))

# 2. encrypt / decrypt round trip, nothing readable stored
os.environ["PUBBANK_KEY_SECRET"] = "a long random test secret"
ok, msg = apikey.save("demo", KEY)
check("save works with a secret", ok)
check("stored value is not the key", store["demo"] and KEY not in store["demo"] and "sk-ant" not in store["demo"])
check("round trip returns the key", apikey.own_key("demo") == KEY)
check("status shows only last four", apikey.status("demo") == {"source": "own", "last4": KEY[-4:]})
os.environ["PUBBANK_KEY_SECRET"] = "a different secret"
check("a different secret cannot read it", apikey.own_key("demo") == "")
os.environ["PUBBANK_KEY_SECRET"] = "a long random test secret"

# 3. an old plain-text file moves into the database and is deleted
store.clear()
legacy = tmp / "demo" / "api_key.txt"
legacy.write_text(KEY + "\n", encoding="utf-8")
check("legacy key is still read", apikey.own_key("demo") == KEY)
check("legacy file was deleted", not legacy.exists())
check("legacy key now stored encrypted", bool(store.get("demo")) and KEY not in store["demo"])
check("key still readable afterwards", apikey.own_key("demo") == KEY)

# 4. removing
apikey.remove("demo")
check("remove clears the key", apikey.own_key("demo") == "" and not store.get("demo"))

# 5. scrubbing
dirty = f"Error 401 for key {KEY} and again {KEY[:20]}xyz"
clean = apikey.scrub(dirty)
check("scrub hides keys", "sk-ant-" not in clean and "[hidden]" in clean)
check("scrub leaves ordinary text alone", apikey.scrub("Nothing to hide here.") == "Nothing to hide here.")

# 6. quotes
passage = ("Patients with DADA2 showed reduced ADA2 activity in plasma. " + "Methods were standard. " * 30
           + "The p.R169Q variant abolished enzyme activity in transfected cells. Controls were normal.")
ex = quotes.excerpt(passage, hint="what did p.R169Q do to enzyme activity")
check("excerpt is short", len(ex) <= quotes.MAX_EXCERPT + 3)
check("excerpt picks the matching sentence", "R169Q" in ex)
check("short passages are untouched", quotes.excerpt("One short sentence.") == "One short sentence.")
long_quote = "“" + "word " * 120 + "”"
capped = quotes.cap_quoted("The authors wrote " + long_quote + " in the paper.")
check("long quotation is trimmed", len(capped) < len(long_quote) and "…" in capped)
para = "ADA2 deficiency causes vasculopathy and “short quote” appeared [1]."
check("paraphrase and short quotes are untouched", quotes.cap_quoted(para) == para)

# 7. the settings zip never carries a key
check("settings zip allowlist excludes key files", not bundle.ALLOWED.match("api_key.txt") and not bundle.ALLOWED.match("api_key.enc"))

print()
print("All checks passed." if not problems else f"{len(problems)} problem(s): " + "; ".join(problems))
sys.exit(1 if problems else 0)
