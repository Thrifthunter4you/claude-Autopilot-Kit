"""Configuration for the task loop.

Precedence (highest wins):
  1. Environment variables (``TASKLOOP_*``)
  2. ``$TASKLOOP_HOME/config.yaml`` (or ``~/.taskloop/config.yaml``)
  3. Built-in defaults below

Everything state-related lives under one home directory so the kit never
scatters files across your machine:

  ~/.taskloop/
    config.yaml          your overrides (optional)
    queue/active/        runnable + parked task files
    queue/done/          completed task files
    queue/superseded/    replaced/abandoned task files
    runs/                per-run JSON manifests (chunked-run state)
    logs/                drainer + worker logs
    memory/              saved memories (the memory MCP server's default)
    mailbox.jsonl        cross-session announcements
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import yaml
except ImportError:  # PyYAML is the kit's only hard dependency
    yaml = None


def _load_file_config() -> dict:
    home = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
    cfg_path = home / "config.yaml"
    if yaml is None or not cfg_path.exists():
        return {}
    try:
        data = yaml.safe_load(cfg_path.read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_FILE = _load_file_config()


def _get(key: str, default):
    """env TASKLOOP_<KEY> > config.yaml <key> > default (typed by default)."""
    env = os.environ.get(f"TASKLOOP_{key.upper()}")
    if env is not None:
        if isinstance(default, bool):
            return env.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(default, int):
            try:
                return int(env)
            except ValueError:
                return default
        return env
    val = _FILE.get(key)
    return default if val is None else val


def _get_list(key: str, default: list) -> list:
    """List-valued setting; env override is comma-separated."""
    env = os.environ.get(f"TASKLOOP_{key.upper()}")
    if env is not None:
        return [s.strip() for s in env.split(",") if s.strip()]
    val = _FILE.get(key)
    return list(val) if isinstance(val, (list, tuple)) else list(default)


# --- Layout -----------------------------------------------------------------
HOME = Path(os.environ.get("TASKLOOP_HOME", Path.home() / ".taskloop"))
ACTIVE_DIR = HOME / "queue" / "active"
DONE_DIR = HOME / "queue" / "done"
SUPERSEDED_DIR = HOME / "queue" / "superseded"
RUNS_DIR = HOME / "runs"
LOG_DIR = HOME / "logs"
MAILBOX = HOME / "mailbox.jsonl"

# --- Queue semantics --------------------------------------------------------
AUTO_STATUS = "todo"                 # the only status the drainer picks up
DEFAULT_CLASS = _get("default_class", "safe")
AUTOPILOT_CLASS = "autopilot"        # runs the full phase pipeline (skill)
# Classes that NEVER auto-run. Intake refuses to file them and the queue
# refuses to select them — same knob on both sides so they can't drift apart.
GATED_CLASSES = set(_get_list("gated_classes", ["destructive"]))
DEFAULT_PRIORITY = int(_get("default_priority", 3))   # 0 = most urgent
MAX_STRIKES = int(_get("max_strikes", 3))             # crashes before parking
STALE_LEASE_S = int(_get("stale_lease_s", 6 * 3600))  # reap dead claims after
FOLLOWON_DAILY_CAP = int(_get("followon_daily_cap", 6))

# --- Worker spawning --------------------------------------------------------
# The repository a task runs against when intake didn't specify one.
# There is deliberately NO built-in default: set it in config.yaml or pass
# --repo at intake time.
DEFAULT_REPO = str(_get("default_repo", "")) or None
CLAUDE_BIN = str(_get("claude_bin", "claude"))
WORKER_MODEL = str(_get("worker_model", "sonnet"))
# Extra CLI args for every worker spawn.  Unattended queues generally need
# permission bypass; the default grants edit-level autonomy only.  If you
# fully trust your queue contents, set:
#   claude_args: ["--dangerously-skip-permissions"]
CLAUDE_ARGS = _get_list("claude_args", ["--permission-mode", "acceptEdits"])
WORKER_TIMEOUT_S = int(_get("worker_timeout_s", 6 * 3600))
# --effort for every worker (low|medium|high|xhigh|max); "" = CLI default.
WORKER_EFFORT = str(_get("worker_effort", "")).strip().lower()
# Print mode kills a session whose background tasks outlive a wait ceiling
# (600 s by default) — with no verdict.  0 lifts the ceiling so
# worker_timeout_s is the only bound; -1 leaves the CLI default untouched.
WORKER_BG_WAIT_CEILING_MS = int(_get("worker_bg_wait_ceiling_ms", 0))
MAX_CONCURRENCY = int(_get("max_concurrency", 1))
SPAWN_STAGGER_S = int(_get("spawn_stagger_s", 8))
# Pipeline profiles the autopilot skill may record in a run manifest.
PROFILE_PHASES = {
    "trivial": ["execute", "finish"],
    "small": ["research", "design", "plan", "execute", "finish"],
    "large": ["research", "design", "plan", "execute", "finish"],
}


def ensure_dirs() -> None:
    for d in (ACTIVE_DIR, DONE_DIR, SUPERSEDED_DIR, RUNS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
