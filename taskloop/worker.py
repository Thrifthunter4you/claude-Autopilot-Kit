"""Spawn one `claude -p` worker for one claimed task and parse its verdict.

The worker contract is textual and dead simple: the prompt template tells the
model to finish with EXACTLY ONE machine-parseable line:

    LOOP_RESULT: {"status": "<done|continue|needs-human|blocked>",
                  "outcome": "<=140 chars"}

- ``done``        the task is complete (only the finish phase may emit this)
- ``continue``    one chunk finished; requeue so a FRESH worker resumes from
                  the run manifest with a clean context window
- ``needs-human`` a guardrail or genuine judgment call stopped the run
- ``blocked``     an external dependency stopped the run

A worker that exits without a LOOP_RESULT line counts as a crash (strike).
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
from pathlib import Path
from typing import Optional

from . import config
from .handoff import Handoff

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
_RESULT_RE = re.compile(r"LOOP_RESULT:\s*(\{.*\})")


def _template_for(klass: str) -> Path:
    if klass == config.AUTOPILOT_CLASS:
        return _TEMPLATES / "autopilot_prompt.txt"
    return _TEMPLATES / "worker_prompt.txt"


def build_prompt(h: Handoff) -> str:
    lp = h.loop or {}
    tpl = _template_for(str(lp.get("class") or config.DEFAULT_CLASS))
    text = tpl.read_text()
    return text.format(
        path=str(h.path),
        klass=str(lp.get("class") or config.DEFAULT_CLASS),
        hint=str(lp.get("recall_hint") or lp.get("goal") or ""),
    )


def parse_result(output: str) -> Optional[dict]:
    """Last LOOP_RESULT line wins (the model may echo the contract earlier)."""
    last = None
    for m in _RESULT_RE.finditer(output):
        last = m.group(1)
    if last is None:
        return None
    try:
        data = json.loads(last)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "status" not in data:
        return None
    return data


def run_one(h: Handoff) -> Optional[dict]:
    """Blocking: spawn the worker, stream output to a log, return the parsed
    LOOP_RESULT (or None on crash/timeout/no-verdict)."""
    config.ensure_dirs()
    lp = h.loop or {}
    repo = str(lp.get("repo") or config.DEFAULT_REPO or Path.home())
    prompt = build_prompt(h)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = Path(config.LOG_DIR) / f"{h.path.stem}-{ts}.log"
    cmd = [config.CLAUDE_BIN, "-p", prompt,
           "--model", config.WORKER_MODEL, *config.CLAUDE_ARGS]
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.run(cmd, cwd=repo, stdout=log,
                                  stderr=subprocess.STDOUT,
                                  timeout=config.WORKER_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return None
    try:
        output = log_path.read_text(errors="ignore")
    except OSError:
        return None
    if proc.returncode != 0 and "LOOP_RESULT" not in output:
        return None
    return parse_result(output)
