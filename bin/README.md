# CLIs

Five commands, symlinked onto your PATH by `install.sh`:

| Command | Job |
|---|---|
| [`task-add`](task-add) | file a goal as a runnable task (three layers of dedup) |
| [`task-go`](task-go) | `task-add` + kick the drainer in the background |
| [`task-list`](task-list) | queue snapshot (`--all` for every status) |
| [`drain-once`](drain-once) | one foreground drainer activation (the cron/systemd entry point) |
| [`run-manifest`](run-manifest) | read/write chunked-run state from worker prompts |

All five support `--help` without side effects.
