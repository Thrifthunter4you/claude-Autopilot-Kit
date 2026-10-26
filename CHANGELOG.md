# Changelog

## 0.6.0 — 2026-10

- **Breaking: the systemd units are renamed** to `taskloop.service` /
  `taskloop.timer`, matching the package name. The unit contents are
  unchanged. Installed copies keep running under their pre-0.6 names until
  you swap them; the steps are in
  [`systemd/README.md`](systemd/README.md#upgrading-from-05-or-earlier).
  Update any script that names the old units (`journalctl -u`,
  `systemctl status`).

## 0.5.1 — 2026-10

- **Docs:** harness gotcha 11 corrected. It said every event except
  PreCompact accepts `hookSpecificOutput`; on Claude Code 2.1.310 eleven
  events reject it (PreCompact, PostCompact, SessionEnd, StopFailure,
  TeammateIdle, TaskCreated, TaskCompleted, ConfigChange, WorktreeRemove,
  InstructionsLoaded, DirectoryAdded). The gotcha now lists them and notes
  that the rejection is silent except on a manual `/compact`:
  auto-compaction, SessionEnd, TaskCreated and TaskCompleted failures
  appear only in a `--debug-file` log.
  No hook changes: the kit's only hook on those events,
  `precompact_ledger.py`, already prints plain text.

## 0.5.0 — 2026-10

Long sessions: survive compaction, and know when to stop.

- **`precompact_ledger.py`** (PreCompact): writes a continuity ledger to
  `~/.taskloop/ledgers/` before every compaction — recent user messages,
  files edited and read, landed commits (hash + branch, parsed from
  `git commit` output), tool errors since the previous compaction, git
  status — and prints a plain-text directive the summarizer follows (keep
  in-flight edits, next steps, owed checks, commit hashes, decisions; name
  the ledger). The compaction number comes from the transcript's
  `compact_boundary` records, never from hook fires; from #2
  (`TASKLOOP_COMPACT_ESCALATE_AT`) the directive adds a wrap-up clause.
  Subagent compactions never escalate and never move the session pointer.
  Ledgers older than 30 days are pruned. Verified end-to-end against
  Claude Code 2.1.310 (both summaries named the ledger; the second carried
  the wrap-up into its next step).
- **`pretool_context_ceiling.py`** (PreToolUse, working tools): reads the
  newest model call's context size from the transcript tail and, at or
  over the ceiling (default 150K, `TASKLOOP_CONTEXT_CEILING_K`), injects a
  wrap-up reminder. Never blocks; main loop only; silent on the wrap-up
  calls (`task-add`/`task-go`, writes under `handoffs/`); one reminder per
  cooldown, serialized across a parallel batch.
- **`session_start_context.py`** points the session at the ledger after a
  compaction, or a new session at one written in the same directory within
  the hour.
- **Docs:** harness gotchas 11 (a PreCompact `hookSpecificOutput` fails the
  output schema and is dropped whole) and 12 (a hook fire is not a
  compaction); README "What's new in 0.5"; a context-ceiling line in the
  CLAUDE.md template.
- Test suite: 137 → 160.

## 0.4.0 — 2026-10

Context economy, enforced by hooks instead of habit.

- **`posttool_output_spill.py`** (PostToolUse, Bash + MCP): a successful
  result over 8 KB (4 KB in a subagent) goes to `~/.taskloop/spills/` and
  the model sees a head/tail preview naming the file, via
  `updatedToolOutput`. Error, interrupted, ambiguous-exit
  (`returnCodeInterpretation`) and harness-truncated
  (`persistedOutputPath`) and image results pass through; `# keep-output`
  opts a command out; each stream is cut only when it is itself big, and
  stderr keeps its tail in context; common secret shapes (provider keys,
  JWTs, Bearer tokens, URL credentials, `*_PASSWORD=` / JSON secret pairs)
  masked on disk and in the preview, best effort; a spill that saves
  < 1 KB is skipped and leaves no file; spills older than 7 days are
  pruned. Verified end-to-end against Claude Code 2.1.310.
- **`pretool_subagent_turn_budget.py`** (PreToolUse, working tools):
  counts a subagent's tool turns from its own transcript (parallel calls =
  one turn) and reminds it at the budget and every half-budget after;
  optional ceiling (`TASKLOOP_TURN_CEILING_MULT`) denies every tool except
  `StructuredOutput`. Main-loop calls exit immediately; parallel calls of
  one batch serialize on a lock (one reminder, not N); finished subagents'
  state is pruned after two days.
- **`hooks/hook_log.py` + `hook-cost`:** every kit hook that denies,
  reminds or rewrites appends one JSONL row (the toolbox nudge and spill
  read guard now log too); `hook-cost` reports per-hook counts, per-session
  means, bytes saved and subagents over budget. The log rotates past
  ~5 MB.
- **Docs:** harness gotchas 9 (harness-truncated output) and 10 (subagent
  payloads carry the parent's transcript path); README "What's new in 0.4"
  and a `hook-cost` walkthrough.
- Leftover internal wording removed from `agents/tier_policy.yaml` and the
  prompt classifier's recursion guard.
- Test suite: 96 → 137.

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
