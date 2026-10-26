"""pretool_context_ceiling: newest call's context from the transcript tail,
advise at/over the ceiling on a cooldown, silent on wrap-up calls and for
subagents, never a permission decision."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pretool_context_ceiling", KIT / "hooks" / "pretool_context_ceiling.py")
assert spec is not None and spec.loader is not None
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKLOOP_HOME", str(tmp_path / "home"))
    for var in ("TASKLOOP_CONTEXT_CEILING_K", "TASKLOOP_CONTEXT_COOLDOWN_S",
                "TASKLOOP_CONTEXT_CEILING_DISABLE"):
        monkeypatch.delenv(var, raising=False)


def _gen(read, write=0, fresh=10):
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "usage": {
        "input_tokens": fresh, "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write, "output_tokens": 50},
        "content": [{"type": "tool_use", "id": "x", "name": "Read", "input": {}}]}})


def _session(tmp_path, *lines):
    path = tmp_path / "sess.jsonl"
    path.write_text("".join(l + "\n" for l in lines))
    return path


def _call(path, tool="Read", tool_input=None, **extra):
    return cc.decide({"session_id": "S", "transcript_path": str(path),
                      "tool_name": tool, "tool_input": tool_input or {}, **extra})


def test_newest_generation_wins(tmp_path):
    path = _session(tmp_path, _gen(400_000), _gen(90_000, 5_000))
    assert cc.last_context_tokens(str(path)) == 95_010
    assert _call(path) is None


def test_over_ceiling_advises_and_never_decides_permission(tmp_path):
    path = _session(tmp_path, _gen(150_000, 2_000))
    out = _call(path)["hookSpecificOutput"]
    assert "permissionDecision" not in out
    assert "~152K tokens" in out["additionalContext"] and "ceiling 150K" in out["additionalContext"]
    rows = [json.loads(l) for l in
            (tmp_path / "home" / "hook_decisions.jsonl").read_text().splitlines()]
    assert rows[-1]["hook"] == "context_ceiling" and rows[-1]["decision"] == "advise"


def test_cooldown_then_repeat(tmp_path, monkeypatch):
    path = _session(tmp_path, _gen(200_000))
    assert _call(path) is not None
    assert _call(path) is None                         # same batch / within cooldown
    monkeypatch.setenv("TASKLOOP_CONTEXT_COOLDOWN_S", "0")
    assert _call(path) is not None


def test_wrapup_calls_are_left_alone(tmp_path):
    path = _session(tmp_path, _gen(300_000))
    assert _call(path, "Write", {"file_path": "/r/memory/handoffs/active/next.md"}) is None
    assert _call(path, "Bash", {"command": "task-add 'finish the parser' --repo ."}) is None
    assert _call(path, "Bash", {"command": "pytest"}) is not None


def test_subagents_and_missing_transcripts_skip(tmp_path):
    path = _session(tmp_path, _gen(300_000))
    assert _call(path, agent_id="abc") is None
    assert _call(tmp_path / "missing.jsonl") is None
    assert not (tmp_path / "home" / "context_ceiling").exists()


def test_ceiling_is_configurable_and_zero_disables(tmp_path, monkeypatch):
    path = _session(tmp_path, _gen(120_000))
    assert _call(path) is None
    monkeypatch.setenv("TASKLOOP_CONTEXT_CEILING_K", "100")
    assert _call(path) is not None
    monkeypatch.setenv("TASKLOOP_CONTEXT_CEILING_K", "0")
    monkeypatch.setenv("TASKLOOP_CONTEXT_COOLDOWN_S", "0")
    assert _call(path) is None


def test_usage_line_behind_a_huge_last_line_is_still_found(tmp_path):
    big = json.dumps({"type": "user", "message": {"role": "user",
                      "content": "x" * (300 * 1024)}})
    path = _session(tmp_path, _gen(250_000), big)
    assert cc.last_context_tokens(str(path)) == 250_010


def test_young_session_reads_as_zero(tmp_path):
    path = _session(tmp_path, json.dumps({"type": "user", "message": {"content": "hi"}}))
    assert cc.last_context_tokens(str(path)) == 0


def test_main_kill_switch_and_fail_open(tmp_path, monkeypatch, capsys):
    path = _session(tmp_path, _gen(300_000))
    payload = json.dumps({"session_id": "S", "transcript_path": str(path),
                          "tool_name": "Bash", "tool_input": {}})
    monkeypatch.setenv("TASKLOOP_CONTEXT_CEILING_DISABLE", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert cc.main() == 0 and capsys.readouterr().out == ""
    monkeypatch.delenv("TASKLOOP_CONTEXT_CEILING_DISABLE")
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert cc.main() == 0 and capsys.readouterr().out == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert cc.main() == 0
    assert "context-ceiling" in json.loads(capsys.readouterr().out)[
        "hookSpecificOutput"]["additionalContext"]
