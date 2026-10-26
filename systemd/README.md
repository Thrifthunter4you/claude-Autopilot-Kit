# Scheduled draining (optional)

The drainer is single-flight (a second start just prints "busy"), so a
timer + on-demand `task-go` kicks coexist safely.

```bash
mkdir -p ~/.config/systemd/user
cp taskloop.service taskloop.timer ~/.config/systemd/user/
# Edit ExecStart in the .service if the kit isn't at ~/claude-workflow-kit
systemctl --user daemon-reload
systemctl --user enable --now taskloop.timer
journalctl --user -u taskloop.service -f     # logs
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

## Upgrading from 0.5 or earlier

The units had different names before 0.6.0. An existing install keeps
working after a pull, because systemd runs the copies in
`~/.config/systemd/user/`; only commands and scripts that name the units
need the new names. The unit contents did not change, so switching is a
rename of your installed copies, which keeps any `ExecStart` or `[Timer]`
edits you made. Find the old service first: it is the one that runs the
drainer, and its timer has the same base name (no output = nothing to
upgrade):

```bash
grep -l drain-once ~/.config/systemd/user/*.service | grep -v '/taskloop\.service$'
```

Then swap them. If both timers stay enabled, the drainer gets two wake-ups
and the second one exits as busy:

```bash
OLD=old-unit-name    # replace with the base name printed above, without .service
systemctl --user disable --now "$OLD.timer"
mv ~/.config/systemd/user/"$OLD".service ~/.config/systemd/user/taskloop.service
mv ~/.config/systemd/user/"$OLD".timer ~/.config/systemd/user/taskloop.timer
systemctl --user daemon-reload
systemctl --user enable --now taskloop.timer
```
