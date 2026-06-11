"""
agent_dashboard.py — Generates output/dashboard.html
A self-contained HTML dashboard of all job applications.
Open output/dashboard.html in any browser — no server needed.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [dashboard] %(message)s")
log = logging.getLogger(__name__)

LOG_FILE = Path("output/applications_log.json")
RESUME_MAP_FILE = Path("output/resume_map.json")
DASHBOARD_FILE = Path("output/dashboard.html")

STATUS_COLOR = {
    "applied":       ("#16a34a", "#dcfce7", "✓ Applied"),
    "manual_review": ("#d97706", "#fef3c7", "⚠ Manual"),
    "error":         ("#dc2626", "#fee2e2", "✗ Error"),
    "skipped":       ("#6b7280", "#f3f4f6", "– Skipped"),
    "dry_run":       ("#2563eb", "#dbeafe", "◎ Dry Run"),
}

PLATFORM_LABEL = {
    "linkedin": "LinkedIn",
    "indeed":   "Indeed",
    "greenhouse": "Greenhouse",
    "lever":    "Lever",
    "workday":  "Workday",
    "bamboohr": "BambooHR",
    "generic":  "External",
    "manual":   "Manual",
}


def load_data() -> tuple[list[dict], dict]:
    if not LOG_FILE.exists():
        log.error("No applications_log.json found")
        return [], {}
    entries = json.loads(LOG_FILE.read_text())

    resume_map: dict = {}
    if RESUME_MAP_FILE.exists():
        rm = json.loads(RESUME_MAP_FILE.read_text())
        resume_map = {v["job_id"]: v for v in rm.values()}

    return entries, resume_map


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %Y  %I:%M %p")
    except Exception:
        return iso


def short_resume(path: str) -> str:
    p = Path(path)
    # Return the folder name (which is "Job Title - Company") or just filename
    if p.parent.name and p.parent.name != "resumes":
        return p.parent.name
    return p.name


def build_html(entries: list[dict], resume_map: dict) -> str:
    # ── Stats ──────────────────────────────────────────────────────────────
    status_counts = Counter(e["status"] for e in entries)
    platform_counts = Counter(
        PLATFORM_LABEL.get(e["platform"], e["platform"])
        for e in entries if e["status"] == "applied"
    )
    by_date = defaultdict(list)
    for e in entries:
        date = e.get("applied_at", "")[:10]
        by_date[date].append(e)

    total = len(entries)
    applied = status_counts.get("applied", 0)
    manual  = status_counts.get("manual_review", 0)
    errors  = status_counts.get("error", 0)
    dry     = status_counts.get("dry_run", 0)

    # ── Table rows ────────────────────────────────────────────────────────
    rows_html = []
    for e in sorted(entries, key=lambda x: x.get("applied_at", ""), reverse=True):
        status = e.get("status", "unknown")
        color, bg, label = STATUS_COLOR.get(status, ("#374151", "#f9fafb", status))
        platform = PLATFORM_LABEL.get(e.get("platform", ""), e.get("platform", ""))
        date_str = fmt_date(e.get("applied_at", ""))
        date_only = e.get("applied_at", "")[:10]
        resume_label = short_resume(e.get("resume_path", ""))
        reason = (e.get("reason") or "")[:120]

        rm_entry = resume_map.get(e["job_id"], {})
        score = rm_entry.get("relevance_score", "")
        score_html = f'<span class="score">{score}</span>' if score else ""
        location = rm_entry.get("location", e.get("location", ""))

        job_url = e.get("job_url", "#")
        title_link = f'<a href="{job_url}" target="_blank" class="job-link">{e["job_title"]}</a>'

        rows_html.append(f"""
        <tr data-status="{status}" data-date="{date_only}" data-platform="{e.get('platform','')}">
          <td class="td-date">{date_str}</td>
          <td class="td-title">{title_link}<br><span class="company">{e["company"]}</span></td>
          <td class="td-loc">{location}</td>
          <td class="td-platform">{platform}</td>
          <td class="td-status">
            <span class="badge" style="color:{color};background:{bg}">{label}</span>
          </td>
          <td class="td-resume" title="{e.get('resume_path','')}">
            {resume_label}
          </td>
          <td class="td-reason">{reason}</td>
        </tr>""")

    rows = "\n".join(rows_html)

    # ── Date filter options ───────────────────────────────────────────────
    date_options = "\n".join(
        f'<option value="{d}">{d}</option>'
        for d in sorted(by_date.keys(), reverse=True)
    )

    # ── Platform breakdown for stats ──────────────────────────────────────
    platform_stat_html = "".join(
        f'<div class="stat-sub"><span>{plat}</span><b>{cnt}</b></div>'
        for plat, cnt in platform_counts.most_common()
    )

    # ── Daily breakdown ───────────────────────────────────────────────────
    daily_rows = ""
    for d in sorted(by_date.keys(), reverse=True):
        day_entries = by_date[d]
        day_applied = sum(1 for e in day_entries if e["status"] == "applied")
        day_manual  = sum(1 for e in day_entries if e["status"] == "manual_review")
        day_error   = sum(1 for e in day_entries if e["status"] == "error")
        daily_rows += (
            f'<div class="day-row">'
            f'<span class="day-date">{d}</span>'
            f'<span class="day-pill green">{day_applied} applied</span>'
            f'<span class="day-pill orange">{day_manual} manual</span>'
            f'<span class="day-pill red">{day_error} errors</span>'
            f'<span class="day-total">{len(day_entries)} total</span>'
            f'</div>'
        )

    now = datetime.now().strftime("%b %d, %Y %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Hunt Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: #f1f5f9; color: #1e293b; font-size: 14px; }}

  /* ── Header ── */
  .header {{ background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
             color: white; padding: 24px 32px; }}
  .header h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }}
  .header p  {{ opacity: .75; font-size: 12px; margin-top: 4px; }}

  /* ── Stats grid ── */
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px; padding: 24px 32px 0; }}
  .stat-card {{ background: white; border-radius: 12px; padding: 18px 20px;
                box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .stat-card .num  {{ font-size: 32px; font-weight: 700; line-height: 1; }}
  .stat-card .lbl  {{ font-size: 12px; color: #64748b; margin-top: 4px; text-transform: uppercase; letter-spacing: .5px; }}
  .stat-card.green .num {{ color: #16a34a; }}
  .stat-card.orange .num {{ color: #d97706; }}
  .stat-card.red .num {{ color: #dc2626; }}
  .stat-card.blue .num {{ color: #2563eb; }}
  .stat-sub {{ display: flex; justify-content: space-between; font-size: 12px;
               color: #475569; margin-top: 6px; padding-top: 4px;
               border-top: 1px solid #f1f5f9; }}

  /* ── Daily breakdown ── */
  .daily-section {{ margin: 20px 32px 0; background: white; border-radius: 12px;
                    padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .daily-section h2 {{ font-size: 13px; font-weight: 600; color: #64748b;
                       text-transform: uppercase; letter-spacing: .5px; margin-bottom: 10px; }}
  .day-row {{ display: flex; align-items: center; gap: 10px; padding: 6px 0;
              border-bottom: 1px solid #f8fafc; font-size: 13px; }}
  .day-row:last-child {{ border-bottom: none; }}
  .day-date  {{ font-weight: 600; width: 100px; color: #334155; }}
  .day-total {{ margin-left: auto; color: #94a3b8; font-size: 12px; }}
  .day-pill  {{ border-radius: 20px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}
  .day-pill.green  {{ background: #dcfce7; color: #16a34a; }}
  .day-pill.orange {{ background: #fef3c7; color: #d97706; }}
  .day-pill.red    {{ background: #fee2e2; color: #dc2626; }}

  /* ── Filters ── */
  .filters {{ margin: 20px 32px 0; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
  .filters select, .filters input {{
    border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 12px;
    font-size: 13px; background: white; color: #334155; outline: none;
    box-shadow: 0 1px 2px rgba(0,0,0,.05);
  }}
  .filters select:focus, .filters input:focus {{ border-color: #2563eb; }}
  .filters input {{ min-width: 220px; }}
  .filter-count {{ font-size: 12px; color: #94a3b8; margin-left: auto; }}
  .btn-reset {{ background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 8px;
                padding: 8px 14px; font-size: 13px; cursor: pointer; color: #475569; }}
  .btn-reset:hover {{ background: #e2e8f0; }}

  /* ── Table ── */
  .table-wrap {{ margin: 16px 32px 32px; background: white; border-radius: 12px;
                 box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow: hidden; }}
  table {{ width: 100%; border-collapse: collapse; }}
  thead {{ background: #f8fafc; }}
  th {{ padding: 11px 14px; text-align: left; font-size: 11px; font-weight: 600;
        color: #64748b; text-transform: uppercase; letter-spacing: .5px;
        border-bottom: 1px solid #e2e8f0; white-space: nowrap; cursor: pointer; }}
  th:hover {{ color: #2563eb; }}
  td {{ padding: 12px 14px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}
  tr.hidden {{ display: none; }}

  .td-date     {{ white-space: nowrap; font-size: 12px; color: #64748b; min-width: 140px; }}
  .td-title    {{ min-width: 220px; max-width: 280px; }}
  .td-loc      {{ font-size: 12px; color: #64748b; min-width: 100px; }}
  .td-platform {{ white-space: nowrap; font-size: 12px; }}
  .td-status   {{ white-space: nowrap; }}
  .td-resume   {{ font-size: 11px; color: #64748b; max-width: 180px;
                  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .td-reason   {{ font-size: 11px; color: #94a3b8; max-width: 240px; }}

  .job-link {{ color: #1e40af; text-decoration: none; font-weight: 500; font-size: 13px; }}
  .job-link:hover {{ text-decoration: underline; color: #2563eb; }}
  .company  {{ font-size: 12px; color: #64748b; margin-top: 2px; }}
  .badge    {{ display: inline-block; border-radius: 20px; padding: 3px 10px;
               font-size: 11px; font-weight: 600; white-space: nowrap; }}
  .score    {{ font-size: 11px; color: #7c3aed; background: #ede9fe;
               border-radius: 4px; padding: 1px 5px; margin-left: 4px; }}

  .no-rows {{ padding: 48px; text-align: center; color: #94a3b8; font-size: 14px; display: none; }}
</style>
</head>
<body>

<div class="header">
  <h1>Job Hunt Dashboard</h1>
  <p>Last updated: {now} &nbsp;·&nbsp; {total} total entries</p>
</div>

<!-- Stats -->
<div class="stats">
  <div class="stat-card green">
    <div class="num">{applied}</div>
    <div class="lbl">Applied</div>
    {platform_stat_html}
  </div>
  <div class="stat-card orange">
    <div class="num">{manual}</div>
    <div class="lbl">Manual Review</div>
  </div>
  <div class="stat-card red">
    <div class="num">{errors}</div>
    <div class="lbl">Errors</div>
  </div>
  <div class="stat-card blue">
    <div class="num">{dry}</div>
    <div class="lbl">Dry Runs</div>
  </div>
  <div class="stat-card">
    <div class="num">{total}</div>
    <div class="lbl">Total Logged</div>
  </div>
</div>

<!-- Daily Breakdown -->
<div class="daily-section">
  <h2>Activity by Day</h2>
  {daily_rows}
</div>

<!-- Filters -->
<div class="filters">
  <input type="text" id="search" placeholder="Search job title or company..." oninput="applyFilters()">
  <select id="statusFilter" onchange="applyFilters()">
    <option value="">All Statuses</option>
    <option value="applied">✓ Applied</option>
    <option value="manual_review">⚠ Manual Review</option>
    <option value="error">✗ Error</option>
    <option value="skipped">– Skipped</option>
    <option value="dry_run">◎ Dry Run</option>
  </select>
  <select id="dateFilter" onchange="applyFilters()">
    <option value="">All Dates</option>
    {date_options}
  </select>
  <select id="platformFilter" onchange="applyFilters()">
    <option value="">All Platforms</option>
    <option value="linkedin">LinkedIn</option>
    <option value="generic">External</option>
    <option value="manual">Manual</option>
    <option value="greenhouse">Greenhouse</option>
    <option value="lever">Lever</option>
  </select>
  <button class="btn-reset" onclick="resetFilters()">Reset</button>
  <span class="filter-count" id="filterCount"></span>
</div>

<!-- Table -->
<div class="table-wrap">
  <table id="jobTable">
    <thead>
      <tr>
        <th onclick="sortTable(0)">Date ↕</th>
        <th onclick="sortTable(1)">Job / Company ↕</th>
        <th>Location</th>
        <th onclick="sortTable(3)">Platform ↕</th>
        <th onclick="sortTable(4)">Status ↕</th>
        <th>Resume</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody id="tableBody">
      {rows}
    </tbody>
  </table>
  <div class="no-rows" id="noRows">No jobs match the current filters.</div>
</div>

<script>
  let sortDir = {{}};

  function applyFilters() {{
    const search   = document.getElementById('search').value.toLowerCase();
    const status   = document.getElementById('statusFilter').value;
    const date     = document.getElementById('dateFilter').value;
    const platform = document.getElementById('platformFilter').value;
    const rows = document.querySelectorAll('#tableBody tr');
    let visible = 0;
    rows.forEach(row => {{
      const text     = row.textContent.toLowerCase();
      const rowStat  = row.dataset.status;
      const rowDate  = row.dataset.date;
      const rowPlat  = row.dataset.platform;
      const show = (
        (!search   || text.includes(search)) &&
        (!status   || rowStat  === status) &&
        (!date     || rowDate  === date) &&
        (!platform || rowPlat  === platform)
      );
      row.classList.toggle('hidden', !show);
      if (show) visible++;
    }});
    document.getElementById('filterCount').textContent =
      visible === rows.length ? `${{rows.length}} jobs` : `${{visible}} of ${{rows.length}} jobs`;
    document.getElementById('noRows').style.display = visible === 0 ? 'block' : 'none';
  }}

  function resetFilters() {{
    document.getElementById('search').value = '';
    document.getElementById('statusFilter').value = '';
    document.getElementById('dateFilter').value = '';
    document.getElementById('platformFilter').value = '';
    applyFilters();
  }}

  function sortTable(col) {{
    const tbody = document.getElementById('tableBody');
    const rows  = Array.from(tbody.querySelectorAll('tr'));
    const asc   = !sortDir[col];
    sortDir = {{}};
    sortDir[col] = asc;
    rows.sort((a, b) => {{
      const ta = a.cells[col]?.textContent.trim() || '';
      const tb = b.cells[col]?.textContent.trim() || '';
      return asc ? ta.localeCompare(tb) : tb.localeCompare(ta);
    }});
    rows.forEach(r => tbody.appendChild(r));
  }}

  // Init count
  applyFilters();
</script>
</body>
</html>"""


def run(app_log: list[dict] | None = None) -> str:
    entries, resume_map = load_data()
    if app_log is not None:
        entries = app_log  # use live data if passed from pipeline

    if not entries:
        log.warning("No application log data found — dashboard will be empty")

    html = build_html(entries, resume_map)
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    log.info("Dashboard saved → %s (%d entries)", DASHBOARD_FILE, len(entries))
    return str(DASHBOARD_FILE)


if __name__ == "__main__":
    path = run()
    print(f"\nDashboard generated: {path}")
    print("Open it in your browser:")
    print(f"  open {path}")
