# Templates

Prompt templates rendered by [`taskloop/worker.py`](../taskloop/worker.py),
plus a starter project-instructions file:

| File | Used for |
|---|---|
| [`autopilot_prompt.txt`](autopilot_prompt.txt) | autopilot-class tasks — invokes the autopilot skill |
| [`worker_prompt.txt`](worker_prompt.txt) | plain tasks — pursue the goal, return the structured verdict |
| [`maintenance_prompt.txt`](maintenance_prompt.txt) | scheduled file reviews — confirmed defects become fix tasks |
| [`CLAUDE.md.template`](CLAUDE.md.template) | starter CLAUDE.md carrying the session discipline |
