"""taskloop.toolbox: registry roundtrip + validation, keyword find, cmd and
playbook execution with fire telemetry, and stats aggregation."""
import json
import sys

import pytest

from taskloop import toolbox


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(toolbox, "REGISTRY", tmp_path / "toolbox.yaml")
    monkeypatch.setattr(toolbox, "LOCK", tmp_path / "toolbox.yaml.lock")
    monkeypatch.setattr(toolbox, "FIRES", tmp_path / "fires.jsonl")
    monkeypatch.setattr(toolbox, "NUDGES", tmp_path / "nudges.jsonl")


def _register(**entry):
    toolbox.mutate(lambda reg: reg["tools"].append(entry))


ECHO = {"name": "echo-tool", "description": "prints its arguments",
        "cmd": [sys.executable, "-c", "import sys; print(sys.argv[1:])"]}


class TestRegistry:
    def test_missing_or_corrupt_file_is_empty_registry(self):
        assert toolbox.load() == {"tools": []}
        toolbox.REGISTRY.write_text(":\tnot yaml [")
        assert toolbox.load() == {"tools": []}

    def test_mutate_roundtrip_and_get(self):
        _register(**ECHO)
        assert toolbox.get("echo-tool")["description"] == ECHO["description"]
        assert toolbox.get("nope") is None

    def test_validate_rejects_the_unusable(self):
        good = dict(ECHO)
        assert toolbox.validate(good) == []
        assert toolbox.validate(good, existing_names={"echo-tool"})  # dup name
        assert toolbox.validate({"name": "Bad Name", "description": "x",
                                 "cmd": ["true"]})                   # bad name
        assert toolbox.validate({"name": "x", "description": ""})    # no desc
        assert toolbox.validate({"name": "x", "description": "d"})   # no cmd
        assert toolbox.validate({"name": "x", "description": "d",
                                 "cmd": ["true"],
                                 "playbook": {"steps": ["a"]}})      # both
        assert toolbox.validate({"name": "x", "description": "d",
                                 "cmd": ["true"],
                                 "territory": "("})                  # bad regex


class TestFind:
    def test_name_hits_outrank_description_hits(self):
        _register(name="db-health", description="check the database",
                  cmd=["true"])
        _register(name="unrelated", description="db health mentioned here",
                  cmd=["true"])
        hits = toolbox.find("db health")
        assert [e["name"] for _s, e in hits] == ["db-health", "unrelated"]

    def test_no_overlap_is_empty(self):
        _register(**ECHO)
        assert toolbox.find("kubernetes failover") == []


class TestRun:
    def test_cmd_runs_and_records_fire(self):
        _register(**ECHO)
        assert toolbox.run("echo-tool", extra_args=["hello"]) == 0
        fires = [json.loads(l) for l in open(toolbox.FIRES)]
        assert fires[0]["tool"] == "echo-tool" and fires[0]["ok"] is True

    def test_unknown_tool_is_127_no_fire(self):
        assert toolbox.run("ghost") == 127
        assert not toolbox.FIRES.exists()

    def test_failing_cmd_records_not_ok(self):
        _register(name="failer", description="always fails",
                  cmd=[sys.executable, "-c", "raise SystemExit(3)"])
        assert toolbox.run("failer") == 3
        fire = json.loads(open(toolbox.FIRES).readline())
        assert fire["ok"] is False and fire["exit"] == 3

    def test_playbook_interpolates_and_stops_on_failure(self, tmp_path):
        marker = tmp_path / "ran-{{step}}"
        _register(name="steps", description="two steps",
                  playbook={
                      "vars": {"first": str(tmp_path / "one")},
                      "steps": ["touch {{first}}",
                                sys.executable + " -c 'raise SystemExit(9)'",
                                "touch " + str(tmp_path / "never")],
                  })
        assert toolbox.run("steps") == 9
        assert (tmp_path / "one").exists()
        assert not (tmp_path / "never").exists()

    def test_playbook_var_override_and_unknown_var(self, tmp_path):
        _register(name="mk", description="touch a var-named file",
                  playbook={"vars": {"f": str(tmp_path / "default")},
                            "steps": ["touch {{f}}"]})
        assert toolbox.run("mk",
                           var_overrides={"f": str(tmp_path / "chosen")}) == 0
        assert (tmp_path / "chosen").exists()
        _register(name="broken", description="references undeclared var",
                  playbook={"steps": ["touch {{missing}}"]})
        assert toolbox.run("broken") == 2  # hard error, not silent-empty


class TestStats:
    def test_counts_fires_nudges_and_zero_fire_tools(self):
        _register(**ECHO)
        _register(name="dusty", description="never used", cmd=["true"])
        toolbox.run("echo-tool")
        toolbox.NUDGES.write_text(
            json.dumps({"tool": "echo-tool", "mode": "advisory"}) + "\n" +
            json.dumps({"tool": "echo-tool", "mode": "deny"}) + "\n")
        s = toolbox.stats()
        assert s["echo-tool"]["fires"] == 1
        assert s["echo-tool"]["advisory_nudges"] == 1
        assert s["echo-tool"]["blocked"] == 1
        assert s["dusty"]["fires"] == 0  # dead weight is visible
