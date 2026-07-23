# How sessions feed the queue (and find the gaps themselves)

The task loop only compounds if tasks keep flowing INTO it. This kit wires
six producers so the queue fills itself; you rarely file tasks by hand.

## 1. Your prompts (the autopilot-detect hook)

[`hooks/userprompt_autopilot_detect.py`](../hooks/userprompt_autopilot_detect.py)
fires on EVERY prompt and classifies it:

- **clear** build initiative ("build X", "add Y to Z") → the agent
  auto-runs `task-go "<goal>" --repo <repo>` and relays progress — you never
  hand-run intake.
- **fuzzy** ("maybe we should…", "what if…") → the agent asks ONE question:
  hand to the loop, or discuss inline?
- **none** (questions, acks, continuations) → nothing injected.

Detection lives in a hook — not in instructions — because a hook fires on
every prompt and survives context compaction. The classifier is
conservative regex, fail-open, and has a recursion guard so background
workers can't queue themselves.

## 2. The sessions themselves (follow-ons)

Both worker prompt templates carry a FOLLOW-ONS step: when a task surfaces
genuinely valuable adjacent work — an improvement, a cleanup, a
reusable-script candidate — the worker files each as its own queue item
BEFORE finishing:

    task-add "<concrete goal>" --repo <repo> --priority 4 \
      --hint "[followon] from <current task>"

Guardrails that keep this from flooding the queue:
- **cap ~3 per task**, and "zero is a fine outcome" is stated in the prompt
- **daily fleet-wide cap** on `[followon]` intakes (soft-drop past it)
- **dedup**: exact goal-hash, then fuzzy Jaccard ≥0.85 against queued goals
- a failed `task-add` is NEVER a task failure — the worker moves on

The same pattern applies to your interactive sessions: the CLAUDE.md
template's session-discipline section tells the agent that finishing the
asked task is the midpoint — fix what it found, file what it can't fix, then
pull the next queue item.

## 3. Session stop (the queue nudge)

[`hooks/stop_queue_nudge.py`](../hooks/stop_queue_nudge.py) runs when a
session ends. Any recently-touched
file in `queue/active/` that is NOT runnable (no `loop:` status) gets a
nudge printed to the author: give it a concrete goal now, or it sits as a
note nobody runs. Suggest-only — the hook never edits files — and
idempotent via a ledger so you're nudged once per file, not every session.

This closes the classic failure mode: sessions ending with "found X but
didn't fix it" notes that nothing ever executes. A RUNNABLE task with an
exact goal starts within minutes; a parked note waits for triage to infer
one.

## 4. Scheduled sweeps (maintenance mode)

[`templates/maintenance_prompt.txt`](../templates/maintenance_prompt.txt)
runs a file-only review against a target file: find CONFIRMED defects (real bugs, dead references, config drift —
not style), and file each as a fix task with a **stable dedupe tag**:

    --dedupe-tag "[maintenance:<file>:<line>]"

The tag is what makes re-running the same review idempotent: a re-detected
defect hits the tag and files nothing; a clean file files nothing; only NEW
defects create tasks. Point it at your most load-bearing files on a timer
(see [`systemd/`](../systemd/README.md)).

## 5. Repeated operations (the crystallize nudge)

The third time a session hand-assembles the same grep/awk/json pipeline,
that operation wanted to be a script — every future re-derivation costs
tool calls and is a fresh chance to get it subtly wrong.
[`hooks/stop_crystallize_nudge.py`](../hooks/stop_crystallize_nudge.py)
closes the gap at session Stop: it normalizes every Bash command in the
transcript (numbers and quoted strings collapsed), and any *derived*
command — a pipeline, heredoc, or long one-liner — seen ≥3 times
(`TASKLOOP_CRYSTALLIZE_MIN`) gets a suggested intake:

    task-add "Crystallize this repeated operation into a reusable script
    (with --help): <command>" --repo <repo> --dedupe-tag "[crystallize:<hash>]"

The stable hash tag means re-suggesting or re-filing is always a no-op,
and a ledger guarantees one nudge per operation, ever. Suggest-only by
default; `TASKLOOP_AUTO_CRYSTALLIZE=1` auto-files the top candidate (one
per stop). Over time this producer turns your sessions' habits into a
toolbox: the loop writes the scripts, and later sessions call them
instead of re-deriving.

## 6. Reusable scripts with no telemetry (the telemetry nudge)

A script the loop will run again and again but that emits nothing — no
counts, no trace of when it fired or on what — is invisible the first time
it misbehaves in the background: a silent no-op looks identical to a healthy
quiet run. A one-line JSONL fire/usage log written from birth turns every
later debug, trace, and cost question into a `tail`.
[`hooks/stop_telemetry_nudge.py`](../hooks/stop_telemetry_nudge.py) closes
the gap at session Stop: it scans what the session actually WROTE (no
arbitrary file reads) and flags any reusable script — a code file past a
size floor (`TASKLOOP_TELEMETRY_MIN_CHARS`) carrying an entrypoint signal
(argparse, `def main`, `__main__`, `sys.argv`) — whose content shows no
telemetry marker (no fire/usage log, no logging, no `.jsonl` sink):

    task-add "Add fire/usage telemetry (a small env-overridable JSONL log)
    to this reusable script: <name>" --repo <repo> --dedupe-tag "[telemetry:<hash>]"

It is deliberately conservative — plain modules, config, tests, and
already-logged scripts are left alone — so the nudge stays rare and worth
acting on. The tag (a hash of the script path) makes re-suggesting a no-op,
and a ledger guarantees one nudge per script, ever. Suggest-only by default;
`TASKLOOP_AUTO_TELEMETRY=1` auto-files the top candidate (one per stop). This
producer is the mirror image of the crystallize nudge: that one gets scripts
BUILT, this one gets them INSTRUMENTED — so the toolbox the loop grows is
observable, not a set of black boxes.

## Triage: notes → runnable tasks

Anything that lands in the queue without a runnable `loop:` block (or gets
parked `needs-human`) is triage material. The
[`triage-judge`](../agents/triage-judge.md) agent judges ONE parked item at
a time and returns:

- **promote** — extracted one-line goal + repo + the `loop:` block to write
- **park** — the ONE specific question a human must answer first
- **likely-done** — evidence (commit/file/test) the work already happened

Run it ad hoc over parked items, or wire it on a timer. Its key discipline:
verify the note's claims against the real repo before judging — parked
notes go stale, and promoting already-done work wastes a worker run.

## Dedup, in depth

Producers retry; hooks re-fire; sweeps re-run. Every intake path goes
through three filters (in [`bin/task-add`](../bin/task-add)):

1. **goal_hash** — sha256 of the casefolded goal, embedded in every task
   file and searched as a literal. Exact retries never double-file.
2. **token Jaccard ≥0.85** vs queued todo/needs-triage goals — near
   duplicates refused (override with `--force`, which marks the new task as
   a dup-suspect for triage).
3. **--dedupe-tag** — the producer-side stable key for anything that
   re-runs mechanically.

All three fail open: an unreadable queue file never blocks an intake.
