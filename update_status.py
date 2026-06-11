"""
update_status.py — CLI to track application progress beyond "applied".

Usage:
    python update_status.py                            # list all applications
    python update_status.py list --status interview    # filter by status
    python update_status.py <job_id> <status>          # update a job's status
    python update_status.py <job_id> <status> --notes "Spoke with HR"

Valid statuses:
    applied | phone_screen | interview | offer | rejected | withdrawn
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

LOG_FILE = Path("output/applications_log.json")

VALID_STATUSES = {"applied", "phone_screen", "interview", "offer", "rejected", "withdrawn", "dry_run"}

STATUS_LABELS = {
    "applied":      "Applied",
    "phone_screen": "Phone Screen",
    "interview":    "Interview",
    "offer":        "Offer",
    "rejected":     "Rejected",
    "withdrawn":    "Withdrawn",
    "dry_run":      "Dry Run",
}


def load_log() -> list[dict]:
    if not LOG_FILE.exists():
        print(f"No applications log found at {LOG_FILE}")
        return []
    return json.loads(LOG_FILE.read_text())


def save_log(app_log: list[dict]) -> None:
    LOG_FILE.write_text(json.dumps(app_log, indent=2))


def list_applications(app_log: list[dict], status_filter: str | None = None) -> None:
    entries = app_log
    if status_filter:
        entries = [e for e in app_log if e.get("status") == status_filter]

    if not entries:
        print("No applications found.")
        return

    # Group by status
    from collections import Counter
    counts = Counter(e.get("status", "unknown") for e in app_log)
    print("\n── Application Summary ──────────────────────────")
    for status, count in sorted(counts.items()):
        print(f"  {STATUS_LABELS.get(status, status):<15} {count}")
    print(f"  {'TOTAL':<15} {len(app_log)}")
    print("─────────────────────────────────────────────────\n")

    print(f"{'#':<4} {'Job ID':<14} {'Status':<14} {'Company':<25} {'Title'}")
    print("-" * 90)
    for i, entry in enumerate(entries, 1):
        status = STATUS_LABELS.get(entry.get("status", ""), entry.get("status", ""))
        company = entry.get("company", "")[:24]
        title = entry.get("job_title", "")[:35]
        job_id = entry.get("job_id", "")[:13]
        print(f"{i:<4} {job_id:<14} {status:<14} {company:<25} {title}")
    print()


def update_status(app_log: list[dict], job_id: str, new_status: str, notes: str = "") -> None:
    if new_status not in VALID_STATUSES:
        print(f"Invalid status '{new_status}'. Valid: {', '.join(sorted(VALID_STATUSES))}")
        return

    # Match by full or partial job_id
    matches = [e for e in app_log if e.get("job_id", "").startswith(job_id)]
    if not matches:
        print(f"No application found with job_id starting with '{job_id}'")
        return
    if len(matches) > 1:
        print(f"Multiple matches for '{job_id}':")
        for m in matches:
            print(f"  {m['job_id']} — {m['job_title']} @ {m['company']}")
        return

    entry = matches[0]
    old_status = entry.get("status", "unknown")

    # Append to status_history
    if "status_history" not in entry:
        entry["status_history"] = [
            {"status": old_status, "timestamp": entry.get("applied_at", ""), "notes": ""}
        ]

    entry["status"] = new_status
    entry["status_history"].append({
        "status": new_status,
        "timestamp": datetime.utcnow().isoformat(),
        "notes": notes,
    })
    if notes:
        entry["latest_notes"] = notes

    save_log(app_log)
    print(
        f"\n✓ Updated: {entry['job_title']} @ {entry['company']}\n"
        f"  {STATUS_LABELS.get(old_status, old_status)} → {STATUS_LABELS.get(new_status, new_status)}"
        + (f"\n  Notes: {notes}" if notes else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Track job application status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update_status.py                              # list all
  python update_status.py list --status interview      # filter by status
  python update_status.py abc123 interview             # update status
  python update_status.py abc123 offer --notes "$95/hr offer received"
        """,
    )
    parser.add_argument("job_id", nargs="?", default="list", help="Job ID (or 'list')")
    parser.add_argument("status", nargs="?", help="New status")
    parser.add_argument("--status", dest="status_filter", help="Filter listed applications by status")
    parser.add_argument("--notes", default="", help="Optional notes for the status update")
    args = parser.parse_args()

    app_log = load_log()
    if not app_log:
        return

    if args.job_id == "list" or args.status is None:
        list_applications(app_log, args.status_filter)
    else:
        update_status(app_log, args.job_id, args.status, args.notes)


if __name__ == "__main__":
    main()
