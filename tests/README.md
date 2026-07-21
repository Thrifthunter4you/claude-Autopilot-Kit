# Tests

Pytest suite over the core semantics: handoff round-trips, queue
eligibility and selection, manifest phase/task flow, `LOOP_RESULT`
parsing, drainer verdicts (done / continue / strikes / acceptance gate /
stale-lease reaping), and the mailbox.

```bash
python3 -m pytest tests/ -q
```

Self-contained: [`conftest.py`](conftest.py) points `TASKLOOP_HOME` at a
temp dir before importing, so runs never touch your real state home.
