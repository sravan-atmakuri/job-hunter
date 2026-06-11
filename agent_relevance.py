"""
agent_relevance.py — Reads jobs_raw.json, scores each job 1-10 for relevance,
checks hiring status, filters low scores, saves to output/jobs_filtered.json.
Uses the Claude CLI (claude -p) via claude_cli.py — no API key needed.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from claude_cli import call_claude_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [relevance] %(message)s")
log = logging.getLogger(__name__)

INPUT_FILE = Path("output/jobs_raw.json")
OUTPUT_FILE = Path("output/jobs_filtered.json")
SEEN_JOBS_FILE = Path("output/seen_jobs.json")
CONFIG_FILE = Path("config.yaml")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Job description fetcher
# ---------------------------------------------------------------------------

def fetch_job_description(url: str) -> str:
    if not url or not url.startswith("http"):
        return ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # LinkedIn-specific selectors (guest job pages)
        from urllib.parse import urlparse
        if "linkedin.com" in urlparse(url).netloc:
            for sel in [
                "div.show-more-less-html__markup",
                "div.description__text--rich",
                "div.description__text",
                "section.description",
            ]:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(separator="\n", strip=True)
                    if len(text) > 200:
                        return text[:5000]

        # Generic fallback selectors
        for sel in [
            "div#jobDescriptionText",
            "div[class*='job-description']",
            "div[class*='jobDescription']",
            "main article",
            "main",
        ]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return text[:5000]

        return soup.get_text(separator="\n", strip=True)[:5000]
    except Exception as exc:
        log.debug("Could not fetch JD for %s: %s", url, exc)
        return ""


# ---------------------------------------------------------------------------
# Contract filter
# ---------------------------------------------------------------------------

CONTRACT_SIGNALS = {
    "contract", "c2c", "corp-to-corp", "corp to corp", "c2h",
    "contract-to-hire", "contract to hire", "w2 contract", "1099",
    "third party", "third-party",
}

FULLTIME_ONLY_SIGNALS = {
    "full-time only", "full time only", "permanent only", "no c2c",
    "no corp to corp", "no third party", "no 3rd party",
    "citizens and green card", "direct hire only",
}


H1B_OK_SIGNALS = {
    "h1b", "h-1b", "h1-b", "visa sponsorship", "sponsor visa",
    "will sponsor", "sponsorship provided", "sponsorship available",
    "open to h1b", "h1b welcome", "visa transfer",
}

H1B_NO_SIGNALS = {
    "no h1b", "no h-1b", "no visa", "no sponsorship", "not sponsor",
    "cannot sponsor", "will not sponsor", "does not sponsor",
    "sponsorship not available", "us citizens only", "us citizen only",
    "citizens only", "green card only", "permanent residents only",
    "must be a us citizen", "only us citizens", "no opt", "no cpt",
    "citizens and green card holders only",
}


def is_h1b_eligible(job: dict, jd_text: str) -> bool:
    """
    Return False only when there is an explicit H1B rejection signal and no
    positive sponsorship signal. Keep the job if unclear.
    """
    combined = (
        job.get("title", "") + " " +
        job.get("description_snippet", "") + " " +
        jd_text
    ).lower()

    has_ok = any(s in combined for s in H1B_OK_SIGNALS)
    has_no = any(s in combined for s in H1B_NO_SIGNALS)

    if has_no and not has_ok:
        return False  # explicitly excludes H1B → filter out
    return True  # sponsorship mentioned or unclear → keep


# ---------------------------------------------------------------------------
# Salary filter
# ---------------------------------------------------------------------------

def parse_salary(text: str) -> tuple[float, str] | None:
    """
    Extract the first salary figure from text.
    Returns (amount, kind) where kind is 'hourly' or 'annual', or None if not found.
    Requires a $ sign OR a salary-related keyword nearby to avoid false positives.
    Handles: $65/hr, $65/hour, $100,000/yr, $100k, 80k-100k salary.
    """
    text_lower = text.lower().replace(",", "")

    SALARY_CONTEXT = r"(?:salary|compensation|pay|rate|earn|wage|remuneration|offer)"

    # Hourly with $ sign: $65/hr or $65.50/hour or $65 per hour
    m = re.search(r"\$\s*(\d+(?:\.\d+)?)\s*(?:k\b)?\s*(?:/\s*(?:hr|hour)|per\s+hour)", text_lower)
    if m:
        rate = float(m.group(1))
        if "k" in m.group(0):
            rate *= 1000
        return (rate, "hourly")

    # Hourly with context keyword: "rate of 65/hr"
    m = re.search(SALARY_CONTEXT + r".{0,30}?(\d+(?:\.\d+)?)\s*/\s*(?:hr|hour)", text_lower)
    if m:
        return (float(m.group(1)), "hourly")

    # Annual with $ and k suffix: $100k or $80k-$100k (take lower bound)
    m = re.search(r"\$\s*(\d+(?:\.\d+)?)k\b", text_lower)
    if m:
        return (float(m.group(1)) * 1000, "annual")

    # Annual with $ sign and 5-6 digit number: $100000 or $85,000/year
    m = re.search(r"\$\s*(\d{5,6})(?:\s*/\s*(?:year|yr|annually))?", text_lower)
    if m:
        return (float(m.group(1)), "annual")

    # Annual with salary context keyword: "salary: 90000" or "salary up to 120000"
    m = re.search(SALARY_CONTEXT + r".{0,30}?(\d{5,6})\b", text_lower)
    if m:
        return (float(m.group(1)), "annual")

    return None


def is_above_min_salary(
    job: dict,
    jd_text: str,
    min_hourly: float | None,
    min_annual: float | None,
) -> bool:
    """
    Return False only when a salary is clearly mentioned AND is below the threshold.
    Hourly rates compared against min_hourly; annual figures against min_annual.
    Keep the job if no salary is found (many postings omit it).
    """
    combined = job.get("description_snippet", "") + " " + jd_text
    result = parse_salary(combined)
    if result is None:
        return True  # no salary mentioned → keep
    amount, kind = result
    if kind == "hourly":
        if min_hourly is not None and amount < min_hourly:
            log.info("  Salary ~$%.0f/hr below minimum $%.0f/hr — skipping", amount, min_hourly)
            return False
    else:  # annual
        if min_annual is not None and amount < min_annual:
            log.info("  Salary ~$%.0f/yr below minimum $%.0f/yr — skipping", amount, min_annual)
            return False
    return True


# ---------------------------------------------------------------------------
# Recency filter
# ---------------------------------------------------------------------------

def parse_posted_at(job: dict, jd_text: str) -> datetime | None:
    """
    Return the posting date as a timezone-aware datetime, or None if unknown.
    Tries: job['posted_at'] field first, then parses 'X days/weeks ago' from JD.
    """
    posted_at = job.get("posted_at", "")
    if posted_at:
        try:
            dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Parse "X days ago", "X weeks ago", "X months ago" from JD text
    text = jd_text.lower()
    m = re.search(r"(\d+)\s*(day|week|month)s?\s*ago", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n if unit == "day" else n * 7 if unit == "week" else n * 30
        from datetime import timedelta
        return datetime.now(timezone.utc) - timedelta(days=days)

    return None


def is_recent_enough(job: dict, jd_text: str, max_days: int) -> bool:
    """Return False only when posting date is known and older than max_days."""
    posted = parse_posted_at(job, jd_text)
    if posted is None:
        return True  # unknown date → keep
    age_days = (datetime.now(timezone.utc) - posted).days
    if age_days > max_days:
        log.info("  Posted %d days ago (max %d) — skipping", age_days, max_days)
        return False
    return True


# ---------------------------------------------------------------------------
# Seen jobs persistence
# ---------------------------------------------------------------------------

def load_seen_jobs() -> set[str]:
    if SEEN_JOBS_FILE.exists():
        return set(json.loads(SEEN_JOBS_FILE.read_text()))
    return set()


def mark_jobs_seen(job_ids: list[str]) -> None:
    seen = load_seen_jobs()
    seen.update(job_ids)
    SEEN_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_JOBS_FILE.write_text(json.dumps(list(seen)))


def is_contract_role(job: dict, jd_text: str) -> bool:
    """
    Return True if the job is a contract/C2C/C2H role or unclear.
    Return False only if it is explicitly full-time/permanent with no contract signals.
    """
    # Check config job_types — if not filtering by contract, allow all
    combined = (
        job.get("title", "") + " " +
        job.get("description_snippet", "") + " " +
        jd_text
    ).lower()

    has_contract_signal = any(s in combined for s in CONTRACT_SIGNALS)
    has_fulltime_only = any(s in combined for s in FULLTIME_ONLY_SIGNALS)

    if has_fulltime_only and not has_contract_signal:
        return False  # explicitly full-time, no contract mention → filter out

    return True  # contract signal present, or unclear → keep


# ---------------------------------------------------------------------------
# Hiring status check
# ---------------------------------------------------------------------------

def is_job_still_open(url: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 404:
            return False
        text = resp.text.lower()
        closed_signals = [
            "no longer accepting",
            "job is closed",
            "position has been filled",
            "this job has expired",
            "application period has ended",
        ]
        return not any(sig in text for sig in closed_signals)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Claude-based relevance scorer
# ---------------------------------------------------------------------------

SCORING_SYSTEM = """You are an expert technical recruiter and job relevance analyst.
You will be given a job listing and must evaluate its relevance to a candidate's
job search criteria. Return ONLY a JSON object with these fields:
- score: integer 1-10 (10 = perfect match, 1 = completely irrelevant)
- reasoning: one-sentence explanation of the score
- keywords: list of up to 5 key technical skills mentioned in the job
- seniority: "junior", "mid", "senior", or "staff/principal"

Return valid JSON only, no markdown fences."""


def score_job(job: dict, config: dict, jd_text: str) -> dict:
    titles = ", ".join(config["job_titles"])
    states = ", ".join(config["target_states"])
    work_type = config.get("work_type", "any")
    job_types = config.get("job_types", [])
    job_types_str = ", ".join(job_types) if job_types else "any"

    prompt = f"""{SCORING_SYSTEM}

Candidate is looking for: {titles}
Preferred locations/states: {states}
Preferred job type: {job_types_str} (includes C2C and C2H arrangements)
Work type preference: {work_type}

Evaluate this job listing:
Job Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Snippet: {job.get('description_snippet', '')}

Full Description (if available):
{jd_text[:3000] if jd_text else '(not available)'}

Return a JSON object with: score (1-10), reasoning, keywords (list), seniority."""

    result = call_claude_json(prompt)
    if not result:
        return {"score": 5, "reasoning": "Parse error", "keywords": [], "seniority": "mid"}

    return {
        "score": int(result.get("score", 5)),
        "reasoning": result.get("reasoning", ""),
        "keywords": result.get("keywords", []),
        "seniority": result.get("seniority", "mid"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(jobs: list[dict] | None = None) -> list[dict]:
    config = load_config()
    min_score: int = config.get("min_relevance_score", 6)
    max_days: int = config.get("max_days_old", 15)
    job_types = [jt.lower() for jt in config.get("job_types", [])]
    visa_types = [v.lower() for v in config.get("visa_types", [])]

    # Parse min_hourly_rate and min_annual_salary from config
    min_hourly: float | None = None
    min_annual: float | None = None
    raw_hourly = config.get("min_hourly_rate")
    raw_annual = config.get("min_annual_salary")
    if raw_hourly is not None:
        try:
            min_hourly = float(str(raw_hourly).replace("$", "").replace(",", "").split("/")[0])
        except ValueError:
            pass
    if raw_annual is not None:
        try:
            min_annual = float(str(raw_annual).replace("$", "").replace(",", ""))
        except ValueError:
            pass

    if jobs is None:
        if not INPUT_FILE.exists():
            log.error("Input file not found: %s — run agent_scraper first", INPUT_FILE)
            return []
        jobs = json.loads(INPUT_FILE.read_text())

    log.info("Scoring %d jobs for relevance...", len(jobs))
    if min_hourly:
        log.info("Min hourly rate filter: $%.0f/hr", min_hourly)
    if min_annual:
        log.info("Min annual salary filter: $%.0f/yr", min_annual)
    log.info("Max days old filter: %d days", max_days)

    scored_jobs: list[dict] = []
    evaluated_ids: list[str] = []

    for i, job in enumerate(jobs, 1):
        log.info("[%d/%d] Checking '%s' @ %s", i, len(jobs), job["title"], job["company"])

        is_open = is_job_still_open(job["url"])
        if not is_open:
            log.info("  → Skipping (job appears closed)")
            evaluated_ids.append(job["id"])
            continue

        jd_text = fetch_job_description(job["url"])
        time.sleep(0.5)

        # Recency filter
        if not is_recent_enough(job, jd_text, max_days):
            log.info("  → Skipping (too old)")
            evaluated_ids.append(job["id"])
            continue

        # Contract filter
        if "contract" in job_types:
            if not is_contract_role(job, jd_text):
                log.info("  → Skipping (full-time only, no contract/C2C/C2H signals)")
                evaluated_ids.append(job["id"])
                continue

        # H1B filter
        if "h1b" in visa_types:
            if not is_h1b_eligible(job, jd_text):
                log.info("  → Skipping (explicitly excludes H1B/visa sponsorship)")
                evaluated_ids.append(job["id"])
                continue

        # Salary filter
        if (min_hourly or min_annual) and not is_above_min_salary(job, jd_text, min_hourly, min_annual):
            evaluated_ids.append(job["id"])
            continue

        score_result = score_job(job, config, jd_text)
        log.info(
            "  → Score: %d/10 (%s)",
            score_result["score"],
            score_result["reasoning"][:60],
        )
        evaluated_ids.append(job["id"])

        if score_result["score"] >= min_score:
            scored_jobs.append({
                **job,
                "relevance_score": score_result["score"],
                "relevance_reasoning": score_result["reasoning"],
                "keywords": score_result["keywords"],
                "seniority": score_result["seniority"],
                "full_description": jd_text,
                "is_open": is_open,
            })

        time.sleep(1)

    scored_jobs.sort(key=lambda j: j["relevance_score"], reverse=True)

    # Persist seen job IDs so future runs skip them
    mark_jobs_seen(evaluated_ids)
    log.info("Marked %d jobs as seen", len(evaluated_ids))

    log.info(
        "Filtered to %d jobs (score >= %d) from %d total",
        len(scored_jobs), min_score, len(jobs),
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(scored_jobs, indent=2))
    log.info("Saved to %s", OUTPUT_FILE)

    return scored_jobs


if __name__ == "__main__":
    run()
