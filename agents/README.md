# Agents

The tiered subagent fleet the conductor dispatches: cheap finder/sweep
leaves, mid-tier verifier/judge/fixer leaves, one synthesis leaf, plus
`scout` (interactive recon) and `triage-judge` (queue triage).

- Start with [AGENT_DESIGN.md](AGENT_DESIGN.md) — the design rules, model
  tiering, and each agent's one-line contract.
- [tier_policy.yaml](tier_policy.yaml) is the single source of truth for
  who runs on which tier; `tests/test_tier_policy.py` fails the suite when
  an agent's `model:` line drifts from it (change both, same commit).
- Mint your own from [agent_def_template.md](agent_def_template.md) — and
  add it to the tier policy, or the lint will remind you.
- `install.sh` symlinks these into `~/.claude/agents/` (non-clobber: an
  existing real file with the same name is never overwritten).
