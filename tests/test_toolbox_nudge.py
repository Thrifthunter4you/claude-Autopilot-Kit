"""pretool_toolbox_nudge: territory matching (unless/enforce precedence),
override marker, advisory throttle, deny/advisory JSON output, kill switches."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from taskloop import toolbox

KIT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "pretool_toolbox_nudge", KIT / "hooks" / "pretool_toolbox_nudge.py")
assert spec is not None and spec.loader is not None
nudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge)


ADVISORY = {"name": "db-health", "description": "database health check",
            "cmd": ["true"], "territory": r"psql\s+.*-c\s"}
ENFORCED = {"name": "deployer", "description": "the only sanctioned deploy",
            "cmd": ["true"], "territory": r"rsync .*prod", "enforce": True,
            "unless": "--dry-run"}


class TestMatches:
    def test_territory_hits_and_unless_exempts(self):
        assert nudge.matches("psql app -c 'select 1'",
                             [ADVISORY])[0]["name"] == "db-health"
        assert nudge.matches("rsync ./ prod:/srv --dry-run", [ENFORCED]) is None
        assert nudge.matches("ls -la", [ADVISORY, ENFORCED]) is None

    def test_enforced_match_beats_earlier_advisory(self):
        both = "psql app -c x && rsync ./ prod:/srv"
        entry, enforced = nudge.matches(both, [ADVISORY, ENFORCED])
        assert entry["name"] == "deployer" and enforced

    def test_broken_registry_regex_is_skipped(self):
        bad = {"name": "b", "description": "d", "cmd": ["true"],
               "territory": "("}
        assert nudge.matches("anything", [bad, ADVISORY]) is None


class TestMain:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nudge, "HOME", tmp_path)
        monkeypatch.setattr(nudge, "LEDGER", tmp_path / "nudges.jsonl")
        monkeypatch.setattr(nudge, "SEEN_DIR", tmp_path / "seen")
        monkeypatch.setattr(toolbox, "REGISTRY", tmp_path / "toolbox.yaml")
        monkeypatch.setattr(toolbox, "LOCK", tmp_path / "toolbox.yaml.lock")
        toolbox.mutate(lambda r: r["tools"].extend([ADVISORY, ENFORCED]))
        monkeypatch.delenv("TOOLBOX_NUDGE_DISABLE", raising=False)
        monkeypatch.delenv("TOOLBOX_NUDGE_ENFORCE_DISABLE", raising=False)

    def _run(self, monkeypatch, capsys, command, session="sess-1",
             tool_name="Bash"):
        payload = {"tool_name": tool_name, "session_id": session,
                   "tool_input": {"command": command}}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        with pytest.raises(SystemExit) as exc:
            nudge.main()
        assert exc.value.code in (0, None)
        out = capsys.readouterr().out.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None

    def test_advisory_injects_context_once_per_session(self, monkeypatch,
                                                       capsys):
        out = self._run(monkeypatch, capsys, "psql app -c 'select 1'")
        assert "toolbox run db-health" in out["additionalContext"]
        assert "permissionDecision" not in out  # command still runs
        assert self._run(monkeypatch, capsys, "psql app -c 'x'") is None
        assert self._run(monkeypatch, capsys, "psql app -c 'x'",
                         session="other") is not None  # per-session throttle

    def test_enforced_denies_every_time_with_instruction(self, monkeypatch,
                                                         capsys):
        for _ in range(2):  # no throttle on denies
            out = self._run(monkeypatch, capsys, "rsync ./ prod:/srv")
            assert out["permissionDecision"] == "deny"
            assert "toolbox run deployer" in out["permissionDecisionReason"]
            assert nudge.OVERRIDE_MARKER in out["permissionDecisionReason"]

    def test_override_marker_passes_and_is_logged(self, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys,
                        "rsync ./ prod:/srv  # TOOLBOX_NUDGE_OVERRIDE why")
        assert out is None
        rows = [json.loads(l) for l in open(nudge.LEDGER)]
        assert rows[-1]["mode"] == "override"

    def test_enforce_disable_demotes_deny_to_advisory(self, monkeypatch,
                                                      capsys):
        monkeypatch.setenv("TOOLBOX_NUDGE_ENFORCE_DISABLE", "1")
        out = self._run(monkeypatch, capsys, "rsync ./ prod:/srv")
        assert out is not None and "permissionDecision" not in out

    def test_kill_switch_and_non_bash_and_self_calls_pass(self, monkeypatch,
                                                          capsys):
        monkeypatch.setenv("TOOLBOX_NUDGE_DISABLE", "1")
        assert self._run(monkeypatch, capsys, "psql app -c x") is None
        monkeypatch.delenv("TOOLBOX_NUDGE_DISABLE")
        assert self._run(monkeypatch, capsys, "psql app -c x",
                         tool_name="Read") is None
        assert self._run(monkeypatch, capsys,
                         "toolbox run db-health") is None  # own invocation
