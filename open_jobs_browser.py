"""
open_jobs_browser.py — Opens all pending job URLs in your browser as tabs.
Use this as a fallback when LinkedIn auto-apply fails.

Usage:
    python3 open_jobs_browser.py              # open all pending jobs
    python3 open_jobs_browser.py --limit 10  # open first 10 only
    python3 open_jobs_browser.py --mark-applied --limit 10  # mark last 10 opened as applied
    python3 open_jobs_browser.py --list       # list pending jobs with numbers
"""

from __future__ import annotations

import argparse
import json
import webbrowser
import time
from datetime import datetime
from pathlib import Path

RESUME_MAP_FILE = Path("output/resume_map.json")
LOG_FILE = Path("output/applications_log.json")


def load_log() -> list[dict]:
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text())
    return []


def save_log(log: list[dict]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(json.dumps(log, indent=2))


def get_pending(resume_map: dict, log: list[dict]) -> list[dict]:
    already_applied = {e["job_id"] for e in log if e.get("status") == "applied"}
    return [job for job in resume_map.values() if job["job_id"] not in already_applied]


def mark_applied(pending: list[dict], log: list[dict]) -> None:
    """Add or update log entries to 'applied' for each job in pending."""
    log_by_id = {e["job_id"]: e for e in log}

    for job in pending:
        jid = job["job_id"]
        if jid in log_by_id:
            entry = log_by_id[jid]
            old_status = entry.get("status", "unknown")
            if "status_history" not in entry:
                entry["status_history"] = [{"status": old_status, "timestamp": entry.get("applied_at", ""), "notes": ""}]
            entry["status_history"].append({"status": "applied", "timestamp": datetime.utcnow().isoformat(), "notes": "marked via open_jobs_browser"})
            entry["status"] = "applied"
            entry["applied_at"] = datetime.utcnow().isoformat()
        else:
            log.append({
                "job_id": jid,
                "job_title": job["job_title"],
                "company": job["company"],
                "job_url": job["job_url"],
                "resume_path": job.get("resume_path", ""),
                "platform": "manual",
                "status": "applied",
                "reason": "Manually applied via browser",
                "applied_at": datetime.utcnow().isoformat(),
            })
        print(f"  ✓ Applied: {job['job_title']} @ {job['company']}")

    save_log(log)
    print(f"\nMarked {len(pending)} job(s) as applied → {LOG_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max number of jobs (0 = all)")
    parser.add_argument("--mark-applied", action="store_true", help="Mark the jobs as applied instead of opening browser")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--list", action="store_true", help="List pending jobs without opening browser")
    args = parser.parse_args()

    if not RESUME_MAP_FILE.exists():
        print("No resume_map.json found — run the pipeline first.")
        return

    resume_map = json.loads(RESUME_MAP_FILE.read_text())
    log = load_log()
    pending = get_pending(resume_map, log)

    if args.limit:
        pending = pending[: args.limit]

    if not pending:
        print("No pending jobs — all are already marked as applied.")
        return

    if args.list or args.mark_applied:
        print(f"\n{'#':<4} {'Company':<30} {'Title'}")
        print("-" * 80)
        for i, job in enumerate(pending, 1):
            print(f"{i:<4} {job['company'][:29]:<30} {job['job_title'][:45]}")
        print()

    if args.mark_applied:
        if not args.yes:
            confirm = input(f"Mark all {len(pending)} jobs above as applied? [y/N]: ").strip().lower()
        else:
            confirm = "y"
        if confirm == "y":
            mark_applied(pending, log)
        else:
            print("Cancelled.")
        return

    if args.list:
        return

    # Open in browser
    print(f"\nOpening {len(pending)} job URLs in your browser...\n")
    for i, job in enumerate(pending, 1):
        print(f"  [{i}/{len(pending)}] {job['job_title']} @ {job['company']}")
        print(f"           Resume: {Path(job['resume_path']).parent.name}")
        print(f"           URL:    {job['job_url']}")
        webbrowser.open(job["job_url"])
        time.sleep(1.5)

    print(f"\nAll {len(pending)} tabs opened.")
    print(f"\nOnce you've applied, run:")
    print(f"  python3 open_jobs_browser.py --mark-applied --limit {len(pending)}")


if __name__ == "__main__":
    main()
