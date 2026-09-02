"""
job_scraper.py
--------------
Fetches recent job postings (within the last 24 hours) from company career
pages via Greenhouse, Lever, Workday, Ashby, and SmartRecruiters APIs.

Credentials are loaded from .env — never hardcoded here.

Usage:
    python job_scraper.py           # runs scraper, prints results
    python job_scraper.py --tier 1  # only scrape Tier 1 companies
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load .env from the project root (same directory as this script)
load_dotenv(Path(__file__).parent / ".env")

# ── File paths ────────────────────────────────────────────────────────────────
BASE_DIR         = Path(__file__).parent
COMPANY_MAP_FILE = BASE_DIR / "company_ats_map.json"
SEEN_JOBS_FILE   = BASE_DIR / "seen_jobs.json"
LOGS_DIR         = BASE_DIR / "logs"

# ── Job matching keywords (case-insensitive substring match on title) ─────────
TARGET_KEYWORDS = [
    # Program / Project Management
    "program manager",
    "programme manager",
    "technical program manager",
    " tpm ",
    "project manager",
    "project lead",
    "delivery manager",
    "engagement manager",
    "portfolio manager",
    " pmo ",
    # Operations
    "operations manager",
    "operations consultant",
    "operations analyst",
    "operations associate",
    "operations lead",
    "operations specialist",
    "business operations",
    "revenue operations",
    "sales operations",
    "marketing operations",
    "people operations",
    "growth operations",
    # Product
    "product manager",
    "product operations",
    "product analyst",
    "product lead",
    "product strategy",
    # Strategy & Consulting
    "strategy manager",
    "strategy analyst",
    "strategy consultant",
    "strategy & operations",
    "business analyst",
    "management consultant",
    "associate consultant",
    # Supply Chain & Logistics
    "supply chain manager",
    "supply chain analyst",
    "supply chain consultant",
    "logistics manager",
    "procurement manager",
    # General Management
    "chief of staff",
    "general manager",
    "business development manager",
]

LOOKBACK_HOURS = 24

# ── Visa sponsorship detector ─────────────────────────────────────────────────
# Scans job title + description for explicit sponsorship signals.
# Returns True  → company explicitly offers sponsorship
# Returns False → company explicitly says they will NOT sponsor
# Returns None  → no mention found (unknown)

_SPONSOR_YES = re.compile(
    r"""
    \b(
        (visa\s+)?sponsorship\s+(is\s+)?(available|provided|offered|supported|possible)
        | we\s+(do\s+)?(sponsor|support)\s+(h[\-\s]?1b|visa|work\s+authorization)
        | (h[\-\s]?1b|visa|work\s+authorization)\s+sponsor(ship|ed)?
        | open\s+to\s+(visa\s+)?sponsorship
        | sponsorship\s+considered
        | (will|can|able\s+to)\s+sponsor\s+(work\s+)?(visa|h[\-\s]?1b|authorization)
        | eligible\s+for\s+(visa\s+)?sponsorship
        | immigration\s+support\s+(is\s+)?(available|provided|offered)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SPONSOR_NO = re.compile(
    r"""
    \b(
        (will\s+)?not\s+(be\s+able\s+to\s+)?(provide|offer|support|consider|sponsor)\s+
            (visa\s+)?(sponsorship|h[\-\s]?1b|work\s+authorization)
        | no\s+visa\s+sponsorship
        | sponsorship\s+(is\s+)?not\s+(available|offered|provided)
        | must\s+be\s+(authorized|eligible)\s+to\s+work\s+in\s+the\s+(us|u\.s\.|united\s+states)
            \s+(without|and\s+not\s+require)\s+(current\s+or\s+future\s+)?(visa\s+)?sponsorship
        | does\s+not\s+sponsor\s+(work\s+)?(visa|h[\-\s]?1b|authorization)
        | authorization\s+to\s+work\s+in\s+the\s+(us|u\.s\.)\s+required\s+without\s+sponsorship
        | (we\s+are\s+)?unable\s+to\s+sponsor
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def detect_visa_sponsorship(title: str, description: str):
    """
    Returns True if the posting explicitly mentions visa sponsorship is available.
    Returns False if the posting explicitly says sponsorship is NOT available.
    Returns None if no mention found.
    """
    text = f"{title} {_strip_html(description)}"
    if _SPONSOR_YES.search(text):
        return True
    if _SPONSOR_NO.search(text):
        return False
    return None


# ── Experience-level filter ────────────────────────────────────────────────────
# Parse the job description for "X years of experience" patterns.
# Include the job if the MINIMUM years required is < 5 (or no mention found).
# Examples that PASS:  "2+ years", "1-3 years", "0-2 years", "up to 4 years"
# Examples that FAIL:  "5+ years", "6-8 years", "minimum 7 years"
_YEARS_RE = re.compile(
    r"""
    (?:
        (?P<low>\d+)\s*[-–to]+\s*(?P<high>\d+)   # range: "2-5 years" / "2 to 5"
        |
        (?:at\s+least|minimum|min\.?)\s+(?P<atleast>\d+)  # "at least 3 years"
        |
        (?P<plus>\d+)\s*\+                         # "3+ years"
        |
        (?P<bare>\d+)\s+years?                     # plain "3 years"
    )
    \s*(?:years?\s+of\s+)?(?:experience|exp\.?)
    """,
    re.IGNORECASE | re.VERBOSE,
)

MAX_EXPERIENCE_YEARS = 5  # roles requiring MORE than this are excluded

# ── Location filter ────────────────────────────────────────────────────────────
# Allow: Remote, Hybrid, US-based, UK-based.
# Exclude: clearly non-US/UK countries (India, Germany, France, etc.).
# If location is unknown/ambiguous → include (benefit of the doubt).

_ALLOW_LOCATION = re.compile(
    r"""
    \b(
        remote | hybrid | work\s+from\s+home | wfh
        # United States
        | united\s+states | u\.?s\.?a?\.? | \bUS\b
        | alabama | alaska | arizona | arkansas | california | colorado
        | connecticut | delaware | florida | georgia | hawaii | idaho
        | illinois | indiana | iowa | kansas | kentucky | louisiana
        | maine | maryland | massachusetts | michigan | minnesota
        | mississippi | missouri | montana | nebraska | nevada
        | new\s+hampshire | new\s+jersey | new\s+mexico | new\s+york
        | north\s+carolina | north\s+dakota | ohio | oklahoma | oregon
        | pennsylvania | rhode\s+island | south\s+carolina | south\s+dakota
        | tennessee | texas | utah | vermont | virginia | washington
        | west\s+virginia | wisconsin | wyoming | district\s+of\s+columbia
        # US state abbreviations (2-letter, word boundary)
        | \bAL\b|\bAK\b|\bAZ\b|\bAR\b|\bCA\b|\bCO\b|\bCT\b|\bDE\b
        | \bFL\b|\bGA\b|\bHI\b|\bID\b|\bIL\b|\bIN\b|\bIA\b|\bKS\b
        | \bKY\b|\bLA\b|\bME\b|\bMD\b|\bMA\b|\bMI\b|\bMN\b|\bMS\b
        | \bMO\b|\bMT\b|\bNE\b|\bNV\b|\bNH\b|\bNJ\b|\bNM\b|\bNY\b
        | \bNC\b|\bND\b|\bOH\b|\bOK\b|\bOR\b|\bPA\b|\bRI\b|\bSC\b
        | \bSD\b|\bTN\b|\bTX\b|\bUT\b|\bVT\b|\bVA\b|\bWA\b|\bWV\b
        | \bWI\b|\bWY\b|\bDC\b
        # United Kingdom
        | united\s+kingdom | u\.?k\.? | great\s+britain
        | england | scotland | wales | northern\s+ireland
        | london | manchester | birmingham | edinburgh | glasgow
        | bristol | leeds | liverpool | oxford | cambridge
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_EXCLUDE_LOCATION = re.compile(
    r"""
    \b(
        india | bangalore | bengaluru | mumbai | delhi | hyderabad | pune | chennai
        | china | beijing | shanghai | shenzhen | hong\s+kong
        | germany | berlin | munich | frankfurt | hamburg
        | france | paris | lyon
        | japan | tokyo | osaka
        | brazil | sao\s+paulo
        | mexico | mexico\s+city
        | australia | sydney | melbourne
        | singapore
        | netherlands | amsterdam
        | sweden | stockholm
        | poland | warsaw | krakow
        | canada | toronto | vancouver | montreal
        | switzerland | zurich | geneva
        | spain | madrid | barcelona
        | italy | rome | milan
        | south\s+korea | seoul
        | taiwan | taipei
        | israel | tel\s+aviv
        | uae | dubai | abu\s+dhabi
        | saudi\s+arabia | riyadh
        | turkey | istanbul
        | russia | moscow
        | argentina | buenos\s+aires
        | colombia | bogota
        | philippines | manila
        | malaysia | kuala\s+lumpur
        | indonesia | jakarta
        | vietnam | ho\s+chi\s+minh
        | thailand | bangkok
        | egypt | cairo
        | south\s+africa | johannesburg
        | kenya | nairobi
        | nigeria | lagos
        | pakistan | karachi | lahore
        | bangladesh | dhaka
        | sri\s+lanka | colombo
        | czech\s+republic | prague
        | hungary | budapest
        | romania | bucharest
        | belgium | brussels
        | austria | vienna
        | denmark | copenhagen
        | finland | helsinki
        | norway | oslo
        | portugal | lisbon
        | greece | athens
        | new\s+zealand | auckland
        | ireland | dublin
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_AMBIGUOUS_LOCATION = {"unknown", "see link", "", "n/a", "multiple locations", "various"}


def location_ok(location: str) -> bool:
    """
    Returns True if the job location is acceptable:
    - Remote / Hybrid anywhere
    - US-based
    - UK-based
    - Unknown/ambiguous (benefit of the doubt)
    Returns False if clearly in a non-US/UK country.
    """
    if not location or location.strip().lower() in _AMBIGUOUS_LOCATION:
        return True  # no data → include

    loc = location.strip()

    # If it explicitly mentions remote or hybrid, always include
    if re.search(r'\b(remote|hybrid)\b', loc, re.IGNORECASE):
        return True

    # If it matches an allowed region → include
    if _ALLOW_LOCATION.search(loc):
        return True

    # If it matches an excluded country → reject
    if _EXCLUDE_LOCATION.search(loc):
        return False

    # Anything else (truly ambiguous) → include
    return True


def _strip_html(html: str) -> str:
    """Remove HTML tags to get plain text for regex matching."""
    return re.sub(r"<[^>]+>", " ", html)


def experience_ok(description: str) -> bool:
    """
    Returns True if the job is suitable for candidates with < 5 years experience.
    - If no years-of-experience mention is found → include (benefit of the doubt).
    - If found → include only if the minimum required years < MAX_EXPERIENCE_YEARS.
    """
    if not description:
        return True  # no description available — don't exclude

    text = _strip_html(description)
    matches = list(_YEARS_RE.finditer(text))
    if not matches:
        return True  # no years mentioned → include

    min_required = float("inf")
    for m in matches:
        if m.group("low"):
            min_required = min(min_required, int(m.group("low")))
        if m.group("high"):
            min_required = min(min_required, int(m.group("low") or m.group("high")))
        if m.group("atleast"):
            min_required = min(min_required, int(m.group("atleast")))
        if m.group("plus"):
            min_required = min(min_required, int(m.group("plus")))
        if m.group("bare"):
            min_required = min(min_required, int(m.group("bare")))

    return min_required < MAX_EXPERIENCE_YEARS

# ── Workday search keywords (used in POST body) ───────────────────────────────
WORKDAY_SEARCH_TEXT = (
    "program manager OR technical program manager OR "
    "operations manager OR product manager OR strategy"
)


# ── Logging helper ────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


# ── Persistence helpers ───────────────────────────────────────────────────────
def load_company_map() -> dict:
    with open(COMPANY_MAP_FILE) as f:
        return json.load(f)


SEEN_JOBS_RETENTION_DAYS = 15


def load_seen_jobs() -> set:
    if SEEN_JOBS_FILE.exists():
        with open(SEEN_JOBS_FILE) as f:
            data = json.load(f)
        # ── 15-day cleanup ────────────────────────────────────────────────────
        # seen_ids is stored as list of "id|YYYY-MM-DD" strings.
        # Plain IDs (legacy) are kept as-is until they age out naturally.
        cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_JOBS_RETENTION_DAYS)
        cleaned = set()
        removed = 0
        for entry in data.get("seen_ids", []):
            if "|" in entry:
                job_id, date_str = entry.rsplit("|", 1)
                try:
                    if datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc) >= cutoff:
                        cleaned.add(entry)
                    else:
                        removed += 1
                except ValueError:
                    cleaned.add(entry)
            else:
                cleaned.add(entry)  # legacy plain ID — keep
        if removed:
            log(f"[Cleanup] Removed {removed} seen_jobs entries older than {SEEN_JOBS_RETENTION_DAYS} days")
        return cleaned
    return set()


def save_seen_jobs(seen: set) -> None:
    # Store each id with today's date so cleanup can age them out
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stamped = set()
    for entry in seen:
        if "|" not in entry:
            entry = f"{entry}|{today}"
        stamped.add(entry)
    SEEN_JOBS_FILE.write_text(
        json.dumps(
            {
                "seen_ids": list(stamped),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )


def seen_id(job_id: str) -> str:
    """Returns the bare job ID regardless of whether it has a date stamp."""
    return job_id.split("|")[0] if "|" in job_id else job_id


# ── Filtering helpers ─────────────────────────────────────────────────────────
def is_within_lookback(dt: datetime) -> bool:
    """Returns True if dt is within the last LOOKBACK_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


def matches_keywords(title: str) -> bool:
    """Returns True if the job title contains at least one target keyword."""
    title_lower = f" {title.lower()} "   # pad so " tpm " doesn't match mid-word
    return any(kw in title_lower for kw in TARGET_KEYWORDS)


# ── Greenhouse API ─────────────────────────────────────────────────────────────
def fetch_greenhouse_jobs(board_token: str, company_name: str) -> list[dict]:
    """
    Fetch jobs from Greenhouse's public Jobs Board API.
    No authentication required.
    Docs: https://developers.greenhouse.io/job-board.html
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    jobs = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url)
            resp.raise_for_status()
            for job in resp.json().get("jobs", []):
                updated_str = job.get("updated_at", "")
                if not updated_str:
                    continue
                try:
                    updated_at = datetime.fromisoformat(
                        updated_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue

                title = job.get("title", "")
                if not (is_within_lookback(updated_at) and matches_keywords(title)):
                    continue
                description = job.get("content", "")
                if not experience_ok(description):
                    log(f"[Greenhouse] Skipped (5+ yrs): {title} @ {company_name}")
                    continue
                visa = detect_visa_sponsorship(title, description)
                if visa is False:
                    log(f"[Greenhouse] Skipped (no sponsorship): {title} @ {company_name}")
                    continue
                location = job.get("location", {})
                loc_str = location.get("name", "Unknown") if isinstance(location, dict) else str(location)
                if not location_ok(loc_str):
                    log(f"[Greenhouse] Skipped (location): {title} @ {company_name} [{loc_str}]")
                    continue
                jobs.append({
                    "id":               f"greenhouse_{board_token}_{job['id']}",
                    "company":          company_name,
                    "title":            title,
                    "location":         loc_str,
                    "url":              job.get("absolute_url", ""),
                    "posted_at":        updated_at.isoformat(),
                    "source":           "Greenhouse",
                    "visa_sponsorship": visa,
                })
    except httpx.HTTPStatusError as e:
        log(f"[Greenhouse] HTTP {e.response.status_code} for {company_name} ({board_token})")
    except Exception as e:
        log(f"[Greenhouse] Error fetching {company_name}: {e}")

    log(f"[Greenhouse] {company_name}: {len(jobs)} new matching job(s)")
    return jobs


# ── Lever API ──────────────────────────────────────────────────────────────────
def fetch_lever_jobs(company_slug: str, company_name: str) -> list[dict]:
    """
    Fetch jobs from Lever's public Postings API.
    No authentication required.
    Docs: https://hire.lever.co/developer/postings
    """
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    jobs = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url)
            resp.raise_for_status()
            for job in resp.json():
                # Lever returns createdAt as a Unix timestamp in milliseconds
                created_ms = job.get("createdAt", 0)
                created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)

                title = job.get("text", "")
                if not (is_within_lookback(created_at) and matches_keywords(title)):
                    continue
                description = job.get("descriptionBody", "") or job.get("description", "")
                if not experience_ok(description):
                    log(f"[Lever] Skipped (5+ yrs): {title} @ {company_name}")
                    continue
                visa = detect_visa_sponsorship(title, description)
                if visa is False:
                    log(f"[Lever] Skipped (no sponsorship): {title} @ {company_name}")
                    continue
                categories = job.get("categories", {})
                loc_str = categories.get("location", "Unknown")
                if not location_ok(loc_str):
                    log(f"[Lever] Skipped (location): {title} @ {company_name} [{loc_str}]")
                    continue
                jobs.append({
                    "id":               f"lever_{company_slug}_{job['id']}",
                    "company":          company_name,
                    "title":            title,
                    "location":         loc_str,
                    "url":              job.get("hostedUrl", ""),
                    "posted_at":        created_at.isoformat(),
                    "source":           "Lever",
                    "visa_sponsorship": visa,
                    })
    except httpx.HTTPStatusError as e:
        log(f"[Lever] HTTP {e.response.status_code} for {company_name} ({company_slug})")
    except Exception as e:
        log(f"[Lever] Error fetching {company_name}: {e}")

    log(f"[Lever] {company_name}: {len(jobs)} new matching job(s)")
    return jobs


# ── Workday API ────────────────────────────────────────────────────────────────
def fetch_workday_jobs(
    tenant: str, site: str, company_name: str, wd_num: int = 5
) -> list[dict]:
    """
    Fetch jobs from Workday's undocumented but publicly accessible search API.

    NOTE: Workday tenant IDs and site names must match each company's exact
    Workday configuration. Entries in company_ats_map.json marked
    verified:false are best-guess values that may need adjustment.

    Workday returns relative date labels ("Posted 1 Day Ago") rather than
    ISO timestamps, so all returned results are treated as potentially recent
    and filtered by title keyword only. Re-querying the same company within
    24 hours is handled by the deduplicator (seen_jobs.json).
    """
    url = (
        f"https://{tenant}.wd{wd_num}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{site}/jobs"
    )
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    payload = {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": WORKDAY_SEARCH_TEXT,
    }
    jobs = []
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            for job in data.get("jobPostings", []):
                title = job.get("title", "")
                if not matches_keywords(title):
                    continue

                posted_label = job.get("postedOn", "")
                # Only include jobs posted within ~2 days based on the label
                recent_labels = ("today", "1 day", "yesterday", "just now")
                if posted_label and not any(
                    label in posted_label.lower() for label in recent_labels
                ):
                    continue

                ext_path = job.get("externalPath", "")
                # externalPath is the job-specific tail: "/job/City/Title_ID"
                # Full URL needs locale prefix + site name inserted before it
                job_url = (
                    f"https://{tenant}.wd{wd_num}.myworkdayjobs.com/en-US/{site}{ext_path}"
                    if ext_path else ""
                )
                loc_str = job.get("locationsText", "Unknown")
                if not location_ok(loc_str):
                    log(f"[Workday] Skipped (location): {title} @ {company_name} [{loc_str}]")
                    continue
                # Use externalPath as the unique ID component
                job_id = ext_path.replace("/", "_").strip("_") or title[:40]
                visa = detect_visa_sponsorship(title, "")
                if visa is False:
                    log(f"[Workday] Skipped (no sponsorship): {title} @ {company_name}")
                    continue
                jobs.append({
                    "id":               f"workday_{tenant}_{job_id}",
                    "company":          company_name,
                    "title":            title,
                    "location":         loc_str,
                    "url":              job_url,
                    "posted_at":        datetime.now(timezone.utc).isoformat(),
                    "posted_on_label":  posted_label,
                    "source":           "Workday",
                    "visa_sponsorship": visa,
                })
    except httpx.HTTPStatusError as e:
        log(f"[Workday] HTTP {e.response.status_code} for {company_name} — tenant may need verification")
    except Exception as e:
        log(f"[Workday] Error fetching {company_name}: {e}")

    log(f"[Workday] {company_name}: {len(jobs)} new matching job(s)")
    return jobs


# ── Ashby API ─────────────────────────────────────────────────────────────────
def fetch_ashby_jobs(org_slug: str, company_name: str) -> list[dict]:
    """
    Fetch jobs from Ashby's public job board API.
    No authentication required.
    Docs: https://developers.ashbyhq.com/reference/apipostingjobboard
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org_slug}"
    jobs = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params={"includeCompensation": "false"})
            resp.raise_for_status()
            for job in resp.json().get("jobs", []):
                title = job.get("title", "")
                if not matches_keywords(title):
                    continue

                published_str = job.get("publishedAt", "")
                if published_str:
                    try:
                        published_at = datetime.fromisoformat(
                            published_str.replace("Z", "+00:00")
                        )
                        if not is_within_lookback(published_at):
                            continue
                    except ValueError:
                        pass

                description = job.get("descriptionHtml", "") or job.get("description", "")
                if not experience_ok(description):
                    log(f"[Ashby] Skipped (5+ yrs): {title} @ {company_name}")
                    continue
                visa = detect_visa_sponsorship(title, description)
                if visa is False:
                    log(f"[Ashby] Skipped (no sponsorship): {title} @ {company_name}")
                    continue
                location = job.get("location", "") or job.get("locationName", "")
                if not location_ok(location):
                    log(f"[Ashby] Skipped (location): {title} @ {company_name} [{location}]")
                    continue
                apply_url = job.get("applyUrl") or job.get("jobUrl", "")
                jobs.append({
                    "id":               f"ashby_{org_slug}_{job['id']}",
                    "company":          company_name,
                    "title":            title,
                    "location":         location or "Unknown",
                    "url":              apply_url,
                    "posted_at":        published_str or datetime.now(timezone.utc).isoformat(),
                    "source":           "Ashby",
                    "visa_sponsorship": visa,
                })
    except httpx.HTTPStatusError as e:
        log(f"[Ashby] HTTP {e.response.status_code} for {company_name} ({org_slug})")
    except Exception as e:
        log(f"[Ashby] Error fetching {company_name}: {e}")

    log(f"[Ashby] {company_name}: {len(jobs)} new matching job(s)")
    return jobs


# ── SmartRecruiters API ────────────────────────────────────────────────────────
def fetch_smartrecruiters_jobs(company_id: str, company_name: str) -> list[dict]:
    """
    Fetch jobs from SmartRecruiters' public postings API.
    No authentication required for public job boards.
    Docs: https://dev.smartrecruiters.com/customer-api/live-docs/
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
    jobs = []
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, params={"status": "PUBLIC", "limit": 100})
            resp.raise_for_status()
            for job in resp.json().get("content", []):
                title = job.get("name", "")
                if not matches_keywords(title):
                    continue

                posted_str = job.get("releasedDate", "")
                if posted_str:
                    try:
                        posted_at = datetime.fromisoformat(
                            posted_str.replace("Z", "+00:00")
                        )
                        if not is_within_lookback(posted_at):
                            continue
                    except ValueError:
                        pass
                else:
                    posted_at = datetime.now(timezone.utc)
                    posted_str = posted_at.isoformat()

                ref = job.get("ref", "")
                job_url = (
                    f"https://jobs.smartrecruiters.com/{company_id}/{ref}"
                    if ref else ""
                )
                location = job.get("location", {})
                city    = location.get("city", "")
                country = location.get("country", "")
                loc_str = ", ".join(filter(None, [city, country])) or "Unknown"

                # Fetch individual job description to filter by years of experience
                description = ""
                if ref:
                    try:
                        detail_resp = client.get(
                            f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{ref}",
                            timeout=10,
                        )
                        if detail_resp.status_code == 200:
                            detail = detail_resp.json()
                            desc_sections = detail.get("jobAd", {}).get("sections", {})
                            description = " ".join(
                                v.get("text", "") for v in desc_sections.values()
                                if isinstance(v, dict)
                            )
                    except Exception:
                        pass  # if detail fetch fails, still include the job

                if not experience_ok(description):
                    log(f"[SmartRecruiters] Skipped (5+ yrs): {title} @ {company_name}")
                    continue
                visa = detect_visa_sponsorship(title, description)
                if visa is False:
                    log(f"[SmartRecruiters] Skipped (no sponsorship): {title} @ {company_name}")
                    continue
                if not location_ok(loc_str):
                    log(f"[SmartRecruiters] Skipped (location): {title} @ {company_name} [{loc_str}]")
                    continue

                jobs.append({
                    "id":               f"smartrecruiters_{company_id}_{ref or title[:30]}",
                    "company":          company_name,
                    "title":            title,
                    "location":         loc_str,
                    "url":              job_url,
                    "posted_at":        posted_str,
                    "source":           "SmartRecruiters",
                    "visa_sponsorship": visa,
                })
    except httpx.HTTPStatusError as e:
        log(f"[SmartRecruiters] HTTP {e.response.status_code} for {company_name} ({company_id})")
    except Exception as e:
        log(f"[SmartRecruiters] Error fetching {company_name}: {e}")

    log(f"[SmartRecruiters] {company_name}: {len(jobs)} new matching job(s)")
    return jobs


# ── iCIMS API ─────────────────────────────────────────────────────────────────
def fetch_icims_jobs(tenant: str, company_name: str) -> list[dict]:
    """
    Fetch jobs from iCIMS public search API.
    Each company has its own subdomain: https://{tenant}.icims.com
    No authentication required for public postings.
    """
    url = f"https://{tenant}.icims.com/jobs/search"
    jobs = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params={"ss": "1", "pr": "1", "format": "json"})
            resp.raise_for_status()
            for job in resp.json().get("searchResults", []):
                title = job.get("jobtitle", "")
                if not matches_keywords(title):
                    continue
                posted_str = job.get("postingdate", "")
                if posted_str:
                    try:
                        posted_at = datetime.fromisoformat(posted_str)
                        if not is_within_lookback(posted_at):
                            continue
                    except ValueError:
                        pass
                job_id = str(job.get("id", title[:40]))
                loc_str = job.get("joblocation", "Unknown")
                if not location_ok(loc_str):
                    log(f"[iCIMS] Skipped (location): {title} @ {company_name} [{loc_str}]")
                    continue
                jobs.append({
                    "id":        f"icims_{tenant}_{job_id}",
                    "company":   company_name,
                    "title":     title,
                    "location":  loc_str,
                    "url":       f"https://{tenant}.icims.com/jobs/{job_id}/job",
                    "posted_at": posted_str or datetime.now(timezone.utc).isoformat(),
                    "source":    "iCIMS",
                })
    except httpx.HTTPStatusError as e:
        log(f"[iCIMS] HTTP {e.response.status_code} for {company_name} ({tenant})")
    except Exception as e:
        log(f"[iCIMS] Error fetching {company_name}: {e}")

    log(f"[iCIMS] {company_name}: {len(jobs)} new matching job(s)")
    return jobs


# ── Indeed RSS ────────────────────────────────────────────────────────────────
# Indeed generates a free RSS feed for any search — no API key needed.
# Each keyword gets its own feed URL. Results are deduplicated via seen_jobs.json.
# NOTE: Indeed's public RSS feed (indeed.com/rss) now returns 403/429 for
# programmatic access — they block it at the CDN level. The correct free
# alternative is to use Indeed job alert emails via the Gmail API fetcher below.
# This function is kept as a stub; it will skip gracefully if Indeed blocks it.

INDEED_RSS_QUERIES = [
    "technical+program+manager",
    "program+manager",
    "operations+manager",
    "product+manager",
    "strategy+manager",
]
INDEED_LOCATION = os.getenv("INDEED_LOCATION", "United+States")  # set in .env to narrow results


def fetch_indeed_rss() -> list[dict]:
    jobs = []
    seen_titles: set = set()  # dedupe within this batch

    for query in INDEED_RSS_QUERIES:
        url = f"https://www.indeed.com/rss?q={query}&l={INDEED_LOCATION}&sort=date"
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code in (403, 429):
                    log("[IndeedRSS] Blocked by Indeed (403/429) — use Gmail alerts instead")
                    break
                resp.raise_for_status()

            root = ET.fromstring(resp.text)
            items = root.findall(".//item")

            for item in items:
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link") or "").strip()
                pubdate = (item.findtext("pubDate") or "").strip()
                company = (item.findtext("source") or "Indeed").strip()

                if not matches_keywords(title):
                    continue

                # Parse pubDate (RFC 2822 format from RSS)
                if pubdate:
                    try:
                        posted_at = parsedate_to_datetime(pubdate)
                        if not is_within_lookback(posted_at):
                            continue
                        posted_str = posted_at.isoformat()
                    except Exception:
                        posted_str = pubdate
                else:
                    posted_str = datetime.now(timezone.utc).isoformat()

                dedup_key = f"{title}|{company}"
                if dedup_key in seen_titles:
                    continue
                seen_titles.add(dedup_key)

                job_id = link.split("jk=")[-1].split("&")[0] if "jk=" in link else link[-40:]
                jobs.append({
                    "id":        f"indeed_{job_id}",
                    "company":   company,
                    "title":     title,
                    "location":  INDEED_LOCATION.replace("+", " "),
                    "url":       link,
                    "posted_at": posted_str,
                    "source":    "Indeed RSS",
                })

        except httpx.HTTPStatusError as e:
            log(f"[IndeedRSS] HTTP {e.response.status_code} for query '{query}'")
        except Exception as e:
            log(f"[IndeedRSS] Error for '{query}': {e}")

    log(f"[IndeedRSS] {len(jobs)} new matching job(s)")
    return jobs


# ── Gmail API ─────────────────────────────────────────────────────────────────
# Reads job alert emails from LinkedIn, Indeed, and company alerts in your Gmail.
# Setup (one-time):
#   1. Go to console.cloud.google.com → New project → Enable Gmail API
#   2. Create OAuth credentials (Desktop app) → download as gmail_credentials.json
#   3. pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
#   4. Run: python setup_gmail.py  (creates token.json after browser login)
#   5. Set GMAIL_FETCH_ENABLED=true in .env
#
# Email senders scanned:
#   jobalerts-noreply@linkedin.com   (LinkedIn job alerts)
#   invitations@indeed.com           (Indeed job alerts)
#   no-reply@glassdoor.com           (Glassdoor alerts)

GMAIL_ENABLED      = os.getenv("GMAIL_FETCH_ENABLED", "").lower() == "true"
GMAIL_CREDS_FILE   = BASE_DIR / "gmail_credentials.json"
GMAIL_TOKEN_FILE   = BASE_DIR / "token.json"
GMAIL_ALERT_SENDERS = [
    "jobalerts-noreply@linkedin.com",
    "invitations@indeed.com",
    "no-reply@glassdoor.com",
]


def fetch_gmail_alerts() -> list[dict]:
    if not GMAIL_ENABLED:
        log("[Gmail] Skipped — set GMAIL_FETCH_ENABLED=true in .env after running setup_gmail.py")
        return []
    if not GMAIL_TOKEN_FILE.exists():
        log("[Gmail] Skipped — token.json not found. Run setup_gmail.py first.")
        return []

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        import base64, re as _re

        creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_FILE))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        service = build("gmail", "v1", credentials=creds)

        # Last 48h to avoid missing emails at boundary
        since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y/%m/%d")
        sender_query = " OR ".join(f"from:{s}" for s in GMAIL_ALERT_SENDERS)
        query = f"({sender_query}) after:{since}"

        results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
        messages = results.get("messages", [])

        def _get_body(payload: dict) -> str:
            """Recursively decode MIME parts into a single HTML string."""
            if payload.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(
                    payload["body"]["data"] + "=="
                ).decode("utf-8", errors="ignore")
            text = ""
            for part in payload.get("parts", []):
                mime = part.get("mimeType", "")
                if mime in ("text/html", "text/plain"):
                    data = part.get("body", {}).get("data", "")
                    if data:
                        text += base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                elif mime.startswith("multipart/"):
                    text += _get_body(part)
            return text

        jobs = []
        seen_job_ids: set = set()

        # LinkedIn job view URL: linkedin.com/comm/jobs/view/{job_id}/
        li_job_re = _re.compile(
            r'https://www\.linkedin\.com/comm/jobs/view/(\d+)/[^\s"\'<>]*'
        )
        # Indeed job URL: indeed.com/viewjob?jk={job_id}
        indeed_job_re = _re.compile(
            r'https://[^\s"\'<>]*indeed\.com/viewjob\?[^\s"\'<>]*jk=([a-f0-9]+)[^\s"\'<>]*'
        )

        for msg in messages:
            full = service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()

            headers = {
                h["name"]: h["value"]
                for h in full["payload"].get("headers", [])
            }
            subject = headers.get("Subject", "")
            body    = _get_body(full["payload"])
            plain   = _re.sub(r"<[^>]+>", " ", body)
            plain   = _re.sub(r"\s+", " ", plain).strip()

            # ── LinkedIn alerts ───────────────────────────────────────────────
            for m in li_job_re.finditer(body):
                job_id  = m.group(1)
                job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
                if job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job_id)

                # Extract title from surrounding HTML:
                # LinkedIn wraps job titles in <strong> or aria-label near the URL
                start = max(0, m.start() - 800)
                snippet = plain[max(0, plain.find(job_id) - 300) : plain.find(job_id) + 50]

                # Title is in subject line for single-job alerts;
                # for digest emails use subject as fallback
                title = subject.strip()
                # Try to pull a cleaner title from HTML near the link
                title_m = _re.search(
                    r'aria-label="([^"]{10,120})"[^>]*>' +
                    r'|<strong[^>]*>([^<]{10,120})</strong>',
                    body[max(0, m.start() - 600) : m.start()],
                    _re.IGNORECASE,
                )
                if title_m:
                    title = (title_m.group(1) or title_m.group(2) or title).strip()

                # Extract company name: usually before " - " or " at " in title
                company = "LinkedIn Alert"
                company_m = _re.match(r'^"?([^":]+)"?\s*:', subject)
                if company_m:
                    company = company_m.group(1).strip()

                # Trust LinkedIn's pre-filtering — do NOT re-apply keyword filter.
                # LinkedIn already matched these to the user's saved search.
                jobs.append({
                    "id":        f"gmail_li_{job_id}",
                    "company":   company,
                    "title":     title,
                    "location":  "See link",
                    "url":       job_url,
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                    "source":    "LinkedIn Alert",
                })

            # ── Indeed alerts ─────────────────────────────────────────────────
            for m in indeed_job_re.finditer(body):
                job_id  = m.group(1)
                job_url = f"https://www.indeed.com/viewjob?jk={job_id}"
                if job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job_id)
                jobs.append({
                    "id":        f"gmail_indeed_{job_id}",
                    "company":   "Indeed Alert",
                    "title":     subject.strip(),
                    "location":  "See link",
                    "url":       job_url,
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                    "source":    "Indeed Alert",
                })

        log(f"[Gmail] {len(jobs)} job(s) extracted from {len(messages)} alert emails")
        return jobs

    except ImportError:
        log("[Gmail] Missing libraries — run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        return []
    except Exception as e:
        log(f"[Gmail] Error: {e}")
        return []


# ── Phenom People (CareerConnect) ─────────────────────────────────────────────
def fetch_phenom_jobs(base_url: str, company_name: str) -> list[dict]:
    """
    Fetch jobs from Phenom People / CareerConnect sites via the sitemap.
    Phenom's REST API blocks unauthenticated calls; the sitemap is always public.
    Handles both flat sitemaps and sitemap index files (sub-sitemaps).
    Deduplication via seen_jobs.json handles repeat scrapes.
    """
    sitemap_url = f"{base_url.rstrip('/')}/sitemap.xml"
    jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}

    def _get_urls_from_sitemap(url: str, client: httpx.Client) -> list[str]:
        try:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            # Sitemap index: contains <sitemap> elements
            sub = [loc.text for loc in root.findall(".//sm:sitemap/sm:loc", ns) if loc.text]
            if sub:
                all_urls = []
                for sub_url in sub:
                    all_urls.extend(_get_urls_from_sitemap(sub_url, client))
                return all_urls
            # Regular sitemap: contains <url> elements
            return [loc.text for loc in root.findall(".//sm:url/sm:loc", ns) if loc.text]
        except Exception:
            return []

    try:
        with httpx.Client(timeout=20) as client:
            urls = _get_urls_from_sitemap(sitemap_url, client)

        # Filter to job URLs: /job/{ID}/{title-slug} — ID may contain hyphens (e.g. JR-3053)
        job_url_re = re.compile(r"/job/([\w-]+)/([^/?\s]+)$", re.IGNORECASE)

        for url in urls:
            m = job_url_re.search(url)
            if not m:
                continue
            job_id   = m.group(1)
            slug     = m.group(2)
            # Convert URL slug to readable title
            title = slug.replace("-", " ").replace("%20", " ").strip()
            if not matches_keywords(title):
                continue
            jobs.append({
                "id":        f"phenom_{company_name.lower().replace(' ','_')}_{job_id}",
                "company":   company_name,
                "title":     title,
                "location":  "See link",
                "url":       url,
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "source":    "Phenom",
            })
    except httpx.HTTPStatusError as e:
        log(f"[Phenom] HTTP {e.response.status_code} for {company_name}")
    except Exception as e:
        log(f"[Phenom] Error fetching {company_name}: {e}")

    log(f"[Phenom] {company_name}: {len(jobs)} matching job(s)")
    return jobs


# ── Otta aggregator ────────────────────────────────────────────────────────────
def fetch_otta_jobs() -> list[dict]:
    """
    Otta (otta.com) is a curated job aggregator.
    Their API blocks unauthenticated programmatic access (WAF/Cloudflare).
    To enable: obtain an Otta API key or use their partner feed, then implement
    here. Set OTTA_API_KEY in .env and update this function.
    """
    api_key = os.getenv("OTTA_API_KEY", "")
    if not api_key:
        log("[Otta] Skipped — no OTTA_API_KEY in .env (see job_scraper.py for setup)")
        return []

    url = "https://api.otta.com/graphql"
    query = """
    query { jobs(filters: {titles: ["program manager","operations manager","product manager"]}, limit: 50) {
        id title publishedAt externalUrl
        company { name }
        locationPreferences { live { location } }
    } }
    """
    jobs = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                url,
                json={"query": query},
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            for node in resp.json().get("data", {}).get("jobs", []):
                title = node.get("title", "")
                if not matches_keywords(title):
                    continue
                published_str = node.get("publishedAt", "")
                if published_str:
                    try:
                        published_at = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                        if not is_within_lookback(published_at):
                            continue
                    except ValueError:
                        pass
                locs = node.get("locationPreferences", {}).get("live", [])
                jobs.append({
                    "id":        f"otta_{node['id']}",
                    "company":   node.get("company", {}).get("name", "Unknown"),
                    "title":     title,
                    "location":  locs[0].get("location", "Unknown") if locs else "Unknown",
                    "url":       node.get("externalUrl", ""),
                    "posted_at": published_str,
                    "source":    "Otta",
                })
    except httpx.HTTPStatusError as e:
        log(f"[Otta] HTTP {e.response.status_code}")
    except Exception as e:
        log(f"[Otta] Error: {e}")

    log(f"[Otta] {len(jobs)} new matching job(s)")
    return jobs


# ── Remote Rocketship aggregator ───────────────────────────────────────────────
def fetch_remote_rocketship_jobs() -> list[dict]:
    """
    Remote Rocketship (remoterocketship.com) is a remote job aggregator.
    Their site blocks unauthenticated API calls (Cloudflare 403).
    To enable: find their current API endpoint or RSS feed URL and update here.
    Set REMOTE_ROCKETSHIP_API_URL in .env to override the endpoint.
    """
    api_url = os.getenv("REMOTE_ROCKETSHIP_API_URL", "")
    if not api_url:
        log("[RemoteRocketship] Skipped — no REMOTE_ROCKETSHIP_API_URL in .env (site blocks direct access)")
        return []

    jobs = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                api_url,
                params={"search": "program manager OR operations manager OR product manager", "limit": 50},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            raw = resp.json()
            listing = raw.get("jobs", raw) if isinstance(raw, dict) else raw
            for job in listing if isinstance(listing, list) else []:
                title = job.get("title", "") or job.get("name", "")
                if not matches_keywords(title):
                    continue
                posted_str = job.get("date", "") or job.get("postedAt", "") or job.get("created_at", "")
                if posted_str:
                    try:
                        if not is_within_lookback(datetime.fromisoformat(posted_str.replace("Z", "+00:00"))):
                            continue
                    except ValueError:
                        pass
                job_id = str(job.get("id", title[:40]))
                jobs.append({
                    "id":        f"remoterocketship_{job_id}",
                    "company":   job.get("company", "Unknown"),
                    "title":     title,
                    "location":  job.get("location", "Remote"),
                    "url":       job.get("url", "") or job.get("applyUrl", ""),
                    "posted_at": posted_str,
                    "source":    "Remote Rocketship",
                })
    except httpx.HTTPStatusError as e:
        log(f"[RemoteRocketship] HTTP {e.response.status_code}")
    except Exception as e:
        log(f"[RemoteRocketship] Error: {e}")

    log(f"[RemoteRocketship] {len(jobs)} new matching job(s)")
    return jobs


# ── Main orchestrator ──────────────────────────────────────────────────────────
def get_new_jobs(tier_filter=None) -> list[dict]:
    """
    Fetch all new jobs from tracked companies, deduplicate against seen_jobs.json,
    and return only unseen jobs.

    Args:
        tier_filter: If set (1, 2, or 3), only scrape companies of that tier.
                     If None, scrape all tiers.
    """
    LOGS_DIR.mkdir(exist_ok=True)
    company_map = load_company_map()
    seen = load_seen_jobs()
    all_new_jobs: list[dict] = []

    companies = company_map.get("companies", {})
    log(f"Scanning {len(companies)} companies (tier_filter={tier_filter or 'all'})")

    for key, info in companies.items():
        if tier_filter and info.get("tier") != tier_filter:
            continue

        ats  = info.get("ats")
        name = info["name"]

        if ats == "greenhouse":
            fetched = fetch_greenhouse_jobs(info["board_token"], name)

        elif ats == "lever":
            fetched = fetch_lever_jobs(info["company_slug"], name)

        elif ats == "workday":
            fetched = fetch_workday_jobs(
                tenant=info["tenant"],
                site=info["site"],
                company_name=name,
                wd_num=info.get("wd_num", 5),
            )

        elif ats == "ashby":
            fetched = fetch_ashby_jobs(info["org_slug"], name)

        elif ats == "smartrecruiters":
            fetched = fetch_smartrecruiters_jobs(info["company_id"], name)

        elif ats == "icims":
            fetched = fetch_icims_jobs(info["tenant"], name)

        elif ats == "phenom":
            fetched = fetch_phenom_jobs(info["base_url"], name)

        else:
            log(f"[Skip] {name}: unsupported ATS type '{ats}'")
            continue

        for job in fetched:
            if job["id"] not in seen and not any(seen_id(s) == job["id"] for s in seen):
                if "visa_sponsorship" not in job:
                    job["visa_sponsorship"] = detect_visa_sponsorship(
                        job.get("title", ""), job.get("description", "")
                    )
                all_new_jobs.append(job)
                seen.add(job["id"])

    # ── Aggregators (keyword-based, not per-company) ───────────────────────────
    if not tier_filter:
        for job in fetch_otta_jobs() + fetch_remote_rocketship_jobs() + fetch_indeed_rss() + fetch_gmail_alerts():
            if job["id"] not in seen and not any(seen_id(s) == job["id"] for s in seen):
                if "visa_sponsorship" not in job:
                    job["visa_sponsorship"] = detect_visa_sponsorship(
                        job.get("title", ""), job.get("description", "")
                    )
                all_new_jobs.append(job)
                seen.add(job["id"])

    save_seen_jobs(seen)
    log(f"Total new jobs found: {len(all_new_jobs)}")
    return all_new_jobs


# ── CLI entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tier = None
    if "--tier" in sys.argv:
        try:
            tier = int(sys.argv[sys.argv.index("--tier") + 1])
        except (IndexError, ValueError):
            print("Usage: python job_scraper.py [--tier 1|2|3]")
            sys.exit(1)

    jobs = get_new_jobs(tier_filter=tier)

    if not jobs:
        print("No new matching jobs found in the last 24 hours.")
    else:
        print(f"\n{'─'*60}")
        print(f"  {len(jobs)} new job(s) found")
        print(f"{'─'*60}")
        for job in jobs:
            print(f"\n  [{job['company']}]  {job['title']}")
            print(f"  Location : {job.get('location', 'N/A')}")
            print(f"  Source   : {job.get('source', 'N/A')}")
            print(f"  URL      : {job.get('url', 'N/A')}")
        print(f"\n{'─'*60}")
