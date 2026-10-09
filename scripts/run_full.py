"""Full-library ingestion driver. Pass 1: PDFs up to 30 pages, two parallel processes.
Pass 2: the long documents (31+ pages), one process (a 200-page PDF can use a lot of memory).
Then dedupe, software-paper flagging, retry of unmatched lookups, and the report.

Safe to re-run: finished PDFs are skipped. If a worker process dies hard on one PDF (the parser can
crash without a Python error), that PDF is recorded as a failure and the worker is restarted.
If the laptop is unplugged and the battery gets low, workers are stopped cleanly.
Logs go to data/logs/.
  .venv\\Scripts\\python.exe scripts\\run_full.py --org "EXD Papers"
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
INGEST = str(ROOT / "scripts" / "ingest.py")
LOW_BATTERY_PERCENT = 20
MAX_CRASH_RESTARTS = 40


def stamp():
    return time.strftime("%H:%M:%S")


def battery_low():
    """True when running on battery at or below LOW_BATTERY_PERCENT (stop cleanly instead of dying)."""
    if sys.platform != "win32":
        return False
    import ctypes

    class PowerStatus(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    st = PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return False
    return st.ACLineStatus == 0 and st.BatteryLifePercent <= LOW_BATTERY_PERCENT


def unfinished_file(log_path):
    """Name of the PDF a worker was on when it died: the last 'starting:' line with no result line after it."""
    lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        m = re.match(r"^\[\d+/\d+\] starting: (.+)$", lines[idx])
        if m:
            name = m.group(1)
            if any(name in later for later in lines[idx + 1:]):
                return None
            return name
    return None


def python_traceback_in_tail(log_path):
    tail = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
    return any("Traceback (most recent call last)" in ln for ln in tail)


def run_pass(name, org, shards, threads, extra):
    logs = ROOT / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    def launch(k):
        log_path = logs / f"full_run_{org.replace(' ', '_')}_{name}_shard{k}.log"
        cmd = [PY, INGEST, "--org", org, "--threads", str(threads), "--no-report", *extra]
        if shards > 1:
            cmd += ["--shard", f"{k}/{shards}"]
        with open(log_path, "a", encoding="utf-8") as log:
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        return p, log_path

    workers = {}
    restarts = {}
    for k in range(1, shards + 1):
        workers[k] = launch(k)
        restarts[k] = 0
        time.sleep(10)

    while workers:
        time.sleep(20)
        if battery_low():
            for p, _ in workers.values():  # kill the whole process tree; finished papers are already saved
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            print(f"[{stamp()}] battery at or below {LOW_BATTERY_PERCENT}% and unplugged: "
                  f"stopped cleanly. Plug in and re-run to resume.", flush=True)
            return False
        for k, (p, log_path) in list(workers.items()):
            code = p.poll()
            if code is None:
                continue
            if code == 0:
                print(f"[{stamp()}] pass '{name}' worker {k} finished", flush=True)
                del workers[k]
                continue
            restarts[k] += 1
            culprit = unfinished_file(log_path)
            if culprit and not python_traceback_in_tail(log_path):
                print(f"[{stamp()}] worker {k} crashed (exit {code}) on '{culprit}': recording as failed, restarting", flush=True)
                subprocess.run([PY, INGEST, "--org", org, "--mark-failed", culprit, "--reason",
                                f"parser process crashed (exit code {code})"], cwd=ROOT, capture_output=True)
            else:
                print(f"[{stamp()}] worker {k} exited with code {code} (python error or between files): restarting", flush=True)
            if restarts[k] > MAX_CRASH_RESTARTS:
                print(f"[{stamp()}] worker {k}: too many crashes, giving up on it", flush=True)
                del workers[k]
            else:
                workers[k] = launch(k)
    print(f"[{stamp()}] pass '{name}' finished", flush=True)
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    ap.add_argument("--flag-software", action="store_true", help="EXD library only: flag EXD-software papers")
    a = ap.parse_args()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # no idle sleep while this process runs
    print(f"[{stamp()}] starting full run", flush=True)
    if not run_pass("short", a.org, shards=2, threads=6, extra=["--max-pages", "30"]):
        sys.exit(0)
    if not run_pass("long", a.org, shards=1, threads=12, extra=["--min-pages", "31"]):
        sys.exit(0)
    subprocess.run([PY, INGEST, "--org", a.org, "--rematch", *(["--flag-software"] if a.flag_software else [])], cwd=ROOT)  # dedupes, builds report
    print(f"[{stamp()}] ALL DONE", flush=True)
