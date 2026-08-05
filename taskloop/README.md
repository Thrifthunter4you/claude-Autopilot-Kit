# taskloop

The core package — plain Python, PyYAML as the only dependency:

| Module | Job |
|---|---|
| [`config.py`](config.py) | env > `config.yaml` > defaults; the state-home layout |
| [`handoff.py`](handoff.py) | task files: markdown + YAML `loop:` frontmatter |
| [`queue.py`](queue.py) | eligibility gates + selection (priority, then aging) |
| [`drainer.py`](drainer.py) | single-flight loop: claim → spawn worker → apply verdict |
| [`worker.py`](worker.py) | render prompt template → `claude -p` → parse `LOOP_RESULT` |
| [`manifest.py`](manifest.py) | chunked-run state: phases, execute tasks, profile |
| [`toolbox.py`](toolbox.py) | the script registry behind `bin/toolbox`: a YAML file of named tools (plain commands or multi-step playbooks), keyword lookup, lock-safe registration, and a JSONL fire log written on every run |

Design rationale: [../docs/architecture.md](../docs/architecture.md).
The toolbox's full story (why register scripts at all, the territory-defense
hook, the crystallize loop it closes): its module docstring and the
["Meet the new pieces" section of the root README](../README.md#meet-the-new-pieces).
