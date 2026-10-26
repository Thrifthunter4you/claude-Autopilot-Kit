# Claude Autopilot Kit

**Autonomous engineering loop for Claude Code.** The **Claude Autopilot
Kit** (package name `claude-workflow-kit`) turns
[Claude Code](https://claude.com/claude-code) into an unattended,
self-feeding development loop: queue a one-line goal and it gets
researched, designed, planned, executed, tested, and reported — one clean
context window per phase, every step on disk, no database, no framework.
Built for people who run coding agents for hours and want them cheaper,
safer, and resumable. It is six pieces that compose: a file-backed **task
loop**, **hooks** that make sessions queue their own work, a tiered
**agent fleet**, a **script toolbox** that turns repeated operations into
registered tools, **token tooling** that shows where long sessions
actually spend, and **memory/context MCP servers** that give every
session state awareness.

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
verdict (schema-validated CLI output, not transcript text):
  {"status":"done","outcome":"rate limiting shipped, 12 tests green"}
```

**Contents:** [What's in the box](#whats-in-the-box) ·
[Why this exists](#why-this-exists) · [Install](#install) ·
[Try it](#try-it) · [What's new in 0.5](#whats-new-in-05) ·
[0.4](#whats-new-in-04) · [0.3](#whats-new-in-03) ·
[Meet the new pieces](#meet-the-new-pieces) ·
[The ideas that matter](#the-ideas-that-matter) ·
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
  a territory nudge that steers sessions to registered tools instead of
  hand-derived commands, session-start state briefing, automatic
  spilling of oversized tool output, a turn-budget reminder for
  subagents that run long, a context-ceiling wrap-up reminder, and a
  continuity ledger written before every compaction. A per-project
  opt-in: [`hooks/install_hooks.py --project <repo>`](hooks/install_hooks.py)
  writes `<repo>/.claude/settings.local.json`, so nothing fires in
  sessions outside the repositories you enabled.
- **The toolbox** ([`taskloop/toolbox.py`](taskloop/toolbox.py) +
  [`bin/toolbox`](bin/toolbox)) — a registry that makes every reusable
  script findable by intent (`toolbox find "restart the api"`),
  self-describing, runnable by name, and telemetered from birth; entries
  can declare a `territory` regex the PreToolUse hook defends. Example
  registry: [`toolbox.example.yaml`](toolbox.example.yaml); full
  walkthrough: [Meet the new pieces](#meet-the-new-pieces).
- **Token + measurement tooling** ([`bin/token-audit`](bin/token-audit) +
  [`bin/spill`](bin/spill) + [`bin/noise-floor`](bin/noise-floor) +
  [`bin/hook-cost`](bin/hook-cost) +
  [`docs/token-economy.md`](docs/token-economy.md)) — rank a session's
  token carriers by carry cost (size × turns remaining), keep bulk
  command output out of the context window, grade a claimed improvement
  against the rerun noise floor, see what your hooks cost and save, and
  the working model of why cache
  *writes*, not reads, are the bill. Sample reports:
  [Meet the new pieces](#meet-the-new-pieces).
- [**`agents/`**](agents/AGENT_DESIGN.md) — a tiered subagent fleet
  (finder/verifier/judge/critic/dedup/tester/fixer/synth/scout/
  triage-judge) plus the design guide, a template for minting your own,
  and [`tier_policy.yaml`](agents/tier_policy.yaml) — the single source
  of truth for who runs on which model tier, drift-linted by the test
  suite.
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
git clone https://github.com/OWNER/claude-Autopilot-Kit.git ~/claude-workflow-kit
cd ~/claude-workflow-kit
python3 -m pip install --user pyyaml   # or: sudo apt install python3-yaml
./install.sh
```

Replace `OWNER` with this repository's owner (or copy the clone URL from
the **Code** button above). `install.sh` symlinks the bins into
`~/.local/bin`, creates the `~/.taskloop` state home, installs the hooks
and links the agents and autopilot skill — it is idempotent, so re-run it
after every `git pull`. Hooks are enabled per repository:
`./install.sh --project ~/dev/yourproject` (repeat for each repo you want
hooked; nothing is armed machine-wide).

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

# Or let the hook do it: in a repository where you ran
# `install_hooks.py --project`, typing a clear build ask auto-queues it.
```

Standalone taste (no queue required):

```bash
# Where did that long session's tokens go? Biggest carriers first:
token-audit ~/.claude/projects/<project-slug>/<session>.jsonl

# Run something noisy without pasting 500 lines into the conversation:
spill run -- pytest -x -q          # preview + spill file + real exit code

# What did the hooks deny, remind and rewrite today — and what did it save?
hook-cost --since 24

# Did the change beat the rerun noise floor, or just wiggle inside it?
noise-floor --baseline "./bench.sh" --delta -0.8 --direction lower

# Register a script once, find and run it by name forever:
toolbox add --name db-health --description "quick db health check" \
            --cmd "python3 ~/scripts/db_health.py"
toolbox find "health"
toolbox run db-health
```

## What's new in 0.5

Long sessions: survive compaction, and know when to stop
([CHANGELOG](CHANGELOG.md)):

- **A ledger before every compaction.** A PreCompact hook writes what the
  summary tends to drop — recent user messages, files edited, commits with
  their hashes, tool errors, git status — to `~/.taskloop/ledgers/`, and
  tells the summarizer what it must keep and where the ledger is. The next
  session start points at it.
- **Second compaction = wrap up.** Counted from the compactions that
  actually landed in the transcript, not from hook fires; the summary then
  carries "finish the edit in flight, commit, hand off" forward.
- **A context ceiling.** Past 150K tokens of context (configurable), every
  tool call in the main session gets a reminder, on a cooldown, to finish
  what is in flight, write a handoff and end the turn. Never blocks.
- **Two more [harness gotchas](docs/harness-gotchas.md)**: the hook output
  shape the harness silently rejects on PreCompact and ten other events, and
  why a hook fire is not a compaction.

## What's new in 0.4

Context economy, enforced by hooks instead of habit
([CHANGELOG](CHANGELOG.md)):

- **Big tool output spills itself.** A PostToolUse hook moves any
  successful Bash or MCP result over 8 KB (4 KB inside a subagent) to a
  spill file and hands the model a head/tail preview that names it. Errors,
  interrupted runs, images and results the harness already truncated land
  untouched; `# keep-output` keeps a deliverable whole; common secret
  shapes are masked before anything hits disk (best effort), and spill
  files older than a week are pruned.
- **Subagents see their turn budget.** A subagent's cost grows with the
  square of its turns, and the "answer within 25 turns" line in its brief
  scrolls away. A PreToolUse hook counts its tool turns from its own
  transcript and reminds it at the budget, then every half-budget; an
  opt-in ceiling stops tool calls (never the answer) at a multiple.
- **`hook-cost`.** Every kit hook that denies, reminds or rewrites now
  writes one JSONL row; `hook-cost` turns them into denies per session,
  bytes kept out of context and the subagents that ran past budget — the
  numbers you need to keep, tune or drop a guard. Below.
- **Two more [harness gotchas](docs/harness-gotchas.md)**: oversized output
  the harness already saved, and where a subagent's transcript really is.

## What's new in 0.3

A month of running the loop unattended, folded back in
([CHANGELOG](CHANGELOG.md)):

- **Verdicts you can't forge.** Workers return their verdict as
  schema-validated CLI output (`--json-schema`), not a magic transcript
  line that any file the worker read could supply.
- **Hooks are a per-project opt-in.** `install_hooks.py --project <repo>`
  arms them for that repository only; nothing fires machine-wide unless you
  say `--global`.
- **Two guard hooks.** No unbounded Reads of spill files; a warning when
  the memory index outgrows what a session start can actually load.
- **`noise-floor`.** Before believing "we cut it 4%", measure how far the
  same command moves on rerun. Below.
- **Worker knobs** for effort level and the print-mode background ceiling
  that silently killed long workers at 600 s.
- **[Harness gotchas](docs/harness-gotchas.md)** — eight non-obvious
  failure modes of unattended Claude Code and what the kit does about each.

## Meet the new pieces

Additions ported from a production setup where Claude Code runs unattended
for weeks: a script registry, a token profiler, an output bounder, (0.3)
a noise-floor gate for measurements, and (0.4) a hook cost report. Each works standalone — none requires
the task loop.

### `toolbox` — stop re-deriving what you already built

Watch any long-running agent setup and you'll see the same failure: the
agent (or you) hand-assembles the same grep-awk-json pipeline today that it
hand-assembled last Tuesday, slightly differently, occasionally wrongly. A
scripts directory doesn't fix this — scripts nobody can *find* get
re-derived anyway.

The toolbox is a small YAML registry that makes each reusable script:

- **findable by intent** — `toolbox find "restart the api"` does
  keyword-scored lookup over names and descriptions, so the words you'd
  *search* for are what gets matched, not the filename you forgot;
- **self-describing** — `toolbox describe db-health` prints the argument
  contract and output contract you wrote at registration, so nobody has to
  open the source to know how to call it;
- **runnable by name** — `toolbox run db-health --verbose` from anywhere,
  no paths to remember;
- **observable from birth** — every run appends one JSONL record (tool,
  timestamp, exit code, duration), so `toolbox stats` answers "does
  anything actually use this?" with numbers instead of guesses:

  ```
  tool         fires   ok% nudges blocked  last run
  db-health       41  100%      3       0  2026-08-05
  dusty-tool       0     -      0       0  never
  ```

**Playbooks** register multi-step sequences under one name — steps run in
order, `{{var}}` placeholders interpolate, and `toolbox run morning-check
table=users` overrides the defaults. Your morning ritual becomes one word.

**Territories** are the enforcement half. A registry entry can declare a
regex of shell commands it *owns* (say, `psql\s+.*-c` for your db-health
script). When any Claude Code session — you or an unattended worker —
types a matching hand-derived command, the PreToolUse hook
(`hooks/pretool_toolbox_nudge.py`) injects a note at exactly the right
moment:

> a registered tool owns this territory: `toolbox run db-health` — quick
> db health check. Prefer it over the hand-derived command.

Advisory nudges fire once per session per tool (no nagging), and the
command still runs. For tools where hand-derivation has real cost you can
set `enforce: true` on that one entry, and matching commands are blocked
with the escape hatch named in the message. The intended lifecycle:
everything starts advisory; `toolbox stats` shows you which advisories get
ignored; only those earn enforcement. See
[`toolbox.example.yaml`](toolbox.example.yaml) for a commented registry.

Together with the crystallize hook this closes a loop: repeated
hand-derived operation → queued script-building task → built script gets
registered → territory nudge steers every future session to it →
stats tell you it stuck.

### `token-audit` — find out what your session actually pays for

The intuition that fails everyone: a big output late in a session looks
expensive, and a small file injected at session start looks free. It's
backwards. Everything that enters the context window is **re-read on every
later turn**, so the true cost of any block is
`size × turns remaining after it appears`. `token-audit` parses a session
transcript (the `.jsonl` Claude Code already writes) and ranks every tool
result by exactly that:

```
TOTALS
  assistant turns:        153
  output tokens:          121,944
  cache READS:            24,214,198  (cheap — ~0.1x input price)
  cache WRITES:           556,055  (the bill — ~1.25x; high = your prefix keeps changing)

TOP CARRIERS  (size x turns-remaining; estimates)
     582,394  =   3,989t x 146 turns   Bash(some command that dumped early)
     399,448  =   4,076t x  98 turns   Read(/path/to/a/big/file)

RE-READS  (same file Read multiple times = duplicate carry)
  2x  /repo/config/settings.py

SESSION-START  first-turn context ≈ 61t; full-session carry ≈ 9,333t

ADVICE (biggest first)
- your #1 carrier is Bash(...) — delegate sweeps like this to a subagent
  (only the conclusion returns) or bound the output (head/grep/`spill run`)
```

The sections in order: real token totals from the API's own usage records
(including the cache read/write split — if writes rival reads, something
mutates your prompt prefix every turn and breaks caching); the ranked
carriers; files read twice (pure waste); what your session-start
attachments cost across the whole session; and a short ranked cut list.
The intended loop is boring and effective: audit, fix only the #1 item,
re-audit next week. The full cost model is in
[docs/token-economy.md](docs/token-economy.md).

### `spill` — run noisy commands without flooding the window

A 500-line test run pasted into the conversation is not read once — it
carries for the rest of the session (see above), and almost all of it
exists only in case something failed. `spill run -- pytest -x -q` executes
the command, writes the **full** output to a file under
`~/.taskloop/spills/`, and prints only the head, the tail, and the file
path. The exit code passes through untouched, so scripts and CI behave
identically.

The retrieval half is what makes it safe to use everywhere: `spill list`
shows recent spills, `spill show 1` previews the newest, and
`spill show 1 --grep "ERROR|FAIL"` pulls exactly the failing lines (with
two lines of context) into the conversation — so you drill into the 1% you
need instead of carrying the 99% you don't.

### `noise-floor` — is that delta real, or just the rerun jitter?

Every "the change improved X" claim compares two numbers. Run the SAME
command twice and the number moves — often by more than the improvement
being claimed. `noise-floor` measures that movement and grades the delta
against it, so a measurement inside the floor is reported as what it is: an
underpowered design, not a small win.

```bash
noise-floor --baseline "./bench.sh" --candidate "./bench.sh --new" -n 5 --direction lower
```

```
VERDICT: INSIDE-NOISE-FLOOR
baseline  n=5 min=41.2 max=44.9 mean=43.1 spread=3.7 ci=[41.8, 44.3]
candidate n=5 mean=42.4 diff_ci=[-2.9, 1.6]
delta     -0.7 (measured; lower is better)
MDE       2.25  [bootstrap_diff_ci_half_width]
→ |delta| <= MDE: not a result at any confidence — rerun with a larger -n or a less noisy metric
```

The minimum detectable effect comes from the baseline's spread (a seeded
bootstrap CI from five runs), and a delta you *measured* is graded against
the two-arm floor, since it carries the candidate's noise too. Three
verdicts, three exit codes — `DONT-SHIP` exits 3 so a script can gate on
it. `--delta` grades a number you already have; `--regex` pulls the metric
out of chatty output. The measure-then-cut loop in
[docs/token-economy.md](docs/token-economy.md) ends with this step.

### `hook-cost` — is each guard hook worth its turns?

A hook that denies a tool call is never free: the model reads the reason,
picks another route and calls again, and each of those turns re-sends the
whole context. A hook that rewrites output saves tokens. Neither shows up
anywhere unless it is written down, so every kit hook that denies, reminds
or rewrites appends one row to `~/.taskloop/hook_decisions.jsonl`, and
`hook-cost` reads it back:

```bash
hook-cost --since 24
```

```
212 decisions in ~/.taskloop/hook_decisions.jsonl

BY HOOK
  hook                     decision   count  sessions  /session
  output_spill             rewrite      171        14     12.21
  subagent_turn_budget     advise        23         9      2.56
  spill_read_guard         deny          11         6      1.83
  toolbox_nudge            advise         5         4      1.25
  subagent_turn_budget     deny           2         2       1.0

SAVED
  output spill kept 3,914,220 bytes (~978,555 tokens) out of context, before counting the turns each would have been re-read

SUBAGENTS
  19 subagent(s) reached their turn budget
  a001c09e2b7d35f1 reminders=2 denies=1  last: wf-verifier 51/25
  a011c09e2b7d35f1 reminders=2 denies=1  last: wf-verifier 51/25
  a021c09e2b7d35f1 reminders=2 denies=0  last: wf-verifier 25/25
```

Read it as costs against savings: each deny is at least one extra turn,
each spill is a result that would otherwise be re-read on every later turn.
A guard that fires often on things that were fine should be loosened; one
that rarely fires is cheap insurance. The turn-budget ceiling ships off —
switch it on (`TASKLOOP_TURN_CEILING_MULT=2`) once the SUBAGENTS section
shows where your subagents actually end. `--session <prefix>` narrows to
one session, `--json` gives the summary for scripts.

### `tier_policy.yaml` — the fleet's model tiers, drift-linted

The agent fleet runs cheap models for sweeps, mid models for judgment, and
reserves your best model for the one synthesis stage. That only stays true
while something enforces it: tier assignments drift one "just this once"
at a time. So every agent's tier now lives in ONE file —
[`agents/tier_policy.yaml`](agents/tier_policy.yaml), each row with a
one-line rationale — and `tests/test_tier_policy.py` fails the suite when
any agent's `model:` frontmatter disagrees with the policy, when an agent
is missing from it, or when a policy row points at a deleted agent.
Changing a tier means changing both files in the same commit, on purpose.

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
4. **Tier the fleet — and lint the tiers.** Cheap models sweep, mid models
   judge, your best model only synthesizes; the assignments live in ONE
   policy file the test suite reconciles against every agent definition.
   See [agents/AGENT_DESIGN.md](agents/AGENT_DESIGN.md).
5. **Tokens are carry, not size.** Everything entering the context window
   is re-read every later turn, so cost ≈ size × turns remaining — which
   makes session-start attachments and early dumps the real bill.
   `token-audit` measures it; `spill`, subagent delegation, and on-demand
   loading cut it. See [docs/token-economy.md](docs/token-economy.md).
6. **Scripts beat re-derivation.** The third hand-assembled pipeline
   becomes a queued script task (crystallize), the built script gets
   registered (`toolbox add`), and a hook steers future sessions to
   `toolbox run` instead of deriving it a fourth time — with `toolbox
   stats` showing whether any of it is actually used.
7. **Files over databases.** Queue, manifest, memory, mailbox, registry —
   all plain files: greppable, diffable, hand-editable, recoverable.

## Safety

Unattended autonomy is opt-in and bounded: `destructive`-class tasks never
auto-run; every worker prompt carries hard-stops (money, deletion, external
sends, credentials, destructive git → `needs-human`); crash-looping tasks
strike out and park; `acceptance_command` lets you make "done" objective.
Read [docs/architecture.md#safety-model](docs/architecture.md#safety-model)
before raising autonomy.

## License

MIT — see [`LICENSE`](LICENSE).
