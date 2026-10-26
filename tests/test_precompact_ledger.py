"""precompact_ledger: ledger from the transcript, compaction number from
landed compact_boundary records (never fires), plain-text directive with a
wrap-up clause from the 2nd compaction, subagents never escalate, and the
session-start pointer it feeds."""
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, KIT / "hooks" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pl = _load("precompact_ledger")
ssc = _load("session_start_context")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKLOOP_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ssc, "HOME", tmp_path / "home")
    for var in ("TASKLOOP_COMPACT_ESCALATE_AT", "TASKLOOP_PRECOMPACT_DISABLE",
                "TASKLOOP_LEDGER_RETENTION_DAYS"):
        monkeypatch.delenv(var, raising=False)


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _call(tid, name, inp):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _result(tid, text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}


BOUNDARY = {"type": "system", "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "auto", "preTokens": 180000}}


def _transcript(tmp_path, rows, name="sess-1.jsonl"):
    path = tmp_path / "proj" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _payload(path, **extra):
    return {"session_id": "sess-1", "transcript_path": str(path), "cwd": str(path.parent),
            "hook_event_name": "PreCompact", "trigger": "auto", **extra}


def _ledgers(tmp_path):
    return sorted((tmp_path / "home" / "ledgers").glob("compact_*.md"))


def test_ledger_records_messages_files_commits_errors(tmp_path):
    rows = [
        _user("please add a --json flag to the report script"),
        _user("<system-reminder>injected</system-reminder>"),
        _user("the summary", isCompactSummary=True),
        _call("t1", "Read", {"file_path": "/repo/report.py"}),
        _result("t1", "contents"),
        _call("t2", "Edit", {"file_path": "/repo/report.py"}),
        _result("t2", "ok"),
        _call("t3", "Bash", {"command": "git commit -m 'add --json' -- report.py"}),
        _result("t3", "[main 1a2b3c4] add --json flag\n 1 file changed"),
        _call("t4", "Bash", {"command": "pytest"}),
        _result("t4", "FAILED test_report.py::test_json", is_error=True),
    ]
    out = pl.run(_payload(_transcript(tmp_path, rows)))
    body = _ledgers(tmp_path)[0].read_text()
    assert "please add a --json flag" in body
    assert "injected" not in body and "the summary" not in body
    assert "`/repo/report.py`" in body
    assert "1a2b3c4 (main) add --json flag" in body
    assert "FAILED test_report.py::test_json" in body
    assert "compaction #1" in body and "WRAP-UP" not in out
    assert str(_ledgers(tmp_path)[0]) in out


def test_commit_in_a_failed_chain_still_counts(tmp_path):
    rows = [_call("t1", "Bash", {"command": "git commit -m fix -- a.py && git push"}),
            _result("t1", "[feature/x 9f8e7d6] fix the parser\n 1 file changed\n"
                          "! [rejected] feature/x -> feature/x (fetch first)", is_error=True),
            _call("t2", "Read", {"file_path": "/repo/a.py"}),
            _result("t2", "[main 0000000] not a shell result")]
    pl.run(_payload(_transcript(tmp_path, rows)))
    body = _ledgers(tmp_path)[0].read_text()
    assert "9f8e7d6 (feature/x) fix the parser" in body and "rejected" in body
    assert "0000000" not in body


def test_window_is_since_the_last_compaction(tmp_path):
    rows = [_call("t1", "Edit", {"file_path": "/repo/old.py"}), _result("t1", "ok"),
            BOUNDARY,
            _call("t2", "Edit", {"file_path": "/repo/new.py"}), _result("t2", "ok")]
    pl.run(_payload(_transcript(tmp_path, rows)))
    body = _ledgers(tmp_path)[0].read_text()
    assert "/repo/new.py" in body and "/repo/old.py" not in body


def test_compaction_number_counts_landed_boundaries_not_fires(tmp_path):
    path = _transcript(tmp_path, [_user("start the work")])
    for _ in range(3):                                   # fires that never landed
        assert "WRAP-UP" not in pl.run(_payload(path))
    path = _transcript(tmp_path, [_user("start the work"), BOUNDARY])
    out = pl.run(_payload(path))
    assert "WRAP-UP: this is compaction #2" in out


def test_escalation_threshold_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKLOOP_COMPACT_ESCALATE_AT", "0")
    path = _transcript(tmp_path, [BOUNDARY, BOUNDARY, BOUNDARY])
    assert "WRAP-UP" not in pl.run(_payload(path))


def test_subagent_never_escalates_or_moves_the_pointer(tmp_path):
    parent = _transcript(tmp_path, [_user("main work")])
    own = tmp_path / "proj" / "sess-1" / "subagents" / "agent-abc.jsonl"
    own.parent.mkdir(parents=True)
    own.write_text(json.dumps(BOUNDARY) + "\n" + json.dumps(BOUNDARY) + "\n")
    out = pl.run(_payload(parent, agent_id="abc"))
    assert "WRAP-UP" not in out
    assert _ledgers(tmp_path)[0].name.endswith("_agent-abc.md")
    assert "compaction #3 of this subagent transcript" in _ledgers(tmp_path)[0].read_text()
    assert not (tmp_path / "home" / "ledgers" / "latest.json").exists()


def test_subagent_without_its_own_file_never_summarizes_the_parent(tmp_path):
    parent = _transcript(tmp_path, [_user("secret main-session plan"), BOUNDARY])
    pl.run(_payload(parent, agent_id="zzz"))
    body = _ledgers(tmp_path)[0].read_text()
    assert "No transcript found" in body and "secret main-session plan" not in body


def test_main_cli_prints_plain_text_and_fails_open(tmp_path, monkeypatch, capsys):
    path = _transcript(tmp_path, [_user("hello there")])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload(path))))
    assert pl.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("Compaction instructions") and not out.lstrip().startswith("{")
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert pl.main() == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("TASKLOOP_PRECOMPACT_DISABLE", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload(path))))
    assert pl.main() == 0 and capsys.readouterr().out == ""


def test_git_snapshot_ignores_a_leaked_git_dir(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True)
    (repo / "dirty.txt").write_text("x")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere" / ".git"))
    branch, status = pl.git_snapshot(str(repo))
    assert branch == "trunk" and "dirty.txt" in status
    assert pl.git_snapshot(str(tmp_path))[1] == "(not a git repository)"


def test_old_ledgers_are_pruned(tmp_path):
    import os
    import time
    d = tmp_path / "home" / "ledgers"
    d.mkdir(parents=True)
    stale = d / "compact_old.md"
    stale.write_text("x")
    t = time.time() - 40 * 86400
    os.utime(stale, (t, t))
    pl.run(_payload(_transcript(tmp_path, [_user("hello there")])))
    assert not stale.exists()


def test_hook_log_row(tmp_path):
    pl.run(_payload(_transcript(tmp_path, [BOUNDARY])))
    rows = [json.loads(l) for l in
            (tmp_path / "home" / "hook_decisions.jsonl").read_text().splitlines()]
    assert rows[-1]["hook"] == "precompact_ledger" and "compaction #2" in rows[-1]["reason"]


class TestSessionStartPointer:
    def test_after_compaction_of_this_session(self, tmp_path):
        path = _transcript(tmp_path, [_user("hello there")])
        pl.run(_payload(path))
        line = ssc._continuity({"source": "compact", "session_id": "sess-1",
                                "cwd": "/somewhere/else"})
        assert line and str(_ledgers(tmp_path)[0]) in line

    def test_new_session_in_same_directory_within_the_hour(self, tmp_path):
        path = _transcript(tmp_path, [_user("hello there")])
        pl.run(_payload(path))
        here = {"source": "startup", "session_id": "new", "cwd": str(path.parent)}
        assert ssc._continuity(here)
        assert ssc._continuity({**here, "cwd": "/other/project"}) is None

    def test_stale_or_missing_ledger_is_silent(self, tmp_path):
        assert ssc._continuity({"source": "startup", "cwd": "/x"}) is None
        path = _transcript(tmp_path, [_user("hello there")])
        pl.run(_payload(path))
        latest = tmp_path / "home" / "ledgers" / "latest.json"
        data = json.loads(latest.read_text())
        latest.write_text(json.dumps({**data, "ts": data["ts"] - 7200}))
        assert ssc._continuity({"source": "startup", "cwd": str(path.parent)}) is None
