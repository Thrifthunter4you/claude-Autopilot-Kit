"""hook_log + bin/hook-cost: rows written by hooks aggregate into per-hook
counts, per-session means, bytes saved and subagents over budget."""
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "hooks"))
import hook_log  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("hook_cost", str(KIT / "bin" / "hook-cost"))
_spec = importlib.util.spec_from_loader("hook_cost", _loader)
assert _spec is not None
hc = importlib.util.module_from_spec(_spec)
_loader.exec_module(hc)


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("TASKLOOP_HOOK_LOG", str(path))
    monkeypatch.delenv("TASKLOOP_HOOK_LOG_DISABLE", raising=False)
    return path


def test_record_writes_one_trimmed_row(log):
    hook_log.record("PreToolUse", "g", "deny", session="s", tool="Bash",
                    reason="x" * 500)
    row = json.loads(log.read_text())
    assert row["hook"] == "g" and len(row["reason"]) == hook_log.REASON_CHARS
    assert "agent" not in row and "saved_bytes" not in row


def test_record_disabled_and_unwritable_never_raise(log, monkeypatch, tmp_path):
    monkeypatch.setenv("TASKLOOP_HOOK_LOG_DISABLE", "1")
    hook_log.record("Stop", "g", "block")
    assert not log.exists()
    monkeypatch.delenv("TASKLOOP_HOOK_LOG_DISABLE")
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("TASKLOOP_HOOK_LOG", str(blocker / "sub" / "x.jsonl"))
    hook_log.record("Stop", "g", "block")   # parent is a file: must not raise


def test_summary_counts_sessions_saving_and_subagents(log):
    for s in ("a", "a", "b"):
        hook_log.record("PreToolUse", "spill_read_guard", "deny", session=s, tool="Read")
    hook_log.record("PostToolUse", "output_spill", "rewrite", session="a", saved_bytes=40_000)
    hook_log.record("PreToolUse", "subagent_turn_budget", "advise", session="a",
                    agent="leaf1", reason="scout 25/25")
    hook_log.record("PreToolUse", "subagent_turn_budget", "deny", session="a",
                    agent="leaf1", reason="scout 50/25")
    s = hc.summarize(hc.load(log))
    guard = next(r for r in s["by_hook"] if r["hook"] == "spill_read_guard")
    assert guard == {"hook": "spill_read_guard", "decision": "deny", "count": 3,
                     "sessions": 2, "per_session": 1.5}
    assert s["saved_bytes"] == 40_000
    assert s["subagents_over_budget"] == 1
    assert s["worst_subagents"][0]["deny"] == 1
    text = hc.render(s, log)
    assert "BY HOOK" in text and "SAVED" in text and "SUBAGENTS" in text


def test_filters_and_empty_log(log, capsys):
    hook_log.record("PreToolUse", "g", "deny", session="keep-me")
    hook_log.record("PreToolUse", "g", "deny", session="other")
    assert len(hc.load(log, session="keep")) == 1
    assert hc.load(log, since_hours=0.001)          # just written
    assert hc.main(["--log", str(log.parent / "missing.jsonl")]) == 0
    assert "no hook decisions" in capsys.readouterr().out
    assert hc.main(["--log", str(log), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 2


def test_log_rotates_and_reader_sees_both_generations(log, monkeypatch):
    monkeypatch.setattr(hook_log, "ROTATE_BYTES", 200)
    for i in range(10):
        hook_log.record("PreToolUse", "g", "deny", session=f"s{i}")
    assert (log.parent / (log.name + ".1")).exists()
    assert len(hc.load(log)) >= 2
