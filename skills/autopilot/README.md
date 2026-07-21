# autopilot

The conductor protocol task-loop workers follow. [SKILL.md](SKILL.md)
right-sizes the pipeline (trivial/small/large), runs each phase in a
fresh subagent, gates design and plan with adversarial critics instead of
human approval, and checkpoints to the run manifest before yielding.

`install.sh` links this folder into `~/.claude/skills/autopilot`; the
worker prompt invokes it by name, and you can invoke it directly in an
interactive session too.
