# Hooks

Six Claude Code hooks make the task loop ambient — you never invoke it by
hand.
All are fail-open (any error → silent exit 0); only the toolbox nudge can
block, and only for registry entries you explicitly mark `enforce: true`.

| Event | Hook | What it does |
|---|---|---|
| UserPromptSubmit | `userprompt_autopilot_detect.py` | classifies every prompt (clear/fuzzy/none build initiative) and injects an `<autopilot-detect>` directive telling the agent to queue clear asks via `task-go` |
| SessionStart | `session_start_context.py` | briefs the new session: queue snapshot, next runnable task, recent mailbox messages |
| PreToolUse (Bash) | `pretool_toolbox_nudge.py` | when a shell command re-derives what a registered tool already does (its `territory` regex matches), nudges toward `toolbox run <name>` — advisory by default (once per session per tool), a hard block for entries marked `enforce: true`; escape marker `TOOLBOX_NUDGE_OVERRIDE`, kill switch `TOOLBOX_NUDGE_DISABLE=1` |
| Stop | `stop_queue_nudge.py` | nudges (never edits) when a session leaves an unrunnable note in the queue — give it a concrete goal so the loop can run it |
| Stop | `stop_crystallize_nudge.py` | spots operations the session derived by hand ≥3× (pipelines/heredocs/long one-liners, normalized) and suggests a dedupe-tagged `task-add` so the loop builds a reusable script — which then gets REGISTERED (`toolbox add`), closing the loop the PreToolUse nudge defends; `TASKLOOP_AUTO_CRYSTALLIZE=1` auto-files the top one |
| Stop | `stop_telemetry_nudge.py` | spots reusable scripts the session wrote with no observable fire/usage telemetry (argparse/`main`/`__main__` signal, no log/JSONL sink) and suggests a dedupe-tagged `task-add` to instrument them; `TASKLOOP_AUTO_TELEMETRY=1` auto-files the top one |

Install/refresh/remove (idempotent; backs up `~/.claude/settings.json`):

```bash
python3 hooks/install_hooks.py [--dry-run | --remove]
```

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
