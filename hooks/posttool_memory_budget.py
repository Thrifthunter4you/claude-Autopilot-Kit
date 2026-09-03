#!/usr/bin/env python3
"""PostToolUse (Write|Edit) hook: keep the memory INDEX inside its budget.

MEMORY.md is loaded into context at the start of every session — the one
file that is never "on demand".  Two things silently break it:

1. Size.  Past a harness cap (about 200 lines / 25 KB in practice) the tail
   of the index is simply dropped, so whatever you appended last — usually
   the freshest pointer — is the part no future session sees.
2. Bullet bloat.  An index line is a pointer: topic, state, link.  When a
   bullet grows to a paragraph it is mirroring the memory it points at, the
   index stops being cheap to load, and the cap arrives sooner.

This hook fires after any Write/Edit whose target is named MEMORY.md and
WARNS (never blocks — the edit already happened) when the file is over its
line or byte budget, or carries bullets longer than the per-line cap.  The
warning names the offending lines with a trimmed suggestion so the fix is
mechanical.  Budgets are env-tunable:

    TASKLOOP_MEMORY_INDEX_MAX_LINES=200   hard: content past this truncates
    TASKLOOP_MEMORY_INDEX_WARN_LINES=180  soft: start archiving now
    TASKLOOP_MEMORY_INDEX_MAX_BYTES=25600
    TASKLOOP_MEMORY_BULLET_MAX_CHARS=200
    TASKLOOP_MEMORY_BUDGET_DISABLE=1      kill switch

Wire it (hooks/install_hooks.py registers it): PostToolUse, "Write|Edit".
"""
import json
import os
import re
import sys
from pathlib import Path

MAX_LINES = int(os.environ.get("TASKLOOP_MEMORY_INDEX_MAX_LINES", "200"))
WARN_LINES = int(os.environ.get("TASKLOOP_MEMORY_INDEX_WARN_LINES", "180"))
MAX_BYTES = int(os.environ.get("TASKLOOP_MEMORY_INDEX_MAX_BYTES", "25600"))
BULLET_MAX = int(os.environ.get("TASKLOOP_MEMORY_BULLET_MAX_CHARS", "200"))
INDEX_NAME = "MEMORY.md"

_BULLET = re.compile(r"^\s*[-*+]\s+")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def trimmed(line: str, cap: int = BULLET_MAX) -> str:
    """Suggested index form of an over-long bullet: keep the link, cut the
    prose after it to fit the cap."""
    m = _LINK.search(line)
    if not m:
        return line[: cap - 1].rstrip() + "…"
    head = line[: m.end()]
    if len(head) >= cap:
        return head
    rest = line[m.end():].strip(" —-:")
    room = cap - len(head) - 4  # ' — ' + '…'
    return head + (" — " + rest[:room].rstrip() + "…" if room > 8 and rest else "")


def check(text: str) -> list:
    """Budget findings for an index body; empty list = healthy."""
    lines = text.splitlines()
    n, size = len(lines), len(text.encode("utf-8"))
    out = []
    if n > MAX_LINES:
        out.append(f"{n} lines > hard cap {MAX_LINES}: lines past the cap are "
                   f"DROPPED at session start — archive settled entries now")
    elif n > WARN_LINES:
        out.append(f"{n} lines (hard cap {MAX_LINES}): start archiving settled "
                   f"entries before the cap silently truncates the tail")
    if size > MAX_BYTES:
        out.append(f"{size} bytes > {MAX_BYTES}: the index is loaded every "
                   f"session — move detail into the memories it points at")
    fat = [(i + 1, ln) for i, ln in enumerate(lines)
           if _BULLET.match(ln) and len(ln) > BULLET_MAX]
    if fat:
        shown = "; ".join(f"L{i} ({len(ln)}c) → `{trimmed(ln)}`"
                          for i, ln in fat[:3])
        more = f" (+{len(fat) - 3} more)" if len(fat) > 3 else ""
        out.append(f"{len(fat)} bullet(s) over {BULLET_MAX} chars — an index "
                   f"line is a pointer, not a summary: {shown}{more}")
    return out


def main():
    if os.environ.get("TASKLOOP_MEMORY_BUDGET_DISABLE") == "1":
        sys.exit(0)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if payload.get("tool_name") not in {"Write", "Edit", "MultiEdit"}:
        sys.exit(0)
    raw = str((payload.get("tool_input") or {}).get("file_path") or "")
    if Path(raw).name != INDEX_NAME:
        sys.exit(0)
    try:
        text = Path(raw).expanduser().read_text(encoding="utf-8",
                                                errors="replace")
    except OSError:
        sys.exit(0)
    findings = check(text)
    if not findings:
        sys.exit(0)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            f"memory-index budget ({Path(raw).name}): " + " | ".join(findings)
            + ". Kill switch: TASKLOOP_MEMORY_BUDGET_DISABLE=1."),
    }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
