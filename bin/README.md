# CLIs

Eight commands, symlinked onto your PATH by `install.sh`. The first five
run the task loop; the last three are standalone (useful even if you never
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

All eight support `--help` without side effects.
