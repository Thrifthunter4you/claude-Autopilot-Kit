import datetime

from taskloop import config, manifest, queue, worker
from taskloop.handoff import Handoff


def _mk(tmp_name="t.md", **loop):
    path = config.ACTIVE_DIR / tmp_name
    h = Handoff(path=path, front={}, body="# Goal\n\nx\n")
    base = {"status": "todo", "class": "safe", "priority": 3, "goal": "x",
            "repo": "/tmp", "lease": "", "strikes": 0}
    base.update(loop)
    h.set_loop(base)
    h.save()
    return h


def _clear_queue():
    for p in config.ACTIVE_DIR.glob("*.md"):
        p.unlink()


class TestHandoff:
    def test_roundtrip(self):
        h = _mk("rt.md", goal="round trip")
        loaded = Handoff.load(h.path)
        assert loaded.loop["goal"] == "round trip"
        assert loaded.body.startswith("# Goal")
        h.path.unlink()

    def test_unmanaged(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text("just a note\n")
        assert Handoff.load(p).loop is None


class TestQueue:
    def setup_method(self):
        _clear_queue()

    def test_eligibility_gates(self):
        assert queue.eligible(_mk("a.md"))
        assert not queue.eligible(_mk("b.md", status="needs-human"))
        assert not queue.eligible(_mk("c.md", **{"class": "destructive"}))
        assert not queue.eligible(_mk("d.md", lease="loop-1-1"))
        assert not queue.eligible(_mk("e.md", strikes=config.MAX_STRIKES))
        future = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(days=1)).isoformat()
        assert not queue.eligible(_mk("f.md", not_before=future))
        assert queue.eligible(_mk("g.md", not_before="not-a-date"))  # fail-open

    def test_priority_then_aging(self):
        _mk("low.md", priority=5)
        _mk("hi.md", priority=0)
        picked = queue.select_next()
        assert picked.path.name == "hi.md"
        # After the high-priority task is picked recently, a same-priority
        # never-picked task wins the band.
        queue.mark_picked(picked)
        picked.save()
        _mk("hi2.md", priority=0)
        assert queue.select_next().path.name == "hi2.md"

    def test_snapshot_matches_selection(self):
        _mk("a.md")
        _mk("b.md", lease="loop-1-1")
        snap = queue.snapshot()
        assert snap["eligible"] == 1
        assert snap["lease_held"] == 1


class TestManifest:
    def test_phase_and_task_flow(self):
        d = manifest.load("m1")
        phases = config.PROFILE_PHASES["small"]
        assert manifest.next_pending(d, phases) == "research"
        manifest.mark_phase(d, "research", artifact="a.md", summary="s")
        manifest.mark_phase(d, "design")
        manifest.mark_phase(d, "plan")
        assert manifest.next_pending(d, phases) == "execute"
        manifest.set_tasks(d, ["1", "2"])
        manifest.set_tasks(d, ["1", "2"])  # idempotent
        assert d["task_order"] == ["1", "2"]
        assert manifest.next_pending_task(d) == "1"
        manifest.mark_task(d, "1")
        assert manifest.next_pending_task(d) == "2"
        manifest.save(d)
        reloaded = manifest.load("m1")
        assert reloaded["phases"]["research"]["artifact"] == "a.md"
        assert reloaded["tasks"]["1"] == "done"


class TestWorkerParse:
    def test_last_result_wins(self):
        out = ('LOOP_RESULT: {"status": "continue", "outcome": "one"}\n'
               'noise\n'
               'LOOP_RESULT: {"status": "done", "outcome": "two"}\n')
        assert worker.parse_result(out)["status"] == "done"

    def test_missing_and_malformed(self):
        assert worker.parse_result("no verdict here") is None
        assert worker.parse_result("LOOP_RESULT: {not json}") is None

    def test_prompt_renders(self):
        _clear_queue()
        h = _mk("p.md", **{"class": "safe"}, recall_hint="the hint")
        prompt = worker.build_prompt(h)
        assert str(h.path) in prompt
        assert "the hint" in prompt
        assert "LOOP_RESULT" in prompt
        h.path.unlink()


class TestDrainerVerdicts:
    def setup_method(self):
        _clear_queue()
        for p in config.DONE_DIR.glob("*.md"):
            p.unlink()

    def test_done_archives(self):
        from taskloop import drainer
        h = _mk("v1.md", lease="loop-1-1", status="in-progress")
        drainer.apply_verdict(h, {"status": "done", "outcome": "ok"})
        assert not h.path.exists()
        moved = Handoff.load(config.DONE_DIR / "v1.md")
        assert moved.loop["status"] == "done"

    def test_continue_requeues_and_resets_strikes(self):
        from taskloop import drainer
        h = _mk("v2.md", lease="loop-1-1", status="in-progress", strikes=2)
        drainer.apply_verdict(h, {"status": "continue", "outcome": "next"})
        lp = Handoff.load(h.path).loop
        assert lp["status"] == "todo" and lp["lease"] == "" \
            and lp["strikes"] == 0

    def test_crash_strikes_then_parks(self):
        from taskloop import drainer
        h = _mk("v3.md", lease="loop-1-1", status="in-progress")
        for _ in range(config.MAX_STRIKES):
            drainer.apply_verdict(Handoff.load(h.path), None)
        assert Handoff.load(h.path).loop["status"] == "needs-human"

    def test_acceptance_command_gates_done(self):
        from taskloop import drainer
        h = _mk("v4.md", lease="loop-1-1", status="in-progress",
                acceptance_command="false")
        drainer.apply_verdict(h, {"status": "done", "outcome": "claimed"})
        lp = Handoff.load(h.path).loop
        assert lp["status"] == "todo"  # refused: requeued, not archived

    def test_stale_lease_reaped(self):
        from taskloop import drainer
        old = f"loop-{int(__import__('time').time()) - config.STALE_LEASE_S - 5}-999999"
        h = _mk("v5.md", lease=old)
        drainer.reap_stale_leases()
        assert Handoff.load(h.path).loop["lease"] == ""


class TestMailbox:
    def test_post_and_list(self, capsys):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "session_mailbox",
            Path(__file__).resolve().parents[1] / "state" / "session_mailbox.py")
        mb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mb)
        assert mb.post("claim", "editing foo.py", repo="/tmp") == 0
        mb.list_rows(since_hours=1)
        out = capsys.readouterr().out
        assert "editing foo.py" in out


class TestHookClassifier:
    def _classify(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "detect", Path(__file__).resolve().parents[1]
            / "hooks" / "userprompt_autopilot_detect.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.classify

    def test_verdicts(self):
        classify = self._classify()
        assert classify("build a rate limiter for the api service") == "clear"
        assert classify("maybe we should add caching somewhere?") == "fuzzy"
        assert classify("what does the drainer do?") == "none"
        assert classify("ok") == "none"
        assert classify("/help") == "none"
        assert classify("<system-reminder> build stuff") == "none"
