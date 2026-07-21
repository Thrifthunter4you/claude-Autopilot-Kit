# claude-workflow-kit

A self-contained autonomous workflow system for [Claude Code](https://claude.com/claude-code):
a file-based **task queue** that runs goals unattended in chunked
`claude -p` workers, **hooks** that make sessions queue their own work and
find the gaps, a tiered **agent fleet**, and **memory/context MCP servers**
that give every session state awareness.

You say "build X" — a hook detects it, queues it, and a background loop
researches, designs, plans, executes, and reports, one clean context window
per unit, while your interactive session stays light.

```
you: "add rate limiting to the API"
        │  (autopilot-detect hook classifies the prompt)
        ▼
task-go "add rate limiting to the API" --repo ~/dev/myapi
        │  files queue/active/2026-01-15-add-rate-limiting....md
        ▼
drainer ──► claude -p (worker 1): research  ──► manifest
        ──► claude -p (worker 2): design+critic
        ──► claude -p (worker 3): plan+critic
        ──► claude -p (worker 4..n): execute (≤3 tasks each)
        ──► claude -p (final): finish + report
        ▼
LOOP_RESULT: {"status":"done","outcome":"rate limiting shipped, 12 tests green"}
```

## What's in the box

- **`taskloop/` + `bin/`** — the queue. Tasks are markdown files with a
  YAML `loop:` block; `task-add` files them (with three layers of dedup),
  `task-go` files + kicks the drainer, the drainer runs each task in fresh
  `claude -p` workers with leases, strikes, and an acceptance-command gate.
- **`skills/autopilot/`** — the conductor protocol workers follow:
  right-size the pipeline (trivial/small/large), one fresh subagent per
  phase, adversarial critics instead of human approval, checkpoint & yield.
- **`hooks/`** — prompt-time build-initiative detection, stop-time "make it
  runnable" queue nudges, session-start state briefing. Installed
  idempotently into `~/.claude/settings.json` by `hooks/install_hooks.py`.
- **`agents/`** — a tiered subagent fleet (finder/verifier/judge/critic/
  dedup/tester/fixer/synth/scout/triage-judge) plus the design guide and
  template for minting your own.
- **`mcp/`** — two dependency-light MCP servers: persistent file-based
  **memory** (save/search/read/list) and **context packs** (memory + grep +
  git history legs, budget-bounded).
- **`memory/` + `templates/`** — the memory model, a starter CLAUDE.md
  carrying the session discipline, and the worker prompt templates.
- **`docs/`** — architecture, how the queue feeds itself, advanced patterns.

## Install

Requires: Python 3.10+, PyYAML, the `claude` CLI on PATH.
Optional: `pip install mcp` for the MCP servers.

```bash
git clone <this repo> ~/claude-workflow-kit
cd ~/claude-workflow-kit
./install.sh           # symlinks bins to ~/.local/bin, creates ~/.taskloop,
                       # installs hooks (with backup), copies agents+skill
```

Then set your default repo:

```bash
cat > ~/.taskloop/config.yaml <<'EOF'
default_repo: /home/you/dev/yourproject
worker_model: sonnet
max_concurrency: 1
# Unattended queues need permission autonomy. Default is acceptEdits;
# if you fully trust your queue contents:
# claude_args: ["--dangerously-skip-permissions"]
EOF
```

## Try it

```bash
# Queue a goal and kick the loop:
task-go "add a --json flag to the report script" --repo ~/dev/yourproject

# Watch:
tail -f ~/.taskloop/logs/drain.log
task-list --all
python3 -m json.tool ~/.taskloop/runs/<run_id>.json   # phase progress

# Or let the hook do it: with hooks installed, typing a clear build ask
# into any Claude Code session auto-queues it.
```

## The ideas that matter

1. **Chunked context.** No run ever lives in one context window. Each
   worker does ONE unit (a phase, or ≤3 small tasks), persists to the run
   manifest, and yields; a fresh worker resumes. See
   `docs/architecture.md`.
2. **The queue feeds itself.** A prompt hook queues your build asks;
   workers file verified follow-on discoveries (capped + deduped); a stop
   hook nudges unrunnable notes into runnable tasks; a maintenance sweep
   turns confirmed defects into fix tasks with stable dedupe tags. See
   `docs/queueing-and-gap-finding.md`.
3. **Critics instead of approvals.** Design and plan phases are gated by
   adversarial critic subagents with one repair round; unresolvable gaps
   park `needs-human` with the exact question a human must answer.
4. **Tier the fleet.** Cheap models sweep, mid models judge, your best
   model only synthesizes. See `agents/AGENT_DESIGN.md`.
5. **Files over databases.** Queue, manifest, memory, mailbox — all plain
   files: greppable, diffable, hand-editable, recoverable.

## Safety

Unattended autonomy is opt-in and bounded: `destructive`-class tasks never
auto-run; every worker prompt carries hard-stops (money, deletion, external
sends, credentials, destructive git → `needs-human`); crash-looping tasks
strike out and park; `acceptance_command` lets you make "done" objective.
Read `docs/architecture.md#safety-model` before raising autonomy.

## License

MIT — see `LICENSE`.
