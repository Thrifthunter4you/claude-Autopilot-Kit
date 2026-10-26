# Hooks

Ten Claude Code hooks make the task loop ambient — you never invoke it by
hand — and keep long sessions lean. They are a **per-project opt-in**: installed into
`<project>/.claude/settings.local.json`, so they fire only in sessions
started inside a repository you enabled, never machine-wide.
All are fail-open (any error → silent exit 0); only the toolbox nudge (for
registry entries you explicitly mark `enforce: true`), the spill read
guard (unbounded reads of spill files) and the opt-in subagent turn
ceiling can block, and each names the way through.

| Event | Hook | What it does |
|---|---|---|
| UserPromptSubmit | `userprompt_autopilot_detect.py` | classifies every prompt (clear/fuzzy/none build initiative) and injects an `<autopilot-detect>` directive telling the agent to queue clear asks via `task-go` |
| SessionStart | `session_start_context.py` | briefs the new session: queue snapshot, next runnable task, recent mailbox messages |
| PreToolUse (Bash) | `pretool_toolbox_nudge.py` | when a shell command re-derives what a registered tool already does (its `territory` regex matches), nudges toward `toolbox run <name>` — advisory by default (once per session per tool), a hard block for entries marked `enforce: true`; escape marker `TOOLBOX_NUDGE_OVERRIDE`, kill switch `TOOLBOX_NUDGE_DISABLE=1` |
| PreToolUse (Read) | `pretool_spill_read_guard.py` | denies an UNBOUNDED Read of a file under `~/.taskloop/spills/` — the bulk `spill run` kept out of context — and names the bounded ways in (`spill show --grep`, `--head/--tail`, Read with offset+limit); small spills and Reads with a `limit` pass; kill switch `TASKLOOP_SPILL_GUARD_DISABLE=1` |
| PreToolUse (working tools) | `pretool_subagent_turn_budget.py` | subagent calls only (payload carries `agent_id`; main-loop calls exit at once): counts the subagent's tool turns from its own transcript — parallel calls in one message are one turn — and injects a reminder at the budget (default 25; 2× for implementer types) and every half-budget after: batch, stop exploring, answer with what is unverified named, never weaken a verdict. Optional ceiling `TASKLOOP_TURN_CEILING_MULT=2` denies every tool but the answer at 2× budget; kill switch `TASKLOOP_TURN_BUDGET_DISABLE=1` |
| PostToolUse (Write/Edit) | `posttool_memory_budget.py` | after any edit to a `MEMORY.md`, warns (never blocks) when the index is over its line/byte budget — past the harness cap the tail is silently dropped at session start — or carries bullets over 200 chars, with a trimmed pointer-form suggestion per line; budgets env-tunable, kill switch `TASKLOOP_MEMORY_BUDGET_DISABLE=1` |
| PostToolUse (Bash, MCP) | `posttool_output_spill.py` | when a SUCCESSFUL Bash result or a plain-string MCP result is over 8 KB (4 KB inside a subagent), writes the full text to `~/.taskloop/spills/` and replaces what the model sees with a head/tail preview naming the file (`updatedToolOutput`). Error, interrupted, image, ambiguous-exit and harness-truncated results pass through; `# keep-output` in a command keeps it whole; each stream is cut only if it is itself big, and stderr keeps its tail in context; common secret shapes are masked on disk and in the preview (best effort — treat the spill directory like shell history); a spill that would save under 1 KB is skipped; spills older than `TASKLOOP_SPILL_RETENTION_DAYS` (7) are pruned. Kill switch `TASKLOOP_OUTPUT_SPILL_DISABLE=1` |
| Stop | `stop_queue_nudge.py` | nudges (never edits) when a session leaves an unrunnable note in the queue — give it a concrete goal so the loop can run it |
| Stop | `stop_crystallize_nudge.py` | spots operations the session derived by hand ≥3× (pipelines/heredocs/long one-liners, normalized) and suggests a dedupe-tagged `task-add` so the loop builds a reusable script — which then gets REGISTERED (`toolbox add`), closing the loop the PreToolUse nudge defends; `TASKLOOP_AUTO_CRYSTALLIZE=1` auto-files the top one |
| Stop | `stop_telemetry_nudge.py` | spots reusable scripts the session wrote with no observable fire/usage telemetry (argparse/`main`/`__main__` signal, no log/JSONL sink) and suggests a dedupe-tagged `task-add` to instrument them; `TASKLOOP_AUTO_TELEMETRY=1` auto-files the top one |

Install/refresh/remove for one project (idempotent; backs up the file it
edits; never touches hooks that aren't the kit's):

```bash
python3 hooks/install_hooks.py --project ~/dev/myapp [--dry-run | --remove]
```

The `*` matcher in the table means "every tool for that event **within an
opted-in project**" — it is event/tool matching, not a scope mechanism.
Upgrading from a kit that wrote `~/.claude/settings.json` (v0.2 or earlier):
`python3 hooks/install_hooks.py --remove-global` strips only the kit's own
entries from that file. `--global` still exists for people who want every
session on the machine hooked, and prints what that implies.

Every deny, reminder and rewrite is also recorded as one JSONL row by
[`hook_log.py`](hook_log.py) (a library, not a hook) in
`~/.taskloop/hook_decisions.jsonl` (rotated to `.1` past ~5 MB);
[`bin/hook-cost`](../bin/hook-cost) reports what each hook costs and
saves. `TASKLOOP_HOOK_LOG_DISABLE=1` turns the log off.

How the queue-side hooks work together to keep the queue fed:
[../docs/queueing-and-gap-finding.md](../docs/queueing-and-gap-finding.md).
How crystallize → `toolbox add` → territory nudge close the script-reuse
loop: [../taskloop/toolbox.py](../taskloop/toolbox.py) (module docstring).

## Why hooks instead of instructions

Instructions live in context and die at compaction; a hook fires on EVERY
event from outside the context window. Detection-in-hook +
actuation-in-agent is the split that survives long sessions: the hook is
deterministic regex (cheap, always on), the agent applies judgment to the
directive (skip trivial one-liners, extract the goal, pick the repo).

## Design rules the kit's hooks follow

1. **Fail-open, fast, no network.** A broken hook must never cost a prompt.
2. **Suggest, never edit.** Stop-time hooks print guidance; they don't
   mutate files behind your back.
3. **Idempotent via ledgers.** Nudge once per file (a `.processed_*.jsonl`
   ledger), not once per session.
4. **Recursion guards.** The prompt classifier ignores harness-injected
   blocks and the loop's own worker prompts — otherwise background workers
   would queue themselves forever.
5. **Enforcement is opt-in, measured, and escapable.** The toolbox nudge
   ships advisory; you escalate a single registry entry to `enforce: true`
   only after `toolbox stats` shows its advisories being ignored — and even
   a block names its escape marker. A hook that silently hard-blocks with
   no data and no exit teaches people to disable hooks.
