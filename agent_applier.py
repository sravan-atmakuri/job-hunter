"""
agent_applier.py — Reads resume_map.json and applies to each job using Playwright.
Logs results to output/applications_log.json.

Usage:
    python agent_applier.py             # live mode
    python agent_applier.py --dry-run  # print what would happen, no browser
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [applier] %(message)s")
log = logging.getLogger(__name__)

INPUT_FILE = Path("output/resume_map.json")
LOG_FILE = Path("output/applications_log.json")
CONFIG_FILE = Path("config.yaml")


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def get_applicant(config: dict) -> dict:
    """Return contact info with safe defaults."""
    a = config.get("applicant", {})
    return {
        "first_name": a.get("first_name", ""),
        "last_name": a.get("last_name", ""),
        "full_name": f"{a.get('first_name', '')} {a.get('last_name', '')}".strip(),
        "email": a.get("email", ""),
        "phone": a.get("phone", ""),
        "linkedin_url": a.get("linkedin_url", ""),
        "location": a.get("location", ""),
    }


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if "linkedin.com" in domain:
        return "linkedin"
    if "indeed.com" in domain:
        return "indeed"
    if "greenhouse.io" in domain:
        return "greenhouse"
    if "lever.co" in domain:
        return "lever"
    if "workday" in domain:
        return "workday"
    if "bamboohr.com" in domain:
        return "bamboohr"
    return "generic"


# ---------------------------------------------------------------------------
# Application handlers — each receives (page, job, applicant)
# ---------------------------------------------------------------------------

def _goto(page, url: str, retries: int = 2) -> bool:
    """Navigate to URL with retries. Returns True on success."""
    for attempt in range(retries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return True
        except Exception as e:
            if attempt < retries - 1:
                log.warning("  Page load attempt %d failed, retrying... (%s)", attempt + 1, str(e)[:60])
                page.wait_for_timeout(3000)
            else:
                raise
    return False


def _linkedin_autofill_page(page, applicant: dict) -> None:
    """Auto-fill all recognizable fields on the current LinkedIn Easy Apply page."""

    # ── Phone number ──────────────────────────────────────────────────────────
    phone_field = page.query_selector(
        "input[id*='phoneNumber']:not([type='hidden']), "
        "input[name*='phoneNumber'], "
        "input[aria-label*='Phone number'], "
        "input[aria-label*='phone number']"
    )
    if phone_field and applicant["phone"]:
        try:
            phone_field.scroll_into_view_if_needed()
            current = phone_field.input_value() or ""
            if not current.strip():
                phone_field.fill(applicant["phone"])
        except Exception:
            pass

    # ── City / location ───────────────────────────────────────────────────────
    city_field = page.query_selector(
        "input[id*='city'], input[aria-label*='City'], "
        "input[placeholder*='City'], input[aria-label*='city']"
    )
    if city_field and applicant["location"]:
        try:
            city_field.scroll_into_view_if_needed()
            current = city_field.input_value() or ""
            if not current.strip():
                city_field.fill(applicant["location"])
                page.wait_for_timeout(800)
                # Accept the first autocomplete suggestion if it appears
                suggestion = page.query_selector(
                    "div[role='option'], li[role='option'], .basic-typeahead__selectable"
                )
                if suggestion:
                    suggestion.click()
        except Exception:
            pass

    # ── Phone country code dropdown ───────────────────────────────────────────
    country_select = page.query_selector(
        "select[id*='phoneNumber-country'], select[name*='countryCode'], "
        "select[aria-label*='Phone country code']"
    )
    if country_select:
        try:
            country_select.select_option(value="US")
        except Exception:
            pass

    # ── Yes/No radio questions ────────────────────────────────────────────────
    # Iterate over form groups that contain radio buttons
    for form_group in page.query_selector_all(
        "div.jobs-easy-apply-form-section__grouping, "
        "div[data-test-form-element], "
        "fieldset.fb-radio-button-group"
    ):
        try:
            label_el = form_group.query_selector("label, legend, span.fb-form-element-label")
            label_text = (label_el.inner_text() if label_el else "").lower()
            if not label_text:
                continue

            # Work authorization → Yes
            if any(k in label_text for k in ("authorized to work", "legally authorized", "authorization to work")):
                yes_radio = form_group.query_selector(
                    "input[type='radio'][value='Yes'], "
                    "label:has-text('Yes') input[type='radio']"
                )
                if not yes_radio:
                    # Try by visible label text
                    for r in form_group.query_selector_all("input[type='radio']"):
                        parent = r.evaluate("n => n.closest('label') || n.parentElement")
                        if parent and "yes" in (page.evaluate("n => n.innerText", parent) or "").lower():
                            yes_radio = r
                            break
                if yes_radio:
                    yes_radio.scroll_into_view_if_needed()
                    yes_radio.check()
                    continue

            # Visa sponsorship — check config visa_types to decide
            if any(k in label_text for k in ("sponsorship", "visa sponsorship", "require sponsorship")):
                # Default: "Yes" (need sponsorship) since config has h1b in visa_types
                yes_radio = None
                for r in form_group.query_selector_all("input[type='radio']"):
                    try:
                        parent_text = (r.evaluate("n => { let p = n.closest('label') || n.parentElement; return p ? p.innerText : ''; }") or "").lower()
                        if "yes" in parent_text:
                            yes_radio = r
                            break
                    except Exception:
                        pass
                if yes_radio:
                    yes_radio.scroll_into_view_if_needed()
                    yes_radio.check()
                continue

            # Generic Yes/No with only two radio options → pick "Yes"
            radios = form_group.query_selector_all("input[type='radio']")
            if len(radios) == 2:
                for r in radios:
                    try:
                        parent_text = (r.evaluate("n => { let p = n.closest('label') || n.parentElement; return p ? p.innerText : ''; }") or "").lower()
                        if "yes" in parent_text:
                            r.scroll_into_view_if_needed()
                            r.check()
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    # ── Numeric / text inputs (years of experience, etc.) ────────────────────
    for field in page.query_selector_all("input[type='text'], input[type='number']"):
        try:
            current = field.input_value() or ""
            if current.strip():
                continue  # already filled
            aria = (field.get_attribute("aria-label") or "").lower()
            placeholder = (field.get_attribute("placeholder") or "").lower()
            label_text = aria or placeholder

            # Years of experience: default 5
            if "year" in label_text and "experience" in label_text:
                field.scroll_into_view_if_needed()
                field.fill("5")
        except Exception:
            pass

    # ── Select/dropdown fields ────────────────────────────────────────────────
    for sel in page.query_selector_all("select"):
        try:
            val = sel.input_value()
            if val and val not in ("", "Select an option"):
                continue
            options = sel.query_selector_all("option")
            # Skip the placeholder option (index 0 if it's empty/select-an-option)
            for opt in options[1:]:
                opt_val = opt.get_attribute("value") or ""
                opt_text = (opt.inner_text() or "").strip()
                if opt_val and opt_text and opt_text.lower() not in ("select an option", "please select"):
                    sel.select_option(value=opt_val)
                    break
        except Exception:
            pass


def apply_linkedin(page, job: dict, applicant: dict) -> dict:
    """Apply via LinkedIn Easy Apply."""
    try:
        _goto(page, job["job_url"])
        page.wait_for_timeout(3000)

        # Detect "Already Applied" — skip early
        try:
            body_text = (page.inner_text("body") or "").lower()
            if "you've already applied" in body_text or "already applied on" in body_text:
                return {"status": "skipped", "reason": "Already applied to this job on LinkedIn"}
        except Exception:
            pass

        # Comprehensive Easy Apply selectors — LinkedIn changes these frequently
        EASY_APPLY_SELECTOR = (
            "button.jobs-apply-button, "
            "button[aria-label*='Easy Apply'], "
            "button[aria-label*='easy apply'], "
            ".jobs-apply-button, "
            "button[data-job-id][aria-label*='Apply'], "
            "div.jobs-apply-button--top-card button, "
            "button.jobs-s-apply button"
        )
        easy_apply = page.query_selector(EASY_APPLY_SELECTOR)
        if not easy_apply:
            # Wait up to 12s for page to fully render the apply section
            try:
                page.wait_for_selector(EASY_APPLY_SELECTOR, timeout=12000)
                easy_apply = page.query_selector(EASY_APPLY_SELECTOR)
            except Exception:
                pass
        if not easy_apply:
            # Last resort: find any button whose visible text contains "Easy Apply"
            for btn in page.query_selector_all("button"):
                try:
                    if "easy apply" in (btn.inner_text() or "").lower():
                        easy_apply = btn
                        break
                except Exception:
                    pass
        if not easy_apply:
            # Check if there's a regular external Apply button and grab its URL
            ext_btn = page.query_selector(
                "button.jobs-apply-button, "
                "a[data-tracking-control-name*='apply'], "
                "a[href*='/apply'], a.apply-button"
            )
            ext_url = None
            if ext_btn:
                try:
                    ext_url = ext_btn.get_attribute("href")
                except Exception:
                    pass
            return {
                "status": "manual_review",
                "reason": f"No Easy Apply — requires external application: {ext_url or job['job_url']}",
            }

        easy_apply.scroll_into_view_if_needed()
        easy_apply.click()
        page.wait_for_timeout(3000)

        # Multi-step: auto-fill each page and advance until Submit or stuck
        prev_page_html = ""
        for step_num in range(12):
            page.wait_for_timeout(1500)

            # Auto-fill all recognizable fields on this page
            _linkedin_autofill_page(page, applicant)
            page.wait_for_timeout(600)

            # Check for Submit button first
            submit_btn = page.query_selector(
                "button[aria-label='Submit application'], "
                "button[aria-label*='Submit application'], "
                "button[aria-label*='Submit']"
            )
            if submit_btn:
                try:
                    submit_btn.scroll_into_view_if_needed()
                    submit_btn.click()
                    page.wait_for_timeout(2000)
                    return {"status": "applied", "reason": f"LinkedIn Easy Apply submitted (step {step_num + 1})"}
                except Exception as e:
                    return {"status": "error", "reason": f"Submit click failed: {str(e)[:150]}"}

            # Look for Next/Continue/Review buttons
            next_btn = page.query_selector(
                "button[aria-label='Continue to next step'], "
                "button[aria-label='Review your application'], "
                "button[aria-label*='Continue to next'], "
                "button[aria-label*='Review your'], "
                "button[aria-label*='Continue'], "
                "button[aria-label*='Next']"
            )
            if next_btn:
                # Check if button is disabled (unfilled required fields)
                is_disabled = next_btn.is_disabled()
                if is_disabled:
                    log.warning("  Next button disabled on step %d — required fields may be unfilled", step_num + 1)
                    return {
                        "status": "manual_review",
                        "reason": f"Easy Apply step {step_num + 1} has required fields that could not be auto-filled — complete manually",
                    }
                try:
                    next_btn.scroll_into_view_if_needed()
                    next_btn.click()
                    page.wait_for_timeout(1500)
                    # Detect if page didn't advance (validation error shown)
                    current_html = page.inner_html("body")[:500]
                    if current_html == prev_page_html:
                        return {
                            "status": "manual_review",
                            "reason": f"Easy Apply stuck on step {step_num + 1} — page did not advance after clicking Next",
                        }
                    prev_page_html = current_html
                except Exception as e:
                    return {"status": "error", "reason": f"Next click failed on step {step_num + 1}: {str(e)[:150]}"}
            else:
                # No Next and no Submit — form may have closed (already submitted) or errored
                break

        # Check if application was actually submitted (success banner)
        try:
            body_text = (page.inner_text("body") or "").lower()
            if "application submitted" in body_text or "your application was sent" in body_text:
                return {"status": "applied", "reason": "LinkedIn Easy Apply submitted (detected success banner)"}
        except Exception:
            pass

        return {"status": "skipped", "reason": "Multi-step Easy Apply — could not auto-complete all steps"}

    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:200]}


def apply_greenhouse(page, job: dict, applicant: dict) -> dict:
    """Apply via Greenhouse ATS."""
    try:
        _goto(page, job["job_url"])
        page.wait_for_timeout(2000)

        first_name = page.query_selector("input#first_name")
        last_name = page.query_selector("input#last_name")
        email = page.query_selector("input#email")
        phone = page.query_selector("input#phone")
        resume_upload = page.query_selector("input#resume")

        if not (first_name and last_name and email):
            return {"status": "skipped", "reason": "Could not find Greenhouse form fields"}

        first_name.fill(applicant["first_name"])
        last_name.fill(applicant["last_name"])
        email.fill(applicant["email"])
        if phone and applicant["phone"]:
            phone.fill(applicant["phone"])

        resume_path = job.get("resume_path", "")
        if resume_upload and resume_path and Path(resume_path).exists():
            resume_upload.set_input_files(resume_path)
            page.wait_for_timeout(2000)

        # LinkedIn URL field (common in Greenhouse)
        linkedin_field = page.query_selector(
            "input[id*='linkedin'], input[placeholder*='LinkedIn']"
        )
        if linkedin_field and applicant["linkedin_url"]:
            linkedin_field.fill(applicant["linkedin_url"])

        submit_btn = page.query_selector("input[type='submit'], button[type='submit']")
        if submit_btn:
            submit_btn.click()
            page.wait_for_timeout(3000)
            return {"status": "applied", "reason": "Greenhouse form submitted"}

        return {"status": "skipped", "reason": "Submit button not found"}

    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:200]}


def apply_lever(page, job: dict, applicant: dict) -> dict:
    """Apply via Lever ATS."""
    try:
        _goto(page, job["job_url"])
        page.wait_for_timeout(2000)

        apply_btn = page.query_selector(
            "a.postings-btn[href*='/apply'], a[data-qa='btn-apply']"
        )
        if apply_btn:
            apply_btn.click()
            page.wait_for_timeout(2000)

        name_field = page.query_selector("input[name='name']")
        email_field = page.query_selector("input[name='email']")
        phone_field = page.query_selector("input[name='phone']")
        resume_field = page.query_selector(
            "input[type='file'][name*='resume'], input[type='file'][name*='file']"
        )

        if not (name_field and email_field):
            return {"status": "skipped", "reason": "Could not find Lever form fields"}

        name_field.fill(applicant["full_name"])
        email_field.fill(applicant["email"])
        if phone_field and applicant["phone"]:
            phone_field.fill(applicant["phone"])

        if resume_field:
            resume_path = job.get("resume_path", "")
            if resume_path and Path(resume_path).exists():
                resume_field.set_input_files(resume_path)
                page.wait_for_timeout(2000)

        # LinkedIn URL (Lever often asks for this)
        linkedin_field = page.query_selector(
            "input[name='urls[LinkedIn]'], input[placeholder*='LinkedIn']"
        )
        if linkedin_field and applicant["linkedin_url"]:
            linkedin_field.fill(applicant["linkedin_url"])

        submit_btn = page.query_selector("button[type='submit'], input[type='submit']")
        if submit_btn:
            submit_btn.click()
            page.wait_for_timeout(3000)
            return {"status": "applied", "reason": "Lever form submitted"}

        return {"status": "skipped", "reason": "Submit button not found"}

    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:200]}


def apply_indeed(page, job: dict, applicant: dict) -> dict:
    """Open Indeed posting — full automation requires a logged-in session."""
    try:
        _goto(page, job["job_url"])
        page.wait_for_timeout(2000)
        return {
            "status": "manual_review",
            "reason": "Indeed opened — sign in and complete application manually",
        }
    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:200]}


def apply_generic(page, job: dict, applicant: dict) -> dict:
    """Fallback: open the page for manual review."""
    try:
        _goto(page, job["job_url"])
        return {
            "status": "manual_review",
            "reason": f"Opened — platform '{detect_platform(job['job_url'])}' requires manual application",
        }
    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:200]}


PLATFORM_HANDLERS = {
    "linkedin": apply_linkedin,
    "indeed": apply_indeed,
    "greenhouse": apply_greenhouse,
    "lever": apply_lever,
}


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def dry_run_apply(job: dict, applicant: dict) -> dict:
    platform = detect_platform(job["job_url"])
    return {
        "status": "dry_run",
        "reason": (
            f"Would apply via {platform} as {applicant['full_name']} "
            f"<{applicant['email']}> — {job['job_url']}"
        ),
        "resume": Path(job.get("resume_path", "N/A")).name,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(resume_map: dict | None = None, dry_run: bool = False) -> list[dict]:
    config = load_config()
    applicant = get_applicant(config)

    if not applicant["email"] and not dry_run:
        log.error("applicant.email is empty in config.yaml — cannot apply without contact info")
        return []

    if resume_map is None:
        if not INPUT_FILE.exists():
            log.error("Input file not found: %s — run agent_resume first", INPUT_FILE)
            return []
        resume_map = json.loads(INPUT_FILE.read_text())

    jobs = list(resume_map.values())
    log.info(
        "%s %d jobs as %s <%s>",
        "DRY RUN —" if dry_run else "Applying to",
        len(jobs),
        applicant["full_name"],
        applicant["email"],
    )

    existing_log: list[dict] = []
    if LOG_FILE.exists():
        existing_log = json.loads(LOG_FILE.read_text())
    already_applied = {
        e["job_id"] for e in existing_log if e.get("status") == "applied"
    }

    application_log = list(existing_log)

    if dry_run:
        for job in jobs:
            if job["job_id"] in already_applied:
                log.info("Skipping (already applied): %s @ %s", job["job_title"], job["company"])
                continue
            result = dry_run_apply(job, applicant)
            log.info("[DRY RUN] %s @ %s — %s", job["job_title"], job["company"], result["reason"])
            application_log.append({
                "job_id": job["job_id"],
                "job_title": job["job_title"],
                "company": job["company"],
                "job_url": job["job_url"],
                "resume_path": job.get("resume_path", ""),
                "platform": detect_platform(job["job_url"]),
                "applied_at": datetime.utcnow().isoformat(),
                **result,
            })
    else:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        # Primary: connect to Chrome via CDP (requires launch_apply.sh first).
        # Fallback: Playwright Chromium + saved LinkedIn session.
        SESSION_FILE = Path("output/linkedin_session.json")

        def _cdp_is_up(port: int = 9222) -> bool:
            import socket
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=2)
                s.close()
                return True
            except OSError:
                return False

        def _make_stealth_page(ctx):
            p = ctx.new_page()
            try:
                # playwright-stealth v2.x API
                from playwright_stealth import Stealth
                Stealth().apply_stealth_sync(p)
                log.info("Stealth mode applied (v2)")
            except ImportError:
                log.warning("playwright-stealth not installed — run: pip install playwright-stealth")
            except Exception as e:
                log.warning("Stealth apply failed: %s", e)
            return p

        with sync_playwright() as pw:
            cdp_connected = False
            if _cdp_is_up():
                try:
                    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
                    log.info("Connected to existing Chrome via CDP")
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = _make_stealth_page(context)
                    cdp_connected = True
                except Exception as cdp_err:
                    log.warning("CDP connect failed (%s) — using Playwright Chromium fallback", str(cdp_err)[:80])

            if not cdp_connected:
                log.info("Starting Playwright Chromium with saved session...")
                browser = pw.chromium.launch(
                    headless=False,
                    slow_mo=200,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-extensions-except=",
                    ],
                )
                context_kwargs: dict = {
                    "viewport": {"width": 1280, "height": 800},
                    "user_agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                }
                if SESSION_FILE.exists():
                    context_kwargs["storage_state"] = str(SESSION_FILE)
                    log.info("Loaded LinkedIn session from %s", SESSION_FILE)
                else:
                    log.warning("No saved session — LinkedIn will require manual login. Run: python3 login_linkedin.py")
                context = browser.new_context(**context_kwargs)
                page = _make_stealth_page(context)

            for job in jobs:
                if job["job_id"] in already_applied:
                    log.info(
                        "Skipping (already applied): %s @ %s",
                        job["job_title"], job["company"],
                    )
                    continue

                platform = detect_platform(job["job_url"])
                log.info(
                    "Applying via %s: %s @ %s",
                    platform, job["job_title"], job["company"],
                )

                handler = PLATFORM_HANDLERS.get(platform, apply_generic)
                result = handler(page, job, applicant)

                log.info("  → %s: %s", result["status"].upper(), result["reason"][:90])

                application_log.append({
                    "job_id": job["job_id"],
                    "job_title": job["job_title"],
                    "company": job["company"],
                    "job_url": job["job_url"],
                    "resume_path": job.get("resume_path", ""),
                    "platform": platform,
                    "applied_at": datetime.utcnow().isoformat(),
                    **result,
                })

                # Save after every application in case of crash
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                LOG_FILE.write_text(json.dumps(application_log, indent=2))

                time.sleep(2)

            page.close()

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(json.dumps(application_log, indent=2))

    applied = sum(1 for e in application_log if e.get("status") == "applied")
    log.info(
        "Done — %d applied, %d total logged → %s",
        applied, len(application_log), LOG_FILE,
    )
    return application_log


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job application agent")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without applying")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
