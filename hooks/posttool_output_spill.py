#!/usr/bin/env python3
"""PostToolUse hook: big tool output goes to a spill file, a preview stays.

`spill run` keeps a command's bulk out of the context window — when you
remember to use it.  This hook does it for every call: when a successful
Bash result or an MCP tool's text result is bigger than the threshold, the
full text is written to ``$TASKLOOP_HOME/spills/`` and the result the model
sees is replaced (``hookSpecificOutput.updatedToolOutput``) by a head/tail
preview that names the file:

    [Output spilled: 48211 stdout bytes -> ~/.taskloop/spills/spill-….txt]
    --- head (2048 bytes) ---
    …
    --- 44115 bytes omitted ---
    --- tail (2048 bytes) ---
    …
    [Full text: spill show <path> --grep "<pattern>"  |  --head 40 --tail 20]

The spill file is a normal `spill` file, so `spill list` / `spill show`
work on it and `pretool_spill_read_guard.py` stops a later turn from
Reading it back whole.

What is deliberately NOT rewritten:
  - anything that is not a known SUCCESS shape.  PostToolUse fires after a
    tool succeeded; a Bash dict that was interrupted, is an image, carries a
    ``returnCodeInterpretation`` (a non-zero exit read as benign, e.g. grep
    no-match), a non-zero ``exitCode``, or a ``persistedOutputPath`` (the
    harness already truncated and saved it) passes through untouched.  Error
    text is the next turn's evidence, not bulk.
  - Read/Edit/Write and other first-party tools: their result shapes are
    tool-specific and must not be flattened into text.
  - MCP results that are not a plain string.
  - a Bash command containing the marker ``# keep-output`` — when the output
    IS the deliverable, say so and it lands whole.
  - a spill that would not pay for itself: if the preview saves less than
    MIN_SAVING bytes the original lands and no file is left behind.

Each stream is judged on its own: stdout is cut to head/tail only when it
is bigger than both previews together, and stderr keeps its own small
in-context budget (its tail — where errors end up), so a warning is never
hidden behind a spill of stdout.  Common secret shapes (provider API keys,
JWTs, Bearer tokens, URL credentials, private-key blocks, ``*_PASSWORD=`` /
``"client_secret": …`` style pairs) are masked in the spill file and the
preview before anything touches disk — best effort, not a guarantee, so
treat the spill directory like your shell history.  Spill files older than
TASKLOOP_SPILL_RETENTION_DAYS are pruned on each write.

Subagent calls (the payload carries ``agent_id``) use a lower threshold:
a subagent re-reads its own context every turn just like the main loop,
and its results are usually consumed once.

Configuration (all optional):
  TASKLOOP_OUTPUT_SPILL_DISABLE=1          hook off
  TASKLOOP_OUTPUT_SPILL_THRESHOLD=8192     spill above this many bytes landing
  TASKLOOP_OUTPUT_SPILL_SUBAGENT_THRESHOLD=4096
  TASKLOOP_OUTPUT_SPILL_PREVIEW=2048       bytes kept from each end
  TASKLOOP_OUTPUT_SPILL_STDERR=2048        stderr bytes kept in context
  TASKLOOP_OUTPUT_SPILL_MIN_SAVING=1024    0 disables the pay-for-itself check
  TASKLOOP_OUTPUT_SPILL_TOOLS=Bash,mcp     which tool classes are eligible
  TASKLOOP_SPILL_RETENTION_DAYS=7          prune older spill files (0 = keep)

Needs a Claude Code version whose PostToolUse hooks accept
``updatedToolOutput`` (current releases do).  Fail-open: any error and the
original result lands unchanged.

Wire it (hooks/install_hooks.py registers it): PostToolUse, matcher
"Bash|mcp__.*".
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import hook_log
except Exception:  # pragma: no cover - logging is optional
    hook_log = None

KEEP_MARKER = "# keep-output"

MASK = "[MASKED]"
# (pattern, replacement).  Best effort: common shapes, not a guarantee.
_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.S), MASK),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"), MASK),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),
    # Authorization: Bearer <token>
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1" + MASK),
    # scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)"), r"\1" + MASK + r"\2"),
    # KEY=value / "key": "value" for names containing a secret-ish word
    # (DB_PASSWORD, client_secret, access_token, …).  The value must hold a
    # letter, so counters like max_tokens=100000 are left alone.
    (re.compile(r"(?i)(\b[A-Za-z0-9_]*(?:api[_-]?key|secret|token|password|passwd|pwd)"
                r"[A-Za-z0-9_]*['\"]?\s*[=:]\s*)(['\"]?)(?=[^\s'\"]*[A-Za-z])"
                r"[^\s'\",}]{6,}\2"), r"\1" + MASK),
]


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def spill_dir() -> Path:
    home = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
    return home / "spills"


def mask_secrets(text: str) -> str:
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def prune(directory: Path, days: float) -> None:
    """Delete spill files older than `days` (0 keeps everything)."""
    if days <= 0:
        return
    cutoff = _dt.datetime.now().timestamp() - days * 86400
    for old in directory.glob("spill-*.txt"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _eligible(tool_name: str) -> bool:
    raw = os.environ.get("TASKLOOP_OUTPUT_SPILL_TOOLS", "").strip() or "Bash,mcp"
    classes = {c.strip().lower() for c in raw.split(",") if c.strip()}
    if tool_name == "Bash":
        return "bash" in classes
    return tool_name.startswith("mcp__") and "mcp" in classes


def is_successful_bash(response) -> bool:
    if not isinstance(response, dict) or not isinstance(response.get("stdout"), str):
        return False
    if response.get("interrupted") is True or response.get("isImage"):
        return False
    if "returnCodeInterpretation" in response or response.get("persistedOutputPath"):
        return False
    code = response.get("exitCode")
    return code is None or code == 0


def _head(text: str, nbytes: int) -> str:
    cut = text.encode("utf-8")[:nbytes].decode("utf-8", "ignore")
    nl = cut.rfind("\n")
    return cut[:nl] if nl > len(cut) // 2 else cut


def _tail(text: str, nbytes: int) -> str:
    cut = text.encode("utf-8")[-nbytes:].decode("utf-8", "ignore") if nbytes else ""
    nl = cut.find("\n")
    return cut[nl + 1:] if 0 <= nl < len(cut) // 2 else cut


def preview(text: str, path: Path, nbytes: int, stream: str) -> str:
    head, tail = _head(text, nbytes), _tail(text, nbytes)
    omitted = _nbytes(text) - _nbytes(head) - _nbytes(tail)
    return (
        f"[Output spilled: {_nbytes(text)} {stream} bytes -> {path}]\n"
        f"--- head ({_nbytes(head)} bytes) ---\n{head}\n"
        f"--- {max(omitted, 0)} bytes omitted ---\n"
        f"--- tail ({_nbytes(tail)} bytes) ---\n{tail}\n"
        f"[Full text: spill show {path} --grep \"<pattern>\"  |  "
        f"spill show {path} --head 40 --tail 20]"
    )


def _write(text: str, tool_use_id: str) -> Path:
    d = spill_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = re.sub(r"[^A-Za-z0-9_-]", "", tool_use_id or "")[-16:] or str(os.getpid())
    path = d / f"spill-{stamp}-{suffix}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def process(payload: dict) -> dict | None:
    """The PostToolUse output for one call, or None to pass it through."""
    if not isinstance(payload, dict):
        return None
    tool = str(payload.get("tool_name") or "")
    if not _eligible(tool):
        return None
    response = payload.get("tool_response")
    tool_input = payload.get("tool_input") or {}

    if tool == "Bash":
        if KEEP_MARKER in str(tool_input.get("command") or ""):
            return None
        if not is_successful_bash(response):
            return None
        stdout, stderr = response["stdout"], str(response.get("stderr") or "")
        before = _nbytes(stdout) + _nbytes(stderr)
        body, stream = stdout, "stdout"
    elif isinstance(response, str):
        stderr = ""
        before = _nbytes(response)
        body, stream = response, "result"
    else:
        return None

    agent = str(payload.get("agent_id") or "")
    threshold = (_env_int("TASKLOOP_OUTPUT_SPILL_SUBAGENT_THRESHOLD", 4096) if agent
                 else _env_int("TASKLOOP_OUTPUT_SPILL_THRESHOLD", 8192))
    if before <= threshold:
        return None

    preview_bytes = _env_int("TASKLOOP_OUTPUT_SPILL_PREVIEW", 2048)
    stderr_budget = _env_int("TASKLOOP_OUTPUT_SPILL_STDERR", 2048)
    # Only a stream that is actually big gets cut: a 3 KB stdout next to a
    # 10 KB stderr keeps its stdout whole (a head/tail of it would overlap).
    cut_body = _nbytes(body) > 2 * preview_bytes
    cut_stderr = _nbytes(stderr) > stderr_budget
    if not (cut_body or cut_stderr):
        return None

    body = mask_secrets(body)
    stderr = mask_secrets(stderr)
    file_text = body if not stderr else f"{body}\n--- stderr ---\n{stderr}"
    d = spill_dir()
    path = _write(file_text, str(payload.get("tool_use_id") or ""))
    try:
        prune(d, float(os.environ.get("TASKLOOP_SPILL_RETENTION_DAYS", "7")))
    except ValueError:
        pass
    shown = preview(body, path, preview_bytes, stream) if cut_body else body

    if tool == "Bash":
        updated = dict(response)
        updated["stdout"] = shown
        if cut_stderr:
            updated["stderr"] = (f"[stderr tail; full text in {path}]\n"
                                 + _tail(stderr, stderr_budget))
        else:
            updated["stderr"] = stderr
        after = _nbytes(updated["stdout"]) + _nbytes(str(updated.get("stderr") or ""))
    else:
        updated = shown
        after = _nbytes(shown)

    if before - after < _env_int("TASKLOOP_OUTPUT_SPILL_MIN_SAVING", 1024):
        try:
            path.unlink()
        except OSError:
            pass
        return None

    if hook_log is not None:
        hook_log.record("PostToolUse", "output_spill", "rewrite",
                        session=str(payload.get("session_id") or ""), tool=tool,
                        reason=str(path), agent=agent, saved_bytes=before - after)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "updatedToolOutput": updated}}


def main() -> int:
    if os.environ.get("TASKLOOP_OUTPUT_SPILL_DISABLE") == "1":
        return 0
    try:
        result = process(json.load(sys.stdin))
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
