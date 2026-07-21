# Agents

The tiered subagent fleet the conductor dispatches: cheap finder/sweep
leaves, mid-tier verifier/judge/fixer leaves, one synthesis leaf, plus
`scout` (interactive recon) and `triage-judge` (queue triage).

- Start with [AGENT_DESIGN.md](AGENT_DESIGN.md) — the design rules, model
  tiering, and each agent's one-line contract.
- Mint your own from [agent_def_template.md](agent_def_template.md).
- `install.sh` symlinks these into `~/.claude/agents/` (non-clobber: an
  existing real file with the same name is never overwritten).
