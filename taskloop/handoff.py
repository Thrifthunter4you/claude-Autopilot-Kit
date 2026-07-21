"""Task files ("handoffs"): markdown with a YAML frontmatter `loop:` block.

The `loop:` block is the queue contract:

    loop:
      status: todo          # todo | in-progress | done | needs-human | blocked
      class: safe           # risk class; `destructive` never auto-runs
      priority: 3           # 0 = most urgent
      goal: "one-line concrete goal"
      goal_hash: "abc123"   # stable identity for exact-duplicate detection
      repo: /path/to/repo   # where the worker runs
      recall_hint: "..."    # what context to load first
      lease: ""             # loop-<epoch>-<pid> while a worker owns it
      strikes: 0            # crash count; parked needs-human at max
      autopilot: true       # run the full phase pipeline (autopilot skill)
      not_before: ""        # optional ISO-8601 gate; ineligible until then
      acceptance_command: "" # optional shell cmd that must exit 0 to accept done

Everything below the frontmatter is the human-readable task body.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_DELIM = "---"


def _split(text: str):
    """Return (front_dict, body_str). front_dict is {} when no frontmatter."""
    if not text.startswith(_DELIM):
        return {}, text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == _DELIM:
            raw = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            front = yaml.safe_load(raw) or {}
            if not isinstance(front, dict):
                return {}, text
            return front, body
    return {}, text


def _dump(front: dict, body: str) -> str:
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"{_DELIM}\n{fm}\n{_DELIM}\n{body}"


@dataclass
class Handoff:
    path: Path
    front: dict = field(default_factory=dict)
    body: str = ""

    @classmethod
    def load(cls, path) -> "Handoff":
        path = Path(path)
        front, body = _split(path.read_text())
        return cls(path=path, front=front, body=body)

    @property
    def loop(self) -> Optional[dict]:
        v = self.front.get("loop")
        return v if isinstance(v, dict) else None

    @property
    def is_managed(self) -> bool:
        return self.loop is not None

    def set_loop(self, block: dict) -> None:
        self.front["loop"] = dict(block)

    def update_loop(self, **changes) -> None:
        block = dict(self.loop or {})
        block.update(changes)
        self.front["loop"] = block

    def save(self) -> None:
        # Atomic write (tmp + os.replace) so a concurrent reader never observes
        # a half-written file.  Under a parallel drainer, one thread's save can
        # overlap another thread's queue scan; a plain truncate+write lets the
        # reader catch an empty file and misclassify the task.  os.replace is
        # atomic within a filesystem: readers see the complete old or new file.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(_dump(self.front, self.body))
        os.replace(tmp, self.path)
