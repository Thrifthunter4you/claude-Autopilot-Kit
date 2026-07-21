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

Design rationale: [../docs/architecture.md](../docs/architecture.md).
