"""Spawn one `claude -p` worker for one claimed task and read its verdict.

The verdict is NOT a line the model writes into its transcript.  The worker
is launched with ``--output-format json --json-schema <RESULT_SCHEMA>``, so
the CLI itself constrains the model's final answer to one small object and
hands it back in the ``structured_output`` field of the JSON envelope it
prints on stdout:

    {"status": "<done|continue|needs-human|blocked>", "outcome": "<=140 chars"}

- ``done``        the task is complete (only the finish phase may say this)
- ``continue``    one chunk finished; requeue so a FRESH worker resumes from
                  the run manifest with a clean context window
- ``needs-human`` a guardrail or genuine judgment call stopped the run
- ``blocked``     an external dependency stopped the run

Why not a magic ``LOOP_RESULT:`` line (the kit's original protocol): anything
that lands in a transcript can supply such a line — a tool result, a file the
worker read, a quoted example inside the task itself — and "last matching
line wins" turned prompt-injected repository content into a verdict.  The
schema-validated envelope is produced by the harness, not by the model's
prose, and :func:`validate_result` re-checks it in Python before the drainer
acts on it.

Failure is fail-safe: a non-zero CLI exit, a timeout, a missing or malformed
envelope, an unknown status, an over-long outcome or any extra field all
yield ``None``, which the drainer counts as a crash (strike).  There is
deliberately NO fallback to a regex, to the envelope's free-text ``result``,
or to a transcript line — a fallback would reopen the injection path.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from . import config
from .handoff import Handoff

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

STATUSES = ("continue", "done", "needs-human", "blocked")
OUTCOME_MAX = 140

# The one object a worker may return.  Kept tiny on purpose: the drainer
# needs a status to route on and a one-line outcome for the log — anything
# richer belongs in the run manifest or the task file, not the verdict.
RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "outcome"],
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "outcome": {"type": "string", "maxLength": OUTCOME_MAX},
    },
}

# Effort levels the CLI accepts for --effort (empty config = CLI default).
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


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


def validate_result(obj) -> Optional[dict]:
    """Defensive re-validation of the structured object the CLI returned.

    Exactly the two schema keys, a known status, a string outcome within the
    length cap.  Anything else is ``None`` — never "close enough".
    """
    if not isinstance(obj, dict) or set(obj) != {"status", "outcome"}:
        return None
    status, outcome = obj["status"], obj["outcome"]
    if status not in STATUSES:
        return None
    if not isinstance(outcome, str) or len(outcome) > OUTCOME_MAX:
        return None
    return {"status": status, "outcome": outcome}


def _result_envelope(stdout: str) -> Optional[dict]:
    """The CLI's final ``type: result`` envelope from stdout, or ``None``.

    ``--output-format json`` prints one object; older CLIs printed a list of
    messages — accept both, but only the terminal result object counts.
    """
    try:
        data = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        results = [m for m in data
                   if isinstance(m, dict) and m.get("type") == "result"]
        data = results[-1] if results else None
    if not isinstance(data, dict):
        return None
    if data.get("type", "result") != "result" or data.get("is_error"):
        return None
    return data


def parse_result(stdout: str) -> Optional[dict]:
    """Worker stdout (the CLI JSON envelope) → validated verdict or ``None``.

    Only ``structured_output`` is consulted.  The envelope's ``result`` text
    and everything the worker printed along the way are diagnostics, never
    a verdict source.
    """
    env = _result_envelope(stdout)
    if env is None:
        return None
    return validate_result(env.get("structured_output"))


def build_command(prompt: str) -> list:
    """The exact argv a worker is spawned with (exposed for tests/docs)."""
    cmd = [config.CLAUDE_BIN, "-p", prompt,
           "--model", config.WORKER_MODEL,
           "--output-format", "json",
           "--json-schema", json.dumps(RESULT_SCHEMA)]
    if config.WORKER_EFFORT in EFFORT_LEVELS:
        cmd += ["--effort", config.WORKER_EFFORT]
    return cmd + list(config.CLAUDE_ARGS)


def build_env() -> dict:
    """Process environment for a worker.

    Print mode terminates a session whose background tasks are still running
    once a wait ceiling expires (600 s by default) — with NO verdict, so the
    drainer strikes a healthy task.  The kit's real bound is
    ``worker_timeout_s``; by default the ceiling is lifted (``0`` = wait) so
    the only way a worker dies is the outer timeout.  Set
    ``worker_bg_wait_ceiling_ms`` to ``-1`` to leave the CLI default alone.
    """
    env = dict(os.environ)
    ceiling = config.WORKER_BG_WAIT_CEILING_MS
    if ceiling is not None and ceiling >= 0:
        env["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] = str(ceiling)
    return env


def run_one(h: Handoff) -> Optional[dict]:
    """Blocking: spawn the worker, log stderr live and the final envelope
    after, return the validated verdict (``None`` on crash/timeout/invalid)."""
    config.ensure_dirs()
    lp = h.loop or {}
    repo = str(lp.get("repo") or config.DEFAULT_REPO or Path.home())
    prompt = build_prompt(h)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = Path(config.LOG_DIR) / f"{h.path.stem}-{ts}.log"
    cmd = build_command(prompt)
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"# worker for {h.path.name} in {repo}\n"
                      f"# model={config.WORKER_MODEL} "
                      f"effort={config.WORKER_EFFORT or 'default'}\n"
                      "# --- stderr (live) ---\n")
            log.flush()
            proc = subprocess.run(cmd, cwd=repo, env=build_env(),
                                  stdout=subprocess.PIPE, stderr=log,
                                  text=True, errors="replace",
                                  timeout=config.WORKER_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return None
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n# --- exit {proc.returncode}; stdout (result "
                      "envelope) ---\n")
            log.write(proc.stdout or "")
            log.write("\n")
    except OSError:
        pass
    if proc.returncode != 0:
        return None
    return parse_result(proc.stdout or "")
