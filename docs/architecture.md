# Architecture

```
 you (or a hook)                     unattended machinery
 ───────────────                     ────────────────────
 "build X" ──► autopilot-detect hook
                    │ (classifies your prompt)
                    ▼
              task-go "<goal>" ────► queue/active/<date>-<slug>.md
                    │                     loop: {status: todo, goal, repo, ...}
                    │
                    └─► drainer (single-flight, N worker threads)
                              │  select_next() by priority + aging
                              │  lease loop-<epoch>-<pid>, status in-progress
                              ▼
                        claude -p  ◄─ templates/autopilot_prompt.txt
                              │        (invokes the autopilot skill)
                              │
              ┌───────────────┴────────────────┐
              │ ONE unit per worker invocation │
              │ research → design → plan →     │
              │ execute (≤3 tasks) → finish    │
              │ state: runs/<run_id>.json      │
              └───────────────┬────────────────┘
                              ▼
                LOOP_RESULT: {"status": ...}
              done → queue/done/    continue → requeue (fresh worker resumes)
              needs-human/blocked → parked    crash → strike (3 = parked)
```

## The pieces

| Piece | File(s) | Job |
|---|---|---|
| Task files | `queue/active/*.md` | one markdown file per task; the `loop:` frontmatter block is the whole queue contract |
| Intake | `bin/task-add` | goal → task file; exact-hash + fuzzy dedup, dedupe-tags, follow-on cap |
| Queue | `taskloop/queue.py` | eligibility (status/class/lease/strikes/not_before) + selection (priority, then aging) |
| Drainer | `taskloop/drainer.py` | single-flight loop: claim → spawn worker → apply verdict |
| Worker | `taskloop/worker.py` | render prompt template → `claude -p` → parse `LOOP_RESULT` |
| Manifest | `taskloop/manifest.py`, `bin/run-manifest` | chunked-run state: phases, execute tasks, profile |
| Autopilot skill | `skills/autopilot/SKILL.md` | the conductor protocol each worker follows |
| Hooks | `hooks/*.py` | prompt-time detection, stop-time queue nudges, session-start state |
| Agents | `agents/*.md` | tiered subagent fleet the conductor dispatches |
| Memory MCP | `mcp/memory_mcp.py` | persistent file-based memory (save/search/read/list) |
| Context MCP | `mcp/context_mcp.py` | budget-bounded context packs (memory + grep + git legs) |
| Mailbox | `state/session_mailbox.py` | cross-session claims/landings |

## Why chunked workers (the core design decision)

A multi-hour autonomous run cannot live in one context window: it inflates,
compacts, and degrades. So the loop never tries. Each worker invocation does
ONE unit (a pipeline phase, or up to three small execute tasks), persists
its progress to the run manifest, and exits with `continue`. The drainer
immediately spawns a FRESH `claude -p` process that reloads the manifest and
picks up at the next unit — a clean context window every time, no human
handoff in between.

Three design consequences:

1. **The manifest is the only memory between units.** If it isn't saved, the
   work didn't happen — workers persist after every task, before yielding.
2. **The verdict line is the only protocol.** Workers end with one
   machine-parseable `LOOP_RESULT` line; everything else in the transcript is
   free-form. Crashed workers (no line) get strikes; three strikes parks the
   task `needs-human` instead of retrying forever.
3. **The conductor stays thin.** Inside a worker, each phase runs in a fresh
   subagent; the conductor holds only artifact paths + summaries. Context
   isolation applies at both layers.

## Why file-based everything

The queue is a directory of markdown files; the manifest is a JSON file; the
mailbox is a JSONL. No database, no daemon state, no locks beyond one
single-flight file. That means: everything is greppable, hand-editable,
diffable, and recoverable with `ls` and a text editor — which matters
precisely because an autonomous system will eventually do something you need
to inspect and undo by hand.

Concurrency safety comes from three small mechanisms instead of a database:
atomic writes (tmp + `os.replace`), leases with dead-holder reaping, and a
claim mutex inside the drainer.

## Safety model

- **Class gates**: `destructive`-class tasks never auto-run — intake refuses
  to file them and selection refuses to pick them (same knob, can't drift).
- **Hard-stops in every prompt**: money, deletion, external sends,
  credentials, destructive git → the worker reports `needs-human` and stops.
- **Acceptance commands**: a task can carry `acceptance_command`; a worker's
  `done` verdict only stands if it exits 0.
- **Strikes**: crash-looping tasks park themselves instead of burning the
  queue.
- **Human parking lot**: `needs-human` tasks wait for a person (or the
  triage-judge agent proposes promote/park/likely-done verdicts with
  receipts).
