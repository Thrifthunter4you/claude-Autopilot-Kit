#!/usr/bin/env python3
"""Stop hook: SUGGEST (never edit) queueing work the session surfaced.

The queue-starvation failure this prevents: sessions end by writing a "found
X but didn't fix it" note with no `loop:` block; nothing ever runs it; the
pile grows while the loop idles.  A RUNNABLE task (loop: status todo + class
+ goal + repo) starts within minutes with an exact goal — better than hoping
a later triage pass infers one.

This hook nudges the author at session Stop when a recently-touched task
file in the active queue is unmanaged or parked.  Suggest-only: it NEVER
edits files.  Idempotent via a ledger; fail-open; exit 0.

Wire it (see hooks/install_hooks.py):
  "Stop": python3 <kit>/hooks/stop_queue_nudge.py
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
ACTIVE = HOME / "queue" / "active"
LEDGER = HOME / ".processed_queue_nudge.jsonl"
WINDOW = 6 * 3600  # modified within 6h of stop ~ "touched this session"

# A task is already runnable/settled when its loop status is one of these.
_SETTLED = re.compile(
    r"^\s*status:\s*(todo|in-progress|done|needs-human|blocked)\s*$", re.M)


def _seen():
    out = set()
    try:
        for line in open(LEDGER, encoding="utf-8"):
            try:
                out.add(json.loads(line).get("file"))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _record(names):
    try:
        with open(LEDGER, "a", encoding="utf-8") as f:
            for n in names:
                f.write(json.dumps({"file": n, "ts": int(time.time())}) + "\n")
    except OSError:
        pass


def main():
    if not ACTIVE.exists():
        sys.exit(0)
    now = time.time()
    seen = _seen()
    flagged = []
    try:
        for f in ACTIVE.glob("*.md"):
            try:
                if now - f.stat().st_mtime > WINDOW:
                    continue
                if f.name in seen:
                    continue
                text = f.read_text(errors="ignore")
                if _SETTLED.search(text[:2000]):
                    continue  # already runnable or terminally routed
                flagged.append(f.name)
            except OSError:
                continue
    except OSError:
        sys.exit(0)
    if flagged:
        for n in flagged:
            print(
                f"🧭 {n} is not RUNNABLE — give it a concrete goal so the loop "
                f"can start it: task-add \"<goal>\" --repo <repo>  (or add "
                f"loop: {{status: todo, class: <safe>, goal, repo}})",
                file=sys.stderr,
            )
        _record(flagged)
    sys.exit(0)


if __name__ == "__main__":
    main()
