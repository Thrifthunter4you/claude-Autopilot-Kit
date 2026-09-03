# Changelog

## 0.3.0 — 2026-09

Workflow hardening and measurement, from a month of running the loop
unattended.

- **Worker verdicts are schema-validated CLI output.** `taskloop/worker.py`
  spawns `claude -p --output-format json --json-schema` and reads only the
  envelope's `structured_output`; the magic `LOOP_RESULT:` transcript line
  is gone (it was forgeable by any file the worker read). No text fallback.
- **Worker knobs:** `worker_effort` (`--effort` passthrough) and
  `worker_bg_wait_ceiling_ms` — print mode killed sessions whose background
  tasks outlived a 600 s ceiling, verdict-less; the kit lifts it so
  `worker_timeout_s` is the only bound.
- **Hooks are a per-project opt-in.** `install_hooks.py --project <repo>`
  writes `<repo>/.claude/settings.local.json`; `--remove-global` migrates a
  0.2 install; `--global` is explicit. `install.sh --project`.
- **New hooks:** `pretool_spill_read_guard.py` (no unbounded Reads of spill
  files) and `posttool_memory_budget.py` (memory-index line/byte/bullet
  budgets, warn-only).
- **`noise-floor`:** grade a metric delta against the empirical rerun noise
  floor of its baseline command — SHIP / INSIDE-NOISE-FLOOR / DONT-SHIP,
  MDE rule named, exit 3 to gate scripts.
- **Docs:** [harness-gotchas.md](docs/harness-gotchas.md) (eight
  non-obvious failure modes and what the kit does about each);
  token-economy gains the noise-floor section.
- Test suite: 57 → 96.

## 0.2.0 — 2026-08

- Toolbox script registry + territory nudge hook; `token-audit`, `spill`;
  tier policy as a drift-linted single source of truth; first-visitor
  README pass.

## 0.1.0 — 2026-07

- Autonomous task loop (queue, drainer, chunked workers, manifests),
  prompt/stop/session hooks, tiered agent fleet, memory + context MCP
  servers, systemd timer.
