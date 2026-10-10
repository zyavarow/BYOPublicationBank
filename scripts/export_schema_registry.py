"""Write the schema registry (standard categories + what each organization customized) to an Excel file in data/reports/.
  .venv/Scripts/python.exe scripts/export_schema_registry.py
(The same file can be downloaded from the home page of the setup kit.)
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pubbank import registry  # noqa: E402

out = ROOT / "data" / "reports" / f"schema_registry_{datetime.now():%Y%m%d_%H%M}.xlsx"
out.write_bytes(registry.workbook())
print("wrote", out)
