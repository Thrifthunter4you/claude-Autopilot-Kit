# CLIs

Ten commands, symlinked onto your PATH by `install.sh`. The first five
run the task loop; the other five are standalone (useful even if you never
touch the queue).

| Command | Job |
|---|---|
| [`task-add`](task-add) | file a one-line goal as a runnable task (three layers of dedup) |
| [`task-go`](task-go) | `task-add` + kick the drainer in the background |
| [`task-list`](task-list) | queue snapshot (`--all` for every status) |
| [`drain-once`](drain-once) | one foreground drainer activation (the cron/systemd entry point) |
| [`run-manifest`](run-manifest) | read/write chunked-run state from worker prompts |
| [`toolbox`](toolbox) | registry of reusable scripts — register once, then `find` / `describe` / `run` by name; every run telemetered ([why](../taskloop/toolbox.py)) |
| [`token-audit`](token-audit) | rank a session transcript's token carriers by carry cost — what to cut, biggest first ([the math](../docs/token-economy.md)) |
| [`spill`](spill) | run a command with bounded output: full text to a file, head+tail to the conversation, `show --grep` to drill in later |
| [`noise-floor`](noise-floor) | grade a metric delta against the empirical rerun noise floor of its baseline command — SHIP / DONT-SHIP / INSIDE-NOISE-FLOOR with the minimum detectable effect and its rule named; exit 3 on DONT-SHIP so scripts can gate on it |
| [`hook-cost`](hook-cost) | read the hook decision log: denies / reminders / rewrites per hook and per session, bytes the output spill kept out of context, subagents that ran past their turn budget |

All ten support `--help` without side effects.
