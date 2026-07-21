# Scheduled draining (optional)

The drainer is single-flight (a second start just prints "busy"), so a
timer + on-demand `task-go` kicks coexist safely.

```bash
mkdir -p ~/.config/systemd/user
cp session-loop.service session-loop.timer ~/.config/systemd/user/
# Edit ExecStart in the .service if the kit isn't at ~/claude-workflow-kit
systemctl --user daemon-reload
systemctl --user enable --now session-loop.timer
journalctl --user -u session-loop.service -f     # logs
```

Cadence: first activation 5 minutes after boot, then 10 minutes after
each activation finishes (±2 min jitter) — tune the `[Timer]` block to
taste.

No systemd? A cron line does the same:

```
*/10 * * * * $HOME/claude-workflow-kit/bin/drain-once
```

The service runs niced (`Nice=19`, idle IO) so unattended work never
competes with your interactive apps.
