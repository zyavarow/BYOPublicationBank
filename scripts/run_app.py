"""Start the Research Portal Setup Kit on this computer only (http://127.0.0.1:8000). Stop it with Ctrl+C.
  .venv/Scripts/python.exe scripts/run_app.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import uvicorn  # noqa: E402

if __name__ == "__main__":
    # 127.0.0.1 = reachable only from this computer, never from the network.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")
