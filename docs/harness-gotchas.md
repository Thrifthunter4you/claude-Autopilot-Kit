# Harness gotchas — the ones that cost a week each

Things about running Claude Code unattended that are true, non-obvious, and
that the kit now handles or lints for. Each entry says what breaks, why, and
what the kit does about it, so you can recognise the symptom when it shows
up somewhere the kit doesn't reach.

## 1. Print mode kills background work at 600 s — with no verdict

`claude -p` cannot wait on an interactive turn, so the harness bounds how
long it waits for background tasks (subagents dispatched with
`run_in_background`, background Bash) before terminating the whole session:
600 s by default, with a one-line notice and NO final answer. A scheduled
job that drives a long skill this way produces nothing, week after week,
while looking healthy from the outside.

- **Symptom:** a cron/timer lane whose runs "complete" without output; a
  worker that dies at ~10 min regardless of task; the log line
  `Background tasks still running ... terminating`.
- **Kit:** workers set `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`
  (`worker_bg_wait_ceiling_ms`, default `0` = wait) so `worker_timeout_s` is
  the only bound, and the autopilot skill still runs dispatches foreground
  in CHUNKED mode. For your own `claude -p` scripts: export the variable and
  wrap the call in an outer `timeout`.

## 2. Hooks are snapshotted when a session starts

A hook you register now is invisible to every session that is already
running; only sessions started AFTER the registration carry it. Grading a
new hook against sessions that predate it will show "0 fires" and tempt you
to conclude it is broken.

- **Symptom:** the hook works in a fresh terminal but "never fires" in the
  long-running one you registered it from.
- **Kit:** the installers say so; measure a hook only on sessions started
  after it landed.

## 3. A transcript line is not a protocol

Anything that lands in a transcript can be forged by the transcript: a tool
result, a file the agent read, an example quoted in the task. "Emit exactly
one `RESULT:` line" worked until a task file contained the example line —
and "last match wins" made that the verdict.

- **Kit:** worker verdicts come from `--output-format json --json-schema`
  — the CLI constrains the final answer to the schema and returns it in
  `structured_output`; Python re-validates it; there is deliberately no
  fallback to text. See [architecture.md](architecture.md).

## 4. The memory index truncates from the tail

The index file loaded at session start has a practical cap (about 200
lines / 25 KB). Past it the tail is dropped silently — and the tail is
where you appended the freshest pointer.

- **Symptom:** "I told it last week and it forgot": the newest index lines
  never reach the model.
- **Kit:** [`hooks/posttool_memory_budget.py`](../hooks/posttool_memory_budget.py)
  warns on line/byte budget and on bullets that grew into paragraphs. Keep
  the index a table of contents ([memory model](../memory/MEMORY_MODEL.md)).

## 5. A spilled output that gets Read back was never spilled

Keeping bulk output on disk saves nothing if the next turn Reads the file
end to end — and an agent handed a path will do exactly that.

- **Kit:** [`hooks/pretool_spill_read_guard.py`](../hooks/pretool_spill_read_guard.py)
  denies unbounded Reads of spill files and names the bounded ways in
  (`spill show --grep`, `--head/--tail`, Read with offset + limit).

## 6. Environment reads happen at fork time

A long-running service reads `os.getenv` when its process starts, not when
you edit the environment. Flipping an env var to disable a lane, then
grading the lane an hour later, grades the OLD environment.

- **Symptom:** a kill switch that "doesn't work" until a restart nobody
  remembers doing.
- **Rule:** an env change to a running service needs a restart; grade on
  the output metric after the restart, not on the variable's value.

## 7. Machine-wide hooks arm every session

A prompt classifier that queues clear build asks is a fine ambient
mechanism inside a project you chose — and a surprise inside the throwaway
directory where you asked for a one-line script.

- **Kit:** hooks install per project (`install_hooks.py --project`) into
  `<repo>/.claude/settings.local.json`; `--global` exists, is explicit, and
  says what it implies.

## 8. A delta inside the rerun noise floor is not a small win

Rerunning the same benchmark, audit or rate command moves the number by
more than most claimed improvements. A before/after pair with no rerun is a
coin flip presented as a result.

- **Kit:** [`noise-floor`](../bin/noise-floor) measures the floor and grades
  the delta; see [token-economy.md](token-economy.md).

## 9. Output past the harness cap is already spilled — by the harness

A Bash result over roughly 30 KB never reaches a PostToolUse hook whole:
the harness cuts it, saves the full text itself and marks the result with
`persistedOutputPath`. A hook that "spills" that result writes a TRUNCATED
copy and labels it the full output.

- **Symptom:** a spill file that ends mid-stream; a model that trusts it.
- **Kit:** `posttool_output_spill.py` passes any result carrying
  `persistedOutputPath` through untouched; the harness's own preview
  already names the complete file.

## 10. A subagent's hook payload carries its PARENT's transcript

PreToolUse fires for subagent tool calls too, with an `agent_id` — but
`transcript_path` is the parent session's file. The subagent's own
transcript sits beside it: `<parent minus .jsonl>/subagents/agent-<id>.jsonl`
(or under `subagents/workflows/*/` for workflow subagents). Count turns
from the parent file and every subagent looks like the main loop.

- **Kit:** `pretool_subagent_turn_budget.py` resolves the subagent's own
  file and counts there; with no file it falls back to counting its own
  calls, an over-count it never uses to deny.
