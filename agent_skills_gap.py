"""
agent_skills_gap.py — Analyzes all filtered JDs against the candidate's resume
to identify skills that appear frequently in job postings but are missing or
underrepresented in the resume.

Saves output/skills_gap_report.md
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

import yaml
from docx import Document

from claude_cli import call_claude_json, call_claude

logging.basicConfig(level=logging.INFO, format="%(asctime)s [skills_gap] %(message)s")
log = logging.getLogger(__name__)

FILTERED_FILE = Path("output/jobs_filtered.json")
REPORT_FILE = Path("output/skills_gap_report.md")
CONFIG_FILE = Path("config.yaml")


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def read_resume(path: str) -> str:
    p = Path(path)
    if not p.exists():
        log.warning("Resume not found: %s", path)
        return ""
    doc = Document(p)
    return "\n".join(para.text for para in doc.paragraphs if para.text.strip())


def extract_jd_skills(job: dict) -> list[str]:
    jd = job.get("full_description") or job.get("description_snippet", "")
    if not jd:
        return job.get("keywords", [])

    prompt = f"""Extract all technical skills, tools, platforms, and certifications required or preferred
in this job description. Return JSON only:
{{"skills": ["skill1", "skill2", ...]}}

Job: {job.get("title")} at {job.get("company")}
Description:
{jd[:3000]}"""

    result = call_claude_json(prompt)
    if result and isinstance(result.get("skills"), list):
        return [s.strip().lower() for s in result["skills"] if s.strip()]
    return [k.lower() for k in job.get("keywords", [])]


def extract_resume_skills(resume_text: str) -> list[str]:
    prompt = f"""Extract all technical skills, tools, platforms, certifications, and methodologies
listed in this resume. Return JSON only:
{{"skills": ["skill1", "skill2", ...]}}

Resume:
{resume_text[:4000]}"""

    result = call_claude_json(prompt)
    if result and isinstance(result.get("skills"), list):
        return [s.strip().lower() for s in result["skills"] if s.strip()]
    return []


def generate_report(
    skill_freq: Counter,
    resume_skills: set[str],
    jobs: list[dict],
) -> str:
    total_jobs = len(jobs)

    # Split into gaps (missing) and strengths (present)
    gaps = [(skill, count) for skill, count in skill_freq.most_common(40)
            if not any(skill in rs or rs in skill for rs in resume_skills)]
    strengths = [(skill, count) for skill, count in skill_freq.most_common(20)
                 if any(skill in rs or rs in skill for rs in resume_skills)]

    # Ask Claude for learning recommendations on top gaps
    top_gaps = [s for s, _ in gaps[:10]]
    recs = ""
    if top_gaps:
        prompt = f"""A job seeker is missing these in-demand skills: {', '.join(top_gaps)}.
They are a QA/Salesforce professional. For each skill, give one concise sentence on
the fastest way to learn or certify in it. Return as a markdown bullet list."""
        recs = call_claude(prompt) or ""

    lines = ["# Skills Gap Report\n"]
    lines.append(f"Analyzed **{total_jobs}** job postings.\n")

    lines.append("## Top Missing Skills (by frequency in job postings)\n")
    lines.append("| Skill | Appears In | % of Jobs |")
    lines.append("|-------|-----------|-----------|")
    for skill, count in gaps[:20]:
        pct = round(count / total_jobs * 100)
        lines.append(f"| {skill.title()} | {count} jobs | {pct}% |")
    lines.append("")

    lines.append("## Your Matching Strengths\n")
    lines.append("| Skill | Appears In | % of Jobs |")
    lines.append("|-------|-----------|-----------|")
    for skill, count in strengths[:15]:
        pct = round(count / total_jobs * 100)
        lines.append(f"| {skill.title()} | {count} jobs | {pct}% |")
    lines.append("")

    if recs:
        lines.append("## How to Close the Top Gaps\n")
        lines.append(recs)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(jobs: list[dict] | None = None) -> str:
    config = load_config()

    if jobs is None:
        if not FILTERED_FILE.exists():
            log.error("Input file not found: %s — run agent_relevance first", FILTERED_FILE)
            return ""
        jobs = json.loads(FILTERED_FILE.read_text())

    if not jobs:
        log.warning("No jobs to analyze")
        return ""

    log.info("Analyzing skills across %d job postings...", len(jobs))

    # Extract skills from all JDs
    skill_freq: Counter = Counter()
    for i, job in enumerate(jobs, 1):
        log.info("[%d/%d] Extracting skills from '%s' @ %s", i, len(jobs), job["title"], job["company"])
        skills = extract_jd_skills(job)
        skill_freq.update(skills)

    log.info("Found %d unique skills across all postings", len(skill_freq))

    # Extract skills from resume
    resume_path = config.get("resume_path", "")
    resume_text = read_resume(resume_path)
    resume_skills: set[str] = set()
    if resume_text:
        log.info("Extracting skills from resume...")
        resume_skills = set(extract_resume_skills(resume_text))
        log.info("Found %d skills in resume", len(resume_skills))

    report = generate_report(skill_freq, resume_skills, jobs)

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(report)
    log.info("Skills gap report saved → %s", REPORT_FILE)

    return report


if __name__ == "__main__":
    run()
