"""stop_crystallize_nudge: signature grouping, derived-command detection,
threshold, ledger idempotence, and the end-to-end stderr nudge."""
import importlib.util
import io
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "stop_crystallize_nudge", KIT / "hooks" / "stop_crystallize_nudge.py")
assert spec is not None and spec.loader is not None
nudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge)

PIPELINE = "grep -n 'status: todo' queue/active/*.md | sort | head -5"
PIPELINE_VARIANT = 'grep -n "status: done" queue/active/*.md | sort | head -20'
SHORT = "git status"


def _transcript(tmp_path, cmds):
    p = tmp_path / "transcript.jsonl"
    lines = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": c}}]}})
        for c in cmds]
    lines.insert(1, "not json")  # malformed lines must be skipped
    p.write_text("\n".join(lines))
    return str(p)


class TestDetection:
    def test_signature_groups_variants(self):
        assert nudge.signature(PIPELINE) == nudge.signature(PIPELINE_VARIANT)
        assert nudge.signature(PIPELINE) != nudge.signature(SHORT)

    def test_derived_heuristic(self):
        assert nudge.is_derived(PIPELINE)
        assert nudge.is_derived("python3 - <<'EOF'\nprint(1)\nEOF")
        assert not nudge.is_derived(SHORT)

    def test_threshold_and_ranking(self, tmp_path):
        cmds = [PIPELINE, PIPELINE_VARIANT, PIPELINE,   # 3x -> candidate
                "awk '{print}' a.txt | wc -l",          # 1x -> below threshold
                SHORT, SHORT, SHORT, SHORT]             # repeated but not derived
        got = nudge.candidates(nudge.bash_commands(_transcript(tmp_path, cmds)))
        assert len(got) == 1
        h, n, raw = got[0]
        assert n == 3 and raw == PIPELINE and len(h) == 12


class TestMain:
    def _run(self, tmp_path, monkeypatch, capsys, cmds, ledger=None):
        monkeypatch.setattr(nudge, "LEDGER",
                            ledger or tmp_path / "ledger.jsonl")
        payload = {"transcript_path": _transcript(tmp_path, cmds),
                   "cwd": "/tmp/somerepo"}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        try:
            nudge.main()
        except SystemExit as e:
            assert e.code == 0
        return capsys.readouterr().err

    def test_nudges_once_then_ledger_suppresses(self, tmp_path, monkeypatch,
                                                capsys):
        ledger = tmp_path / "ledger.jsonl"
        err = self._run(tmp_path, monkeypatch, capsys,
                        [PIPELINE] * 3, ledger=ledger)
        assert "[crystallize:" in err and "--repo /tmp/somerepo" in err
        assert ledger.exists()
        err2 = self._run(tmp_path, monkeypatch, capsys,
                         [PIPELINE] * 5, ledger=ledger)
        assert err2 == ""  # same operation never nudged twice

    def test_quiet_below_threshold(self, tmp_path, monkeypatch, capsys):
        assert self._run(tmp_path, monkeypatch, capsys,
                         [PIPELINE, SHORT]) == ""

    def test_stop_hook_active_exits_silently(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin",
                            io.StringIO(json.dumps({"stop_hook_active": True})))
        try:
            nudge.main()
        except SystemExit as e:
            assert e.code == 0
        assert capsys.readouterr().err == ""

    def test_auto_files_real_task_add_and_dedupes(self, tmp_path, monkeypatch,
                                                   capsys):
        """TASKLOOP_AUTO_CRYSTALLIZE=1: main() shells out to the real
        bin/task-add subprocess and a task file lands in a tmp
        TASKLOOP_HOME's queue/active with the dedupe tag and status todo.
        A second run of the same operation is a no-op (ledger)."""
        taskloop_home = tmp_path / "taskloop_home"
        monkeypatch.setenv("TASKLOOP_HOME", str(taskloop_home))
        monkeypatch.setenv("TASKLOOP_AUTO_CRYSTALLIZE", "1")
        monkeypatch.setattr(nudge, "LEDGER", tmp_path / "ledger.jsonl")
        repo = str(tmp_path / "repo")

        def _run(cmds):
            payload = {"transcript_path": _transcript(tmp_path, cmds),
                       "cwd": repo}
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
            try:
                nudge.main()
            except SystemExit as e:
                assert e.code == 0
            return capsys.readouterr().err

        err = _run([PIPELINE] * 3)
        assert "crystallize auto-queued" in err

        active_dir = taskloop_home / "queue" / "active"
        files = list(active_dir.glob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "[crystallize:" in text
        assert "status: todo" in text

        err2 = _run([PIPELINE] * 5)
        assert err2 == ""  # ledger: same operation never re-filed
        assert len(list(active_dir.glob("*.md"))) == 1
