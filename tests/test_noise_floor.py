"""noise-floor: metric extraction, MDE rules by arm/N, verdicts + exit codes,
hard errors on bad samples, deterministic bootstrap."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader(
    "noise_floor", str(KIT / "bin" / "noise-floor"))
spec = importlib.util.spec_from_loader("noise_floor", loader)
assert spec is not None
nf = importlib.util.module_from_spec(spec)
loader.exec_module(nf)


def _seq_cmd(tmp_path, name, values):
    """A shell command that prints the next value from `values` per call."""
    state = tmp_path / f"{name}.idx"
    state.write_text("0")
    data = tmp_path / f"{name}.json"
    data.write_text(json.dumps(values))
    return (f"python3 -c \"import json,sys; p='{state}'; i=int(open(p).read()); "
            f"v=json.load(open('{data}')); open(p,'w').write(str(i+1)); "
            f"print('run', i, 'metric', v[i % len(v)])\"")


class TestExtract:
    def test_last_number_on_last_line(self):
        assert nf.extract("setup 1\n12 passed in 3.5s\n", None) == 3.5
        assert nf.extract("x\n\nvalue: 42\n\n", None) == 42

    def test_regex_group(self):
        assert nf.extract("recall=0.81 prec=0.5", r"recall=([0-9.]+)") == 0.81

    def test_errors(self):
        for text, rx in [("", None), ("no numbers here", None),
                         ("recall=0.8", r"prec=([0-9.]+)")]:
            with pytest.raises(nf.SampleError):
                nf.extract(text, rx)


class TestGrade:
    def test_given_delta_small_n_uses_half_spread(self):
        rep = nf.grade([10, 12, 11], None, 0.5, "higher", 1, 200, 0.95)
        assert rep["mde_rule"] == "half_baseline_spread" and rep["mde"] == 1.0
        assert rep["verdict"] == "INSIDE-NOISE-FLOOR" and rep["exit_code"] == 0

    def test_given_delta_clears_floor_both_directions(self):
        up = nf.grade([10, 12, 11], None, 3.0, "higher", 1, 200, 0.95)
        assert up["verdict"] == "SHIP"
        down = nf.grade([10, 12, 11], None, 3.0, "lower", 1, 200, 0.95)
        assert down["verdict"] == "DONT-SHIP" and down["exit_code"] == 3

    def test_measured_small_n_uses_pooled_spread(self):
        rep = nf.grade([10, 11], [13, 14], None, "higher", 1, 200, 0.95)
        assert rep["mde_rule"] == "half_pooled_spread" and rep["mde"] == 2.0
        assert rep["candidate"]["delta"] == 3.0 and rep["verdict"] == "SHIP"

    def test_bootstrap_rules_at_n5_and_deterministic(self):
        b = [10.0, 10.2, 9.9, 10.1, 10.0]
        given = nf.grade(b, None, 0.01, "higher", 7, 500, 0.95)
        assert given["mde_rule"] == "bootstrap_ci_half_width"
        assert given["verdict"] == "INSIDE-NOISE-FLOOR"
        c = [12.0, 12.1, 11.9, 12.2, 12.0]
        m1 = nf.grade(b, c, None, "higher", 7, 500, 0.95)
        m2 = nf.grade(b, c, None, "higher", 7, 500, 0.95)
        assert m1 == m2 and m1["mde_rule"] == "bootstrap_diff_ci_half_width"
        assert m1["verdict"] == "SHIP"

    def test_identical_baseline_has_zero_floor(self):
        rep = nf.grade([5, 5, 5], None, 0.001, "higher", 1, 200, 0.95)
        assert rep["mde"] == 0 and rep["verdict"] == "SHIP"


class TestCli:
    def test_measured_end_to_end(self, tmp_path, capsys):
        base = _seq_cmd(tmp_path, "b", [10, 11, 10])
        cand = _seq_cmd(tmp_path, "c", [20, 21, 20])
        code = nf.main(["--baseline", base, "--candidate", cand, "-n", "3",
                        "--direction", "higher", "--json"])
        rep = json.loads(capsys.readouterr().out)
        assert code == 0 and rep["verdict"] == "SHIP"
        assert rep["baseline"]["values"] == [10, 11, 10]

    def test_text_render_and_dont_ship_exit(self, tmp_path, capsys):
        base = _seq_cmd(tmp_path, "b", [10, 10, 10])
        code = nf.main(["--baseline", base, "--delta", "-2", "-n", "2",
                        "--direction", "higher"])
        out = capsys.readouterr().out
        assert code == 3 and out.startswith("VERDICT: DONT-SHIP")
        assert "MDE" in out and "half_baseline_spread" in out

    def test_failed_or_numberless_run_is_exit_1(self, tmp_path, capsys):
        assert nf.main(["--baseline", "false", "--delta", "1", "-n", "2"]) == 1
        assert nf.main(["--baseline", "echo nothing", "--delta", "1",
                        "-n", "2"]) == 1
        assert "noise-floor:" in capsys.readouterr().err

    def test_n_below_two_rejected(self):
        with pytest.raises(SystemExit):
            nf.main(["--baseline", "echo 1", "--delta", "1", "-n", "1"])
