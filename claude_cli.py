"""
claude_cli.py — Thin wrapper around `claude -p` for non-interactive LLM calls.
Uses Claude Code's existing auth — no ANTHROPIC_API_KEY needed.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

CLAUDE_BIN = (
    shutil.which("claude")
    or str(Path.home() / ".npm-global" / "bin" / "claude")
)


def call_claude(prompt: str, system: str = "", max_retries: int = 2) -> str:
    """
    Call `claude -p <prompt>` and return the text response.
    Optionally prepend a system instruction via a combined prompt.
    """
    full_prompt = f"{system}\n\n{prompt}".strip() if system else prompt

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", full_prompt],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            log.warning(
                "claude -p returned code %d (attempt %d): %s",
                result.returncode, attempt + 1, result.stderr[:200],
            )
        except subprocess.TimeoutExpired:
            log.warning("claude -p timed out (attempt %d)", attempt + 1)
        except FileNotFoundError:
            raise RuntimeError(
                f"Claude CLI not found at {CLAUDE_BIN}. "
                "Make sure `claude` is on PATH and you've run `claude auth login`."
            )

    return ""


def call_claude_json(prompt: str, system: str = "") -> dict | list:
    """
    Call Claude and parse the response as JSON.
    Handles three common failure modes:
      1. Markdown fences (```json ... ```)
      2. Extra text after the JSON object ("Extra data" error)
      3. JSON embedded mid-response after a preamble line
    """
    raw = call_claude(prompt, system=system)

    # Strip markdown fences
    clean = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    # Strategy 1: parse the whole thing
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Strategy 2: raw_decode — stops at end of first valid JSON value,
    # ignoring any trailing explanation text Claude appended
    try:
        obj, _ = json.JSONDecoder().raw_decode(clean)
        return obj
    except json.JSONDecodeError:
        pass

    # Strategy 3: find the first '{' or '[' and try from there
    # (handles cases where Claude adds a preamble line before the JSON)
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        idx = clean.find(start_char)
        if idx != -1:
            try:
                obj, _ = json.JSONDecoder().raw_decode(clean[idx:])
                return obj
            except json.JSONDecodeError:
                pass

    log.warning("JSON parse failed after all strategies.\nRaw: %s", clean[:400])
    return {}
