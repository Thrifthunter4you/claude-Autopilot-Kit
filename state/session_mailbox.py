#!/usr/bin/env python3
"""Cross-session mailbox: announce landings/claims so parallel sessions
don't collide, and new sessions catch up instantly.

Append-only JSONL at $TASKLOOP_HOME/mailbox.jsonl:
    {"ts": 1710000000, "session": "abc123", "kind": "landing|claim|note",
     "repo": "/path", "text": "..."}

CLI:
    session_mailbox.py post <kind> "<text>" [--repo PATH] [--session ID]
    session_mailbox.py list [--since-hours N] [--kind K]

Conventions (mirror them in your CLAUDE.md so agents actually use it):
  - post a `claim` BEFORE starting a multi-file change in a shared repo,
    naming the files you'll touch
  - post a `landing` after you commit/ship, with the short SHA
  - read recent messages before big changes; overlapping claims mean
    coordinate, not clobber
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
MAILBOX = HOME / "mailbox.jsonl"
KINDS = {"landing", "claim", "note"}


def post(kind: str, text: str, repo: str = "", session: str = "") -> int:
    if kind not in KINDS:
        print(f"kind must be one of {sorted(KINDS)}", file=sys.stderr)
        return 2
    HOME.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": int(time.time()),
        "session": session or os.environ.get("CLAUDE_CODE_SESSION_ID", "")[:8],
        "kind": kind,
        "repo": repo,
        "text": text,
    }
    with open(MAILBOX, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(f"[mailbox] posted {kind}")
    return 0


def list_rows(since_hours: float = 24, kind: str = "") -> int:
    if not MAILBOX.exists():
        print("(mailbox empty)")
        return 0
    cutoff = time.time() - since_hours * 3600
    shown = 0
    for line in MAILBOX.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(row.get("ts", 0)) < cutoff:
            continue
        if kind and row.get("kind") != kind:
            continue
        age_m = int((time.time() - float(row.get("ts", 0))) / 60)
        repo = f" repo={row.get('repo')}" if row.get("repo") else ""
        print(f"[{row.get('kind', 'note')}] {row.get('session', '?')} "
              f"{age_m}m ago{repo}: {row.get('text', '')}")
        shown += 1
    if not shown:
        print("(no messages in window)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_post = sub.add_parser("post")
    p_post.add_argument("kind", choices=sorted(KINDS))
    p_post.add_argument("text")
    p_post.add_argument("--repo", default="")
    p_post.add_argument("--session", default="")
    p_list = sub.add_parser("list")
    p_list.add_argument("--since-hours", type=float, default=24)
    p_list.add_argument("--kind", default="")
    args = ap.parse_args(argv)
    if args.cmd == "post":
        return post(args.kind, args.text, repo=args.repo, session=args.session)
    return list_rows(since_hours=args.since_hours, kind=args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
