#!/usr/bin/env python3
"""PreToolUse hook: tell a subagent when it has used up its turn budget.

A subagent's cost grows roughly with the square of its turns: every turn
re-sends its whole context, and the context grows every turn.  The brief
that spawned it may say "answer within 25 turns", but that line scrolls
away and nothing inside a running subagent counts.  In practice a small
tail of long-running subagents carries a large share of all subagent
tokens.  This hook is the counter.

Main-loop calls (no ``agent_id`` in the payload) exit immediately.  For a
subagent call it counts the subagent's TOOL TURNS — assistant messages that
carry a ``tool_use``, deduped on ``message.id``, so a message with five
parallel tool calls is ONE turn (batching is the behaviour we want, so it
is never charged per call).  The count comes from the subagent's own
transcript, which sits next to the parent's:

    <parent transcript minus .jsonl>/subagents/agent-<id>.jsonl
    <parent transcript minus .jsonl>/subagents/workflows/*/agent-<id>.jsonl

scanned incrementally from a saved byte offset.  If that file cannot be
found the hook counts its own invocations instead (an over-count, since
parallel calls each fire it — so that source only ever advises).

At the budget, and every half-budget after it, the subagent gets an
``additionalContext`` reminder: batch independent calls, stop exploring,
answer now and say what is unverified — never weaken a verdict to save
turns.  Below the ceiling the hook never decides a permission.

Optional hard ceiling: with TASKLOOP_TURN_CEILING_MULT=2, a subagent at
2 x budget (transcript count only) gets every tool denied except
``StructuredOutput`` and is told to return its answer now, marked
budget-bounded.  Off by default — turn it on once the advisory numbers
from ``hook-cost`` tell you where your subagents actually end.

Configuration:
  TASKLOOP_TURN_BUDGET=25                  base budget
  TASKLOOP_TURN_BUDGET_IMPLEMENTERS=wf-fixer,general-purpose
                                           agent types that get 2 x budget
                                           (edit -> test -> fix loops run long)
  TASKLOOP_TURN_CEILING_MULT=0             0 = no ceiling
  TASKLOOP_TURN_BUDGET_DISABLE=1           hook off

Every reminder and deny is recorded via hook_log, so `hook-cost` shows how
often subagents hit the budget.  Fail-open on any error.

Wire it (hooks/install_hooks.py registers it): PreToolUse, matcher = the
working tools (Bash|Read|Grep|Glob|Edit|Write|…|mcp__.*).  The count comes
from the transcript, so it need not fire on every call.
"""
from __future__ import annotations

import contextlib
import glob
import hashlib
import json
import os
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX: run unlocked
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import hook_log
except Exception:  # pragma: no cover - logging is optional
    hook_log = None

ANSWER_TOOLS = {"StructuredOutput"}
STATE_MAX_AGE_S = 2 * 86400    # a subagent idle this long is finished


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def state_dir() -> Path:
    home = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
    return home / "turn_budget"


def budget_for(agent_type: str) -> int:
    base = _env_int("TASKLOOP_TURN_BUDGET", 25) or 25
    raw = os.environ.get("TASKLOOP_TURN_BUDGET_IMPLEMENTERS", "wf-fixer,general-purpose")
    implementers = {t.strip() for t in raw.split(",") if t.strip()}
    return base * 2 if agent_type in implementers else base


def locate_transcript(payload: dict) -> str | None:
    parent = str(payload.get("transcript_path") or "")
    agent_id = str(payload.get("agent_id") or "")
    if not parent or not agent_id:
        return None
    session_dir = os.path.splitext(parent)[0]
    direct = os.path.join(session_dir, "subagents", f"agent-{agent_id}.jsonl")
    if os.path.isfile(direct):
        return direct
    hits = glob.glob(os.path.join(session_dir, "subagents", "workflows", "*",
                                  f"agent-{agent_id}.jsonl"))
    return hits[0] if hits else None


def scan(path: str, st: dict) -> None:
    """Advance st over complete new lines of path, counting tool turns."""
    offset = int(st.get("offset", 0))
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return
    for line in chunk[:end].splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list) or not any(
                isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
            continue
        mid = msg.get("id") or row.get("uuid")
        if mid and mid == st.get("last_id"):
            continue
        st["last_id"] = mid
        st["turns"] = int(st.get("turns", 0)) + 1
    st["offset"] = offset + end + 1


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, st: dict) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(st))
        os.replace(tmp, path)
    except OSError:
        pass


def _prune(d: Path) -> None:
    cutoff = time.time() - STATE_MAX_AGE_S
    for old in d.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


@contextlib.contextmanager
def _locked(d: Path):
    """Serialize the parallel PreToolUse processes of one tool batch, so
    N parallel calls produce one reminder, not N."""
    if fcntl is None:
        yield
        return
    with open(d / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def decide(payload: dict) -> dict | None:
    agent_id = str(payload.get("agent_id") or "")
    if not agent_id:
        return None
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    with _locked(d):
        return _decide(payload, d, agent_id)


def _decide(payload: dict, d: Path, agent_id: str) -> dict | None:
    agent_type = str(payload.get("agent_type") or "")
    key = hashlib.sha1(f"{payload.get('session_id')}:{agent_id}".encode()).hexdigest()[:20]
    state_path = d / f"{key}.json"
    if not state_path.exists():
        _prune(d)                                  # once per new subagent
    st = _load(state_path)
    st.setdefault("budget", budget_for(agent_type))   # pinned on first call
    budget = int(st["budget"])

    transcript = locate_transcript(payload)
    if transcript:
        if st.get("source") == "calls":
            # The transcript appeared after some calls were counted: start
            # the real count from zero rather than adding to the over-count.
            st.update(turns=0, offset=0, last_id=None)
        scan(transcript, st)
        st["source"] = "transcript"
    else:
        st["calls"] = int(st.get("calls", 0)) + 1
        st["turns"] = st["calls"]
        st["source"] = "calls"
    turns = int(st.get("turns", 0))

    out = None
    session = str(payload.get("session_id") or "")
    tool = str(payload.get("tool_name") or "")
    mult = _env_int("TASKLOOP_TURN_CEILING_MULT", 0)
    if (mult and st["source"] == "transcript" and turns >= mult * budget
            and tool not in ANSWER_TOOLS):
        reason = (f"turn ceiling reached: {turns} tool turns against a budget "
                  f"of {budget}. Stop calling tools and return your answer NOW "
                  f"(as StructuredOutput if your task asked for it, else plain "
                  f"text), marked budget-bounded, naming what is unverified. Do "
                  f"not upgrade a verdict you could not finish checking.")
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "permissionDecision": "deny",
                                      "permissionDecisionReason": reason}}
        decision = "deny"
    elif turns >= int(st.get("next_fire", budget)):
        st["next_fire"] = turns + max(budget // 2, 1)
        warn = (f" A hard stop comes at {mult * budget} turns."
                if mult and st["source"] == "transcript" else "")
        reason = (f"turn budget: you are at {turns} tool turns (budget {budget}). "
                  f"Every further turn re-sends your whole context. Batch the "
                  f"independent calls you still need into one message, stop "
                  f"exploring, and return your answer with what you have, saying "
                  f"plainly what is unverified. Do not lower the quality of a "
                  f"verdict to save turns.{warn}")
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "additionalContext": reason}}
        decision = "advise"

    _save(state_path, st)
    if out is not None and hook_log is not None:
        hook_log.record("PreToolUse", "subagent_turn_budget", decision,
                        session=session, tool=tool, agent=agent_id,
                        reason=f"{agent_type or 'subagent'} {turns}/{budget}")
    return out


def main() -> int:
    if os.environ.get("TASKLOOP_TURN_BUDGET_DISABLE") == "1":
        return 0
    try:
        out = decide(json.load(sys.stdin))
        if out:
            print(json.dumps(out))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
