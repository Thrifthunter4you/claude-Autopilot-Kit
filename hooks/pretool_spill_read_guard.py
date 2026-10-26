#!/usr/bin/env python3
"""PreToolUse (Read) hook: keep whole spill files out of the context window.

`spill run` exists so a command's bulk output lives on disk and the session
sees a bounded preview.  That saving evaporates the moment a later turn
Reads the spill file top to bottom — and that is exactly what an agent does
when a preview names a path.  This hook denies an UNBOUNDED Read of a file
under the spill directory and names the bounded ways in:

    spill show <path> --grep "ERROR|FAIL"      matching lines, ±2 context
    spill show <path> --head 40 --tail 20      both ends
    Read with offset + limit                   an explicit slice

A Read that already carries a `limit` is bounded and passes.  Small spills
(under SMALL_BYTES) pass too — the guard is about bulk, not ceremony.
Kill switch: TASKLOOP_SPILL_GUARD_DISABLE=1.  Fail-open on any error.

Wire it (hooks/install_hooks.py registers it): PreToolUse, matcher "Read".
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import hook_log
except Exception:  # pragma: no cover - logging is optional
    hook_log = None

HOME = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
SPILL_DIR = HOME / "spills"
SMALL_BYTES = int(os.environ.get("TASKLOOP_SPILL_GUARD_SMALL_BYTES", "4096"))


def decide(tool_input: dict, spill_dir: Path = None) -> str:
    """'deny' for an unbounded Read of a non-trivial spill, else 'allow'."""
    spill_dir = spill_dir or SPILL_DIR   # resolved at call time (tests patch it)
    raw = str(tool_input.get("file_path") or "")
    if not raw:
        return "allow"
    try:
        path = Path(raw).expanduser().resolve()
        path.relative_to(spill_dir.resolve())
    except (ValueError, OSError, RuntimeError):
        return "allow"
    if tool_input.get("limit"):
        return "allow"
    try:
        if path.stat().st_size <= SMALL_BYTES:
            return "allow"
    except OSError:
        return "allow"
    return "deny"


def main():
    if os.environ.get("TASKLOOP_SPILL_GUARD_DISABLE") == "1":
        sys.exit(0)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if payload.get("tool_name") != "Read":
        sys.exit(0)
    tool_input = payload.get("tool_input") or {}
    if decide(tool_input) != "deny":
        sys.exit(0)
    path = tool_input.get("file_path")
    if hook_log is not None:
        hook_log.record("PreToolUse", "spill_read_guard", "deny",
                        session=str(payload.get("session_id") or ""),
                        tool="Read", reason=str(path),
                        agent=str(payload.get("agent_id") or ""))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"{path} is a spill file — its full text is exactly what `spill "
            f"run` kept out of the context window. Read it bounded: `spill "
            f"show {path} --grep \"<pattern>\"`, `spill show {path} --head 40 "
            f"--tail 20`, or Read with offset + limit. "
            f"Kill switch: TASKLOOP_SPILL_GUARD_DISABLE=1."),
    }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
