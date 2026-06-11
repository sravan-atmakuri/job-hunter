"""
main.py — Orchestrates all job hunting agents in sequence.
Each agent receives the output of the previous agent.

Usage:
    python main.py                    # full pipeline
    python main.py --dry-run          # skip live applications
    python main.py --start-from 2     # resume from step 2 (relevance scoring)
    python main.py --steps 1,2        # run only specific steps
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [main] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("output/pipeline.log"),
    ],
)
log = logging.getLogger(__name__)

# Ensure output dir exists for log file before anything else
Path("output").mkdir(exist_ok=True)


def config_max_jobs_per_source() -> int:
    """Read max_jobs_per_source from config so the top-up loop knows batch size."""
    import yaml
    try:
        with open("config.yaml") as f:
            return yaml.safe_load(f).get("max_jobs_per_source", 50)
    except Exception:
        return 50


def step1_scrape() -> list[dict]:
    log.info("=" * 60)
    log.info("STEP 1: Scraping job listings")
    log.info("=" * 60)
    from agent_scraper import run
    jobs = run()
    log.info("Step 1 complete: %d raw jobs collected", len(jobs))
    return jobs


def step2_score(jobs: list[dict] | None = None) -> list[dict]:
    log.info("=" * 60)
    log.info("STEP 2: Scoring relevance and filtering jobs")
    log.info("=" * 60)
    from agent_relevance import run
    filtered = run(jobs)
    log.info("Step 2 complete: %d jobs passed relevance filter", len(filtered))
    return filtered


def step3_tailor(jobs: list[dict] | None = None) -> dict:
    log.info("=" * 60)
    log.info("STEP 3: Tailoring resumes for each job")
    log.info("=" * 60)
    from agent_resume import run
    resume_map = run(jobs)
    log.info("Step 3 complete: %d resumes tailored", len(resume_map))
    return resume_map


def step4_apply(resume_map: dict | None = None, dry_run: bool = False) -> list[dict]:
    log.info("=" * 60)
    log.info("STEP 4: Applying to jobs%s", " (DRY RUN)" if dry_run else "")
    log.info("=" * 60)
    from agent_applier import run
    app_log = run(resume_map, dry_run=dry_run)
    applied = sum(1 for e in app_log if e.get("status") == "applied")
    log.info("Step 4 complete: %d applications submitted", applied)
    return app_log


def step5_report(app_log: list[dict] | None = None) -> str:
    log.info("=" * 60)
    log.info("STEP 5: Generating final report + dashboard")
    log.info("=" * 60)
    from agent_reporter import run
    report = run(app_log)
    log.info("Step 5 complete: report saved to output/final_report.md")

    try:
        from agent_dashboard import run as dashboard_run
        dashboard_run(app_log)
    except Exception as exc:
        log.warning("Dashboard generation failed: %s", exc)

    return report


def step6_skills_gap(jobs: list[dict] | None = None) -> str:
    log.info("=" * 60)
    log.info("STEP 6: Generating skills gap report")
    log.info("=" * 60)
    from agent_skills_gap import run
    report = run(jobs)
    log.info("Step 6 complete: report saved to output/skills_gap_report.md")
    return report


# ---------------------------------------------------------------------------
# Load from disk (for --start-from)
# ---------------------------------------------------------------------------

def load_step_output(step: int) -> object:
    """Load persisted output from a previous step."""
    files = {
        1: Path("output/jobs_raw.json"),
        2: Path("output/jobs_filtered.json"),
        3: Path("output/resume_map.json"),
        4: Path("output/applications_log.json"),
    }
    path = files.get(step)
    if path and path.exists():
        data = json.loads(path.read_text())
        log.info("Loaded step %d output from %s (%d items)", step, path, len(data))
        return data
    log.warning("No persisted output found for step %d at %s", step, path)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-agent job hunting pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Steps:
  1 — Scrape job listings from LinkedIn and Dice
  2 — Score and filter jobs by relevance (requires Claude Code CLI login)
  3 — Tailor resumes for each job (requires Claude Code CLI login)
  4 — Apply to jobs via Playwright (requires playwright install)
  5 — Generate final report (requires Claude Code CLI login)
  6 — Skills gap report: compare JDs vs resume (requires Claude Code CLI login)

Examples:
  python main.py                     # run full pipeline
  python main.py --dry-run           # skip actual form submissions
  python main.py --start-from 2      # skip scraping, use existing jobs_raw.json
  python main.py --steps 1,2         # only scrape and score
  python main.py --steps 6           # only run skills gap report
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run step 4 in dry-run mode (no actual form submissions)",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=1,
        metavar="STEP",
        help="Start pipeline from this step (1-5). Uses saved outputs from prior steps.",
    )
    parser.add_argument(
        "--steps",
        type=str,
        default=None,
        metavar="1,2,3",
        help="Comma-separated list of specific steps to run (overrides --start-from).",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of jobs processed after scraping (useful for testing).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.steps:
        steps_to_run = {int(s.strip()) for s in args.steps.split(",")}
    else:
        steps_to_run = set(range(args.start_from, 7))

    log.info("Job Hunt Pipeline starting")
    log.info("Steps to run: %s", sorted(steps_to_run))
    log.info("Dry run: %s", args.dry_run)
    if args.max_jobs:
        log.info("Max jobs cap: %d", args.max_jobs)

    start_time = time.time()

    # Track inter-step data
    raw_jobs: list[dict] | None = None
    filtered_jobs: list[dict] | None = None
    resume_map: dict | None = None
    app_log: list[dict] | None = None

    # Load pre-existing outputs if starting mid-pipeline
    if args.start_from > 1 or args.steps:
        min_step = min(steps_to_run)
        if min_step > 1 and 1 not in steps_to_run:
            raw_jobs = load_step_output(1)
        if min_step > 2 and 2 not in steps_to_run:
            filtered_jobs = load_step_output(2)
        if min_step > 3 and 3 not in steps_to_run:
            resume_map = load_step_output(3)
        if min_step > 4 and 4 not in steps_to_run:
            app_log = load_step_output(4)

    try:
        if 1 in steps_to_run and 2 in steps_to_run and args.max_jobs:
            # Top-up loop: keep scraping + scoring until we have max_jobs filtered jobs
            target = args.max_jobs
            filtered_jobs = []
            page_offset = 0
            batch_size = config_max_jobs_per_source()
            MAX_ROUNDS = 5

            for round_num in range(1, MAX_ROUNDS + 1):
                needed = target - len(filtered_jobs)
                if needed <= 0:
                    break
                log.info(
                    "--- Scrape round %d/%d: need %d more filtered jobs (offset=%d) ---",
                    round_num, MAX_ROUNDS, needed, page_offset,
                )
                from agent_scraper import run as scrape_run
                raw_batch = scrape_run(page_offset=page_offset)
                if not raw_batch:
                    log.info("No more new jobs from scraper — stopping top-up loop")
                    break

                from agent_relevance import run as score_run
                filtered_batch = score_run(raw_batch)
                if filtered_batch:
                    take = filtered_batch[:needed]
                    filtered_jobs.extend(take)
                    log.info(
                        "Round %d: %d/%d new jobs passed filter — total filtered: %d/%d",
                        round_num, len(filtered_batch), len(raw_batch), len(filtered_jobs), target,
                    )
                else:
                    log.info("Round %d: 0 new jobs passed filter", round_num)

                page_offset += batch_size

                if len(filtered_jobs) >= target:
                    log.info("Target of %d filtered jobs reached.", target)
                    break

            raw_jobs = None  # already handled inside loop
            if not filtered_jobs:
                log.warning("No jobs passed relevance filter — pipeline stopping after step 2")
                sys.exit(0)

        else:
            if 1 in steps_to_run:
                raw_jobs = step1_scrape()

            if 2 in steps_to_run:
                filtered_jobs = step2_score(raw_jobs)
                if args.max_jobs and filtered_jobs:
                    filtered_jobs = filtered_jobs[: args.max_jobs]
                    log.info("Capped filtered jobs to %d", len(filtered_jobs))
                if not filtered_jobs:
                    log.warning("No jobs passed relevance filter — pipeline stopping after step 2")
                    sys.exit(0)

        if 3 in steps_to_run:
            resume_map = step3_tailor(filtered_jobs)
            if not resume_map:
                log.warning("No resumes tailored — pipeline stopping after step 3")
                sys.exit(0)

        if 4 in steps_to_run:
            app_log = step4_apply(resume_map, dry_run=args.dry_run)

        if 5 in steps_to_run:
            step5_report(app_log)

        if 6 in steps_to_run:
            step6_skills_gap(filtered_jobs)

    except KeyboardInterrupt:
        log.info("Pipeline interrupted by user")
        sys.exit(1)
    except Exception as exc:
        log.error("Pipeline failed: %s", exc, exc_info=True)
        sys.exit(1)

    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info("Pipeline complete in %.1f seconds", elapsed)
    log.info("Outputs:")
    for path in [
        "output/jobs_raw.json",
        "output/jobs_filtered.json",
        "output/resume_map.json",
        "output/applications_log.json",
        "output/final_report.md",
        "output/skills_gap_report.md",
        "output/seen_jobs.json",
    ]:
        p = Path(path)
        if p.exists():
            log.info("  ✓ %s (%d bytes)", path, p.stat().st_size)
        else:
            log.info("  - %s (not generated)", path)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
