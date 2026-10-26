"""pretool_subagent_turn_budget: count a subagent's tool turns from its own
transcript (parallel calls = one turn), advise at budget and every
half-budget after, deny only past an opt-in ceiling; main loop untouched."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pretool_subagent_turn_budget", KIT / "hooks" / "pretool_subagent_turn_budget.py")
assert spec is not None and spec.loader is not None
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKLOOP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TASKLOOP_TURN_BUDGET", "4")
    for var in ("TASKLOOP_TURN_CEILING_MULT", "TASKLOOP_TURN_BUDGET_IMPLEMENTERS",
                "TASKLOOP_TURN_BUDGET_DISABLE"):
        monkeypatch.delenv(var, raising=False)


def _turn(mid, n_calls=1):
    """One assistant message as the harness writes it: one JSONL row per
    content block, all sharing message.id."""
    return [json.dumps({"type": "assistant", "message": {
        "id": mid, "role": "assistant",
        "content": [{"type": "tool_use", "id": f"{mid}-{i}", "name": "Read", "input": {}}]}})
        for i in range(n_calls)]


class Leaf:
    def __init__(self, tmp_path, agent_id="abc", agent_type="scout"):
        self.parent = tmp_path / "proj" / "sess-1.jsonl"
        self.parent.parent.mkdir(parents=True)
        self.parent.write_text("")
        self.path = tmp_path / "proj" / "sess-1" / "subagents" / f"agent-{agent_id}.jsonl"
        self.path.parent.mkdir(parents=True)
        self.path.write_text("")
        self.agent_id, self.agent_type, self.n = agent_id, agent_type, 0

    def add(self, n_calls=1):
        self.n += 1
        with self.path.open("a") as f:
            for line in _turn(f"msg_{self.n}", n_calls):
                f.write(line + "\n")
            f.write(json.dumps({"type": "user", "message": {"role": "user",
                    "content": [{"type": "tool_result"}]}}) + "\n")

    def call(self, tool="Read"):
        return tb.decide({"session_id": "S", "transcript_path": str(self.parent),
                          "agent_id": self.agent_id, "agent_type": self.agent_type,
                          "tool_name": tool, "tool_input": {}})


def _kind(out):
    if out is None:
        return None
    h = out["hookSpecificOutput"]
    return "deny" if h.get("permissionDecision") == "deny" else "advise"


def test_main_loop_is_untouched(tmp_path):
    assert tb.decide({"session_id": "S", "transcript_path": "/x.jsonl",
                      "tool_name": "Bash"}) is None
    assert not (tmp_path / "home").exists()


def test_parallel_calls_count_as_one_turn(tmp_path):
    leaf = Leaf(tmp_path)
    leaf.add(n_calls=5)
    leaf.call()
    st = json.loads(next((tmp_path / "home" / "turn_budget").glob("*.json")).read_text())
    assert st["turns"] == 1 and st["source"] == "transcript"


def test_advises_at_budget_then_every_half_budget(tmp_path):
    leaf = Leaf(tmp_path)
    kinds = []
    for _ in range(9):
        leaf.add()
        kinds.append(_kind(leaf.call()))
    # budget 4: fires at 4, then every 2 turns (6, 8)
    assert kinds == [None, None, None, "advise", None, "advise", None, "advise", None]


def test_reminder_text_forbids_weakening_verdicts(tmp_path):
    leaf = Leaf(tmp_path)
    for _ in range(4):
        leaf.add()
        out = leaf.call()
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "4 tool turns (budget 4)" in text and "Do not lower the quality" in text


def test_implementers_get_double_budget(tmp_path):
    leaf = Leaf(tmp_path, agent_type="wf-fixer")
    kinds = []
    for _ in range(8):
        leaf.add()
        kinds.append(_kind(leaf.call()))
    assert kinds.index("advise") == 7


def test_ceiling_off_by_default(tmp_path):
    leaf = Leaf(tmp_path)
    for _ in range(20):
        leaf.add()
        assert _kind(leaf.call()) != "deny"


def test_ceiling_denies_tools_but_not_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKLOOP_TURN_CEILING_MULT", "2")
    leaf = Leaf(tmp_path)
    for _ in range(8):
        leaf.add()
    out = leaf.call()
    assert _kind(out) == "deny"
    assert "budget-bounded" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert leaf.call(tool="StructuredOutput") is None or \
        _kind(leaf.call(tool="StructuredOutput")) != "deny"
    rows = [json.loads(l) for l in
            (tmp_path / "home" / "hook_decisions.jsonl").read_text().splitlines()]
    assert any(r["decision"] == "deny" and r["agent"] == "abc" for r in rows)


def test_missing_transcript_counts_calls_and_never_denies(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKLOOP_TURN_CEILING_MULT", "2")
    payload = {"session_id": "S", "transcript_path": str(tmp_path / "none.jsonl"),
               "agent_id": "zzz", "tool_name": "Bash"}
    kinds = [_kind(tb.decide(payload)) for _ in range(12)]
    assert "advise" in kinds and "deny" not in kinds


def test_workflow_subagent_transcript_found(tmp_path):
    parent = tmp_path / "p" / "s.jsonl"
    leaf = tmp_path / "p" / "s" / "subagents" / "workflows" / "wf_1" / "agent-q.jsonl"
    leaf.parent.mkdir(parents=True)
    leaf.write_text("")
    assert tb.locate_transcript({"transcript_path": str(parent), "agent_id": "q"}) == str(leaf)


def test_main_kill_switch_and_fail_open(monkeypatch, capsys):
    monkeypatch.setenv("TASKLOOP_TURN_BUDGET_DISABLE", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"agent_id": "a"})))
    assert tb.main() == 0
    monkeypatch.delenv("TASKLOOP_TURN_BUDGET_DISABLE")
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert tb.main() == 0
    assert capsys.readouterr().out == ""


def test_late_transcript_restarts_count_instead_of_adding(tmp_path):
    leaf = Leaf(tmp_path)
    leaf.path.unlink()                                 # not flushed yet
    for _ in range(3):
        leaf.call()                                    # counted as calls
    leaf.path.write_text("")
    leaf.add()
    leaf.add()
    leaf.call()
    st = json.loads(next((tmp_path / "home" / "turn_budget").glob("*.json")).read_text())
    assert st["source"] == "transcript" and st["turns"] == 2


@pytest.mark.skipif(tb.fcntl is None, reason="POSIX lock only")
def test_state_update_waits_for_the_lock(tmp_path):
    """Parallel calls of one batch must serialize on the state lock, or each
    sees the same next_fire and N reminders are injected instead of one."""
    import subprocess
    import time
    leaf = Leaf(tmp_path)
    for _ in range(4):
        leaf.add()
    d = tmp_path / "home" / "turn_budget"
    d.mkdir(parents=True)
    payload = json.dumps({"session_id": "S", "transcript_path": str(leaf.parent),
                          "agent_id": leaf.agent_id, "agent_type": "scout",
                          "tool_name": "Read", "tool_input": {}})
    hook = str(KIT / "hooks" / "pretool_subagent_turn_budget.py")
    with open(d / ".lock", "w") as fh:
        tb.fcntl.flock(fh, tb.fcntl.LOCK_EX)
        proc = subprocess.Popen([sys.executable, hook], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        proc.stdin.write(payload)
        proc.stdin.close()
        time.sleep(0.6)
        assert proc.poll() is None          # blocked on our lock
        tb.fcntl.flock(fh, tb.fcntl.LOCK_UN)
    assert "additionalContext" in proc.stdout.read()
    assert proc.wait(timeout=10) == 0
    # a second call in the same batch now sees next_fire advanced: silent
    assert leaf.call() is None


def test_stale_state_pruned_when_a_new_subagent_starts(tmp_path):
    import os
    import time
    d = tmp_path / "home" / "turn_budget"
    d.mkdir(parents=True)
    stale = d / "deadbeef.json"
    stale.write_text("{}")
    t = time.time() - 3 * 86400
    os.utime(stale, (t, t))
    Leaf(tmp_path).call()
    assert not stale.exists()
