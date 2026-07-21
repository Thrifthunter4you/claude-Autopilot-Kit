# Advanced patterns

Optional shapes to grow into once the basic task loop is running. None is
required — adopt a pattern when you hit the failure mode it fixes.

## Swarm mode: pre-planned batch draining

When the queue is deep in small items, per-item full pipelines are
overkill. The swarm shape splits planning from fixing:

1. **Planner pass** (read-only, cheap): for each queued item, a planner
   agent reads the task + the cited files and appends ONE section to the
   task file:

   ```
   ## QUICKFIX-PLAN (auto <date>)
   - <step 1: exact file + what to change>
   - <2-6 concrete steps>
   - VERIFY: <the exact test/command that must pass>
   ```

   or `- NOT-QUICKFIX: <one line why>` when it needs the full pipeline.
   The planner NEVER edits repo code — the task file is its only output.

2. **Fixer pass** (one shot per item): a fixer worker executes the embedded
   plan directly — close or release, no re-research. Verdict discipline:
   `done` (fixed AND verified), `needs-human` (names the decision),
   `blocked` (impossibility proof), `continue` (append a FIXER NOTE with
   exactly what broke, so the next attempt starts from your note — the
   note in the task body is the only channel that survives requeue).

3. **Independent re-verify**: a `wf-tester` leaf re-runs the fixer's claimed
   test command. A fixer grading its own patch is not independent evidence.

## Acceptance commands as objective "done"

For anything with a checkable outcome, file it with
`--acceptance-command "pytest tests/test_x.py -q"`. The drainer only
accepts a `done` verdict if the command exits 0 in the repo — a worker
can't talk its way past a red test.

## Maintenance census on a timer

Keep a list of your most load-bearing files. On a schedule, run a worker
with [`templates/maintenance_prompt.txt`](../templates/maintenance_prompt.txt)
against each — confirmed defects
become fix tasks with stable `[maintenance:<file>:<line>]` dedupe tags, so
the sweep is idempotent. Clean file → zero tasks; new defect → exactly one.

## Research dossiers before big changes

For cross-cutting work, make the research phase write a dossier
(`docs/research/<date>-<topic>-dossier.md`) where every fact carries a
`file:line` citation, then have design/plan phases cite the dossier instead
of re-researching. Verify-before-trust: a dossier claim whose cited file
changed since the dossier date gets re-checked against live source.

## Adversarial verification fan-outs

For "find all the bugs" style missions, use the Workflow tool (or nested
Agent dispatches) with the wf-* leaves:

- finders sweep in parallel (different lenses: by-name, by-behavior,
  by-caller)
- plain code dedups exact matches; `wf-dedup` merges fuzzy ones
- each surviving finding gets 1-3 `wf-verifier` dispatches prompted to
  REFUTE it (majority refute = kill)
- `wf-critic` closes the loop: it names what's still missing; loop until it
  returns an empty list twice
- one `wf-synth` writes the final cited deliverable

## Worker resource containment

Parallel workers are RAM-hungry (each can fan out subagents). Options that
composed well: run the drainer under `systemd-run --user --scope` with
`MemoryHigh=`/`MemoryMax=` per worker; gate new spawns on free RAM; clamp
concurrency to 1 while the machine is interactively in use.

## Cross-session leases

When multiple interactive sessions + the drainer share repos, add a lease
file per repo ("tree lease") that sessions acquire before large landings,
and have a PreToolUse hook warn when a command targets a repo another
session holds. The mailbox
([`state/session_mailbox.py`](../state/session_mailbox.py)) covers the
announce/coordinate half; leases add enforcement if you need it.
