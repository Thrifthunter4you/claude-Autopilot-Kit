#!/usr/bin/env python3
"""Register the kit's hooks for ONE project — idempotent, backed up, explicit.

    python3 hooks/install_hooks.py --project ~/dev/myapp             # install/refresh
    python3 hooks/install_hooks.py --project ~/dev/myapp --dry-run   # show the change
    python3 hooks/install_hooks.py --project ~/dev/myapp --remove    # uninstall
    python3 hooks/install_hooks.py --remove-global                   # migrate off <=v0.2

Hooks are written to ``<project>/.claude/settings.local.json`` — the file
Claude Code reads only for sessions started inside that project, and which
it keeps out of version control.  So the kit's hooks are a per-project
opt-in: the prompt classifier that auto-queues clear build asks, the
stop-time nudges and the session briefing fire where you asked for them and
nowhere else.  Earlier kit versions wrote ``~/.claude/settings.json`` and
thereby armed every session on the machine; ``--remove-global`` strips only
the kit's own entries from that file so an upgrade never edits your other
hooks.  ``--global`` still exists for people who want machine-wide hooks and
say so — it prints what that implies.

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

# (event, tool matcher, command) — order preserved on install.  A matcher
# narrows the tools an event fires for: the toolbox nudge only ever inspects
# shell commands, so it is not invoked for Read/Edit/etc.  The turn-budget
# hook names the working tools rather than "*": it reads its count from the
# subagent's transcript, so it need not see every call, and the answer tool
# (StructuredOutput) is never matched, so a ceiling can never block it.
# The context-ceiling reminder shares the list for the same reasons.
WORK_TOOLS = ("Bash|Read|Grep|Glob|Edit|Write|MultiEdit|NotebookEdit|"
              "WebFetch|WebSearch|mcp__.*")
KIT_HOOKS = [
    ("UserPromptSubmit", "*", f"python3 {KIT}/hooks/userprompt_autopilot_detect.py"),
    ("SessionStart", "*", f"python3 {KIT}/hooks/session_start_context.py"),
    ("PreToolUse", "Bash", f"python3 {KIT}/hooks/pretool_toolbox_nudge.py"),
    ("PreToolUse", "Read", f"python3 {KIT}/hooks/pretool_spill_read_guard.py"),
    ("PreToolUse", WORK_TOOLS, f"python3 {KIT}/hooks/pretool_subagent_turn_budget.py"),
    ("PreToolUse", WORK_TOOLS, f"python3 {KIT}/hooks/pretool_context_ceiling.py"),
    ("PostToolUse", "Write|Edit|MultiEdit", f"python3 {KIT}/hooks/posttool_memory_budget.py"),
    ("PostToolUse", "Bash|mcp__.*", f"python3 {KIT}/hooks/posttool_output_spill.py"),
    ("PreCompact", "*", f"python3 {KIT}/hooks/precompact_ledger.py"),
    ("Stop", "*", f"python3 {KIT}/hooks/stop_queue_nudge.py"),
    ("Stop", "*", f"python3 {KIT}/hooks/stop_crystallize_nudge.py"),
    ("Stop", "*", f"python3 {KIT}/hooks/stop_telemetry_nudge.py"),
]


def project_settings(project: Path) -> Path:
    return project / ".claude" / "settings.local.json"


def global_settings() -> Path:
    """The legacy (<=v0.2) machine-wide target; honours CLAUDE_DIR."""
    return Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude")) / "settings.json"


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        print(f"ERROR: {path} is not valid JSON — fix it first, "
              "nothing was changed.", file=sys.stderr)
        sys.exit(1)
    return data if isinstance(data, dict) else {}


def _is_kit_cmd(cmd: str) -> bool:
    return str(KIT) in (cmd or "")


def _entry(cmd: str, matcher: str) -> dict:
    return {"matcher": matcher,
            "hooks": [{"type": "command", "command": cmd}]}


def apply(settings: dict, remove: bool = False) -> dict:
    """Drop every kit-owned group, then (unless removing) re-add the current
    set.  Groups that contain any non-kit command are never touched."""
    hooks = settings.setdefault("hooks", {})
    events = {ev for ev, _, _ in KIT_HOOKS} | set(hooks)
    for event in sorted(events):
        groups = [g for g in hooks.get(event, [])
                  if not any(_is_kit_cmd(h.get("command", ""))
                             for h in g.get("hooks", []))]
        if not remove:
            groups.extend(_entry(cmd, matcher)
                          for ev, matcher, cmd in KIT_HOOKS if ev == event)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    return settings


def write(path: Path, settings: dict) -> None:
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_suffix(f".json.bak-{stamp}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")


def run(path: Path, remove: bool, dry_run: bool) -> int:
    settings = load(path)
    before = json.dumps(settings, indent=2, sort_keys=True)
    settings = apply(settings, remove=remove)
    after = json.dumps(settings, indent=2, sort_keys=True)
    if before == after:
        print(f"{path} already up to date — nothing to do")
        return 0
    if dry_run:
        print("would update", path)
        for event, matcher, cmd in KIT_HOOKS:
            print(f"  {'-' if remove else '+'} {event}[{matcher}]: {cmd}")
        return 0
    write(path, settings)
    verb = "removed from" if remove else "installed into"
    print(f"kit hooks {verb} {path} (backup written)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Install the kit's Claude Code hooks for ONE project "
                    "(<project>/.claude/settings.local.json).")
    ap.add_argument("--project", type=Path,
                    help="repository to enable the hooks in (required "
                         "unless --global or --remove-global)")
    ap.add_argument("--global", dest="global_", action="store_true",
                    help="machine-wide instead: ~/.claude/settings.json — "
                         "every session on this machine gets the prompt "
                         "classifier and stop nudges")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--remove-global", action="store_true",
                    help="upgrade helper: strip the kit's entries from the "
                         "legacy ~/.claude/settings.json and install nothing")
    args = ap.parse_args(argv)

    if args.remove_global:
        return run(global_settings(), remove=True, dry_run=args.dry_run)
    if args.global_:
        print("NOTE: --global arms the kit's hooks for EVERY Claude Code "
              "session on this machine; a clear build ask typed anywhere "
              "will be queued for the background loop.", file=sys.stderr)
        return run(global_settings(), remove=args.remove, dry_run=args.dry_run)
    if args.project is None:
        ap.error("--project PATH is required (or --global / --remove-global)")
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        ap.error(f"--project {project} is not a directory")
    return run(project_settings(project), remove=args.remove,
               dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
