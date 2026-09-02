"""
email_digest.py
---------------
Formats job search results into an HTML email digest and sends it
via Gmail SMTP using an App Password.

Credentials are loaded from .env — never hardcoded here.

.env keys used:
    SENDER_EMAIL        your.email@gmail.com
    GMAIL_APP_PASSWORD  16-character App Password (spaces are stripped automatically)
    RECIPIENT_EMAIL     where the digest is delivered (can match SENDER_EMAIL)
"""

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Credentials (sourced exclusively from .env) ───────────────────────────────
SENDER_EMAIL       = os.getenv("SENDER_EMAIL", "").strip()
# Google's App Password UI uses non-breaking spaces (U+00A0) between groups,
# not regular spaces. Strip all whitespace variants so copying from the browser
# doesn't silently break authentication.
_raw_pw = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_APP_PASSWORD = "".join(c for c in _raw_pw if not c.isspace() and c != "\xa0")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", "").strip()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS


# ── Email formatter ───────────────────────────────────────────────────────────
def format_email(jobs: list[dict], run_label: str) -> tuple[str, str]:
    """
    Build the email subject line and HTML body.
    Returns (subject, html_body).
    All characters are ASCII or safe HTML entities — no raw Unicode in strings.
    """
    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y  %I:%M %p UTC")
    count   = len(jobs)

    # Subject: keep entirely ASCII
    subject = (
        f"[Job Search] {count} new opening{'s' if count != 1 else ''} "
        f"- {datetime.now().strftime('%b %d')} ({run_label})"
    )

    if count == 0:
        html = (
            '<html><body style="font-family:Arial,sans-serif;color:#333;max-width:700px;margin:auto;">'
            '<h2 style="color:#2c3e50;">Job Search Digest &mdash; ' + run_label + '</h2>'
            '<p style="color:#666;">' + now_str + '</p>'
            '<p>No new postings matched your profile in the last 24 hours.</p>'
            '<p style="color:#999;font-size:12px;">Next run: later today.</p>'
            '</body></html>'
        )
        return subject, html

    # Group jobs by company
    by_company: dict[str, list] = {}
    for job in sorted(jobs, key=lambda j: (j.get("company", ""), j.get("title", ""))):
        by_company.setdefault(job["company"], []).append(job)

    rows_html = ""
    for company, company_jobs in by_company.items():
        rows_html += (
            '<tr>'
            '<td colspan="3" style="background:#2c3e50;color:white;padding:8px 12px;'
            'font-weight:bold;font-size:14px;">'
            + company +
            '</td></tr>'
        )
        for job in company_jobs:
            url      = job.get("url", "#")
            title    = job.get("title", "N/A")
            location = job.get("location", "Unknown")
            source   = job.get("source", "")
            posted   = job.get("posted_on_label") or job.get("posted_at", "")[:10]
            visa     = job.get("visa_sponsorship")
            visa_badge = (
                ' <span style="background:#27ae60;color:white;font-size:10px;'
                'padding:2px 6px;border-radius:10px;font-weight:bold;">SPONSORS VISA</span>'
                if visa is True else ""
            )
            row_bg = 'background:#f0fdf4;' if visa is True else ''
            rows_html += (
                '<tr style="border-bottom:1px solid #eee;' + row_bg + '">'
                '<td style="padding:8px 12px;">'
                '<a href="' + url + '" style="color:#2980b9;text-decoration:none;font-weight:500;">'
                + title + '</a>' + visa_badge + '</td>'
                '<td style="padding:8px 12px;color:#555;font-size:13px;">' + location + '</td>'
                '<td style="padding:8px 12px;color:#888;font-size:12px;">'
                + source + ' &middot; ' + posted + '</td>'
                '</tr>'
            )

    tracked_companies = len(by_company)
    plural = 's' if count != 1 else ''
    html = (
        '<html>'
        '<body style="font-family:Arial,sans-serif;color:#333;max-width:760px;margin:auto;padding:20px;">'
        '<h2 style="color:#2c3e50;margin-bottom:4px;">Job Search Digest</h2>'
        '<p style="color:#888;margin-top:0;">' + now_str + ' &nbsp;&middot;&nbsp; ' + run_label + '</p>'
        '<p style="background:#eaf4fb;border-left:4px solid #2980b9;padding:10px 14px;'
        'border-radius:4px;color:#2c3e50;">'
        '<strong>' + str(count) + '</strong> new posting' + plural + ' across '
        '<strong>' + str(tracked_companies) + '</strong> company/companies matching your profile.'
        '</p>'
        '<table width="100%" cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;border:1px solid #ddd;border-radius:4px;">'
        '<thead>'
        '<tr style="background:#f5f5f5;">'
        '<th style="padding:10px 12px;text-align:left;color:#555;font-size:13px;">Role</th>'
        '<th style="padding:10px 12px;text-align:left;color:#555;font-size:13px;">Location</th>'
        '<th style="padding:10px 12px;text-align:left;color:#555;font-size:13px;">Source &middot; Posted</th>'
        '</tr>'
        '</thead>'
        '<tbody>' + rows_html + '</tbody>'
        '</table>'
        '<p style="color:#aaa;font-size:11px;margin-top:20px;">'
        'Keywords tracked: program manager &middot; TPM &middot; operations &middot; '
        'product manager &middot; strategy &middot; PMO<br>'
        'To update tracked companies, edit <code>company_ats_map.json</code>.'
        '</p>'
        '</body></html>'
    )
    return subject, html


# ── Email sender ──────────────────────────────────────────────────────────────
TRACKER_FILE = Path(__file__).parent / "jobs_tracker.xlsx"


def send_digest(jobs: list[dict], run_label: str = "Scheduled Run") -> bool:
    """
    Send the HTML digest email via Gmail SMTP with jobs_tracker.xlsx attached.

    Returns True on success, False on failure.
    Credentials are read from .env — this function never accepts passwords
    as arguments.
    """
    if not SENDER_EMAIL or not GMAIL_APP_PASSWORD or not RECIPIENT_EMAIL:
        print(
            "[Email] Missing credentials in .env — skipping send.\n"
            "        Set SENDER_EMAIL, GMAIL_APP_PASSWORD, and RECIPIENT_EMAIL."
        )
        return False

    subject, html_body = format_email(jobs, run_label)

    # Build multipart message to support both HTML body + attachment
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL

    # Attach HTML body
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Attach jobs_tracker.xlsx if it exists
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE, "rb") as f:
            part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="jobs_tracker.xlsx"')
        msg.attach(part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

        print(f"[Email] Sent: {subject}")
        return True

    except smtplib.SMTPAuthenticationError:
        print(
            "[Email] Authentication failed.\n"
            "        Check that SENDER_EMAIL and GMAIL_APP_PASSWORD in .env are correct.\n"
            "        App Password must be 16 characters with no spaces."
        )
        return False

    except smtplib.SMTPException as e:
        print(f"[Email] SMTP error: {e}")
        return False

    except Exception as e:
        print(f"[Email] Unexpected error: {e}")
        return False


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Sends a test email with a dummy job to verify your credentials work
    test_jobs = [
        {
            "company":   "Test Company",
            "title":     "Senior Program Manager (TEST)",
            "location":  "New York, NY",
            "url":       "https://example.com/jobs/1",
            "source":    "Greenhouse",
            "posted_at": "2026-02-27T09:00:00+00:00",
        }
    ]
    print("Sending test email...")
    success = send_digest(test_jobs, run_label="Test Run")
    if success:
        print("Test email delivered. Check your inbox.")
    else:
        print("Test email failed. Check the error above and your .env file.")
