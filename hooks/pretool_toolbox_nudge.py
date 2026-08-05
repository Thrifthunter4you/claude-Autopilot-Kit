#!/usr/bin/env python3
"""PreToolUse hook: when a Bash command re-derives what a REGISTERED tool
already does, point the session at `toolbox run <name>` instead.

The failure this prevents: you crystallized a repeated operation into a
script (see stop_crystallize_nudge.py), registered it — and the next session
hand-derives the same pipeline anyway, because nothing at the moment of
re-derivation says "this already exists". Advisory text in a README doesn't
fire at that moment; a PreToolUse hook does.

How it works: each registry entry may declare a `territory` — a regex of
shell commands the tool OWNS (curate these by hand; auto-derived patterns
are noisy). When a Bash command matches a territory (and not its `unless`
exemption):

  - **advisory** (default): the command runs, but the model receives context
    naming the registered tool — once per (session, tool), so a busy session
    is never nagged twice about the same thing.
  - **enforce: true** (opt-in, per entry): the command is DENIED with an
    instruction to use `toolbox run <name>`. Denies fire every time — that
    is the point. Reserve enforcement for tools where hand-derivation has
    real cost; escalate an entry only after its advisory nudges are being
    ignored (`toolbox stats` shows exactly that).

Escapes, weakest to strongest:
  - put TOOLBOX_NUDGE_OVERRIDE anywhere in the one command that genuinely
    needs to bypass (e.g. grepping prose that happens to match a territory)
  - TOOLBOX_NUDGE_ENFORCE_DISABLE=1  — demote every deny to advisory
  - TOOLBOX_NUDGE_DISABLE=1          — hook off entirely

Every decision (advisory, deny, override) appends one row to
~/.taskloop/toolbox_nudges.jsonl, so `toolbox stats` can show nudge counts
next to fire counts — whether the nudge is WORKING is measurable, not vibes.
Fail-open; never blocks on error; exit 0 always.

Wire it (see hooks/install_hooks.py):
  "PreToolUse": python3 <kit>/hooks/pretool_toolbox_nudge.py
"""
import json
import os
import re
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

HOME = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
LEDGER = HOME / "toolbox_nudges.jsonl"
SEEN_DIR = HOME / ".toolbox_nudge_seen"
OVERRIDE_MARKER = "TOOLBOX_NUDGE_OVERRIDE"

# Never match the toolbox's own invocations — `toolbox run db-health` must not
# trip db-health's territory.
_SELF_RE = re.compile(r"(^|[\s/;&|])toolbox([\s;&|]|$)")


def _log(session: str, tool: str, mode: str, command: str) -> None:
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "session": session,
                                "tool": tool, "mode": mode,
                                "cmd": command[:160]}) + "\n")
    except OSError:
        pass


def _already_nudged(session: str, tool: str) -> bool:
    """Advisory throttle: one nudge per (session, tool), via sentinel files.
    Denies never consult this — they must fire every time."""
    sentinel = SEEN_DIR / f"{(session or 'nosession')[:8]}-{tool}"
    if sentinel.exists():
        return True
    try:
        SEEN_DIR.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
    except OSError:
        pass
    return False


def matches(command: str, tools: list):
    """(entry, enforced) for the winning territory match, else None. An
    enforced match beats any advisory one — a block you configured must not
    be shadowed by a politer entry earlier in the registry."""
    advisory = None
    for entry in tools:
        pattern = entry.get("territory")
        name = entry.get("name")
        if not pattern or not name:
            continue
        try:
            if not re.search(pattern, command, re.I):
                continue
            unless = entry.get("unless")
            if unless and re.search(unless, command, re.I):
                continue
        except re.error:
            continue
        if entry.get("enforce"):
            return entry, True
        advisory = advisory or entry
    return (advisory, False) if advisory else None


def main():
    if os.environ.get("TOOLBOX_NUDGE_DISABLE") == "1":
        sys.exit(0)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command or _SELF_RE.search(command):
        sys.exit(0)

    try:
        from taskloop import toolbox
        tools = toolbox.load()["tools"]
    except Exception:
        sys.exit(0)

    hit = matches(command, tools)
    if hit is None:
        sys.exit(0)
    entry, enforced = hit
    name = entry["name"]
    session = str(payload.get("session_id") or "")

    if OVERRIDE_MARKER in command:
        _log(session, name, "override", command)
        sys.exit(0)

    if enforced and os.environ.get("TOOLBOX_NUDGE_ENFORCE_DISABLE") != "1":
        _log(session, name, "deny", command)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"this operation is owned by the registered tool "
                f"'{name}' — run `toolbox run {name} ...` instead "
                f"(contract: `toolbox describe {name}`). If the registered "
                f"tool genuinely cannot do this specific job, re-run with "
                f"the marker {OVERRIDE_MARKER} in the command."),
        }}))
        sys.exit(0)

    if _already_nudged(session, name):
        sys.exit(0)
    _log(session, name, "advisory", command)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": (
            f"a registered tool owns this territory: `toolbox run {name}` "
            f"— {entry.get('description', '')} "
            f"(contract: `toolbox describe {name}`). Prefer it over the "
            f"hand-derived command. ≤1 nudge per tool per session; "
            f"kill switch: TOOLBOX_NUDGE_DISABLE=1."),
    }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
