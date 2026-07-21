# claude-workflow-kit

**claude-workflow-kit** turns [Claude Code](https://claude.com/claude-code)
into an unattended engineering loop: queue a one-line goal and it gets
researched, designed, planned, executed, and reported — one clean context
window per phase, every step on disk. It is four pieces that compose: a
file-backed **task loop**, **hooks** that make sessions queue their own
work, a tiered **agent fleet**, and **memory/context MCP servers** that
give every session state awareness.

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

**Contents:** [What's in the box](#whats-in-the-box) ·
[Why this exists](#why-this-exists) · [Install](#install) ·
[Try it](#try-it) · [The ideas that matter](#the-ideas-that-matter) ·
[Safety](#safety) · [License](#license)

## What's in the box

- [**`taskloop/`**](taskloop/) + [**`bin/`**](bin/) — the queue. Tasks are
  markdown files with a YAML `loop:` block; `task-add` files them (with
  three layers of dedup), `task-go` files + kicks the drainer, and the
  drainer runs each task in fresh `claude -p` workers with leases, strikes,
  and an acceptance-command gate.
- [**`skills/autopilot/`**](skills/autopilot/SKILL.md) — the conductor
  protocol workers follow: right-size the pipeline (trivial/small/large),
  one fresh subagent per phase, adversarial critics instead of human
  approval, checkpoint & yield.
- [**`hooks/`**](hooks/README.md) — prompt-time build-initiative detection,
  stop-time "make it runnable" queue nudges, repeated-operation
  crystallize nudges (hand-derived it 3×? the loop builds you a script),
  session-start state briefing. Installed idempotently into
  `~/.claude/settings.json` by
  [`hooks/install_hooks.py`](hooks/install_hooks.py).
- [**`agents/`**](agents/AGENT_DESIGN.md) — a tiered subagent fleet
  (finder/verifier/judge/critic/dedup/tester/fixer/synth/scout/
  triage-judge) plus the design guide and a template for minting your own.
- [**`mcp/`**](mcp/README.md) — two dependency-light MCP servers:
  persistent file-based **memory** (save/search/read/list) and **context
  packs** (memory + grep + git-history legs, budget-bounded).
- [**`memory/`**](memory/MEMORY_MODEL.md) + [**`templates/`**](templates/)
  — the memory model, a starter CLAUDE.md carrying the session discipline,
  and the worker prompt templates.
- **`docs/`** — [architecture](docs/architecture.md),
  [how the queue feeds itself](docs/queueing-and-gap-finding.md),
  [advanced patterns](docs/advanced-patterns.md).

## Why this exists

Long autonomous runs fail for boring reasons: the context window fills up
and quality degrades; the work lives in one fragile session that dies with
it; "found X, should fix it" notes pile up that nothing ever executes; and
unbounded autonomy is rightly scary. This kit is a set of small,
inspectable mechanisms aimed at exactly those failures — chunked workers so
no run outgrows its context, file-backed state so everything resumes,
hooks so the queue fills itself, and class gates + hard-stops + strikes so
autonomy stays bounded. No database, no framework: plain files and plain
Python you can read in an afternoon.

## Install

Requires **Python 3.10+**, **PyYAML**, and the **`claude` CLI** on PATH.

```bash
git clone https://github.com/OWNER/claude-workflow-kit.git ~/claude-workflow-kit
cd ~/claude-workflow-kit
python3 -m pip install --user pyyaml   # or: sudo apt install python3-yaml
./install.sh
```

Replace `OWNER` with this repository's owner (or copy the clone URL from
the **Code** button above). `install.sh` symlinks the bins into
`~/.local/bin`, creates the `~/.taskloop` state home, installs the hooks
(after backing up `~/.claude/settings.json`), and links the agents and
autopilot skill — it is idempotent, so re-run it after every `git pull`.

Optional: `python3 -m pip install --user mcp` for the
[MCP servers](mcp/README.md), `pytest` to run the test suite.

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
# Queue a goal and kick the task loop:
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
   [docs/architecture.md](docs/architecture.md).
2. **The queue feeds itself.** A prompt hook queues your build asks;
   workers file verified follow-on discoveries (capped + deduped); a stop
   hook nudges unrunnable notes into runnable tasks; a maintenance sweep
   turns confirmed defects into fix tasks with stable dedupe tags. See
   [docs/queueing-and-gap-finding.md](docs/queueing-and-gap-finding.md).
3. **Critics instead of approvals.** Design and plan phases are gated by
   adversarial critic subagents with one repair round; unresolvable gaps
   park `needs-human` with the exact question a human must answer.
4. **Tier the fleet.** Cheap models sweep, mid models judge, your best
   model only synthesizes. See
   [agents/AGENT_DESIGN.md](agents/AGENT_DESIGN.md).
5. **Files over databases.** Queue, manifest, memory, mailbox — all plain
   files: greppable, diffable, hand-editable, recoverable.

## Safety

Unattended autonomy is opt-in and bounded: `destructive`-class tasks never
auto-run; every worker prompt carries hard-stops (money, deletion, external
sends, credentials, destructive git → `needs-human`); crash-looping tasks
strike out and park; `acceptance_command` lets you make "done" objective.
Read [docs/architecture.md#safety-model](docs/architecture.md#safety-model)
before raising autonomy.

## License

MIT — see [`LICENSE`](LICENSE).
