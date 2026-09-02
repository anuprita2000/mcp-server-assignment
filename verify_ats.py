"""
verify_ats.py
-------------
Probes every company in company_ats_map.json to find a working ATS config.

Strategy:
  - Greenhouse / Lever: tests the current token/slug, then tries alternatives.
  - Workday: two-phase discovery —
      Phase 1: probe each tenant/wd_num combo with one site name to find
               which hostnames exist (422 = hostname valid, site name wrong;
               404 = hostname doesn't exist).
      Phase 2: for valid hostnames only, iterate site names until 200.
  - Companies known to use custom career sites (Google, Microsoft, Amazon)
    are flagged with a manual-check note instead of wasting probes.

On completion: auto-updates company_ats_map.json with verified=true for
every working config it finds, and prints a full summary.

Usage:
    python verify_ats.py
"""

import asyncio
import json
from pathlib import Path

import httpx

BASE_DIR          = Path(__file__).parent
COMPANY_MAP_FILE  = BASE_DIR / "company_ats_map.json"
MAX_CONCURRENCY   = 6   # max simultaneous company checks
REQUEST_TIMEOUT   = 10  # seconds per HTTP request


# ── Companies known to use fully custom career sites ──────────────────────────
# Workday probes will not work for these — they need manual URL mapping.
CUSTOM_CAREER_SITES = {
    "google":    "careers.google.com  (Google's own system — manual config needed)",
    "microsoft": "careers.microsoft.com  (Phenom People — manual config needed)",
    "amazon":    "amazon.jobs  (custom + some Workday divisions — manual config needed)",
}

# ── Greenhouse: alternative board tokens to try when primary returns 404 ──────
GREENHOUSE_ALTERNATIVES = {
    "uber":             ["uberfreight", "uber", "ubercareers", "ubertechnologies", "uber-eats"],
    "paloaltonetworks": ["paloaltonetworks", "palo-alto-networks", "panw", "paloalto"],
    "doordash":         ["doordash", "doordashinc", "door-dash", "doordashusa"],
    "rivian":           ["rivian", "rivianautomotive", "rivian-automotive"],
    "bain":             ["bain", "bainco", "bain-company", "bainandcompany"],
    "waymo":            ["waymo"],
    "lyft":             ["lyft"],
    "kearney":          ["kearney"],   # listed here so it also tries Lever fallback
}

# ── Lever: alternative slugs to try when primary returns 404 ─────────────────
LEVER_ALTERNATIVES = {
    "kearney": ["kearney", "at-kearney", "atkearney", "kearney-consulting", "kearneygroup"],
}

# ── Workday: per-company discovery hints ─────────────────────────────────────
# alts:     tenant name variations to try
# wd_nums:  Workday instance numbers to try (most common: 5, then 3, then 1)
# sites:    site/board names to probe after a valid hostname is found
WORKDAY_HINTS = {
    "amazon":      {"alts": ["amazon", "amazondev"],      "wd_nums": [1, 5],   "sites": ["Global_Posting_Website", "us_jobs", "External_Career_Site", "External"]},
    "microsoft":   {"alts": ["microsoftcareers", "microsoft"], "wd_nums": [5], "sites": ["External", "External_Career_Site", "MicrosoftCareers"]},
    "google":      {"alts": ["google"],                   "wd_nums": [5],      "sites": ["Google_External_Careers", "External", "External_Career_Site"]},
    "accenture":   {"alts": ["accenture"],                "wd_nums": [3, 5],   "sites": ["Accenture_Non_Referral_Careers_Sis", "External_Career_Site", "External", "Accenture_Careers"]},
    "deloitte":    {"alts": ["deloitte"],                 "wd_nums": [5, 3],   "sites": ["Deloitte_Consulting_Career_Site", "External_Career_Site", "External", "US_External_Careers"]},
    "mckinsey":    {"alts": ["mckinsey"],                 "wd_nums": [5, 3],   "sites": ["McKinsey_Careers", "External_Career_Site", "External"]},
    "bcg":         {"alts": ["bcg", "bostonco"],          "wd_nums": [5, 3],   "sites": ["BCG_Careers", "External_Career_Site", "External"]},
    "ey":          {"alts": ["ey"],                       "wd_nums": [5, 3],   "sites": ["EY_External_Careers", "External_Career_Site", "External"]},
    "ibm":         {"alts": ["ibm"],                      "wd_nums": [3, 5],   "sites": ["External_Career_Site", "External", "IBM_External_Career_Site"]},
    "salesforce":  {"alts": ["salesforce"],               "wd_nums": [5, 3],   "sites": ["External_Career_Site", "External", "Salesforce"]},
    "servicenow":  {"alts": ["servicenow"],               "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "ServiceNow"]},
    "apple":       {"alts": ["apple"],                    "wd_nums": [1, 5],   "sites": ["Apple", "External_Career_Site", "External"]},
    "bain":        {"alts": ["bain"],                     "wd_nums": [5, 3],   "sites": ["Bain_External_Careers", "External_Career_Site", "External"]},
    "kpmg":        {"alts": ["kpmg"],                     "wd_nums": [5, 3],   "sites": ["KPMG_External_Career_Site", "External_Career_Site", "External"]},
    "linkedin":    {"alts": ["linkedin"],                 "wd_nums": [5],      "sites": ["LinkedIn", "External_Career_Site", "External"]},
    "mastercard":  {"alts": ["mastercard"],               "wd_nums": [5, 3],   "sites": ["Global_External_Site", "External_Career_Site", "External"]},
    "paypal":      {"alts": ["paypal"],                   "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "PayPal"]},
    "capitalone":  {"alts": ["capitalone"],               "wd_nums": [5, 3],   "sites": ["Capital_One", "External_Career_Site", "External"]},
    "adobe":       {"alts": ["adobe"],                    "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "Adobe"]},
    "cisco":       {"alts": ["cisco"],                    "wd_nums": [5, 3],   "sites": ["Cisco", "External_Career_Site", "External"]},
    "nvidia":      {"alts": ["nvidia"],                   "wd_nums": [1, 5],   "sites": ["NVIDIAExternalCareerSite", "External_Career_Site", "External", "NVIDIA_External"]},
    "qualcomm":    {"alts": ["qualcomm"],                 "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "Qualcomm"]},
    "oracle":      {"alts": ["oracle"],                   "wd_nums": [1, 5],   "sites": ["oracle", "External_Career_Site", "External"]},
    "dell":        {"alts": ["dell"],                     "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "Dell"]},
    "walmart":     {"alts": ["walmart"],                  "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "Walmart"]},
    "pg":          {"alts": ["pg", "pandg", "pgcareers"], "wd_nums": [5, 3],   "sites": ["PGExternalCareerSite", "External_Career_Site", "External"]},
    "nike":        {"alts": ["nike"],                     "wd_nums": [5, 3],   "sites": ["External_Career_Site", "External", "Nike"]},
    "honeywell":   {"alts": ["honeywell"],                "wd_nums": [1, 5],   "sites": ["honeywell", "External_Career_Site", "External"]},
    "unilever":    {"alts": ["unilever"],                 "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "Unilever"]},
    "generalmills":{"alts": ["generalmills"],             "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "GeneralMills"]},
    "pfizer":      {"alts": ["pfizer"],                   "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "Pfizer"]},
    "iqvia":       {"alts": ["iqvia"],                    "wd_nums": [5, 3],   "sites": ["IQVIA_External", "External_Career_Site", "External"]},
    "alixpartners":{"alts": ["alixpartners"],             "wd_nums": [5, 3],   "sites": ["External", "External_Career_Site", "AlixPartners"]},
}


# ── HTTP probe helpers ────────────────────────────────────────────────────────

async def probe_greenhouse(client: httpx.AsyncClient, token: str) -> bool:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    try:
        r = await client.get(url, timeout=REQUEST_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


async def probe_lever(client: httpx.AsyncClient, slug: str) -> bool:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = await client.get(url, timeout=REQUEST_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


async def probe_workday(
    client: httpx.AsyncClient, tenant: str, site: str, wd_num: int
) -> int:
    """Returns the HTTP status code (200 = success, 422 = valid host/bad site, etc.)"""
    url = (
        f"https://{tenant}.wd{wd_num}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{site}/jobs"
    )
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }
    payload = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "manager"}
    try:
        r = await client.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        return r.status_code
    except Exception:
        return 0


# ── Per-company verification logic ────────────────────────────────────────────

async def verify_greenhouse(
    client: httpx.AsyncClient, key: str, info: dict
) -> dict | None:
    """
    Try the primary board_token first, then alternatives.
    Also falls back to trying the company as a Workday tenant if all fail.
    Returns update dict on success, None on failure.
    """
    alts = GREENHOUSE_ALTERNATIVES.get(key, [info.get("board_token", key)])
    for token in alts:
        if await probe_greenhouse(client, token):
            return {"board_token": token, "verified": True}
    return None


async def verify_lever(
    client: httpx.AsyncClient, key: str, info: dict
) -> dict | None:
    alts = LEVER_ALTERNATIVES.get(key, [info.get("company_slug", key)])
    for slug in alts:
        if await probe_lever(client, slug):
            return {"company_slug": slug, "verified": True}
    return None


async def verify_workday(
    client: httpx.AsyncClient, key: str, info: dict
) -> dict | None:
    """
    Two-phase Workday discovery:
    1) Find hostnames that respond (422 = hostname valid, site wrong).
    2) Try site names only against valid hostnames.
    """
    hints = WORKDAY_HINTS.get(key, {
        "alts":    [info.get("tenant", key)],
        "wd_nums": [5, 3, 1],
        "sites":   [info.get("site", "External_Career_Site"), "External", "External"],
    })

    sites    = hints["sites"]
    valid_hosts: list[tuple[str, int]] = []  # (tenant, wd_num) pairs that returned 422

    # Phase 1: host resolution
    for tenant in hints["alts"]:
        for wd_num in hints["wd_nums"]:
            status = await probe_workday(client, tenant, sites[0], wd_num)
            if status == 200:
                return {"tenant": tenant, "site": sites[0], "wd_num": wd_num, "verified": True}
            elif status in (422, 401):
                # Hostname exists; site name may be wrong
                valid_hosts.append((tenant, wd_num))

    # Phase 2: try remaining site names on valid hosts only
    for tenant, wd_num in valid_hosts:
        for site in sites[1:]:
            status = await probe_workday(client, tenant, site, wd_num)
            if status == 200:
                return {"tenant": tenant, "site": site, "wd_num": wd_num, "verified": True}

    return None


async def verify_company(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    key: str,
    info: dict,
) -> tuple[str, dict | None, str]:
    """
    Returns (key, update_dict_or_None, status_message).
    """
    async with sem:
        name = info["name"]
        ats  = info.get("ats")

        # Companies with custom career sites — skip Workday probing
        if key in CUSTOM_CAREER_SITES:
            note = CUSTOM_CAREER_SITES[key]
            return key, None, f"  [SKIP]  {name:20s}  Custom career site: {note}"

        if ats == "greenhouse":
            result = await verify_greenhouse(client, key, info)
            if result:
                return key, result, f"  [OK]    {name:20s}  Greenhouse  token='{result['board_token']}'"
            # Greenhouse failed — also try Workday as a fallback
            wd_hints = WORKDAY_HINTS.get(key)
            if wd_hints:
                wd_result = await verify_workday(client, key, info)
                if wd_result:
                    wd_result["ats"] = "workday"
                    return key, wd_result, (
                        f"  [OK]    {name:20s}  Moved → Workday  "
                        f"{wd_result['tenant']}.wd{wd_result['wd_num']} / {wd_result['site']}"
                    )
            return key, None, f"  [FAIL]  {name:20s}  Greenhouse 404 + no Workday match"

        elif ats == "lever":
            result = await verify_lever(client, key, info)
            if result:
                return key, result, f"  [OK]    {name:20s}  Lever  slug='{result['company_slug']}'"
            return key, None, f"  [FAIL]  {name:20s}  Lever 404"

        elif ats == "workday":
            result = await verify_workday(client, key, info)
            if result:
                return key, result, (
                    f"  [OK]    {name:20s}  Workday  "
                    f"{result['tenant']}.wd{result['wd_num']} / {result['site']}"
                )
            return key, None, f"  [FAIL]  {name:20s}  No working Workday config found"

        elif ats == "ashby":
            url = f"https://api.ashbyhq.com/posting-api/job-board/{info['org_slug']}"
            try:
                r = await client.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200:
                    count = len(r.json().get("jobs", []))
                    return key, {"verified": True}, f"  [OK]    {name:20s}  Ashby  {count} jobs"
                return key, None, f"  [FAIL]  {name:20s}  Ashby HTTP {r.status_code}"
            except Exception as e:
                return key, None, f"  [FAIL]  {name:20s}  Ashby error: {e}"

        elif ats == "smartrecruiters":
            url = f"https://api.smartrecruiters.com/v1/companies/{info['company_id']}/postings"
            try:
                r = await client.get(url, params={"status": "PUBLIC", "limit": 1}, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200:
                    count = r.json().get("totalFound", "?")
                    return key, {"verified": True}, f"  [OK]    {name:20s}  SmartRecruiters  {count} jobs"
                return key, None, f"  [FAIL]  {name:20s}  SmartRecruiters HTTP {r.status_code}"
            except Exception as e:
                return key, None, f"  [FAIL]  {name:20s}  SmartRecruiters error: {e}"

        else:
            return key, None, f"  [SKIP]  {name:20s}  Unknown ATS '{ats}'"


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    with open(COMPANY_MAP_FILE) as f:
        company_map = json.load(f)

    companies = company_map["companies"]
    sem       = asyncio.Semaphore(MAX_CONCURRENCY)

    print(f"Probing {len(companies)} companies (max {MAX_CONCURRENCY} concurrent)...\n")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [
            verify_company(sem, client, key, info)
            for key, info in companies.items()
        ]
        results = await asyncio.gather(*tasks)

    # ── Print results & collect updates ──────────────────────────────────────
    ok_lines   = []
    fail_lines = []
    skip_lines = []

    updates: dict[str, dict] = {}

    for key, update, message in results:
        if "[OK]" in message:
            ok_lines.append(message)
            updates[key] = update
        elif "[FAIL]" in message:
            fail_lines.append(message)
        else:
            skip_lines.append(message)

    print("── WORKING ─────────────────────────────────────────────────────────────")
    print("\n".join(ok_lines) or "  (none)")

    print("\n── FAILED ──────────────────────────────────────────────────────────────")
    print("\n".join(fail_lines) or "  (none)")

    print("\n── SKIPPED (manual config needed) ──────────────────────────────────────")
    print("\n".join(skip_lines) or "  (none)")

    # ── Apply updates to company_ats_map.json ────────────────────────────────
    print(f"\n{'─'*70}")
    if updates:
        print(f"Updating company_ats_map.json with {len(updates)} verified config(s)...")
        for key, update in updates.items():
            company_map["companies"][key].update(update)
            # Clear stale notes from previously wrong configs
            company_map["companies"][key].pop("notes", None)

        with open(COMPANY_MAP_FILE, "w") as f:
            json.dump(company_map, f, indent=2)
        print("Saved.")
    else:
        print("No updates to save.")

    print(f"\nSummary: {len(ok_lines)} verified | {len(fail_lines)} failed | {len(skip_lines)} skipped")
    if fail_lines:
        print(
            "\nFor failed companies, visit their career pages to find the correct\n"
            "ATS URL, then manually update company_ats_map.json."
        )


if __name__ == "__main__":
    asyncio.run(main())
