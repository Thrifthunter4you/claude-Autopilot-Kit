"""posttool_output_spill: rewrite only big SUCCESS results of eligible tools;
error/ambiguous shapes, keep-marker and non-paying spills pass through."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "posttool_output_spill", KIT / "hooks" / "posttool_output_spill.py")
assert spec is not None and spec.loader is not None
spill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spill)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKLOOP_HOME", str(tmp_path))
    for var in ("TASKLOOP_OUTPUT_SPILL_THRESHOLD", "TASKLOOP_OUTPUT_SPILL_TOOLS",
                "TASKLOOP_OUTPUT_SPILL_MIN_SAVING", "TASKLOOP_OUTPUT_SPILL_DISABLE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _bash(stdout, stderr="", command="make test", **extra):
    resp = {"stdout": stdout, "stderr": stderr, "interrupted": False,
            "isImage": False, **extra}
    return {"session_id": "s1", "tool_use_id": "toolu_abc123", "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_response": resp}


BIG = "".join(f"line {i} of the build log\n" for i in range(3000))


class TestRewrite:
    def test_big_bash_success_is_spilled(self, _home):
        out = spill.process(_bash(BIG))
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PostToolUse"
        updated = hso["updatedToolOutput"]
        assert set(updated) >= {"stdout", "stderr", "interrupted", "isImage"}
        assert updated["stdout"].startswith("[Output spilled:")
        assert len(updated["stdout"]) < 6000
        files = list((_home / "spills").glob("spill-*.txt"))
        assert len(files) == 1 and files[0].read_text() == BIG
        assert str(files[0]) in updated["stdout"]
        assert "line 0 of" in updated["stdout"] and "line 2999 of" in updated["stdout"]

    def test_decision_is_logged_with_saving(self, _home):
        spill.process(_bash(BIG))
        rows = [json.loads(l) for l in (_home / "hook_decisions.jsonl").read_text().splitlines()]
        assert rows[-1]["hook"] == "output_spill" and rows[-1]["decision"] == "rewrite"
        assert rows[-1]["saved_bytes"] > 50_000

    def test_mcp_string_result_is_spilled(self, _home):
        payload = {"tool_name": "mcp__docs__search", "tool_use_id": "t2",
                   "tool_input": {"q": "x"}, "tool_response": BIG}
        updated = spill.process(payload)["hookSpecificOutput"]["updatedToolOutput"]
        assert isinstance(updated, str) and updated.startswith("[Output spilled:")

    def test_big_stderr_keeps_its_tail(self, _home):
        err = "warn\n" * 2000 + "FATAL: disk full\n"
        updated = spill.process(_bash(BIG, stderr=err))["hookSpecificOutput"]["updatedToolOutput"]
        assert "FATAL: disk full" in updated["stderr"]
        assert len(updated["stderr"]) < 2200
        text = next((_home / "spills").glob("spill-*.txt")).read_text()
        assert "--- stderr ---" in text and "FATAL: disk full" in text

    def test_subagent_threshold_is_lower(self, _home):
        mid = "x" * 79 + "\n"
        payload = _bash(mid * 75)                      # ~6 KB
        assert spill.process(payload) is None
        payload["agent_id"] = "a1"
        assert spill.process(payload) is not None


class TestPassThrough:
    @pytest.mark.parametrize("extra", [
        {"interrupted": True},
        {"exitCode": 1},
        {"returnCodeInterpretation": "No matches found"},
        {"persistedOutputPath": "/tmp/x.txt"},
    ])
    def test_non_success_shapes_untouched(self, extra, _home):
        assert spill.process(_bash(BIG, **extra)) is None
        assert not (_home / "spills").exists()

    def test_small_output_untouched(self):
        assert spill.process(_bash("ok\n" * 10)) is None

    def test_keep_marker(self):
        assert spill.process(_bash(BIG, command="cat report.md  # keep-output")) is None

    def test_first_party_tools_untouched(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/x"},
                   "tool_response": {"type": "text", "file": {"content": BIG}}}
        assert spill.process(payload) is None

    def test_mcp_non_string_untouched(self):
        payload = {"tool_name": "mcp__x__y", "tool_response": [{"type": "text", "text": BIG}]}
        assert spill.process(payload) is None

    def test_tools_env_turns_mcp_off(self, monkeypatch):
        monkeypatch.setenv("TASKLOOP_OUTPUT_SPILL_TOOLS", "Bash")
        assert spill.process({"tool_name": "mcp__x__y", "tool_response": BIG}) is None

    def test_spill_that_does_not_pay_is_skipped(self, _home, monkeypatch):
        monkeypatch.setenv("TASKLOOP_OUTPUT_SPILL_MIN_SAVING", "100000")
        assert spill.process(_bash(BIG)) is None
        assert list((_home / "spills").glob("*")) == []


# Fixtures are assembled at runtime so no secret-shaped literal sits in the
# source for scanners to flag.
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_PEM = ("-----BEGIN RSA " + "PRIVATE KEY-----\nabc\n"
            "-----END RSA " + "PRIVATE KEY-----")


class TestMasking:
    def test_secret_shapes_masked_on_disk_and_in_preview(self, _home):
        secret = "sk-ant-" + "A" * 40
        body = f"api_key={secret}\n{FAKE_AWS}\n" + BIG
        updated = spill.process(_bash(body))["hookSpecificOutput"]["updatedToolOutput"]
        disk = next((_home / "spills").glob("spill-*.txt")).read_text()
        for text in (disk, updated["stdout"]):
            assert secret not in text and FAKE_AWS not in text
            assert "api_key=[MASKED]" in text

    def test_private_key_block_masked(self):
        assert spill.mask_secrets(f"x\n{FAKE_PEM}\ny") == "x\n[MASKED]\ny"


class TestHookIO:
    def test_main_prints_rewrite_and_kill_switch(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_bash(BIG))))
        assert spill.main() == 0
        assert "updatedToolOutput" in capsys.readouterr().out
        monkeypatch.setenv("TASKLOOP_OUTPUT_SPILL_DISABLE", "1")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_bash(BIG))))
        assert spill.main() == 0
        assert capsys.readouterr().out == ""

    def test_garbage_input_fails_open(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
        assert spill.main() == 0
        assert capsys.readouterr().out == ""


class TestReviewRegressions:
    def test_common_secret_pairs_masked_but_counters_kept(self):
        v1, v2 = "abcDEF" + "123456", "zzzYYY" + "987654"
        pair = '{"client_' + 'secret": "' + v1 + '", "access_' + 'token":"' + v2 + '"}'
        text = "\n".join([
            "DB_PASSWORD=hunter2hunter",
            pair,
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
            "postgres://app:s3cretpass@db.internal:5432/prod",
            "max_tokens=100000 input_tokens: 123456",
        ])
        out = spill.mask_secrets(text)
        for leaked in ("hunter2hunter", "abcDEF123456", "zzzYYY987654",
                       "abcdefghijklmnopqrstuvwxyz012345", "s3cretpass"):
            assert leaked not in out
        assert "DB_PASSWORD=[MASKED]" in out and "Bearer [MASKED]" in out
        assert "postgres://app:[MASKED]@db.internal" in out
        assert "max_tokens=100000 input_tokens: 123456" in out

    def test_image_results_pass_through(self):
        assert spill.process(_bash("data:image/png;base64," + "A" * 20000,
                                   isImage=True)) is None

    def test_small_stdout_beside_big_stderr_is_not_duplicated(self, _home):
        stdout = "".join(f"result row {i}\n" for i in range(150))     # ~2 KB
        err = "warning: deprecated\n" * 600 + "done\n"
        updated = spill.process(_bash(stdout, stderr=err))["hookSpecificOutput"][
            "updatedToolOutput"]
        assert updated["stdout"] == stdout
        assert updated["stderr"].startswith("[stderr tail; full text in ")
        assert updated["stderr"].rstrip().endswith("done")

    def test_old_spills_pruned_on_write(self, _home, monkeypatch):
        import os
        import time
        d = _home / "spills"
        d.mkdir()
        old = d / "spill-old.txt"
        old.write_text("x")
        t = time.time() - 30 * 86400
        os.utime(old, (t, t))
        spill.process(_bash(BIG))
        assert not old.exists() and len(list(d.glob("spill-*.txt"))) == 1
