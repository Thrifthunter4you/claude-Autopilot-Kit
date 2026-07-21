# Hooks

Three Claude Code hooks make the task loop ambient — you never invoke it by
hand.
All are fail-open (any error → silent exit 0) and never block your prompt or
session.

| Event | Hook | What it does |
|---|---|---|
| UserPromptSubmit | `userprompt_autopilot_detect.py` | classifies every prompt (clear/fuzzy/none build initiative) and injects an `<autopilot-detect>` directive telling the agent to queue clear asks via `task-go` |
| SessionStart | `session_start_context.py` | briefs the new session: queue snapshot, next runnable task, recent mailbox messages |
| Stop | `stop_queue_nudge.py` | nudges (never edits) when a session leaves an unrunnable note in the queue — give it a concrete goal so the loop can run it |

Install/refresh/remove (idempotent; backs up `~/.claude/settings.json`):

```bash
python3 hooks/install_hooks.py [--dry-run | --remove]
```

How the three hooks work together to keep the queue fed:
[../docs/queueing-and-gap-finding.md](../docs/queueing-and-gap-finding.md).

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
