#!/usr/bin/env python3
"""Stop hook: spot reusable scripts you wrote WITHOUT observable telemetry and
nudge (never edit) toward adding a small fire/usage log — via the queue, so the
loop builds the instrumentation for you.

The failure this prevents: you write a tool the loop (and future sessions) will
run again and again, but it emits nothing about when it fired, on what, or
whether it worked. The first time it misbehaves in the background there is
nothing to read — no trace, no counts, no way to tell a silent no-op from a
healthy quiet run. A one-line JSONL fire/usage log written from birth turns
every later debug, trace, and cost question into a `tail`.

At session Stop this hook reads the transcript and flags any *reusable script*
the session wrote — a code file big enough to matter (TASKLOOP_TELEMETRY_MIN_CHARS,
default 800) that carries a CLI/entrypoint signal (argparse, def main, __main__,
sys.argv, click) — whose written content shows NO telemetry evidence (no
fire/usage log, no logging, no .jsonl sink). Each flagged script gets a
suggested intake with a stable dedupe tag, so filing it twice is impossible:

    task-add "Add fire/usage telemetry (a small env-overridable JSONL log) to
    this reusable script: <name>" --repo <repo> --dedupe-tag "[telemetry:<hash>]"

Deliberately conservative — it only reads what the session actually wrote (no
arbitrary source-file reads), and only flags files that both look like a tool
and lack any telemetry marker. Plain modules, config, tests, and already-logged
scripts are left alone, so the nudge stays rare and trustworthy.

Suggest-only by default. Set TASKLOOP_AUTO_TELEMETRY=1 to auto-file the top
candidate (one per stop, still dedupe-tagged). Idempotent via a ledger (one
nudge per script path, ever); fail-open; exit 0.

Wire it (see hooks/install_hooks.py):
  "Stop": python3 <kit>/hooks/stop_telemetry_nudge.py
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
LEDGER = HOME / ".processed_telemetry.jsonl"
MIN_CHARS = max(200, int(os.environ.get("TASKLOOP_TELEMETRY_MIN_CHARS", "800")))
MAX_NUDGES = 3  # per stop — a busy session shouldn't end in a wall of nudges

# Extensions of things worth instrumenting (runnable code, not data/docs).
CODE_EXT = {".py", ".sh", ".js", ".ts", ".go", ".rs", ".rb", ".pl", ".bash"}

# Paths that carry no telemetry debt: tests, examples, and this kit's own dirs.
_EXEMPT_SUBSTR = ("/test", "test_", "/tests/", "/examples/", "/docs/",
                  "/.git/", "conftest.py")

# A file "looks like a reusable tool" — something meant to be RUN again — when
# it carries any of these entrypoint signals.
_TOOL_SIGNAL_RE = re.compile(
    r"import\s+argparse|argparse\.|def\s+main\s*\(|__name__\s*==|sys\.argv|"
    r"click\.command|@app\.command|getopts|def\s+run\s*\(",
    re.I)

# Observable telemetry evidence already present in the written source.
_TELEMETRY_RE = re.compile(
    r"telemetry|\b(?:fire|fires|usage|event|metric)s?[_-]?(?:log|ledger|event|count)?\b|"
    r"\.jsonl\b|\blog(?:ging|ger)?\b|\bemit\b|\bcounter\b|statsd|prometheus",
    re.I)


def written_scripts(transcript_path: str) -> dict:
    """Every Write/Edit target in the session transcript -> concatenated
    written content (JSONL). Later writes to the same path accumulate, so a
    file instrumented in a second edit is correctly seen as instrumented."""
    scripts: dict = {}
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
                    if (not isinstance(block, dict)
                            or block.get("type") != "tool_use"
                            or block.get("name") not in ("Write", "Edit")):
                        continue
                    inp = block.get("input") or {}
                    fp = inp.get("file_path")
                    if not isinstance(fp, str) or not fp:
                        continue
                    text = (inp.get("content") or "") + (inp.get("new_string") or "")
                    scripts[fp] = scripts.get(fp, "") + text
    except OSError:
        pass
    return scripts


def is_exempt(path: str) -> bool:
    return any(s in path for s in _EXEMPT_SUBSTR)


def candidates(scripts: dict) -> list:
    """Reusable scripts written this session that lack telemetry, biggest
    first. Returns [(hash12, name, path), ...]."""
    flagged = []
    for path, text in scripts.items():
        ext = os.path.splitext(path)[1].lower()
        if ext not in CODE_EXT or is_exempt(path):
            continue
        if len(text) < MIN_CHARS:
            continue
        if not _TOOL_SIGNAL_RE.search(text):
            continue
        if _TELEMETRY_RE.search(text):
            continue
        h = hashlib.sha1(path.encode()).hexdigest()[:12]
        flagged.append((len(text), h, Path(path).name, path))
    flagged.sort(key=lambda t: -t[0])
    return [(h, name, path) for _size, h, name, path in flagged]


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


def _goal(name: str) -> str:
    return ("Add fire/usage telemetry (a small env-overridable JSONL log) to "
            f"this reusable script: {name}")


def _auto_file(goal: str, tag: str, repo: str) -> bool:
    """File the intake directly (TASKLOOP_AUTO_TELEMETRY=1). The dedupe tag
    makes retries no-ops; a failed intake is never a session failure."""
    cmd = [sys.executable, str(KIT / "bin" / "task-add"), goal,
           "--dedupe-tag", tag]
    if repo:
        cmd += ["--repo", repo]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        print(f"📈 telemetry auto-queued: {out.stdout.strip() or tag}",
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
    fresh = [c for c in candidates(written_scripts(
        payload.get("transcript_path") or ""))
        if c[0] not in _seen()][:MAX_NUDGES]
    if not fresh:
        sys.exit(0)
    repo = payload.get("cwd") or ""
    auto = os.environ.get("TASKLOOP_AUTO_TELEMETRY") == "1"
    for i, (h, name, _path) in enumerate(fresh):
        tag = f"[telemetry:{h}]"
        if auto and i == 0:
            _auto_file(_goal(name), tag, repo)
            continue
        print(
            f"📈 you shipped a reusable script ({name}) with no observable "
            f"telemetry — instrument it so future runs are traceable:\n"
            f"   task-add \"{_goal(name)}\" --repo {repo or '<repo>'} "
            f"--dedupe-tag \"{tag}\"",
            file=sys.stderr,
        )
    _record(h for h, _, _ in fresh)
    sys.exit(0)


if __name__ == "__main__":
    main()
