#!/usr/bin/env python3
"""PreToolUse hook: past a context ceiling, tell the session to wrap up.

Every model call re-reads the whole context window.  Past a certain size a
session's next tool call costs more than a fresh session resuming from a
good handoff — and attention over a very long context is worse besides.
"Hand off when the context gets big" in CLAUDE.md never fires on its own:
nothing in the session measures its context, and a session driven by tool
results and notifications rarely gets a new prompt where a prompt-time
hook could speak.  Tool calls are what a long session does constantly, so
this hook rides PreToolUse.

On each main-loop call it reads the tail of the session transcript and
takes the context size of the newest model call — input + cache-read +
cache-write tokens from that call's ``usage`` record.  At or over the
ceiling it adds a wrap-up directive as ``additionalContext``: finish only
the edit or test in flight, commit, write a handoff, end the turn, open no
new work.  It never blocks a call.  It stays silent on the wrap-up calls
themselves (a write under a ``handoffs/`` directory, ``task-add`` or
``task-go``) and repeats on a cooldown rather than on every call, so the
directive does not become bloat of its own.

Subagent calls (payload has ``agent_id``) exit at once: a subagent cannot
hand off, and its payload carries the parent's transcript anyway — the
parent hears about it on its own next call.

Configuration:
  TASKLOOP_CONTEXT_CEILING_K=150      ceiling, thousands of tokens.  On a
                                      200K-window model this fires shortly
                                      before auto-compaction; on a 1M
                                      window raise it if you mean to run
                                      long sessions
  TASKLOOP_CONTEXT_COOLDOWN_S=180     minimum seconds between reminders
  TASKLOOP_CONTEXT_CEILING_DISABLE=1  hook off

Each reminder is recorded via hook_log, so ``hook-cost`` shows how many
sessions ran past the ceiling.  Fail-open on any error.

Wire it (hooks/install_hooks.py registers it): PreToolUse, matcher = the
working tools — the same list as the subagent turn budget.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX: run unlocked
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import hook_log
except Exception:  # pragma: no cover - logging is optional
    hook_log = None

TAIL_WINDOWS = (256 * 1024, 4 * 1024 * 1024)  # widen once if one line outgrows the first
STATE_MAX_AGE_S = 2 * 86400
WRAPUP_COMMAND = re.compile(r"\btask-(add|go)\b")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def state_dir() -> Path:
    home = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
    return home / "context_ceiling"


def last_context_tokens(path: str) -> int:
    """Context size of the newest model call in the transcript, or 0 when
    none is found (a young session is by definition under any ceiling)."""
    size = os.path.getsize(path)
    for window in TAIL_WINDOWS:
        with open(path, "rb") as f:
            if size > window:
                f.seek(size - window)
                f.readline()                       # drop the partial line
            lines = f.read().splitlines()
        for raw in reversed(lines):
            if b'"usage"' not in raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "assistant":
                continue
            usage = (row.get("message") or {}).get("usage") or {}
            total = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if total:
                return total
        if size <= window:
            break
    return 0


def is_wrapup(tool: str, tool_input: dict) -> bool:
    """True when this call IS the wrap-up the directive asks for."""
    if tool in ("Write", "Edit", "MultiEdit"):
        return "/handoffs/" in str(tool_input.get("file_path") or "")
    if tool == "Bash":
        return bool(WRAPUP_COMMAND.search(str(tool_input.get("command") or "")))
    return False


def directive(ctx_k: int, ceiling_k: int) -> str:
    return (
        "<context-ceiling>\n"
        f"This session's last model call carried ~{ctx_k}K tokens of context "
        f"(ceiling {ceiling_k}K). Every further call re-reads all of it; a fresh "
        "session resuming from a handoff does the same work for a fraction of the "
        "cost, with sharper attention. Finish only the edit or test in flight "
        "(never leave a file half-written), commit your own files, write a handoff "
        "— `task-add \"<concrete goal>\"` for work the loop can run, or a note "
        "under memory/handoffs/active/ with the exact next step — and end the "
        "turn. A task-loop worker checkpoints and yields instead (autopilot skill "
        "§6). Do not start new searches, files or investigations in this window. "
        "This reminder repeats every few minutes while the context stays over the "
        "ceiling.\n"
        "</context-ceiling>")


def _prune(d: Path) -> None:
    cutoff = time.time() - STATE_MAX_AGE_S
    for old in d.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


@contextlib.contextmanager
def _locked(d: Path):
    """Serialize the parallel calls of one tool batch: one reminder, not N."""
    if fcntl is None:
        yield
        return
    with open(d / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def decide(payload: dict) -> dict | None:
    if payload.get("agent_id"):
        return None
    session = str(payload.get("session_id") or "")
    transcript = str(payload.get("transcript_path") or "")
    tool = str(payload.get("tool_name") or "")
    if not session or not os.path.isfile(transcript):
        return None
    if is_wrapup(tool, payload.get("tool_input") or {}):
        return None
    ceiling_k = _env_int("TASKLOOP_CONTEXT_CEILING_K", 150)
    ctx_k = last_context_tokens(transcript) // 1000
    if not ceiling_k or ctx_k < ceiling_k:
        return None

    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    state_path = d / (hashlib.sha1(session.encode()).hexdigest()[:20] + ".json")
    with _locked(d):
        try:
            st = json.loads(state_path.read_text())
        except (OSError, ValueError):
            st = {}
            _prune(d)                              # once per new session
        now = time.time()
        if now - float(st.get("last_fire", 0)) < _env_int("TASKLOOP_CONTEXT_COOLDOWN_S", 180):
            return None
        st.update(last_fire=now, fires=int(st.get("fires", 0)) + 1, ctx_k=ctx_k)
        tmp = state_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(st))
        os.replace(tmp, state_path)
    if hook_log is not None:
        hook_log.record("PreToolUse", "context_ceiling", "advise", session=session,
                        tool=tool, reason=f"{ctx_k}K >= {ceiling_k}K (reminder {st['fires']})")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "additionalContext": directive(ctx_k, ceiling_k)}}


def main() -> int:
    if os.environ.get("TASKLOOP_CONTEXT_CEILING_DISABLE") == "1":
        return 0
    try:
        out = decide(json.load(sys.stdin))
        if out:
            print(json.dumps(out))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
