#!/usr/bin/env python3
"""Stop hook: spot operations you derived BY HAND repeatedly and nudge
(never edit) toward crystallizing them into a reusable script — via the
queue, so the loop builds the script for you.

The failure this prevents: the third time a session hand-assembles the same
grep/awk/json pipeline, that operation wanted to be a script — but nothing
ever files the task, so every future session re-derives it from scratch
(and each derivation is a fresh chance to get it subtly wrong).

At session Stop this hook reads the transcript, normalizes every Bash
command the agent ran (numbers/strings collapsed), and flags any *derived*
command — a pipeline, heredoc, or long one-liner — that appeared at least
TASKLOOP_CRYSTALLIZE_MIN times (default 3). Each flagged operation gets a
suggested intake with a stable dedupe tag, so filing it twice is impossible:

    task-add "Crystallize this repeated operation into a reusable script
    (with --help): <command>" --repo <repo> --dedupe-tag "[crystallize:<hash>]"

Suggest-only by default. Set TASKLOOP_AUTO_CRYSTALLIZE=1 to auto-file the
top candidate (one per stop, still dedupe-tagged). Idempotent via a ledger
(one nudge per operation, ever); fail-open; exit 0.

Wire it (see hooks/install_hooks.py):
  "Stop": python3 <kit>/hooks/stop_crystallize_nudge.py
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
LEDGER = HOME / ".processed_crystallize.jsonl"
MIN_REPEATS = max(2, int(os.environ.get("TASKLOOP_CRYSTALLIZE_MIN", "3")))
MAX_NUDGES = 3  # per stop — a busy session shouldn't end in a wall of nudges

_STR = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUM = re.compile(r"\b\d+\b")
_WS = re.compile(r"\s+")


def signature(cmd: str) -> str:
    """Normalize a command so near-identical derivations group together:
    quoted strings and bare numbers become placeholders, whitespace folds."""
    s = _STR.sub("'~'", cmd)
    s = _NUM.sub("N", s)
    return _WS.sub(" ", s).strip()[:400]


def is_derived(cmd: str) -> bool:
    """Heuristic for 'hand-assembled operation' (vs a plain tool call):
    pipelines, heredocs, and long one-liners qualify."""
    c = cmd.strip()
    return "|" in c or "<<" in c or len(c) >= 100


def bash_commands(transcript_path: str) -> list:
    """Every Bash tool command in the session transcript (JSONL)."""
    cmds = []
    try:
        with open(transcript_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (isinstance(block, dict)
                            and block.get("type") == "tool_use"
                            and block.get("name") == "Bash"):
                        cmd = (block.get("input") or {}).get("command")
                        if isinstance(cmd, str) and cmd.strip():
                            cmds.append(cmd)
    except OSError:
        pass
    return cmds


def candidates(cmds: list) -> list:
    """Derived commands repeated >= MIN_REPEATS, most-repeated first.
    Returns [(hash12, count, first_raw_command), ...]."""
    groups = {}
    for raw in cmds:
        if not is_derived(raw):
            continue
        sig = signature(raw)
        h = hashlib.sha1(sig.encode()).hexdigest()[:12]
        entry = groups.setdefault(h, [0, raw])
        entry[0] += 1
    return sorted(((h, n, raw) for h, (n, raw) in groups.items()
                   if n >= MIN_REPEATS), key=lambda t: -t[1])


def _seen() -> set:
    out = set()
    try:
        for line in open(LEDGER, encoding="utf-8"):
            try:
                out.add(json.loads(line).get("hash"))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _record(hashes) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            for h in hashes:
                f.write(json.dumps({"hash": h, "ts": int(time.time())}) + "\n")
    except OSError:
        pass


def _goal(raw: str) -> str:
    flat = _WS.sub(" ", raw).replace('"', "'").strip()
    return ("Crystallize this repeated operation into a reusable script "
            f"(with --help): {flat[:120]}")


def _auto_file(goal: str, tag: str, repo: str) -> bool:
    """File the intake directly (TASKLOOP_AUTO_CRYSTALLIZE=1). The dedupe
    tag makes retries no-ops; a failed intake is never a session failure."""
    cmd = [sys.executable, str(KIT / "bin" / "task-add"), goal,
           "--dedupe-tag", tag]
    if repo:
        cmd += ["--repo", repo]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        print(f"🧪 crystallize auto-queued: {out.stdout.strip() or tag}",
              file=sys.stderr)
        return out.returncode == 0
    except Exception:
        return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if payload.get("stop_hook_active"):
        sys.exit(0)
    fresh = [c for c in candidates(bash_commands(
        payload.get("transcript_path") or ""))
        if c[0] not in _seen()][:MAX_NUDGES]
    if not fresh:
        sys.exit(0)
    repo = payload.get("cwd") or ""
    auto = os.environ.get("TASKLOOP_AUTO_CRYSTALLIZE") == "1"
    for i, (h, n, raw) in enumerate(fresh):
        tag = f"[crystallize:{h}]"
        if auto and i == 0:
            _auto_file(_goal(raw), tag, repo)
            continue
        print(
            f"🧪 you derived this by hand {n}× this session — make it a "
            f"script the loop can build:\n"
            f"   task-add \"{_goal(raw)}\" --repo {repo or '<repo>'} "
            f"--dedupe-tag \"{tag}\"",
            file=sys.stderr,
        )
    _record(h for h, _, _ in fresh)
    sys.exit(0)


if __name__ == "__main__":
    main()
