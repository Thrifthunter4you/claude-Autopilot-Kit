# Agent design guide

How the agents in this kit are structured, and how to design your own.
These are the conventions that emerged from running a large fleet of
specialized agents in production; each one earns its place by fixing a real
failure mode.

## The anatomy of an agent definition

Every agent is a markdown file in `~/.claude/agents/` (or your project's
`.claude/agents/`) with YAML frontmatter + a system prompt
(see `agent_def_template.md`):

```
---
name: kebab-case-name
description: |-
  WHEN to use it (this is what the dispatching model reads — write it as a
  trigger, not a summary).
  External grade: how you'd objectively know this agent is good.
model: haiku | sonnet | opus       # omit to inherit the session model
tools: Read, Grep, Glob, Bash      # narrowest set that does the job
---
<system prompt: role, ground rules, protocol, output shape>
```

Design rules, each one load-bearing:

1. **The description is a router, not a blurb.** The main agent picks
   subagents by description. Lead with the trigger conditions ("Use for ANY
   broad codebase search…"), name what it is NOT for, and say whether it's
   for direct use or only for workflow scripts.

2. **Name an external grade.** One line in the description stating how the
   agent would be objectively evaluated (agreement with human-adjudicated
   verdicts, a replay corpus, a measurable hit rate). If you can't name one,
   the agent's job is probably too vague to delegate.

3. **Static protocol in the system prompt, dynamic payload in the user
   message.** Everything that is the same on every dispatch (ground rules,
   protocol, output shape) lives in the agent def; the per-call inputs
   (mission, target, claim, diff) arrive in the user message. This is also
   what makes prompt caching work across a parallel fan-out: identical system
   blocks cache-share.

4. **WARMUP convention.** Every fan-out leaf ends with: "if the user message
   begins with WARMUP, use NO tools and return the placeholder values the
   message specifies." An orchestrator can then fire one serialized warmup
   call to prime the prompt cache before fanning out dozens of parallel
   calls against the same cached system block.

5. **Read-only by default.** Leaves that only need to look get only
   Read/Grep/Glob/Bash, and their ground rules say Bash is for inspection
   only. Only an executor leaf (wf-fixer) gets Edit/Write, and it carries a
   protected-path guard.

6. **"Your final message is parsed, not read."** Fan-out leaves return raw
   structured data for the orchestrating script — telling the agent this
   explicitly kills the preamble/prose it would otherwise produce.

7. **Citations or it didn't happen.** Reader/verifier leaves must cite
   {file, line} they personally opened in current source. Memory files and
   docs are an index for FINDING things, never evidence.

8. **Refute, don't confirm.** Verification leaves are prompted to REFUTE the
   claim, with 'refuted' as the default verdict under uncertainty. A panel
   of confirm-biased verifiers rubber-stamps everything and catches nothing.

## Model tiering

Match the model to the stage, not the project:

| Stage | Tier | Why |
|---|---|---|
| finder / sweep / reader / test-runner / dedup | cheap (haiku) | conclusions-only work; volume matters |
| verify / judge / fixer / review | mid (sonnet) | judgment matters; still parallelizable |
| synthesis (the single final stage) | best available (inherit) | one shot, integrates everything |

Two caveats learned the hard way:
- **Precision-critical reading is NOT cheap-tier work.** If misreading a file
  costs real money or data (billing logic, deletion paths), use mid-tier
  readers even for sweeps — small models drop findings there.
- **Pin the model explicitly in dispatches.** An unpinned dispatch silently
  rides whatever the session default happens to be that day.

## The standard leaf fleet

| Agent | Role | One-line contract |
|---|---|---|
| wf-finder | sweep/read | conclusions + citations, never file dumps |
| wf-verifier | adversarial verify | refute ONE claim; refuted is the default |
| wf-judge | score/rank | full criteria matrix over supplied candidates |
| wf-critic | completeness | name what's MISSING as next-round work items |
| wf-dedup | fuzzy merge | canonicalize same-issue findings; never drop substance |
| wf-tester | independent re-verify | run the supplied command verbatim, report faithfully |
| wf-fixer | executor | minimal diff + verify; orchestrator owns integration |
| wf-synth | synthesis | cited deliverable; no silent caps; refuted excluded |
| scout | interactive recon | broad searches without dumping files into main context |
| triage-judge | queue triage | promote/park/likely-done verdicts on parked tasks |

## Composition patterns

- **find → dedup → verify → synth**: the default review/audit shape.
- **N candidates → judge panel → synth grafts the best ideas**: for design
  questions with a wide solution space.
- **loop-until-dry with a critic**: keep spawning finders until the critic
  returns an empty gap list two rounds running.
- **fixer + independent tester**: never let the agent that wrote a patch be
  the only evidence the patch works.
