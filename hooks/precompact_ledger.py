#!/usr/bin/env python3
"""PreCompact hook: write a continuity ledger before compaction, and tell
the summarizer what it must keep.

Compaction replaces the conversation with a model-written summary.  What a
summary drops most often is what the next turn needs first: which file was
mid-edit, the command about to run, the check still owed, the commit
hashes, the decisions already argued out.  Just before compaction this
hook does two things:

1. Writes a mechanical ledger — recent user messages (whole session);
   files edited and read, commits and tool errors since the previous
   compaction; git branch and status of the session's directory — to
   ``$TASKLOOP_HOME/ledgers/compact_<utc>_<session8>.md``.  Nothing in it
   comes from the model, so it is there to read back when the summary
   missed something.
2. Prints a preservation directive on stdout.  Claude Code passes a
   PreCompact hook's stdout to the summarizer as extra instructions; the
   directive lists what the summary must keep and names the ledger file.
   (Plain text on purpose: a ``hookSpecificOutput`` object naming
   ``PreCompact`` fails the harness's output schema and the whole output
   is dropped — see docs/harness-gotchas.md.)

Compaction number.  After each compaction that LANDS, the harness appends a
``{"type": "system", "subtype": "compact_boundary"}`` record to the
transcript.  The hook counts those, plus one for the compaction about to
run.  It never counts its own fires: a fire is not a compaction (a failed
request fires the hook and compacts nothing).  From the second compaction
on (TASKLOOP_COMPACT_ESCALATE_AT) the directive adds a wrap-up clause —
finish the in-flight edit, commit, leave a handoff, start nothing new — as
a session on its second summary is working from a summary of a summary.

Subagents (payload carries ``agent_id``) get the ledger and the
preservation directive but never the wrap-up clause (a subagent cannot
hand off), and never replace the pointer the next session start reads.

Next session.  For the main session the hook also writes
``ledgers/latest.json`` (ledger path, session, directory, time);
``session_start_context.py`` turns it into a one-line pointer after a
compaction, or for a new session started in the same directory within the
hour.

Configuration:
  TASKLOOP_COMPACT_ESCALATE_AT=2        compaction number that adds wrap-up
  TASKLOOP_LEDGER_RETENTION_DAYS=30     older ledgers are pruned
  TASKLOOP_PRECOMPACT_DISABLE=1         hook off

Recorded via hook_log, so ``hook-cost`` shows compactions per session.
Exits 0 on any error: a PreCompact hook must never stop a compaction.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import hook_log
except Exception:  # pragma: no cover - logging is optional
    hook_log = None

USER_MSGS = 10
USER_MSG_CHARS = 280
LIST_MAX = 15
ERRORS_MAX = 8
ERROR_CHARS = 200
WINDOW_MAX_RECORDS = 4000      # records since the last compaction, newest kept
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Harness-injected blocks inside user records: not something a person typed.
HARNESS_TAG = re.compile(
    r"<(system-reminder|local-command-[a-z]+|command-[a-z]+|task-notification|"
    r"user-prompt-submit-hook)>.*?</\1>", re.DOTALL)
# What `git commit` prints on success: "[main 1a2b3c4] message" (also
# "[main (root-commit) …]" and "[detached HEAD …]").
COMMIT_LINE = re.compile(r"^\[([^\]\n]+?) ([0-9a-f]{7,40})\] (.{0,120})", re.MULTILINE)


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def ledger_dir() -> Path:
    home = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
    return home / "ledgers"


def _one_line(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_result":
            parts.append(_text_of(block.get("content")))
    return "\n".join(parts)


def _typed_text(record: dict) -> str:
    """What a person typed in this user record, or '' for tool results,
    harness-injected blocks and compaction summaries."""
    if record.get("isMeta") or record.get("isCompactSummary"):
        return ""
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "\n".join(str(b.get("text") or "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return ""
    text = HARNESS_TAG.sub("", content).strip()
    return text if len(text) >= 4 else ""


def read_records(path: Path) -> list:
    records = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                records.append(row)
    return records


def summarize(records: list) -> dict:
    boundaries = [i for i, r in enumerate(records)
                  if r.get("type") == "system" and r.get("subtype") == "compact_boundary"]
    start = boundaries[-1] + 1 if boundaries else 0
    window = records[start:][-WINDOW_MAX_RECORDS:]

    user_msgs = [_one_line(t, USER_MSG_CHARS) for r in records
                 if r.get("type") == "user" and (t := _typed_text(r))]
    edited, read, tools = Counter(), Counter(), Counter()
    bash_ids, commits, errors = set(), [], []
    for r in window:
        content = (r.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tools[name] += 1
                inp = block.get("input") or {}
                path = inp.get("file_path") or inp.get("notebook_path")
                if name in EDIT_TOOLS and path:
                    edited[path] += 1
                elif name == "Read" and path:
                    read[path] += 1
                elif name == "Bash":
                    bash_ids.add(block.get("id"))
            elif block.get("type") == "tool_result":
                text = _text_of(block.get("content"))
                if block.get("tool_use_id") in bash_ids:
                    # Error results too: `git commit … && git push` fails
                    # after the commit has landed.
                    commits += [f"{sha} ({branch}) {msg.strip()}"
                                for branch, sha, msg in COMMIT_LINE.findall(text)]
                if block.get("is_error") and text:
                    errors.append(_one_line(text, ERROR_CHARS))
    return {"records": len(records), "window": len(window),
            "landed": len(boundaries), "user_msgs": user_msgs[-USER_MSGS:],
            "edited": edited, "read": read, "tools": tools,
            "commits": commits, "errors": errors[-ERRORS_MAX:]}


def own_transcript(payload: dict) -> Path | None:
    """The transcript being compacted.  For a subagent, prefer its own file
    beside the parent's when the payload names the parent's."""
    path = str(payload.get("transcript_path") or "")
    agent_id = str(payload.get("agent_id") or "")
    if agent_id and path and not Path(path).name.startswith("agent-"):
        session_dir = os.path.splitext(path)[0]
        hits = [os.path.join(session_dir, "subagents", f"agent-{agent_id}.jsonl")]
        hits += glob.glob(os.path.join(session_dir, "subagents", "workflows", "*",
                                       f"agent-{agent_id}.jsonl"))
        found = [h for h in hits if os.path.isfile(h)]
        # No file of its own: no ledger beats summarizing the parent as the subagent.
        return Path(found[0]) if found else None
    return Path(path) if path and os.path.isfile(path) else None


def git_snapshot(cwd: str) -> tuple:
    # Drop GIT_* from the environment: a leaked GIT_DIR would make this
    # report some other repository's state as the session's.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def run(*args):
        try:
            out = subprocess.run(["git", *args], cwd=cwd, env=env, timeout=3,
                                 capture_output=True, text=True)
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    branch = run("branch", "--show-current")
    status = run("status", "--short")
    if status is None:
        return "-", "(not a git repository)"
    lines = status.splitlines()
    more = f"\n… {len(lines) - LIST_MAX} more" if len(lines) > LIST_MAX else ""
    return branch or "(detached)", "\n".join(lines[:LIST_MAX]) + more or "(clean)"


def _counted(counter: Counter) -> list:
    return [f"- `{p}`" + (f" ×{n}" if n > 1 else "")
            for p, n in counter.most_common(LIST_MAX)] or ["_none_"]


def render(s: dict | None, meta: dict) -> str:
    scope = "subagent transcript" if meta["agent"] else "session"
    out = [f"# Continuity ledger — {meta['ts']}", "",
           f"- session: `{meta['session'] or '?'}`"
           + (f" · subagent `{meta['agent']}`" if meta["agent"] else ""),
           f"- directory: `{meta['cwd']}` · branch `{meta['branch']}`",
           f"- compaction #{meta['n']} of this {scope} (trigger: {meta['trigger']})"]
    if s is None:
        out += ["", "_No transcript found; nothing summarized._"]
    else:
        tools = ", ".join(f"{n}×{c}" for n, c in s["tools"].most_common(8))
        out += [f"- summarized {s['window']} records since the last compaction "
                f"({s['records']} in the transcript)"
                + (f"; tools: {tools}" if tools else ""),
                "", "## Recent user messages",
                *([f"- {m}" for m in s["user_msgs"]] or ["_none_"]),
                "", "## Files edited", *_counted(s["edited"]),
                "", "## Files read", *_counted(s["read"]),
                "", "## Commits", *([f"- {c}" for c in s["commits"]] or ["_none_"]),
                "", "## Tool errors", *([f"- {e}" for e in s["errors"]] or ["_none_"])]
    out += ["", "## Git status", "```", meta["status"], "```", ""]
    return "\n".join(out)


def directive(ledger: Path, n: int, subagent: bool) -> str:
    text = ("Compaction instructions from the continuity-ledger hook: the summary "
            "MUST keep, with absolute paths: (1) every file edit in flight and its "
            "state (written? tested? committed?); (2) the exact next command or step "
            "that was about to run; (3) checks still owed and what triggers them; "
            "(4) commit hashes and the repositories touched; (5) decisions made and "
            "constraints found, so they are not argued again. A mechanical ledger of "
            f"this session (files, commits, errors, recent user messages) is at "
            f"{ledger} — name that path in the summary.")
    escalate_at = _env_int("TASKLOOP_COMPACT_ESCALATE_AT", 2)
    if not subagent and escalate_at and n >= escalate_at:
        text += (f" WRAP-UP: this is compaction #{n} of this session. Record in the "
                 "summary that after it the session must finish only the edit or test "
                 "in flight, commit its own files, write a handoff (`task-add` with a "
                 "concrete goal, or a note under memory/handoffs/active/ naming the "
                 "exact next step) and end the turn instead of starting new work.")
    return text


def _prune(d: Path) -> None:
    days = _env_int("TASKLOOP_LEDGER_RETENTION_DAYS", 30)
    if not days:
        return
    cutoff = time.time() - days * 86400
    for old in d.glob("compact_*.md"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def run(payload: dict) -> str:
    session = str(payload.get("session_id") or "")
    agent = str(payload.get("agent_id") or "")
    cwd = str(payload.get("cwd") or os.getcwd())
    transcript = own_transcript(payload)
    if not agent and transcript is not None and (
            "subagents" in transcript.parts or transcript.name.startswith("agent-")):
        agent = transcript.stem[len("agent-"):] or "?"
    summary = None
    if transcript is not None:
        try:
            summary = summarize(read_records(transcript))
        except OSError:
            summary = None
    n = (summary["landed"] if summary else 0) + 1
    branch, status = git_snapshot(cwd)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    d = ledger_dir()
    d.mkdir(parents=True, exist_ok=True)
    _prune(d)
    safe = "".join(c for c in session if c.isalnum() or c == "-")[:8] or "nosession"
    name = f"compact_{stamp}_{safe}" + (f"_agent-{agent[:8]}" if agent else "")
    ledger = d / f"{name}.md"
    ledger.write_text(render(summary, {
        "ts": stamp, "session": session, "agent": agent, "cwd": cwd, "branch": branch,
        "status": status, "n": n, "trigger": payload.get("trigger") or "?"}),
        encoding="utf-8")
    if not agent:
        (d / "latest.json").write_text(json.dumps({
            "path": str(ledger), "session": session, "cwd": cwd,
            "ts": time.time(), "compaction": n}))
    if hook_log is not None:
        files = len(summary["edited"]) if summary else 0
        commits = len(summary["commits"]) if summary else 0
        hook_log.record("PreCompact", "precompact_ledger", "ledger",
                        session=session, agent=agent,
                        reason=f"compaction #{n} ({payload.get('trigger') or '?'}): "
                               f"{files} files edited, {commits} commits")
    return directive(ledger, n, subagent=bool(agent))


def main() -> int:
    if os.environ.get("TASKLOOP_PRECOMPACT_DISABLE") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            print(run(payload))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
