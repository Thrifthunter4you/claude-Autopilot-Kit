"""posttool_memory_budget: line/byte/bullet budgets on MEMORY.md edits —
warn-only, only for files named MEMORY.md, trimmed suggestion keeps the link."""
import importlib.util
import io
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "posttool_memory_budget", KIT / "hooks" / "posttool_memory_budget.py")
assert spec is not None and spec.loader is not None
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

LINK = "- [Rate limiter shipped](rate-limiter.md)"


class TestCheck:
    def test_healthy_index_is_silent(self):
        text = "\n".join(f"- [m{i}](m{i}.md) — pointer" for i in range(50))
        assert mb.check(text) == []

    def test_line_budget_soft_then_hard(self, monkeypatch):
        soft = "\n".join(["- x"] * (mb.WARN_LINES + 1))
        hard = "\n".join(["- x"] * (mb.MAX_LINES + 1))
        assert any("start archiving" in f for f in mb.check(soft))
        assert any("DROPPED" in f for f in mb.check(hard))

    def test_byte_budget(self, monkeypatch):
        monkeypatch.setattr(mb, "MAX_BYTES", 100)
        assert any("bytes" in f for f in mb.check("- " + "y" * 150))

    def test_fat_bullet_named_with_trimmed_form(self):
        fat = LINK + " — " + "detail " * 60
        findings = mb.check(fat)
        assert len(findings) == 1 and "L1" in findings[0]
        suggestion = mb.trimmed(fat)
        assert suggestion.startswith(LINK)
        assert len(suggestion) <= mb.BULLET_MAX
        # non-bullet long lines (headers, prose) are not bullets
        assert mb.check("# " + "h" * 300) == []


class TestHookIO:
    def _run(self, monkeypatch, payload, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        try:
            mb.main()
        except SystemExit as e:
            assert e.code in (0, None)
        return capsys.readouterr().out

    def test_warns_only_for_memory_index(self, tmp_path, monkeypatch, capsys):
        idx = tmp_path / "MEMORY.md"
        idx.write_text("\n".join(["- x"] * (mb.MAX_LINES + 5)))
        out = self._run(monkeypatch, {"tool_name": "Edit",
                                      "tool_input": {"file_path": str(idx)}},
                        capsys)
        ctx = json.loads(out)["hookSpecificOutput"]
        assert "additionalContext" in ctx and "permissionDecision" not in ctx
        other = tmp_path / "NOTES.md"
        other.write_text(idx.read_text())
        assert self._run(monkeypatch, {"tool_name": "Edit", "tool_input":
                                       {"file_path": str(other)}}, capsys) == ""

    def test_kill_switch(self, tmp_path, monkeypatch, capsys):
        idx = tmp_path / "MEMORY.md"
        idx.write_text("\n".join(["- x"] * (mb.MAX_LINES + 5)))
        monkeypatch.setenv("TASKLOOP_MEMORY_BUDGET_DISABLE", "1")
        assert self._run(monkeypatch, {"tool_name": "Write", "tool_input":
                                       {"file_path": str(idx)}}, capsys) == ""
