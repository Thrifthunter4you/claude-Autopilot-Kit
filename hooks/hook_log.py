"""One JSONL row per hook decision that changed what the session saw.

A guard hook is never free: every deny costs the model at least one extra
turn (read the reason, pick another route, call again), and every turn
re-sends the whole context.  Advisory injections and output rewrites cost
or save tokens too.  None of that shows up anywhere unless the hooks write
it down, so the kit's hooks call ``record()`` whenever they deny, block,
advise or rewrite, and ``bin/hook-cost`` reads the file back:

    {"ts": 1767225600.0, "event": "PreToolUse", "hook": "spill_read_guard",
     "decision": "deny", "session": "…", "tool": "Read",
     "reason": "…first 200 chars…"}

Optional keys: ``agent`` (the subagent id when a subagent made the call),
``saved_bytes`` (what a rewrite kept out of context).

Library only — not a hook itself.  Never raises: a logging failure must not
change a hook's decision.  Path: ``$TASKLOOP_HOME/hook_decisions.jsonl``
(override with TASKLOOP_HOOK_LOG); TASKLOOP_HOOK_LOG_DISABLE=1 turns it off.
Past ~5 MB the file rotates to ``hook_decisions.jsonl.1`` (one generation
kept); ``hook-cost`` reads both.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

REASON_CHARS = 200
ROTATE_BYTES = 5_000_000       # past this the file moves to <name>.1


def log_path() -> Path:
    explicit = os.environ.get("TASKLOOP_HOOK_LOG")
    if explicit:
        return Path(explicit)
    home = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
    return home / "hook_decisions.jsonl"


def record(event: str, hook: str, decision: str, *, session: str = "",
           tool: str = "", reason: str = "", agent: str = "",
           saved_bytes: int | None = None) -> None:
    if os.environ.get("TASKLOOP_HOOK_LOG_DISABLE") == "1":
        return
    row = {"ts": round(time.time(), 3), "event": event, "hook": hook,
           "decision": decision, "session": session, "tool": tool,
           "reason": " ".join(str(reason).split())[:REASON_CHARS]}
    if agent:
        row["agent"] = agent
    if saved_bytes is not None:
        row["saved_bytes"] = int(saved_bytes)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > ROTATE_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
