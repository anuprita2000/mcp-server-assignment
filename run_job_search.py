"""
run_job_search.py
-----------------
Entry point called by macOS launchd twice daily (9 AM and 2 PM ET).

Usage:
    python run_job_search.py "Morning Run (9 AM ET)"
    python run_job_search.py "Afternoon Run (2 PM ET)"
    python run_job_search.py               # label defaults to "Scheduled Run"
    python run_job_search.py --test        # sends a test email without scraping
    python run_job_search.py --tier 1      # scrape Tier 1 companies only
"""

import sys
from datetime import datetime
from pathlib import Path

from job_scraper import get_new_jobs
from email_digest import send_digest
from excel_tracker import append_jobs

BASE_DIR = Path(__file__).parent
MORNING_STAMP   = BASE_DIR / ".last_run_morning"
AFTERNOON_STAMP = BASE_DIR / ".last_run_afternoon"


def _stamp_run(label: str) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if "Morning" in label:
        MORNING_STAMP.write_text(today)
    elif "Afternoon" in label:
        AFTERNOON_STAMP.write_text(today)


def main() -> None:
    args = sys.argv[1:]

    # ── Test mode: send a dummy email to verify credentials ──────────────────
    if "--test" in args:
        print("[run] Test mode — sending verification email")
        test_jobs = [{
            "company":   "Test Company",
            "title":     "Senior Program Manager (TEST — delete me)",
            "location":  "New York, NY",
            "url":       "https://example.com/jobs/test",
            "source":    "Greenhouse",
            "posted_at": "now",
        }]
        send_digest(test_jobs, run_label="Credential Test")
        return

    # ── Tier filter ───────────────────────────────────────────────────────────
    tier_filter = None
    if "--tier" in args:
        try:
            tier_filter = int(args[args.index("--tier") + 1])
        except (IndexError, ValueError):
            print("Usage: python run_job_search.py [--tier 1|2|3]")
            sys.exit(1)

    # ── Run label (passed as first non-flag argument) ─────────────────────────
    run_label = next(
        (a for a in args if not a.startswith("--")), "Scheduled Run"
    )

    print(f"[run] Starting — {run_label}")

    jobs = get_new_jobs(tier_filter=tier_filter)
    print(f"[run] {len(jobs)} new job(s) found")

    added = append_jobs(jobs, run_label=run_label)
    print(f"[run] {added} row(s) added to jobs_tracker.xlsx")

    sent = send_digest(jobs, run_label=run_label)
    if sent:
        print("[run] Digest sent successfully")
    else:
        print("[run] Digest not sent — check logs above")

    _stamp_run(run_label)
    print("[run] Done")


if __name__ == "__main__":
    main()
