# Docs

- [architecture.md](architecture.md) — the pieces, why chunked workers,
  why file-based everything, and the safety model.
- [queueing-and-gap-finding.md](queueing-and-gap-finding.md) — the four
  producers that keep the queue feeding itself, triage, and dedup in
  depth.
- [advanced-patterns.md](advanced-patterns.md) — optional shapes to grow
  into: swarm mode, acceptance commands, maintenance sweeps, adversarial
  verification fan-outs.
- [token-economy.md](token-economy.md) — where long sessions actually
  spend tokens (carry cost, cache writes), and the kit's countermeasures:
  `token-audit`, `spill`, the tier policy, and the toolbox.
