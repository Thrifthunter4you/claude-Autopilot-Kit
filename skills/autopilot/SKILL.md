---
name: autopilot
description: >
  Autonomous full-pipeline worker for the task loop. Runs research -> design
  -> plan -> execute-each-task for ONE queued goal, each phase in a FRESH
  subagent so every phase gets a clean context window, while this conductor
  holds only artifact paths + summaries. Invoked by the task-loop worker on
  `autopilot`-class tasks (or directly on a goal). Fully autonomous: critic
  subagents replace human design/plan approval; committing on the repo's
  working branch is the ship path; money/deletion/external-send/destructive-git
  are hard-stops.
---

# autopilot

You are the **conductor** of a one-task pipeline. You do NOT do the research,
design, planning, or coding yourself — you **dispatch one fresh subagent per
phase** and keep only each phase's *artifact path + a short summary* in your
own context. That is what lets you drive a multi-hour run without inflating
context.

**Model policy:** every subagent dispatched below (Agent tool) takes an
explicit `model:` — do not omit it and rely on inheriting the session default,
which drifts over time. A mid-tier model (e.g. `sonnet`) is the sensible floor
for every phase including the critic/review stages; reserve your top tier for
work that genuinely needs it.

**Dispatch policy (CHUNKED mode is print mode — this is load-bearing):** every
Agent dispatch takes `run_in_background: false`, and every long Bash (test
suites, builds) runs FOREGROUND with an explicit `timeout`. Never end your
turn while background tasks are still running: in a `claude -p` worker the
harness terminates the whole session once the background-wait ceiling expires
(no LOOP_RESULT → the drainer strikes the task; enough strikes parks a healthy
run). "I'll wait for the subagent's notification" is an interactive-session
pattern — in CHUNKED mode it is a session kill. Blocking synchronously on the
dispatch is always correct here: the conductor has nothing else to do, and the
worker's own timeout is the outer bound.

## Operating mode — CHUNKED vs DIRECT (read this first)

- **CHUNKED (task-loop / `claude -p` worker)** — you were handed a task-file
  path by the worker prompt. Do **ONE pipeline phase this invocation**; in
  execute, complete **up to THREE small, independent plan tasks**, stopping
  earlier after 45 minutes of execute work or at the first task that needs
  substantial investigation, review, or repair. Persist progress to the run
  manifest after EACH task, then **STOP and yield** with `LOOP_RESULT` status
  `continue`. The drainer re-queues the task and spawns a FRESH worker for
  the next unit — so *your own* context also stays small, not just each
  phase's. The on-disk manifest is the handoff between units; never re-run
  prior units. See **§6 Checkpoint & yield**.
- **DIRECT (at-keyboard)** — the user invoked this skill themself on a goal,
  in an interactive session. There is no drainer to re-spawn you, so run the
  units **inline**, one after another in this session (each phase still in a
  fresh subagent, which keeps the conductor lean). Do not emit `continue`;
  just finish the whole run and emit `done`.

Everything below is written for ONE unit. In CHUNKED mode you execute one
unit then yield; in DIRECT mode you loop over §0's "next unit" selection
inline until the run is done.

## 0. Load the task + resume state

0. **CLAIM GATE (DIRECT mode only).** CHUNKED workers skip this — the drainer
   set a `loop-<epoch>-<pid>` lease before spawning you and clears it on your
   verdict. In DIRECT mode, read the task file's `loop.lease`: if another
   holder's lease is present and fresh, **STOP — run NOTHING** and report the
   holder instead (two conductors on one task corrupt the manifest). If it is
   empty, write your own lease value into `loop.lease` before starting, and
   clear it on EVERY terminal outcome.
1. Read the task file at the path given to you. Its `loop:` block carries
   `goal`, `repo`, and `autopilot: true`. Fallbacks: if `loop.goal` is
   absent, take the goal from the body (`# Goal\n\n<goal>`) or the title; if
   `loop.repo` is absent, use the kit's configured `default_repo`.
2. Derive `run_id = <task-filename-stem>`. Load the run manifest:
   `run-manifest get <run_id>` (exit 1 = no manifest yet — first invocation,
   proceed to research). Resume at the first unfinished phase of your
   profile's phase list (step 4 selects it), reusing any dossier/spec/plan
   already recorded (don't redo them).
3. **Right-size the pipeline** and record the profile
   (`run-manifest set-profile <run_id> <profile>`) as exactly ONE canonical
   key — **`trivial` | `small` | `large`**:
   - **`trivial`** — one-line / mechanical / no new logic or control flow
     (add a flag, bump a constant, fix a typo, rename). Phases =
     `execute,finish` — skip research/design/plan entirely. There is no plan
     to enumerate, so seed a SINGLE execute task
     (`run-manifest set-tasks <run_id> 1`) = the change itself, and do one
     implement+review subagent on it.
   - **`small`** — localized to ~1–3 files, the approach is obvious, no
     cross-cutting redesign (most bug fixes + scoped additions). Full phase
     list with ONE scoped research subagent and a LIGHTWEIGHT design (still
     critic-gated).
   - **`large`** — cross-cutting / multi-subsystem / non-obvious approach.
     Same phases; the difference is research depth.
   **On resume, REUSE the recorded profile — do NOT re-decide.** When
   genuinely torn between `trivial` and `small`, choose `small` — a needless
   research unit is cheaper than shipping unresearched code.
4. **Select THIS invocation's single unit**:
   `run-manifest next-phase <run_id>`. If that phase is `execute`, the unit
   is the next `run-manifest next-task <run_id>` (seed the task list first —
   see §4). Do that ONE unit, then in CHUNKED mode checkpoint & yield (§6).
   If next-phase prints nothing, the run is finished — go to §5 FINISH (or,
   if finish is already done, emit `done`).

## 1. RESEARCH

Dispatch ONE research subagent (Agent tool, explicit `model:`). Prompt it
with: the goal, the `repo` to read, and seed files/questions. It reads the
REAL source and writes `docs/research/<today>-<topic>-dossier.md` (inside the
repo) with `file:line` citations. Code is ground truth — memory files and
docs are an index to verify, never evidence. It returns: dossier path + a
≤200-word key-facts summary. Record via
`run-manifest mark-phase <run_id> research done --artifact <path> --summary "<...>"`.
**This is one unit** → in CHUNKED mode, checkpoint & yield now (§6).

## 2. DESIGN (+ critic)

Dispatch a design subagent: reads the dossier + goal, writes a spec to
`docs/specs/<today>-<topic>.md`, returns spec path + a decisions summary.
**If the task file contains a `DESIGN RESOLUTION` section** (a human resolving
a previously-recorded critic gap), quote it verbatim in BOTH the design and
critic prompts — the spec must incorporate it, and the critic judges against
it instead of re-litigating the resolved question. Then dispatch an
**independent design-critic subagent** (adversarial): "Does this spec
actually answer the goal? Any fatal ambiguity or wrong-thing risk? Reply
pass | fatal-gap + reason."

On `fatal-gap`, run **ONE spec-repair round**: dispatch a repair subagent
with the spec + the critic's verbatim verdict — "revise the spec to CLOSE
this exact gap; do not paper over it" — then re-run the critic on the revised
spec. Repairing to a named gap is not guessing; most critic verdicts state
the fix. Park `needs-human` ONLY if (a) the second verdict is still
`fatal-gap`, or (b) the gap is a genuine business/scope decision the goal
doesn't answer — never guess on a wrong-thing risk that survives repair. On
park, record BOTH verdicts in the phase summary (so a human can write the
answer into the task file's `DESIGN RESOLUTION` section) and emit
`LOOP_RESULT: {"status":"needs-human","outcome":"design gap: <reason>"}`.
Record the phase. **One unit** → checkpoint & yield (§6). The repair round is
part of the same design unit — do not yield between critic and repair.

## 3. PLAN (+ critic)

Dispatch a plan subagent: writes `docs/plans/<today>-<topic>.md` against the
spec + dossier — bite-sized, test-first tasks, each with its own verify step.
Then a **plan-critic subagent**: "Does every spec requirement map to a task?
List any missing." One repair round if gaps; else `needs-human`. Record.
**One unit** → checkpoint & yield (§6).

## 4. EXECUTE (up to 3 plan tasks per invocation, 45-min cap)

Tasks edit interdependent files, so they run strictly IN ORDER,
**checkpointing after each** in CHUNKED mode (in DIRECT mode, loop inline).

**First time you enter execute**: read the plan, enumerate its tasks IN
ORDER, and seed them: `run-manifest set-tasks <run_id> 1 2 3 ...`
(idempotent — safe to call every invocation). (**`trivial` profile** skips
plan; the single task `1` was already seeded in §0.3.)

**This invocation's task** = `run-manifest next-task <run_id>`. If empty,
all tasks are done → `run-manifest mark-phase <run_id> execute done`, then go
to §5 FINISH. Otherwise:

1. **Implement** subagent (explicit `model:`): reads the plan + current repo
   state, does the task test-first, runs the task's tests. Returns files
   touched + test result.
2. **Review** subagent (two-stage): spec compliance first, then code
   quality. Returns pass | findings.
3. If findings or tests red → repair subagent, up to 2 repair rounds. Still
   failing after that: STOP, emit `needs-human` (the drainer will strike the
   task).
4. On green, **ship per the repo's own convention**: `git -C <repo>` commit
   on the repo's **current working branch** with an explicit pathspec and a
   clear message. If the repo's CLAUDE.md defines a promote/deploy step, that
   step is only in scope when it is purely local and reversible — anything
   that pushes to a remote, deploys, or touches production is a HARD-STOP →
   `needs-human`. Then persist this task's progress —
   `run-manifest mark-task <run_id> <task_id> done` — so a fresh worker
   resumes at the NEXT task.
5. **Continue or checkpoint & yield (§6).** In CHUNKED mode: if this task
   was marked done AND you have completed fewer than 3 tasks this invocation
   AND fewer than 45 minutes of execute work have elapsed AND next-task is
   non-empty AND it does not look like it needs substantial investigation —
   loop back to "this invocation's task" in this SAME process (persist after
   it too). Otherwise STOP and yield `continue`. (DIRECT mode: loop back
   without the 3-task/45-minute cap.)

### Guardrails (enforce on EVERY subagent + yourself)
- **Allowed:** repo edits, test-first development, `git commit` on the
  repo's working branch (explicit pathspec), reading/researching anything in
  the repo, filing follow-on queue items via `task-add`.
- **HARD-STOP → emit `needs-human`, do NOT act:** spending money or
  purchasing/pricing judgment, deleting data, destructive git (force-push,
  reset --hard, history rewrites), pushing to remotes or deploying, sending
  anything to an external service (chat, email, tickets), touching
  credentials/secrets/production config.
- Your host's PreToolUse hooks (if any) are the backstop — but you stop
  FIRST when a step needs a hard-stop action.

## 5. FINISH

Dispatch a finish subagent: broad review across the run's commits +
artifacts, write `docs/reports/<run_id>-report.md` (what shipped, per-task
outcomes, anything deferred). `run-manifest mark-phase <run_id> finish done`.

Then emit the **terminal** line (this is the ONLY phase that emits `done`):

`LOOP_RESULT: {"status":"done","outcome":"<=140 chars: what shipped>"}`

Use `blocked` if an external dependency stopped you, `needs-human` if a
guardrail or critic halted the run.

DIRECT mode: after emitting the terminal line — whichever status — clear the
lease you claimed in §0 step 0.

## 6. CHECKPOINT & YIELD (CHUNKED mode)

After you finish ONE unit (a phase in §1–§3, or one task in §4) **and the
manifest is saved** (every run-manifest mutation saves atomically):

1. Confirm the unit's progress is recorded — a fresh worker must not redo it.
2. Emit EXACTLY this terminal line and STOP — do not start the next unit:
   `LOOP_RESULT: {"status":"continue","outcome":"<=140 chars: unit done; next: <what's next>"}`

The drainer sees `continue`, re-queues the task as `todo`, clears the lease,
and immediately spawns a FRESH worker that re-enters this skill, reloads the
manifest, and picks up at the next phase/task. Each unit therefore runs in
its own clean context window — no human handoff, no context inflation. Only
§5 FINISH ever emits `done`.

**Do NOT use `continue` in DIRECT mode** (no drainer to re-spawn you) — there
you loop the units inline and finish with `done`.

## Direct (at-keyboard) mode

If the user invokes this skill on a bare goal (no task file), create the
run_id from a slug of the goal, use the kit's `default_repo` (or the repo
they name), and run the units **inline** — loop §0's "next unit" selection
through research→…→finish in this one session (each phase still in a fresh
subagent), and finish with `done`. You still write the manifest after each
unit, so if this session dies, `task-add` on the same goal lets the loop
resume it in CHUNKED mode from where you stopped. This is the
explicitly-AUTONOMOUS path — do not start asking the user approval questions
here; the critics are the approval gates.
