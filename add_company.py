"""
add_company.py
--------------
Paste any company careers page URL and this script will:
  1. Detect which ATS is behind it (Workday, Greenhouse, Lever, Ashby,
     SmartRecruiters, iCIMS)
  2. Extract the config (tenant/token/slug)
  3. Verify it actually works
  4. Add or update the entry in company_ats_map.json

Usage:
    python add_company.py "https://www.bain.com/careers/find-a-role/position/?jobid=10397"
    python add_company.py "https://bain.wd5.myworkdayjobs.com/en-US/Bain_Careers/job/..."
    python add_company.py "https://jobs.lever.co/palantir/abc123"
    python add_company.py "https://boards.greenhouse.io/stripe/jobs/123"
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

BASE_DIR         = Path(__file__).parent
COMPANY_MAP_FILE = BASE_DIR / "company_ats_map.json"


# ── ATS detection from URL ─────────────────────────────────────────────────────

def detect_from_url(url: str) -> dict | None:
    """
    Try to extract ATS config directly from the URL structure.
    Returns a partial config dict on success, None if URL pattern not recognized.
    """
    parsed = urlparse(url)
    host   = parsed.netloc.lower()
    path   = parsed.path

    # Workday: {tenant}.wd{N}.myworkdayjobs.com/en-US/{site}/...
    m = re.match(r"^([\w-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if m:
        tenant = m.group(1)
        wd_num = int(m.group(2).replace("wd", ""))
        # site is the first path component after /en-US/ (or first component)
        site_match = re.search(r"/(?:en-US|en_US)/([^/]+)/", path)
        if not site_match:
            site_match = re.search(r"/([^/]+)/(?:job|jobs)/", path)
        site = site_match.group(1) if site_match else None
        return {"ats": "workday", "tenant": tenant, "wd_num": wd_num, "site": site}

    # Greenhouse: boards.greenhouse.io/{token}  or  job-boards.greenhouse.io/{token}
    if "greenhouse.io" in host:
        parts = [p for p in path.split("/") if p]
        if parts:
            return {"ats": "greenhouse", "board_token": parts[0]}

    # Lever: jobs.lever.co/{slug}
    if host == "jobs.lever.co":
        parts = [p for p in path.split("/") if p]
        if parts:
            return {"ats": "lever", "company_slug": parts[0]}

    # Ashby: jobs.ashbyhq.com/{slug}
    if "ashbyhq.com" in host:
        parts = [p for p in path.split("/") if p]
        if parts:
            return {"ats": "ashby", "org_slug": parts[0]}

    # SmartRecruiters: jobs.smartrecruiters.com/{company_id}
    if "smartrecruiters.com" in host:
        parts = [p for p in path.split("/") if p]
        if parts:
            return {"ats": "smartrecruiters", "company_id": parts[0]}

    # iCIMS: {tenant}.icims.com
    m = re.match(r"^([\w-]+)\.icims\.com$", host)
    if m:
        return {"ats": "icims", "tenant": m.group(1)}

    return None


def detect_from_page(url: str) -> dict | None:
    """
    Fetch the page HTML and scan for embedded ATS URLs when the company
    uses a custom careers domain (e.g. bain.com/careers → Workday backend).
    """
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        print(f"  Could not fetch page: {e}")
        return None

    html = resp.text

    # Workday iframe/link embedded in page
    m = re.search(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:en-US/|en_US/)?([^/\"' ]+)", html)
    if m:
        tenant = m.group(1)
        wd_num = int(m.group(2).replace("wd", ""))
        site   = m.group(3).split("/")[0]
        return {"ats": "workday", "tenant": tenant, "wd_num": wd_num, "site": site}

    # Greenhouse embed
    m = re.search(r"boards(?:-api)?\.greenhouse\.io/(?:v\d+/boards/)?([^/\"' ]+)", html)
    if m:
        return {"ats": "greenhouse", "board_token": m.group(1).split("/")[0]}

    # Lever embed
    m = re.search(r"jobs\.lever\.co/([^/\"' ]+)", html)
    if m:
        return {"ats": "lever", "company_slug": m.group(1).split("/")[0]}

    # Ashby embed
    m = re.search(r"(?:api\.ashbyhq\.com/posting-api/job-board|jobs\.ashbyhq\.com)/([^/\"' ]+)", html)
    if m:
        return {"ats": "ashby", "org_slug": m.group(1).split("/")[0]}

    # SmartRecruiters embed
    m = re.search(r"api\.smartrecruiters\.com/v1/companies/([^/\"' ]+)", html)
    if m:
        return {"ats": "smartrecruiters", "company_id": m.group(1).split("/")[0]}

    # iCIMS embed
    m = re.search(r"([\w-]+)\.icims\.com", html)
    if m:
        return {"ats": "icims", "tenant": m.group(1)}

    return None


# ── ATS verification ───────────────────────────────────────────────────────────

def verify_config(config: dict) -> bool:
    """Returns True if the config successfully reaches a live ATS endpoint."""
    ats = config["ats"]
    try:
        with httpx.Client(timeout=15) as client:
            if ats == "greenhouse":
                r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{config['board_token']}/jobs")
                return r.status_code == 200

            elif ats == "lever":
                r = client.get(f"https://api.lever.co/v0/postings/{config['company_slug']}?mode=json")
                return r.status_code == 200

            elif ats == "ashby":
                r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{config['org_slug']}")
                return r.status_code == 200

            elif ats == "smartrecruiters":
                r = client.get(f"https://api.smartrecruiters.com/v1/companies/{config['company_id']}/postings",
                               params={"status": "PUBLIC", "limit": 1})
                return r.status_code == 200

            elif ats == "icims":
                r = client.get(f"https://{config['tenant']}.icims.com/jobs/search",
                               params={"ss": "1", "pr": "1", "format": "json"})
                return r.status_code == 200

            elif ats == "workday":
                tenant = config["tenant"]
                wd_num = config.get("wd_num", 5)
                site   = config.get("site", "External")
                r = client.post(
                    f"https://{tenant}.wd{wd_num}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                    json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "manager"},
                    headers={"Content-Type": "application/json"},
                )
                return r.status_code == 200

    except Exception:
        pass
    return False


def try_workday_sites(tenant: str, wd_num: int) -> str | None:
    """
    When we know the Workday tenant/wd_num but not the site name, probe
    common site names to find one that returns 200.
    """
    common_sites = [
        "External", "External_Career_Site", "ExternalCareerSite",
        "Careers", "careers", "External_Careers", "Jobs",
    ]
    with httpx.Client(timeout=10) as client:
        for site in common_sites:
            r = client.post(
                f"https://{tenant}.wd{wd_num}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "manager"},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return site
    return None


# ── company_ats_map.json helpers ───────────────────────────────────────────────

def load_map() -> dict:
    with open(COMPANY_MAP_FILE) as f:
        return json.load(f)


def save_map(data: dict) -> None:
    with open(COMPANY_MAP_FILE, "w") as f:
        json.dump(data, f, indent=2)


def build_entry(company_name: str, tier: int, config: dict) -> dict:
    entry = {"name": company_name, "tier": tier, "verified": True}
    entry.update(config)
    return entry


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    url = sys.argv[1].strip()
    print(f"\nAnalyzing: {url}\n")

    # Step 1: try to detect ATS from the URL itself
    config = detect_from_url(url)
    if config:
        print(f"  Detected from URL:  ATS={config['ats']}")
    else:
        print("  URL pattern not recognized — fetching page to scan for embedded ATS...")
        config = detect_from_page(url)
        if config:
            print(f"  Detected in page HTML:  ATS={config['ats']}")
        else:
            print("  Could not detect ATS from URL or page HTML.")
            print("  Please paste the direct ATS URL instead of the company's careers landing page.")
            print("  Examples:")
            print("    Workday:        https://{tenant}.wd5.myworkdayjobs.com/en-US/{site}/jobs")
            print("    Greenhouse:     https://boards.greenhouse.io/{token}/jobs")
            print("    Lever:          https://jobs.lever.co/{slug}")
            print("    Ashby:          https://jobs.ashbyhq.com/{slug}")
            print("    SmartRecruiters: https://jobs.smartrecruiters.com/{company_id}")
            print("    iCIMS:          https://{tenant}.icims.com/jobs/{id}/job")
            sys.exit(1)

    # Step 2: if Workday site is unknown, probe for it
    if config["ats"] == "workday" and not config.get("site"):
        print(f"  Workday tenant found ({config['tenant']}.wd{config['wd_num']}), probing for site name...")
        site = try_workday_sites(config["tenant"], config["wd_num"])
        if site:
            config["site"] = site
            print(f"  Site found: {site}")
        else:
            print("  Could not auto-detect Workday site name.")
            site = input("  Enter site name manually (check the URL path after /en-US/): ").strip()
            if site:
                config["site"] = site
            else:
                sys.exit(1)

    # Step 3: verify the config works
    print(f"\n  Verifying config: {config}")
    ok = verify_config(config)
    if ok:
        print("  ✓ Config verified — endpoint is live.")
    else:
        print("  ✗ Could not verify endpoint. The config may still be partially correct.")
        proceed = input("  Add to company_ats_map.json anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            print("  Aborted.")
            sys.exit(1)
        config["verified"] = False

    # Step 4: collect company details
    print()
    company_name = input("  Company name (e.g. 'Bain & Company'): ").strip()
    if not company_name:
        print("  Aborted — company name required.")
        sys.exit(1)

    tier_input = input("  Tier [1/2/3] (1=top priority, 3=lower): ").strip()
    tier = int(tier_input) if tier_input in ("1", "2", "3") else 2

    key = re.sub(r"[^a-z0-9]", "", company_name.lower())

    # Step 5: save
    data  = load_map()
    entry = build_entry(company_name, tier, config)
    data["companies"][key] = entry
    save_map(data)

    print(f"\n  Saved '{company_name}' → company_ats_map.json (key: '{key}')")
    print(f"  Entry: {entry}\n")


if __name__ == "__main__":
    main()
