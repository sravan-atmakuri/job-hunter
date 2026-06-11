"""
agent_scraper.py — Scrapes LinkedIn, Dice, Indeed, SimplyHired, Monster, Idealist, and company job pages.
Saves raw job listings to output/jobs_raw.json.

Pre-filters all results by keyword relevance so only titles matching the
config job_titles reach the expensive LLM scoring step.
"""

from __future__ import annotations

import json
import time
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, urljoin

import requests
import yaml
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scraper] %(message)s")
log = logging.getLogger(__name__)

OUTPUT_FILE = Path("output/jobs_raw.json")
SEEN_JOBS_FILE = Path("output/seen_jobs.json")
CONFIG_FILE = Path("config.yaml")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.linkedin.com/jobs/search/",
}

# ---------------------------------------------------------------------------
# Title relevance pre-filter
# ---------------------------------------------------------------------------

# Roles that are never relevant regardless of keyword matches
BLOCKLIST = {
    "architect", "data architect", "solution architect", "enterprise architect",
    "director", "vp ", "vice president", "chief", "cto", "cio", "ceo",
    "accountant", "accounting", "financial", "finance", "recruiter", "hr ",
    "human resources", "marketing", "sales representative", "account executive",
    "account manager", "lawyer", "legal", "nurse", "physician", "doctor",
    "pharmacist", "warehouse", "driver", "delivery",
    "manufacturing", "factory", "machinist", "electrician", "plumber",
    "carpenter", "construction", "hvac", "rail", "bridge engineer",
    "civil engineer", "mechanical engineer", "hardware engineer",
    "firmware engineer", "embedded engineer",
    "product designer", "ux designer", "graphic designer",
    "network engineer", "security engineer", "devops engineer",
    "site reliability", "sre", "infrastructure engineer",
}


def build_keywords(config: dict) -> set[str]:
    """
    Extract root keywords from config job_titles.
    e.g. ["Senior QA Engineer", "Salesforce Developer"] →
         {"qa", "quality", "salesforce", "test", "engineer", "developer", ...}
    """
    keywords: set[str] = set()
    for title in config.get("job_titles", []):
        for word in title.lower().split():
            if len(word) > 2:
                keywords.add(word)

    # Always include these core QA/Salesforce root terms
    keywords.update({
        "qa", "qe", "quality", "assurance", "test", "testing", "tester",
        "automation", "salesforce", "sfdc", "sfdx", "sfqa",
        "contract", "c2c", "c2h",
    })
    return keywords


def title_is_relevant(title: str, keywords: set[str], blocklist: set[str]) -> bool:
    """
    Return True if the job title:
      - Contains at least one keyword from our target set, AND
      - Does not match any blocklist term (unless the title also matches a keyword)
    """
    lower = title.lower()

    # Hard block — reject immediately if any blocklist phrase appears
    # and no whitelist keyword overrides it
    has_keyword = any(kw in lower for kw in keywords)
    has_block = any(bl in lower for bl in blocklist)

    if not has_keyword:
        return False
    if has_block and not has_keyword:
        return False

    # Edge case: "QA Director" — has keyword "qa" but also "director"
    # Keep it only if a core QA/test/salesforce word is present
    core = {"qa", "qe", "quality", "test", "salesforce", "sfdc", "automation"}
    if has_block:
        return any(c in lower for c in core)

    return True


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_seen_jobs() -> set[str]:
    if SEEN_JOBS_FILE.exists():
        return set(json.loads(SEEN_JOBS_FILE.read_text()))
    return set()


def make_job_id(title: str, company: str, url: str) -> str:
    raw = f"{title}|{company}|{url}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Indeed scraper
# ---------------------------------------------------------------------------

def scrape_indeed(
    title: str, location: str, work_type: str | list,
    max_jobs: int, delay: float, keywords: set[str],
) -> list[dict]:
    jobs = []
    params: dict = {"q": title, "l": location, "limit": 25, "start": 0}
    work_types = work_type if isinstance(work_type, list) else [work_type]
    if "remote" in work_types:
        params["remotejob"] = "032b3046-06a3-4876-8dfd-474eb5e7ed11"

    session = requests.Session()
    session.headers.update(HEADERS)

    while len(jobs) < max_jobs:
        url = "https://www.indeed.com/jobs?" + urlencode(params)
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Indeed request failed: %s", exc)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.job_seen_beacon") or soup.select("div[data-jk]")
        if not cards:
            break

        for card in cards:
            if len(jobs) >= max_jobs:
                break
            try:
                title_el = card.select_one("h2.jobTitle span[title], h2.jobTitle a span")
                company_el = card.select_one("span.companyName, [data-testid='company-name']")
                location_el = card.select_one("div.companyLocation, [data-testid='text-location']")
                snippet_el = card.select_one("div.job-snippet ul li, div[class*='underShelfFooter']")
                link_el = card.select_one("h2.jobTitle a, a[data-jk]")

                job_title = title_el.get_text(strip=True) if title_el else "Unknown"

                # Pre-filter before adding
                if not title_is_relevant(job_title, keywords, BLOCKLIST):
                    log.debug("  Filtered (Indeed): %s", job_title)
                    continue

                company = company_el.get_text(strip=True) if company_el else "Unknown"
                job_location = location_el.get_text(strip=True) if location_el else location
                description_snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                href = link_el.get("href", "") if link_el else ""
                if href and not href.startswith("http"):
                    href = "https://www.indeed.com" + href
                jk = link_el.get("data-jk", "") if link_el else ""
                job_url = f"https://www.indeed.com/viewjob?jk={jk}" if jk else href

                jobs.append({
                    "id": make_job_id(job_title, company, job_url),
                    "title": job_title,
                    "company": company,
                    "location": job_location,
                    "description_snippet": description_snippet,
                    "url": job_url,
                    "source": "indeed",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception as exc:
                log.debug("Error parsing Indeed card: %s", exc)

        params["start"] += 25
        time.sleep(delay)

    log.info("Indeed: %d relevant jobs for '%s' / '%s'", len(jobs), title, location)
    return jobs


# ---------------------------------------------------------------------------
# LinkedIn scraper
# ---------------------------------------------------------------------------

def scrape_linkedin(
    title: str, location: str, work_type: str | list,
    max_jobs: int, delay: float, keywords: set[str],
    job_types: list[str] | None = None,
    page_offset: int = 0,
) -> list[dict]:
    jobs = []
    WT_MAP = {"remote": "2", "onsite": "1", "hybrid": "3"}
    work_types = work_type if isinstance(work_type, list) else [work_type]
    f_WT = ",".join(filter(None, (WT_MAP.get(wt, "") for wt in work_types)))

    # LinkedIn job type codes: C=Contract, F=Full-time, P=Part-time, T=Temporary
    JT_MAP = {"contract": "C", "full-time": "F", "part-time": "P", "temporary": "T"}
    f_JT = ",".join(filter(None, (JT_MAP.get(jt.lower(), "") for jt in (job_types or []))))

    session = requests.Session()
    session.headers.update(HEADERS)

    start = page_offset
    while len(jobs) < max_jobs:
        params: dict = {"keywords": title, "location": location, "start": start, "count": 25}
        if f_WT:
            params["f_WT"] = f_WT
        if f_JT:
            params["f_JT"] = f_JT

        url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
            + urlencode(params)
        )
        resp = None
        for attempt in range(3):
            try:
                resp = session.get(url, timeout=15)
                if resp.status_code == 429:
                    wait = 45 * (attempt + 1)
                    log.warning("LinkedIn rate-limited (429) — waiting %ds before retry %d/3...", wait, attempt + 1)
                    time.sleep(wait)
                    resp = None
                    continue
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                log.warning("LinkedIn request failed: %s", exc)
                resp = None
                break
        if resp is None:
            log.warning("LinkedIn: giving up on this search term after retries")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("li")
        if not cards:
            break

        for card in cards:
            if len(jobs) >= max_jobs:
                break
            try:
                title_el = card.select_one("h3.base-search-card__title")
                company_el = card.select_one("h4.base-search-card__subtitle a")
                location_el = card.select_one("span.job-search-card__location")
                link_el = card.select_one("a.base-card__full-link")
                time_el = card.select_one("time")

                job_title = title_el.get_text(strip=True) if title_el else "Unknown"

                # Pre-filter
                if not title_is_relevant(job_title, keywords, BLOCKLIST):
                    log.debug("  Filtered (LinkedIn): %s", job_title)
                    continue

                company = company_el.get_text(strip=True) if company_el else "Unknown"
                job_location = location_el.get_text(strip=True) if location_el else location
                job_url = link_el.get("href", "").split("?")[0] if link_el else ""
                posted_at = time_el.get("datetime", "") if time_el else ""

                if not job_url:
                    continue

                jobs.append({
                    "id": make_job_id(job_title, company, job_url),
                    "title": job_title,
                    "company": company,
                    "location": job_location,
                    "description_snippet": "",
                    "url": job_url,
                    "source": "linkedin",
                    "posted_at": posted_at,
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception as exc:
                log.debug("Error parsing LinkedIn card: %s", exc)

        start += 25
        time.sleep(delay)

    log.info("LinkedIn: %d relevant jobs for '%s' / '%s'", len(jobs), title, location)
    return jobs


# ---------------------------------------------------------------------------
# Company page scraper — now keyword-filtered
# ---------------------------------------------------------------------------

def scrape_company_page(
    name: str, url: str, delay: float, keywords: set[str],
) -> list[dict]:
    jobs = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Company page %s failed: %s", name, exc)
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")

    candidates = (
        soup.select("li a[href]")
        + soup.select("div.job a[href]")
        + soup.select("tr a[href]")
    )

    seen: set[str] = set()
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    skipped = 0

    for el in candidates:
        text = el.get_text(strip=True)
        href = el.get("href", "")
        if not text or not href:
            continue

        # Word count guard — job titles are 2–12 words
        word_count = len(text.split())
        if word_count < 2 or word_count > 12:
            continue

        # Keyword pre-filter — only keep if title matches our targets
        if not title_is_relevant(text, keywords, BLOCKLIST):
            skipped += 1
            log.debug("  Filtered (company page): %s", text)
            continue

        if href not in seen:
            seen.add(href)
            full_url = href if href.startswith("http") else urljoin(base, href)
            jobs.append({
                "id": make_job_id(text, name, full_url),
                "title": text,
                "company": name,
                "location": "See posting",
                "description_snippet": "",
                "url": full_url,
                "source": f"company:{name.lower()}",
                "scraped_at": datetime.utcnow().isoformat(),
            })

    log.info(
        "Company page %s: %d relevant jobs (%d filtered out)",
        name, len(jobs), skipped,
    )
    time.sleep(delay)
    return jobs


# ---------------------------------------------------------------------------
# Dice scraper — uses Dice public search API
# ---------------------------------------------------------------------------

def scrape_dice(
    title: str, location: str, work_type: str | list,
    max_jobs: int, delay: float, keywords: set[str],
    job_types: list[str] | None = None,
    page_offset: int = 0,
) -> list[dict]:
    jobs = []

    # Dice has no reliable workplaceTypes API param — append to query instead
    work_types = work_type if isinstance(work_type, list) else [work_type]
    wt_keywords = [wt for wt in work_types if wt in ("remote", "hybrid")]
    query = title + (" " + " OR ".join(wt_keywords) if wt_keywords else "")

    # Correct Dice employmentType values (single value only)
    JT_MAP = {"contract": "Contract", "full-time": "Full Time", "part-time": "Part Time", "temporary": "Contract"}
    employment_type = JT_MAP.get((job_types or [""])[0].lower(), "")

    # State codes (e.g. "FL") don't geocode well — skip location, rely on query
    is_state_code = len(location) <= 2 and location.isalpha()
    use_location = "" if is_state_code else location

    # Convert linear offset to Dice page number (pageSize=20)
    page = max(1, (page_offset // 20) + 1)
    session = requests.Session()
    session.headers.update({
        **HEADERS,
        "Accept": "application/json",
        "x-api-key": "1YAt0R9wBg4WfsF9VB2778F5CHLAPMVW3WAZcKd8",
    })

    while len(jobs) < max_jobs:
        params: dict = {
            "q": query,
            "countryCode2": "US",
            "page": page,
            "pageSize": 20,
            "language": "en",
        }
        if use_location:
            params["location"] = use_location
            params["radius"] = "30"
            params["radiusUnit"] = "mi"
        if employment_type:
            params["employmentType"] = employment_type

        try:
            resp = session.get(
                "https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("Dice request failed: %s", exc)
            break

        results = data.get("data", [])
        if not results:
            break

        for item in results:
            if len(jobs) >= max_jobs:
                break
            job_title = item.get("title", "Unknown")
            if not title_is_relevant(job_title, keywords, BLOCKLIST):
                log.debug("  Filtered (Dice): %s", job_title)
                continue

            company = item.get("companyName", "Unknown")
            job_loc = item.get("jobLocation", {})
            job_location = job_loc.get("displayName", location)
            job_url = item.get("detailsPageUrl", "")
            posted_at = item.get("postedDate", "")

            jobs.append({
                "id": make_job_id(job_title, company, job_url),
                "title": job_title,
                "company": company,
                "location": job_location,
                "description_snippet": item.get("summary", "")[:300],
                "url": job_url,
                "source": "dice",
                "posted_at": posted_at,
                "scraped_at": datetime.utcnow().isoformat(),
            })

        if len(results) < 20:
            break
        page += 1
        time.sleep(delay)

    log.info("Dice: %d relevant jobs for '%s' / '%s'", len(jobs), title, location)
    return jobs


# ---------------------------------------------------------------------------
# SimplyHired scraper
# ---------------------------------------------------------------------------

def scrape_simplyhired(
    title: str, location: str, work_type: str | list,
    max_jobs: int, delay: float, keywords: set[str],
    job_types: list[str] | None = None,
    page_offset: int = 0,
) -> list[dict]:
    jobs = []
    work_types = work_type if isinstance(work_type, list) else [work_type]

    params: dict = {"q": title, "l": location, "fdb": "30"}
    if "remote" in work_types:
        params["rmt"] = "1"

    # SimplyHired job type: jt=fulltime, jt=parttime, jt=contract, jt=temp
    JT_MAP = {"full-time": "fulltime", "part-time": "parttime", "contract": "contract", "temporary": "temp"}
    jt = JT_MAP.get((job_types or [""])[0].lower(), "")
    if jt:
        params["jt"] = jt

    page = (page_offset // 20) + 1
    session = requests.Session()
    session.headers.update(HEADERS)

    while len(jobs) < max_jobs:
        params["pn"] = page
        url = "https://www.simplyhired.com/search?" + urlencode(params)
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("SimplyHired request failed: %s", exc)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("li[data-jobkey], article.SerpJob, div.jobposting-title")
        if not cards:
            # Try alternate selectors
            cards = soup.select("[data-jobkey]")
        if not cards:
            log.debug("SimplyHired: no cards found on page %d (site may have changed)", page)
            break

        found_any = False
        for card in cards:
            if len(jobs) >= max_jobs:
                break
            try:
                title_el = card.select_one("h2 a, h3 a, a[data-action='click_label']")
                company_el = card.select_one("span[data-testid='companyName'], .company, [class*='company']")
                location_el = card.select_one("span[data-testid='searchSerpJobLocation'], .location, [class*='location']")
                link_el = card.select_one("a[href]")

                job_title = title_el.get_text(strip=True) if title_el else "Unknown"
                if not title_is_relevant(job_title, keywords, BLOCKLIST):
                    log.debug("  Filtered (SimplyHired): %s", job_title)
                    continue

                company = company_el.get_text(strip=True) if company_el else "Unknown"
                job_location = location_el.get_text(strip=True) if location_el else location
                href = link_el.get("href", "") if link_el else ""
                if href and not href.startswith("http"):
                    href = "https://www.simplyhired.com" + href

                if not href or job_title == "Unknown":
                    continue

                jobs.append({
                    "id": make_job_id(job_title, company, href),
                    "title": job_title,
                    "company": company,
                    "location": job_location,
                    "description_snippet": "",
                    "url": href,
                    "source": "simplyhired",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
                found_any = True
            except Exception as exc:
                log.debug("Error parsing SimplyHired card: %s", exc)

        if not found_any:
            break
        page += 1
        time.sleep(delay)

    log.info("SimplyHired: %d relevant jobs for '%s' / '%s'", len(jobs), title, location)
    return jobs


# ---------------------------------------------------------------------------
# Monster scraper
# ---------------------------------------------------------------------------

def scrape_monster(
    title: str, location: str, work_type: str | list,
    max_jobs: int, delay: float, keywords: set[str],
    job_types: list[str] | None = None,
    page_offset: int = 0,
) -> list[dict]:
    jobs = []
    work_types = work_type if isinstance(work_type, list) else [work_type]

    # Monster JSON API (used by their SPA)
    params: dict = {
        "q": title,
        "where": location,
        "isDynamicPage": "true",
        "page": (page_offset // 25) + 1,
    }
    if "remote" in work_types:
        params["where"] = "Remote"

    JT_MAP = {"full-time": "fulltime", "part-time": "parttime", "contract": "contract"}
    jt = JT_MAP.get((job_types or [""])[0].lower(), "")
    if jt:
        params["jobtype"] = jt

    session = requests.Session()
    session.headers.update({
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.monster.com/",
    })

    page = params["page"]
    while len(jobs) < max_jobs:
        params["page"] = page
        try:
            resp = session.get(
                "https://www.monster.com/jobs/search",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Monster request failed: %s", exc)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("section.card-content, div[data-jobid], article.job-cardstyle__JobCardComponent")
        if not cards:
            # Broader fallback
            cards = soup.select("[data-jobid]")
        if not cards:
            log.debug("Monster: no cards found on page %d", page)
            break

        found_any = False
        for card in cards:
            if len(jobs) >= max_jobs:
                break
            try:
                title_el = card.select_one("h2 a, h3 a, a.job-cardstyle__TitleLink, [class*='title'] a")
                company_el = card.select_one("div.company, span.company, [class*='company']")
                location_el = card.select_one("div.location, span.location, [class*='location']")
                link_el = card.select_one("a[href]")

                job_title = title_el.get_text(strip=True) if title_el else "Unknown"
                if not title_is_relevant(job_title, keywords, BLOCKLIST):
                    log.debug("  Filtered (Monster): %s", job_title)
                    continue

                company = company_el.get_text(strip=True) if company_el else "Unknown"
                job_location = location_el.get_text(strip=True) if location_el else location
                href = link_el.get("href", "") if link_el else ""
                if href and not href.startswith("http"):
                    href = "https://www.monster.com" + href

                if not href or job_title == "Unknown":
                    continue

                jobs.append({
                    "id": make_job_id(job_title, company, href),
                    "title": job_title,
                    "company": company,
                    "location": job_location,
                    "description_snippet": "",
                    "url": href,
                    "source": "monster",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
                found_any = True
            except Exception as exc:
                log.debug("Error parsing Monster card: %s", exc)

        if not found_any:
            break
        page += 1
        time.sleep(delay)

    log.info("Monster: %d relevant jobs for '%s' / '%s'", len(jobs), title, location)
    return jobs


# ---------------------------------------------------------------------------
# Idealist scraper — nonprofit-focused job board
# ---------------------------------------------------------------------------

def scrape_idealist(
    title: str, max_jobs: int, delay: float, keywords: set[str],
) -> list[dict]:
    """
    Scrapes Idealist.org for nonprofit jobs matching the given title.
    Uses their public search API.
    """
    jobs = []
    session = requests.Session()
    session.headers.update({
        **HEADERS,
        "Accept": "application/json",
        "Referer": "https://www.idealist.org/",
    })

    params = {
        "q": title,
        "type": "JOB",
        "pageSize": 20,
        "page": 1,
    }

    while len(jobs) < max_jobs:
        try:
            resp = session.get(
                "https://www.idealist.org/api/v1/actions/search",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("Idealist request failed: %s", exc)
            break

        results = data.get("results", [])
        if not results:
            break

        for item in results:
            if len(jobs) >= max_jobs:
                break
            try:
                job_title = item.get("name", "Unknown")
                if not title_is_relevant(job_title, keywords, BLOCKLIST):
                    log.debug("  Filtered (Idealist): %s", job_title)
                    continue

                org = item.get("org", {})
                company = org.get("name", "Unknown") if isinstance(org, dict) else "Unknown"
                city = item.get("city", "")
                state = item.get("state", "")
                job_location = f"{city}, {state}".strip(", ") or "See posting"
                slug = item.get("slug", "")
                item_id = item.get("id", "")
                job_url = f"https://www.idealist.org/en/jobs/{slug}-{item_id}" if slug else ""

                if not job_url:
                    continue

                jobs.append({
                    "id": make_job_id(job_title, company, job_url),
                    "title": job_title,
                    "company": company,
                    "location": job_location,
                    "description_snippet": item.get("description", "")[:300],
                    "url": job_url,
                    "source": "idealist",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception as exc:
                log.debug("Error parsing Idealist item: %s", exc)

        total_pages = data.get("totalPages", 1)
        if params["page"] >= total_pages:
            break
        params["page"] += 1
        time.sleep(delay)

    log.info("Idealist: %d relevant nonprofit jobs for '%s'", len(jobs), title)
    return jobs


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate(jobs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for job in jobs:
        if job["id"] not in seen:
            seen.add(job["id"])
            unique.append(job)
    return unique


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(page_offset: int = 0) -> list[dict]:
    config = load_config()
    titles: list[str] = config["job_titles"]
    states: list[str] = config["target_states"]
    work_type: str | list = config.get("work_type", "any")
    job_types: list[str] = config.get("job_types", [])
    max_jobs: int = config.get("max_jobs_per_source", 50)
    delay: float = float(config.get("request_delay_seconds", 2))
    company_pages: list[dict] = config.get("company_pages") or []

    # Which sources to scrape — default to linkedin + dice if not specified
    default_sources = ["linkedin", "dice"]
    enabled_sources: set[str] = set(config.get("sources", default_sources))
    log.info("Enabled sources: %s", sorted(enabled_sources))

    # Build keyword set once from config titles
    keywords = build_keywords(config)
    if page_offset == 0:
        log.info("Pre-filter keywords: %s", sorted(keywords))
    else:
        log.info("Fetching next page batch (offset=%d)...", page_offset)

    all_jobs: list[dict] = []
    per_source = max(1, max_jobs // max(len(titles), 1))

    for title in titles:
        for state in states:
            location = "Remote" if state == "Remote" else state

            if "linkedin" in enabled_sources:
                log.info("Scraping LinkedIn — '%s' / '%s' (offset=%d)", title, location, page_offset)
                all_jobs.extend(
                    scrape_linkedin(title, location, work_type, per_source, delay, keywords, job_types, page_offset)
                )

            if "dice" in enabled_sources:
                log.info("Scraping Dice — '%s' / '%s' (offset=%d)", title, location, page_offset)
                all_jobs.extend(
                    scrape_dice(title, location, work_type, per_source, delay, keywords, job_types, page_offset)
                )

            if "indeed" in enabled_sources:
                log.info("Scraping Indeed — '%s' / '%s'", title, location)
                all_jobs.extend(
                    scrape_indeed(title, location, work_type, per_source, delay, keywords)
                )

            if "simplyhired" in enabled_sources:
                log.info("Scraping SimplyHired — '%s' / '%s' (offset=%d)", title, location, page_offset)
                all_jobs.extend(
                    scrape_simplyhired(title, location, work_type, per_source, delay, keywords, job_types, page_offset)
                )

            if "monster" in enabled_sources:
                log.info("Scraping Monster — '%s' / '%s' (offset=%d)", title, location, page_offset)
                all_jobs.extend(
                    scrape_monster(title, location, work_type, per_source, delay, keywords, job_types, page_offset)
                )

    # Idealist searches by title only (no location filter — it's nationwide nonprofit)
    if "idealist" in enabled_sources and page_offset == 0:
        for title in titles:
            log.info("Scraping Idealist (nonprofit) — '%s'", title)
            all_jobs.extend(
                scrape_idealist(title, per_source, delay, keywords)
            )

    # Only scrape company pages on the first pass (they don't paginate the same way)
    if page_offset == 0:
        for page in company_pages:
            log.info("Scraping company page: %s", page["name"])
            all_jobs.extend(
                scrape_company_page(page["name"], page["url"], delay, keywords)
            )

    unique_jobs = deduplicate(all_jobs)

    # Filter out jobs already processed in previous runs
    seen = load_seen_jobs()
    new_jobs = [j for j in unique_jobs if j["id"] not in seen]
    log.info(
        "Total unique jobs: %d (%d already seen, %d new)",
        len(unique_jobs), len(unique_jobs) - len(new_jobs), len(new_jobs),
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(new_jobs, indent=2))
    log.info("Saved to %s", OUTPUT_FILE)

    return new_jobs


if __name__ == "__main__":
    run()
