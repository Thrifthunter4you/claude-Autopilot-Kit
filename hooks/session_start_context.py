#!/usr/bin/env python3
"""SessionStart hook: state awareness — brief the new session on what the
task loop and its sibling sessions are doing.

Injects (as additionalContext, a few lines max):
  - queue snapshot: runnable / todo / parked counts
  - the top runnable task (so "pull the next item" needs zero discovery)
  - recent cross-session mailbox announcements (last 6h)

Fail-open: any error -> exit 0 silent.  Read-only.

Wire it (see hooks/install_hooks.py):
  "SessionStart": python3 <kit>/hooks/session_start_context.py
"""
import json
import os
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

HOME = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
MAILBOX = HOME / "mailbox.jsonl"
MAILBOX_WINDOW = 6 * 3600


def _mailbox_recent(limit: int = 5):
    if not MAILBOX.exists():
        return []
    now = time.time()
    rows = []
    try:
        for line in MAILBOX.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if now - float(row.get("ts", 0)) <= MAILBOX_WINDOW:
                rows.append(row)
    except OSError:
        return []
    return rows[-limit:]


def main():
    lines = []
    try:
        from taskloop import config, queue
        if config.ACTIVE_DIR.exists():
            snap = queue.snapshot()
            lines.append(
                f"task loop: {snap['eligible']} runnable / "
                f"{snap['total_todo']} todo / {snap['parked']} parked")
            nxt = queue.select_next()
            if nxt is not None:
                goal = str((nxt.loop or {}).get("goal") or "")[:120]
                lines.append(f"next up: {nxt.path.name} — {goal}")
    except Exception:
        pass
    try:
        for row in _mailbox_recent():
            age_m = int((time.time() - float(row.get("ts", 0))) / 60)
            lines.append(f"mailbox [{row.get('kind', 'note')}] "
                         f"{age_m}m ago: {str(row.get('text', ''))[:160]}")
    except Exception:
        pass
    if not lines:
        sys.exit(0)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "<task-loop-state>\n"
                                 + "\n".join(lines) + "\n</task-loop-state>",
        }
    }
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
