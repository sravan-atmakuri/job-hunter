# Job Hunter

A multi-agent pipeline that scrapes job listings, scores their relevance, tailors your resume to each job, and automatically submits applications via LinkedIn Easy Apply, Greenhouse, and Lever.

## Pipeline Steps

| Step | Agent | What it does |
|------|-------|--------------|
| 1 | `agent_scraper` | Scrapes LinkedIn, Dice, SimplyHired, Monster, Idealist, and company pages |
| 2 | `agent_relevance` | Scores each job 1–10 and filters below threshold |
| 3 | `agent_resume` | Tailors your resume to each job using Claude |
| 4 | `agent_applier` | Fills and submits application forms via Playwright |
| 5 | `agent_reporter` | Generates a final report + HTML dashboard |
| 6 | `agent_skills_gap` | Compares job requirements vs. your resume |

---

## Quick Start

### macOS

```bash
# 1. Clone and enter directory
git clone <repo-url>
cd job_hunter

# 2. Run setup (installs everything)
bash setup.sh

# 3. Copy example config and fill in your details
cp config.example.yaml config.yaml
nano config.yaml      # set resume paths, name, email, phone, job titles

# 4. Save your LinkedIn session (one-time)
bash run.sh login_linkedin.py

# 5. Test with dry-run (no actual form submissions)
bash run.sh --dry-run

# 6. Full pipeline
bash run.sh
```

### Windows

```bat
:: 1. Clone and enter directory
git clone <repo-url>
cd job_hunter

:: 2. Run setup (installs everything)
setup.bat

:: 3. Copy example config and fill in your details
copy config.example.yaml config.yaml
notepad config.yaml

:: 4. Save your LinkedIn session (one-time)
run.bat login_linkedin.py

:: 5. Test with dry-run
run.bat --dry-run

:: 6. Full pipeline
run.bat
```

> **Note:** `config.yaml` is in `.gitignore` and will never be committed — it contains your personal info (email, phone, resume paths). `config.example.yaml` is the safe shareable template.

---

## Prerequisites

Both `setup.sh` (Mac) and `setup.bat` (Windows) install these automatically. If you prefer to install manually:

| Requirement | Mac | Windows |
|-------------|-----|---------|
| Python 3.10+ | `brew install python@3.12` | [python.org](https://python.org/downloads) — check "Add to PATH" |
| Node.js | `brew install node` | [nodejs.org](https://nodejs.org) (LTS) |
| Claude Code CLI | `npm install -g @anthropic-ai/claude-code` | same |
| Claude login | `claude auth login` | same |
| Python packages | `pip install -r requirements.txt` | same |
| Playwright browser | `python -m playwright install chromium` | same |

---

## Configuration (`config.yaml`)

Copy `config.example.yaml` to `config.yaml` and update every section:

**Resume files** (absolute paths):
```yaml
resume_path: "/path/to/your/base_resume.docx"

resume_map:
  qa:         "/path/to/qa_resume.docx"
  salesforce: "/path/to/salesforce_resume.docx"
  datacloud:  "/path/to/datacloud_resume.docx"
```

**Your contact info** (used to fill application forms):
```yaml
applicant:
  first_name:   "Jane"
  last_name:    "Doe"
  email:        "jane@example.com"
  phone:        "555-123-4567"
  linkedin_url: "https://www.linkedin.com/in/janedoe"
  location:     "Austin, TX"
```

**Job search preferences:**
```yaml
job_titles:
  - "Senior Software Engineer"
  - "Staff Engineer"

target_states:
  - "TX"
  - "Remote"

work_type:
  - "remote"
  - "hybrid"

job_types:
  - "full-time"
  - "contract"
```

**Thresholds:**
```yaml
min_relevance_score: 6     # 1–10; jobs below this are skipped
min_hourly_rate: 60        # $/hr minimum for contract roles
min_annual_salary: 100000  # $/yr minimum for salaried roles
max_days_old: 30           # ignore jobs older than this
```

---

## Running the Pipeline

### Using `run.sh` (Mac) / `run.bat` (Windows)

```bash
bash run.sh                       # full pipeline
bash run.sh --dry-run             # skip actual form submissions
bash run.sh --start-from 2        # skip scraping, reuse jobs_raw.json
bash run.sh --steps 1,2           # scrape + score only
bash run.sh --steps 6             # skills gap report only
bash run.sh --max-jobs 10         # cap to 10 jobs (for testing)
bash run.sh login_linkedin.py     # save LinkedIn session (one-time)
```

### Using `python main.py` directly (if venv is already active)

```bash
source venv/bin/activate      # Mac/Linux
venv\Scripts\activate         # Windows

python main.py                # full pipeline
python main.py --dry-run      # dry run
python main.py --start-from 4 # apply only (steps 3+ already done)
```

### Apply with real Chrome session (recommended for LinkedIn)

```bash
bash launch_apply.sh
```

This kills Chrome, relaunches it with remote debugging enabled on port 9222 using your real Chrome profile (already logged into LinkedIn), then runs Step 4 connected to that session. More reliable than the saved-session fallback.

---

## Auto-Apply: How It Works & Troubleshooting

The applier (Step 4) handles these platforms automatically:

| Platform | Auto-apply? | Notes |
|----------|-------------|-------|
| LinkedIn | Yes (Easy Apply only) | Multi-step forms; common questions auto-answered |
| Greenhouse | Yes | Fills all standard fields + resume upload |
| Lever | Yes | Fills all standard fields + resume upload |
| Indeed | Opens for manual review | Full automation requires Indeed account session |
| Workday/BambooHR | Opens for manual review | Complex ATS; manual required |

### Why applications show `manual_review` instead of `applied`

1. **Job doesn't have Easy Apply** — requires applying on company's own site. The applier opens the URL for you to complete.
2. **Multi-step form has required fields that couldn't be auto-filled** — e.g., essay questions, custom dropdowns. Complete those manually in the open browser window.
3. **LinkedIn session expired** — run `bash run.sh login_linkedin.py` to refresh, or use `launch_apply.sh` to connect to your real Chrome.
4. **Not logged in** — run `python login_linkedin.py` to save a fresh session.

### Fix: auto-apply not working at all

```bash
# Check Playwright is installed in the venv
source venv/bin/activate
python -c "import playwright; print('ok')"

# Re-install if missing
pip install playwright
python -m playwright install chromium

# Refresh LinkedIn session
python login_linkedin.py
```

### For the best auto-apply results

1. Run `bash launch_apply.sh` instead of `bash run.sh --start-from 4`. This uses your real Chrome session (already logged in) which is far more reliable than the saved-session fallback.
2. Make sure your `config.yaml` has a valid `phone` and `location` — these are required on most LinkedIn Easy Apply forms.
3. Set `visa_types: [h1b]` if you need sponsorship — the applier auto-answers the sponsorship question accordingly.

---

## Outputs

All outputs land in the `output/` directory:

| File | Contents |
|------|----------|
| `jobs_raw.json` | All scraped jobs |
| `jobs_filtered.json` | Jobs that passed relevance scoring |
| `resume_map.json` | Tailored resume paths per job |
| `applications_log.json` | Application results (applied / manual_review / error) |
| `final_report.md` | Summary report with stats by platform/location/company |
| `skills_gap_report.md` | Skills gap analysis vs. job requirements |
| `dashboard.html` | Visual dashboard — open in any browser |
| `pipeline.log` | Full run log |

---

## Notes

- `seen_jobs.json` tracks already-processed jobs so re-runs skip duplicates.
- Increase `request_delay_seconds` to 6–8 in `config.yaml` if LinkedIn returns 429 rate-limit errors.
- Step 2 (scoring), 3 (resume tailoring), 5 (report), and 6 (skills gap) all call Claude via the `claude` CLI. Make sure you're logged in: `claude auth login`.
- The pipeline can be resumed from any step: `bash run.sh --start-from 3`
- Use `--dry-run` when testing — it logs what would happen without submitting any forms.
