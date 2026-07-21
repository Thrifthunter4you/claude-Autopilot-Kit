"""Run manifests: the on-disk state that makes chunked runs resumable.

One JSON file per run at ``$TASKLOOP_HOME/runs/<run_id>.json``.  The autopilot
skill (skills/autopilot/SKILL.md) reads and writes it between worker
invocations, so each pipeline phase can run in a FRESH context window while
the run as a whole never loses its place.

Shape:
    {
      "run_id": "...",
      "profile": "trivial|small|large",
      "phases": {"research": {"status": "done", "artifact": "...",
                              "summary": "..."}, ...},
      "tasks":  {"1": "done", "2": "pending", ...},
      "task_order": ["1", "2", ...],
      "created_at": "...", "updated_at": "..."
    }
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional

from . import config


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def path_for(run_id: str) -> Path:
    return Path(config.RUNS_DIR) / f"{run_id}.json"


def load(run_id: str) -> dict:
    """Load a run manifest, creating an empty one in memory if absent."""
    p = path_for(run_id)
    if p.exists():
        data = json.loads(p.read_text())
    else:
        data = {"run_id": run_id, "profile": None, "phases": {},
                "tasks": {}, "task_order": [], "created_at": _now()}
    data.setdefault("phases", {})
    data.setdefault("tasks", {})
    data.setdefault("task_order", [])
    return data


def save(data: dict) -> None:
    """Atomic write — a crash mid-save must never leave a torn manifest."""
    config.ensure_dirs()
    p = path_for(data["run_id"])
    data["updated_at"] = _now()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, p)


# --- Phases -----------------------------------------------------------------

def mark_phase(data: dict, phase: str, status: str = "done",
               artifact: str = "", summary: str = "") -> None:
    entry = dict(data["phases"].get(phase) or {})
    entry["status"] = status
    if artifact:
        entry["artifact"] = artifact
    if summary:
        entry["summary"] = summary
    data["phases"][phase] = entry


def phase_status(data: dict, phase: str) -> Optional[str]:
    entry = data["phases"].get(phase)
    return entry.get("status") if isinstance(entry, dict) else None


def next_pending(data: dict, phases: list) -> Optional[str]:
    """First phase in ``phases`` not yet marked done, else None."""
    for ph in phases:
        if phase_status(data, ph) != "done":
            return ph
    return None


# --- Execute-phase tasks ----------------------------------------------------

def set_tasks(data: dict, task_ids: list) -> None:
    """Seed the execute-phase task list.  Idempotent: existing task states are
    preserved, so it is safe to call on every invocation."""
    for tid in task_ids:
        tid = str(tid)
        data["tasks"].setdefault(tid, "pending")
        if tid not in data["task_order"]:
            data["task_order"].append(tid)


def next_pending_task(data: dict) -> Optional[str]:
    for tid in data["task_order"]:
        if data["tasks"].get(tid) != "done":
            return tid
    return None


def mark_task(data: dict, task_id: str, status: str = "done") -> None:
    data["tasks"][str(task_id)] = status
