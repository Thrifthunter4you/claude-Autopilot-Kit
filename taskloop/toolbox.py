"""The toolbox: a deterministic registry of the reusable scripts you keep.

The crystallize nudge (hooks/stop_crystallize_nudge.py) notices when a session
derives the same operation by hand again and again and queues a task to build
a script for it. This module is where that script LANDS — and what makes
building it worth the trouble:

  - **findable**    `toolbox find "restart the api"` — keyword-scored lookup,
                    so the next session reaches for the script instead of
                    re-deriving the operation from scratch.
  - **describable** `toolbox describe db-health` — the arg contract and output
                    contract, written once at registration.
  - **runnable**    `toolbox run db-health --verbose` — one canonical
                    invocation; no path-remembering, no flag archaeology.
  - **observable**  every run appends one JSONL fire record (tool, timestamp,
                    exit, duration), so "is anything actually using this?" is
                    a `toolbox stats` away — telemetry from birth, the same
                    principle hooks/stop_telemetry_nudge.py enforces.
  - **defended**    an entry may declare a `territory` regex — commands it
                    owns. The PreToolUse hook (hooks/pretool_toolbox_nudge.py)
                    then nudges (or, opt-in, blocks) hand-derived equivalents
                    and points at the registered tool instead.

Registry file:  ~/.taskloop/toolbox.yaml   (version-controllable, hand-editable)
Fire ledger:    ~/.taskloop/toolbox_fires.jsonl
Nudge ledger:   ~/.taskloop/toolbox_nudges.jsonl  (written by the hook)

Entry schema (see toolbox.example.yaml at the repo root for a walkthrough):

  tools:
    - name: db-health                    # unique, kebab-case
      description: one line, plain language — this is what `find` scores
      cmd: [python3, /home/you/scripts/db_health.py]   # argv prefix
      args: "[--verbose] [TABLE...]"     # contract prose shown by describe
      output: "one OK/FAIL line per table"
      territory: "pg_isready|psql\\s.*-c"  # optional: commands this tool owns
      unless: "--help"                   # optional exemption regex
      enforce: false                     # advisory nudge (default) vs block

    - name: morning-check                # a PLAYBOOK: steps, not a cmd
      description: queue snapshot + db health in one shot
      playbook:
        vars: {table: all}
        steps:
          - "task-list"
          - "toolbox run db-health {{table}}"

Concurrency: registry mutations serialize on an fcntl lock file and re-read
AFTER acquiring it, so two sessions registering tools at once can't tear the
YAML. Reads are lock-free (worst case: a reader sees the pre-write registry).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from . import config

REGISTRY = config.HOME / "toolbox.yaml"
LOCK = config.HOME / "toolbox.yaml.lock"
FIRES = config.HOME / "toolbox_fires.jsonl"
NUDGES = config.HOME / "toolbox_nudges.jsonl"

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


# --- Registry I/O ------------------------------------------------------------

def load() -> dict:
    """Read the registry; a missing or unparsable file is an empty registry
    (fail-open — a corrupt YAML must never break `run` for scripts you can
    still invoke directly)."""
    import yaml
    try:
        data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"tools": []}
    tools = data.get("tools")
    return {"tools": tools if isinstance(tools, list) else []}


def mutate(fn) -> dict:
    """Locked read-modify-write: acquire the lock, RE-READ (another writer may
    have landed while we waited), apply ``fn(registry)``, write atomically."""
    import yaml
    config.HOME.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        reg = load()
        fn(reg)
        tmp = REGISTRY.with_suffix(".yaml.tmp")
        tmp.write_text(
            yaml.safe_dump(reg, sort_keys=False, allow_unicode=True),
            encoding="utf-8")
        os.replace(tmp, REGISTRY)
        return reg


def get(name: str) -> dict | None:
    for entry in load()["tools"]:
        if entry.get("name") == name:
            return entry
    return None


# --- Validation --------------------------------------------------------------

def validate(entry: dict, existing_names=()) -> list:
    """Problems that would make an entry unusable or unsafe to register.
    Returns [] when the entry is sound."""
    problems = []
    name = entry.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name or ""):
        problems.append("name: required, lowercase [a-z0-9._-], starts "
                        "alphanumeric")
    elif name in existing_names:
        problems.append(f"name: '{name}' already registered "
                        "(remove it first, or pick another)")
    if not isinstance(entry.get("description"), str) or \
            not entry.get("description", "").strip():
        problems.append("description: required — it is what `find` scores")
    has_cmd = bool(isinstance(entry.get("cmd"), list) and entry["cmd"])
    has_playbook = isinstance(entry.get("playbook"), dict)
    if has_cmd == has_playbook:
        problems.append("exactly one of cmd (argv list) or playbook required")
    if has_playbook:
        steps = entry["playbook"].get("steps")
        if not isinstance(steps, list) or not steps or not all(
                isinstance(s, str) and s.strip() for s in steps):
            problems.append("playbook.steps: non-empty list of command strings")
    for field in ("territory", "unless"):
        pattern = entry.get(field)
        if pattern is not None:
            try:
                re.compile(pattern)
            except (re.error, TypeError):
                problems.append(f"{field}: not a valid regex")
    return problems


# --- Lookup ------------------------------------------------------------------

def find(query: str) -> list:
    """Keyword-scored lookup: name hits weigh 3x, description 1x, contract
    prose 1x. Returns [(score, entry), ...] best-first; empty on no overlap."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w]
    scored = []
    for entry in load()["tools"]:
        name = str(entry.get("name", "")).lower()
        desc = str(entry.get("description", "")).lower()
        prose = (str(entry.get("args", "")) + " " +
                 str(entry.get("output", ""))).lower()
        score = sum(3 for w in words if w in name)
        score += sum(1 for w in words if w in desc)
        score += sum(1 for w in words if w in prose)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda t: (-t[0], t[1].get("name", "")))
    return scored


# --- Running -----------------------------------------------------------------

def _interpolate(step: str, variables: dict) -> str:
    """Replace every ``{{var}}``; an unknown variable is a hard error (a
    silently-empty substitution would run a DIFFERENT command)."""
    def sub(m):
        key = m.group(1)
        if key not in variables:
            raise KeyError(f"playbook step references unknown var "
                           f"'{{{{{key}}}}}' — pass {key}=VALUE")
        return str(variables[key])
    return _VAR_RE.sub(sub, step)


def _record_fire(name: str, ok: bool, exit_code: int, ms: int) -> None:
    try:
        config.HOME.mkdir(parents=True, exist_ok=True)
        with open(FIRES, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": name, "ts": int(time.time()),
                                "ok": ok, "exit": exit_code, "ms": ms}) + "\n")
    except OSError:
        pass  # telemetry must never fail a run


def run(name: str, extra_args=(), var_overrides=None) -> int:
    """Run a registered tool by name; returns its exit code (127 = unknown
    tool, 2 = bad playbook vars). Every attempt lands one fire record.

    cmd entries exec ``cmd + extra_args`` directly (argv, no shell — nothing
    to quote-escape). Playbooks run each interpolated step via shlex.split,
    also shell-less; a failing step stops the playbook and returns its code.
    """
    entry = get(name)
    if entry is None:
        return 127
    started = time.monotonic()
    code = 0
    try:
        if entry.get("cmd"):
            code = subprocess.run(
                [str(a) for a in entry["cmd"]] + [str(a) for a in extra_args],
            ).returncode
        else:
            playbook = entry.get("playbook") or {}
            variables = dict(playbook.get("vars") or {})
            variables.update(var_overrides or {})
            for step in playbook.get("steps") or []:
                code = subprocess.run(
                    shlex.split(_interpolate(step, variables))).returncode
                if code != 0:
                    break
    except KeyError as exc:                       # unknown {{var}}
        print(f"toolbox: {exc}", flush=True)
        code = 2
    except (OSError, ValueError) as exc:          # missing binary, bad split
        print(f"toolbox: {name}: {exc}", flush=True)
        code = 126
    _record_fire(name, code == 0, code,
                 int((time.monotonic() - started) * 1000))
    return code


# --- Stats -------------------------------------------------------------------

def _ledger(path: Path) -> list:
    rows = []
    try:
        for line in open(path, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return rows


def stats() -> dict:
    """Per-tool usage: fire counts + success rate from the fire ledger, nudge
    counts (advisory/blocked) from the hook's ledger. Registered tools with
    zero fires are listed too — a tool nobody runs is the finding."""
    out = {e["name"]: {"fires": 0, "ok": 0, "last": 0,
                       "advisory_nudges": 0, "blocked": 0}
           for e in load()["tools"] if e.get("name")}
    for row in _ledger(FIRES):
        s = out.setdefault(str(row.get("tool")),
                           {"fires": 0, "ok": 0, "last": 0,
                            "advisory_nudges": 0, "blocked": 0})
        s["fires"] += 1
        s["ok"] += 1 if row.get("ok") else 0
        s["last"] = max(s["last"], int(row.get("ts", 0)))
    for row in _ledger(NUDGES):
        s = out.get(str(row.get("tool")))
        if s is None:
            continue
        if row.get("mode") == "deny":
            s["blocked"] += 1
        else:
            s["advisory_nudges"] += 1
    return out
