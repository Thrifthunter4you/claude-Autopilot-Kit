# State

[`session_mailbox.py`](session_mailbox.py) — the cross-session mailbox.
Sessions append `claim` / `landing` / `note` lines to an append-only
JSONL (`$TASKLOOP_HOME/mailbox.jsonl`) so parallel sessions don't collide
and new sessions catch up instantly. The session-start hook surfaces
recent messages; post a `claim` before multi-file work in a shared repo
and a `landing` after you ship.
