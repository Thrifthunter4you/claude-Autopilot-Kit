"""install_hooks: per-project target, non-clobber of foreign hooks, idempotent
replace, project-limited removal, --remove-global migration, CLI guards."""
import importlib.util
import json
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "install_hooks", KIT / "hooks" / "install_hooks.py")
assert spec is not None and spec.loader is not None
ih = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ih)

FOREIGN = {"matcher": "*", "hooks": [{"type": "command",
                                      "command": "python3 /elsewhere/mine.py"}]}


def _kit_cmds(settings: dict) -> list:
    return [h["command"] for groups in settings.get("hooks", {}).values()
            for g in groups for h in g["hooks"] if str(KIT) in h["command"]]


class TestCli:
    def test_project_required_before_any_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
        with pytest.raises(SystemExit) as e:
            ih.main([])
        assert e.value.code == 2
        assert not (tmp_path / "claude").exists()

    def test_project_must_be_a_directory(self, tmp_path):
        with pytest.raises(SystemExit):
            ih.main(["--project", str(tmp_path / "missing")])

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        assert ih.main(["--project", str(tmp_path), "--dry-run"]) == 0
        assert not ih.project_settings(tmp_path).exists()
        assert "would update" in capsys.readouterr().out


class TestProjectInstall:
    def test_writes_only_the_project_local_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
        assert ih.main(["--project", str(tmp_path)]) == 0
        target = ih.project_settings(tmp_path)
        assert target.exists()
        assert not (tmp_path / "claude").exists()
        data = json.loads(target.read_text())
        assert len(_kit_cmds(data)) == len(ih.KIT_HOOKS)
        # every registered hook lands under its own event with its own matcher
        for event, matcher, cmd in ih.KIT_HOOKS:
            assert any(g["matcher"] == matcher and
                       g["hooks"][0]["command"] == cmd
                       for g in data["hooks"][event]), (event, matcher)
        assert all(g["matcher"] != "*" for g in data["hooks"]["PreToolUse"])

    def test_foreign_hook_survives_and_kit_is_idempotent(self, tmp_path):
        target = ih.project_settings(tmp_path)
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"hooks": {"Stop": [FOREIGN]},
                                      "theme": "dark"}))
        ih.main(["--project", str(tmp_path)])
        ih.main(["--project", str(tmp_path)])          # second run: no dupes
        data = json.loads(target.read_text())
        assert data["theme"] == "dark"
        assert FOREIGN in data["hooks"]["Stop"]
        assert len(_kit_cmds(data)) == len(ih.KIT_HOOKS)
        assert len(list(target.parent.glob("*.bak-*"))) == 1  # one real write

    def test_remove_is_project_limited(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        ih.main(["--project", str(tmp_path)])
        ih.main(["--project", str(other)])
        ih.main(["--project", str(tmp_path), "--remove"])
        assert _kit_cmds(json.loads(ih.project_settings(tmp_path).read_text())) == []
        assert len(_kit_cmds(json.loads(ih.project_settings(other).read_text()))) \
            == len(ih.KIT_HOOKS)

    def test_invalid_json_is_a_hard_error(self, tmp_path):
        target = ih.project_settings(tmp_path)
        target.parent.mkdir(parents=True)
        target.write_text("{not json")
        with pytest.raises(SystemExit) as e:
            ih.main(["--project", str(tmp_path)])
        assert e.value.code == 1
        assert target.read_text() == "{not json"


class TestGlobalMigration:
    def test_remove_global_strips_only_kit_entries(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
        legacy = tmp_path / "settings.json"
        legacy.write_text(json.dumps({"hooks": {
            "Stop": [FOREIGN, ih._entry(ih.KIT_HOOKS[3][2], "*")],
            "SessionStart": [ih._entry(ih.KIT_HOOKS[1][2], "*")]},
            "theme": "light"}))
        assert ih.main(["--remove-global"]) == 0
        data = json.loads(legacy.read_text())
        assert data["theme"] == "light"
        assert data["hooks"] == {"Stop": [FOREIGN]}
        assert _kit_cmds(data) == []

    def test_global_is_explicit_and_says_so(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
        assert ih.main(["--global"]) == 0
        assert "EVERY" in capsys.readouterr().err
        assert len(_kit_cmds(json.loads((tmp_path / "settings.json").read_text()))) \
            == len(ih.KIT_HOOKS)
