"""bin/token-audit: transcript parsing, carry math (size x turns-remaining),
re-read detection, and usage totals — on a synthetic session."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]

loader = importlib.machinery.SourceFileLoader(
    "token_audit", str(KIT / "bin" / "token-audit"))
spec = importlib.util.spec_from_loader("token_audit", loader)
assert spec is not None
audit = importlib.util.module_from_spec(spec)
loader.exec_module(audit)


def _assistant(blocks=(), usage=True):
    msg = {"content": list(blocks)}
    if usage:
        msg["usage"] = {"input_tokens": 10, "output_tokens": 50,
                        "cache_read_input_tokens": 1000,
                        "cache_creation_input_tokens": 200}
    return {"type": "assistant", "message": msg}


def _tool_use(tid, name, tool_input):
    return {"type": "tool_use", "id": tid, "name": name, "input": tool_input}


def _tool_result(tid, text):
    return {"type": "user",
            "message": {"content": [{"type": "tool_result",
                                     "tool_use_id": tid,
                                     "content": [{"type": "text",
                                                  "text": text}]}]}}


def _write(tmp_path, records):
    p = tmp_path / "session.jsonl"
    lines = [json.dumps(r) for r in records]
    lines.insert(1, "{broken json")  # malformed lines must be skipped
    p.write_text("\n".join(lines))
    return p


def test_analyze_totals_carriers_and_rereads(tmp_path):
    big = "x" * 4000  # ~1000 estimated tokens
    read_use = _tool_use("t2", "Read", {"file_path": "/repo/a.py"})
    records = [
        {"type": "user", "message": {"content": "session-start context here"}},
        _assistant([_tool_use("t1", "Bash", {"command": "pytest -q"})]),
        _tool_result("t1", big),
        _assistant([read_use]),
        _tool_result("t2", "short"),
        _assistant([_tool_use("t3", "Read", {"file_path": "/repo/a.py"})]),
        _tool_result("t3", "short again"),
        _assistant([]),
    ]
    a = audit.analyze(_write(tmp_path, records))

    assert a["turns"] == 4
    assert a["usage"]["output_tokens"] == 200
    assert a["usage"]["cache_read_input_tokens"] == 4000
    assert a["first_user_est"] == len("session-start context here") // 4

    top = a["carriers"][0]
    # The big result lands after turn 1 of 4 -> carries across 3 later turns.
    assert top["label"].startswith("Bash(pytest -q")
    assert top["est"] == 1000 and top["turns_after"] == 3
    assert top["carry"] == 3000

    assert a["re_reads"] == {"/repo/a.py": 2}


def test_late_output_carries_less_than_early_output(tmp_path):
    """Same payload, different position: the early copy must out-rank the
    late one — position, not size, is what the ranking exists to teach."""
    payload = "y" * 2000
    records = [
        _assistant([_tool_use("t1", "Bash", {"command": "early"})]),
        _tool_result("t1", payload),
        _assistant([]), _assistant([]),
        _assistant([_tool_use("t2", "Bash", {"command": "late"})]),
        _tool_result("t2", payload),
        _assistant([]),
    ]
    a = audit.analyze(_write(tmp_path, records))
    by_label = {c["label"]: c for c in a["carriers"]}
    assert by_label["Bash(early)"]["carry"] > by_label["Bash(late)"]["carry"]
