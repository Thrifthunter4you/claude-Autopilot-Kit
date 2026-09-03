"""pretool_spill_read_guard: deny only UNBOUNDED reads of non-trivial spill
files; pass-through for foreign paths, bounded reads, small spills, kill."""
import importlib.util
import io
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pretool_spill_read_guard", KIT / "hooks" / "pretool_spill_read_guard.py")
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def _spill(tmp_path, size=10_000):
    d = tmp_path / "spills"
    d.mkdir(exist_ok=True)
    p = d / "spill-1.txt"
    p.write_text("x" * size)
    return d, p


class TestDecide:
    def test_unbounded_read_of_big_spill_denied(self, tmp_path):
        d, p = _spill(tmp_path)
        assert guard.decide({"file_path": str(p)}, d) == "deny"

    def test_bounded_read_allowed(self, tmp_path):
        d, p = _spill(tmp_path)
        assert guard.decide({"file_path": str(p), "limit": 40}, d) == "allow"

    def test_small_spill_allowed(self, tmp_path):
        d, p = _spill(tmp_path, size=100)
        assert guard.decide({"file_path": str(p)}, d) == "allow"

    def test_foreign_path_allowed(self, tmp_path):
        d, _ = _spill(tmp_path)
        other = tmp_path / "src.py"
        other.write_text("y" * 10_000)
        assert guard.decide({"file_path": str(other)}, d) == "allow"
        assert guard.decide({}, d) == "allow"


class TestHookIO:
    def _run(self, monkeypatch, payload, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        try:
            guard.main()
        except SystemExit as e:
            assert e.code in (0, None)
        return capsys.readouterr().out

    def test_deny_names_bounded_alternatives(self, tmp_path, monkeypatch, capsys):
        d, p = _spill(tmp_path)
        monkeypatch.setattr(guard, "SPILL_DIR", d)
        out = self._run(monkeypatch, {"tool_name": "Read",
                                      "tool_input": {"file_path": str(p)}},
                        capsys)
        data = json.loads(out)["hookSpecificOutput"]
        assert data["permissionDecision"] == "deny"
        assert "--grep" in data["permissionDecisionReason"]
        assert "offset" in data["permissionDecisionReason"]

    def test_other_tools_and_kill_switch_silent(self, tmp_path, monkeypatch,
                                                capsys):
        d, p = _spill(tmp_path)
        monkeypatch.setattr(guard, "SPILL_DIR", d)
        assert self._run(monkeypatch, {"tool_name": "Bash", "tool_input":
                                       {"command": f"cat {p}"}}, capsys) == ""
        monkeypatch.setenv("TASKLOOP_SPILL_GUARD_DISABLE", "1")
        assert self._run(monkeypatch, {"tool_name": "Read", "tool_input":
                                       {"file_path": str(p)}}, capsys) == ""
