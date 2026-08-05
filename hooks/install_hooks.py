#!/usr/bin/env python3
"""Register the kit's hooks in ~/.claude/settings.json — idempotent, with a
timestamped backup before any write.

    python3 hooks/install_hooks.py            # install/refresh all kit hooks
    python3 hooks/install_hooks.py --dry-run  # show what would change
    python3 hooks/install_hooks.py --remove   # remove kit hooks

Only entries whose command lives inside THIS kit directory are ever touched;
your other hooks are left exactly as they are.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SETTINGS = Path(os.environ.get("CLAUDE_DIR",
                               Path.home() / ".claude")) / "settings.json"

# event -> list of hook commands (order preserved on install)
KIT_HOOKS = {
    "UserPromptSubmit": [
        f"python3 {KIT}/hooks/userprompt_autopilot_detect.py",
    ],
    "SessionStart": [
        f"python3 {KIT}/hooks/session_start_context.py",
    ],
    "PreToolUse": [
        f"python3 {KIT}/hooks/pretool_toolbox_nudge.py",
    ],
    "Stop": [
        f"python3 {KIT}/hooks/stop_queue_nudge.py",
        f"python3 {KIT}/hooks/stop_crystallize_nudge.py",
        f"python3 {KIT}/hooks/stop_telemetry_nudge.py",
    ],
}

# Tool-name matcher per event; "*" (every tool) unless narrowed here. The
# toolbox nudge only ever inspects shell commands, so don't invoke it for
# Read/Edit/etc.
EVENT_MATCHER = {"PreToolUse": "Bash"}


def _load() -> dict:
    if not SETTINGS.exists():
        return {}
    try:
        return json.loads(SETTINGS.read_text())
    except json.JSONDecodeError:
        print(f"ERROR: {SETTINGS} is not valid JSON — fix it first, "
              "nothing was changed.", file=sys.stderr)
        sys.exit(1)


def _is_kit_cmd(cmd: str) -> bool:
    return str(KIT) in (cmd or "")


def _entry(cmd: str, matcher: str = "*") -> dict:
    return {"matcher": matcher,
            "hooks": [{"type": "command", "command": cmd}]}


def apply(settings: dict, remove: bool = False) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event, cmds in KIT_HOOKS.items():
        groups = [g for g in hooks.get(event, [])
                  if not any(_is_kit_cmd(h.get("command", ""))
                             for h in g.get("hooks", []))]
        if not remove:
            groups.extend(_entry(c, EVENT_MATCHER.get(event, "*"))
                          for c in cmds)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    return settings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args(argv)

    settings = _load()
    before = json.dumps(settings, indent=2, sort_keys=True)
    settings = apply(settings, remove=args.remove)
    after = json.dumps(settings, indent=2, sort_keys=True)

    if before == after:
        print("settings.json already up to date — nothing to do")
        return 0
    if args.dry_run:
        print("would update", SETTINGS)
        for event, cmds in KIT_HOOKS.items():
            for c in cmds:
                mark = "-" if args.remove else "+"
                print(f"  {mark} {event}: {c}")
        return 0

    if SETTINGS.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(SETTINGS, SETTINGS.with_suffix(f".json.bak-{stamp}"))
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    verb = "removed from" if args.remove else "installed into"
    print(f"kit hooks {verb} {SETTINGS} (backup written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
