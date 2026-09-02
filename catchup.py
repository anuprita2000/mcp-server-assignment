"""
catchup.py
----------
Runs at Mac login/wake via launchd.
Checks whether today's morning (9 AM) and afternoon (2 PM) scrapes have
already happened; fires any that were missed (e.g. laptop was off).

A lightweight timestamp file (.last_run_morning / .last_run_afternoon) in
the project directory is used as the record — no external dependencies needed.
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
PYTHON   = BASE_DIR / ".venv" / "bin" / "python"
SCRIPT   = BASE_DIR / "run_job_search.py"

MORNING_STAMP   = BASE_DIR / ".last_run_morning"
AFTERNOON_STAMP = BASE_DIR / ".last_run_afternoon"

MORNING_HOUR   = 9
AFTERNOON_HOUR = 14


def stamp_today(path: Path) -> None:
    path.write_text(datetime.now().strftime("%Y-%m-%d"))


def already_ran_today(path: Path) -> bool:
    if not path.exists():
        return False
    return path.read_text().strip() == datetime.now().strftime("%Y-%m-%d")


def run(label: str) -> None:
    print(f"[catchup] Firing missed run: {label}", flush=True)
    subprocess.run([str(PYTHON), str(SCRIPT), label], check=False)


def main() -> None:
    now_hour = datetime.now().hour

    # Morning catch-up: runs if past 9 AM and morning hasn't run yet today
    if now_hour >= MORNING_HOUR and not already_ran_today(MORNING_STAMP):
        stamp_today(MORNING_STAMP)
        run("Morning Run (9 AM ET)")

    # Afternoon catch-up: runs if past 2 PM and afternoon hasn't run yet today
    if now_hour >= AFTERNOON_HOUR and not already_ran_today(AFTERNOON_STAMP):
        stamp_today(AFTERNOON_STAMP)
        run("Afternoon Run (2 PM ET)")

    print("[catchup] Done", flush=True)


if __name__ == "__main__":
    main()
