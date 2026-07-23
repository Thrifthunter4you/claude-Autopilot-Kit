"""stop_telemetry_nudge: reusable-script detection, telemetry-evidence gate,
ledger idempotence, and the end-to-end stderr nudge / auto-file."""
import importlib.util
import io
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "stop_telemetry_nudge", KIT / "hooks" / "stop_telemetry_nudge.py")
assert spec is not None and spec.loader is not None
nudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge)

# A reusable tool (argparse + main) with NO telemetry -> should be flagged.
TOOL_NO_TELEMETRY = (
    "import argparse\n\n\n"
    "def main():\n"
    "    ap = argparse.ArgumentParser()\n"
    "    ap.add_argument('--name')\n"
    "    args = ap.parse_args()\n"
    "    return dict(name=args.name)\n\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
) + "# padding to clear the size floor\n" * 40

# Same tool but it writes a JSONL fire log -> instrumented, must NOT flag.
TOOL_WITH_TELEMETRY = (
    "import argparse, json, os\n"
    "from pathlib import Path\n\n"
    "FIRES = Path(os.environ.get('WIDGET_FIRES', '/tmp/widget.jsonl'))\n\n\n"
    "def main():\n"
    "    ap = argparse.ArgumentParser()\n"
    "    args = ap.parse_args()\n"
    "    with open(FIRES, 'a') as f:\n"
    "        f.write(json.dumps({'ran': True}) + '\\n')\n\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
) + "# padding to clear the size floor\n" * 40

# A plain module (no entrypoint signal) -> not a reusable tool, must NOT flag.
PLAIN_MODULE = "def add(a, b):\n    return a + b\n" * 60


def _writef(fp, content):
    return {"type": "tool_use", "name": "Write",
            "input": {"file_path": fp, "content": content}}


def _editf(fp, new_string):
    return {"type": "tool_use", "name": "Edit",
            "input": {"file_path": fp, "old_string": "x", "new_string": new_string}}


def _transcript(tmp_path, blocks):
    p = tmp_path / "transcript.jsonl"
    lines = [json.dumps({"type": "assistant",
                         "message": {"content": [b]}}) for b in blocks]
    lines.insert(1, "not json")  # malformed lines must be skipped
    p.write_text("\n".join(lines))
    return str(p)


class TestDetection:
    def test_flags_reusable_script_without_telemetry(self, tmp_path):
        got = nudge.candidates(nudge.written_scripts(
            _transcript(tmp_path, [_writef("/repo/tools/widget.py",
                                           TOOL_NO_TELEMETRY)])))
        assert len(got) == 1
        h, name, path = got[0]
        assert name == "widget.py" and len(h) == 12

    def test_instrumented_script_is_not_flagged(self, tmp_path):
        got = nudge.candidates(nudge.written_scripts(
            _transcript(tmp_path, [_writef("/repo/tools/widget.py",
                                           TOOL_WITH_TELEMETRY)])))
        assert got == []

    def test_plain_module_is_not_flagged(self, tmp_path):
        got = nudge.candidates(nudge.written_scripts(
            _transcript(tmp_path, [_writef("/repo/lib/mathy.py", PLAIN_MODULE)])))
        assert got == []

    def test_small_script_below_floor_is_not_flagged(self, tmp_path):
        tiny = "import argparse\ndef main():\n    pass\n"
        got = nudge.candidates(nudge.written_scripts(
            _transcript(tmp_path, [_writef("/repo/tools/tiny.py", tiny)])))
        assert got == []

    def test_tests_and_docs_are_exempt(self, tmp_path):
        got = nudge.candidates(nudge.written_scripts(_transcript(tmp_path, [
            _writef("/repo/tests/test_widget.py", TOOL_NO_TELEMETRY),
            _writef("/repo/docs/example.py", TOOL_NO_TELEMETRY),
        ])))
        assert got == []

    def test_second_edit_adding_telemetry_clears_the_flag(self, tmp_path):
        """A tool written bare then instrumented in a later edit is seen as
        instrumented — accumulated written content is what's scanned."""
        got = nudge.candidates(nudge.written_scripts(_transcript(tmp_path, [
            _writef("/repo/tools/widget.py", TOOL_NO_TELEMETRY),
            _editf("/repo/tools/widget.py",
                   "FIRES = open('/tmp/x.jsonl', 'a')  # usage log\n"),
        ])))
        assert got == []


class TestMain:
    def _run(self, tmp_path, monkeypatch, capsys, blocks, ledger=None):
        monkeypatch.setattr(nudge, "LEDGER",
                            ledger or tmp_path / "ledger.jsonl")
        payload = {"transcript_path": _transcript(tmp_path, blocks),
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
                        [_writef("/repo/tools/widget.py", TOOL_NO_TELEMETRY)],
                        ledger=ledger)
        assert "[telemetry:" in err and "--repo /tmp/somerepo" in err
        assert "widget.py" in err
        assert ledger.exists()
        err2 = self._run(tmp_path, monkeypatch, capsys,
                         [_writef("/repo/tools/widget.py", TOOL_NO_TELEMETRY)],
                         ledger=ledger)
        assert err2 == ""  # same script never nudged twice

    def test_quiet_when_nothing_to_instrument(self, tmp_path, monkeypatch,
                                              capsys):
        assert self._run(tmp_path, monkeypatch, capsys,
                         [_writef("/repo/lib/mathy.py", PLAIN_MODULE)]) == ""

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
        """TASKLOOP_AUTO_TELEMETRY=1: main() shells out to the real bin/task-add
        subprocess and a task file lands in a tmp TASKLOOP_HOME's queue/active
        with the dedupe tag and status todo. A second run is a no-op (ledger)."""
        taskloop_home = tmp_path / "taskloop_home"
        monkeypatch.setenv("TASKLOOP_HOME", str(taskloop_home))
        monkeypatch.setenv("TASKLOOP_AUTO_TELEMETRY", "1")
        monkeypatch.setattr(nudge, "LEDGER", tmp_path / "ledger.jsonl")
        repo = str(tmp_path / "repo")

        def _run(blocks):
            payload = {"transcript_path": _transcript(tmp_path, blocks),
                       "cwd": repo}
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
            try:
                nudge.main()
            except SystemExit as e:
                assert e.code == 0
            return capsys.readouterr().err

        err = _run([_writef("/repo/tools/widget.py", TOOL_NO_TELEMETRY)])
        assert "telemetry auto-queued" in err

        active_dir = taskloop_home / "queue" / "active"
        files = list(active_dir.glob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "[telemetry:" in text
        assert "status: todo" in text

        err2 = _run([_writef("/repo/tools/widget.py", TOOL_NO_TELEMETRY)])
        assert err2 == ""  # ledger: same script never re-filed
        assert len(list(active_dir.glob("*.md"))) == 1
