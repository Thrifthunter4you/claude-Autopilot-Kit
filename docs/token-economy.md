# Token economy

Long agent sessions don't get expensive because the model writes a lot.
They get expensive because of what the context window **carries** — and
most of the carrying is invisible until you measure it. This doc is the
kit's working model of where tokens actually go, and the mechanisms it
ships to cut them. Everything here generalizes; none of it requires this
kit's task loop.

## The carry-cost law

Everything that enters the context window is re-read on **every later
turn**. So the true cost of a block of output is not its size — it is

```
carry ≈ size × (turns remaining after it appears)
```

Consequences that feel wrong until you internalize them:

- A modest 4k-token file dump at turn 5 of a 150-turn session costs more
  than a giant 30k dump at turn 145.
- Session-start attachments (CLAUDE.md, hook-injected briefings, memory
  indexes) are almost always the #1 carrier — they are present for *every*
  turn. Prefix budget is the most valuable real estate you own.
- Trimming what you *add late* is cosmetic; trimming what you *add early*
  compounds.

Run [`token-audit`](../bin/token-audit) on any session transcript to see
your own carriers ranked by exactly this math.

## Cache writes are the bill, not cache reads

With prompt caching, re-reading cached context is cheap (~0.1× the input
price); **writing** context into the cache is the expensive part (~1.25×
for short TTL, more for long). Two rules fall out:

1. **A stable prefix is a cheap prefix.** Anything that mutates your
   system prompt or early context every turn — timestamps, counters,
   reordered lists — forces a fresh cache write of everything after it.
   Keep the prefix byte-identical across turns and across parallel agents.
2. **Rank token levers by carry, not by prefix size.** A small block
   injected every session start can out-cost a large block loaded once
   on demand. When cutting, cut carry.

`token-audit` reports your cache read/write split; if writes rival reads,
something is churning your prefix.

## Keep bulk output out of the window

Most tool output exists only in case something went wrong. The pattern:
**full text on disk, bounded preview in the conversation, drill-down on
demand.**

- [`spill run -- <cmd>`](../bin/spill) runs any command, stores the full
  output in a spill file, and shows only head+tail plus the file path.
  `spill show --grep` retrieves exactly the lines you need later.
- Delegate broad sweeps (read-a-whole-module, codebase-wide greps) to a
  cheap-tier subagent — only its *conclusion* enters your window, and the
  file dumps die with the subagent's context.
- Prefer outlines and ranges over whole files: read the 40 lines you need,
  not the 800-line module. Re-reading the same file twice is pure
  duplicate carry (`token-audit` flags it).

## Tier the fleet — and lint the tiers

Fan-out work has three natural price points (see
[AGENT_DESIGN.md](../agents/AGENT_DESIGN.md)):

| Stage | Tier | Why |
|---|---|---|
| sweep / fetch / dedup / test-run | cheap | volume work; conclusions only |
| verify / judge / critique / fix | mid | judgment under bounded scope |
| synthesis (one final stage) | best available | integrates everything, once |

Tier assignments drift unless something enforces them, so the fleet's
tiers live in ONE file — [`agents/tier_policy.yaml`](../agents/tier_policy.yaml)
— and [`tests/test_tier_policy.py`](../tests/test_tier_policy.py) fails the
suite whenever an agent's `model:` line disagrees with it. Changing a tier
means editing both, in the same commit, on purpose. Two hard-won caveats:

- **Precision-critical reading is not cheap-tier work.** Where misreading
  costs money or data, use mid-tier readers even for sweeps.
- **Batch items per leaf.** Every subagent spawn pays a fixed harness
  floor (its system prompt, tool schemas, seed context). Five items per
  leaf amortizes that floor; one item per leaf multiplies it.

## Scripts beat re-derivation

Every time a session hand-assembles a pipeline the last session already
figured out, you pay twice: the tokens spent re-deriving it, and the risk
of deriving it subtly differently. The kit's answer is the
[toolbox](../taskloop/toolbox.py) loop:

1. the crystallize hook notices the repetition and queues a script task,
2. the loop builds the script; it gets registered (`toolbox add`),
3. the territory hook steers future sessions to `toolbox run <name>`
   instead of the hand-derived equivalent,
4. `toolbox stats` shows whether any of it is actually being used.

A registered one-liner is the cheapest token reduction there is: the
operation runs *outside* the context window entirely.

## Measure, then cut

None of the above tells you which lever matters for *your* sessions —
measurement does. The loop we recommend:

1. `token-audit` a few recent transcripts.
2. Take the top carrier — only the top one — and apply its
   countermeasure (delegate it, spill it, load it on demand, register it).
3. Re-audit next week. Repeat.

Cutting from the top of the ranked list beats any amount of general
tidiness, and the audit keeps you honest about whether a change actually
moved the bill.

## Believe a delta only after it clears the noise floor

Every "the change cut spend 4%" claim compares two numbers, and almost
nobody measures how far the SAME command moves when you simply run it
again. Reruns of a token audit, a benchmark, or a recall rate routinely
differ by more than the effect being claimed — so a single before/after pair
proves nothing either way.

[`noise-floor`](../bin/noise-floor) measures that floor empirically: it
reruns the baseline command N times, derives the minimum detectable effect
(MDE) from the spread (a seeded bootstrap CI from N ≥ 5), and grades the
delta — given, or measured by rerunning the candidate — against it:

```bash
noise-floor --baseline "token-audit last.jsonl | tail -1" \
            --candidate "token-audit new.jsonl | tail -1" -n 5 --direction lower
# VERDICT: INSIDE-NOISE-FLOOR
# baseline  n=5 min=41.2 max=44.9 mean=43.1 spread=3.7 ci=[41.8, 44.3]
# candidate n=5 mean=42.4 diff_ci=[-2.9, 1.6]
# delta     -0.7 (measured; lower is better)
# MDE       2.25  [bootstrap_diff_ci_half_width]
# → |delta| <= MDE: not a result at any confidence — rerun with a larger -n or a less noisy metric
```

Three verdicts, three exit codes: `SHIP` (0), `INSIDE-NOISE-FLOOR` (0 — not
a loss, an underpowered measurement), `DONT-SHIP` (3, so a script can gate
on it). The rule that produced the MDE is always named, because a delta
measured from two noisy arms needs a wider floor than one you were handed.
Run it before recording any "measure-after" number in memory; a number
inside the floor is the kind of memory that later lies.
