"""The drainer: claim eligible tasks and run workers until the queue is dry.

Single-flight: one drainer per machine (lock file with pid).  Within one
activation up to ``max_concurrency`` worker threads run, each blocking on its
own `claude -p` process for a DISTINCT leased task.

Verdict handling:
    done         -> optional acceptance_command gate; archive to queue/done/
    continue     -> requeue as todo (a fresh worker resumes from the manifest)
    needs-human  -> park with that status (a human or triage session decides)
    blocked      -> park with that status
    crash        -> strike; at max_strikes park needs-human

Run it directly (``python3 -m taskloop.drainer``), from cron/systemd, or via
``bin/task-go`` after queueing a goal.
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from . import config, queue, worker
from .handoff import Handoff

_LOCK = config.HOME / "drain.lock"
_LEASE_RE = re.compile(r"^loop-(\d+)-(\d+)$")


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# --- Single-flight lock -----------------------------------------------------

def acquire_lock() -> bool:
    config.HOME.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            pid = int(_LOCK.read_text().strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            return False
        # Stale lock from a dead drainer — take it over.
        try:
            _LOCK.write_text(str(os.getpid()))
        except OSError:
            return False
        return True


def release_lock() -> None:
    try:
        _LOCK.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --- Lease lifecycle --------------------------------------------------------

def make_lease() -> str:
    return f"loop-{int(time.time())}-{os.getpid()}"


def reap_stale_leases() -> None:
    """Clear leases whose holder is dead or whose claim is older than the
    stale window, so a crashed worker never wedges its task forever."""
    now = time.time()
    for p in sorted(Path(config.ACTIVE_DIR).glob("*.md")):
        try:
            h = Handoff.load(p)
        except Exception:
            continue
        lp = h.loop or {}
        m = _LEASE_RE.match(str(lp.get("lease") or ""))
        if not m:
            continue
        epoch, pid = int(m.group(1)), int(m.group(2))
        if now - epoch > config.STALE_LEASE_S or not _pid_alive(pid):
            _log(f"reaping stale lease on {p.name}")
            h.update_loop(lease="")
            h.save()


# --- Verdict application ----------------------------------------------------

def _archive_done(h: Handoff) -> None:
    dest = Path(config.DONE_DIR) / h.path.name
    try:
        shutil.move(str(h.path), dest)
    except OSError:
        pass


def _acceptance_ok(h: Handoff) -> bool:
    """Optional objective gate: only exit code 0 lets a done verdict stand."""
    cmd = str((h.loop or {}).get("acceptance_command") or "").strip()
    if not cmd:
        return True
    import subprocess
    repo = str((h.loop or {}).get("repo") or config.DEFAULT_REPO or Path.home())
    try:
        r = subprocess.run(cmd, shell=True, cwd=repo, capture_output=True,
                           text=True, timeout=1800)
        return r.returncode == 0
    except Exception:
        return False


def apply_verdict(h: Handoff, result) -> None:
    # Reload before writing: the worker itself may have edited the task file.
    try:
        h = Handoff.load(h.path)
    except Exception:
        return
    lp = dict(h.loop or {})
    outcome = (result or {}).get("outcome", "")
    status = (result or {}).get("status")

    if status == "done":
        if _acceptance_ok(h):
            h.update_loop(status="done", lease="", outcome=outcome)
            h.save()
            _archive_done(h)
            _log(f"DONE {h.path.name}: {outcome}")
            return
        status = "continue"
        outcome = f"acceptance_command failed; requeued. ({outcome})"

    if status == "continue":
        h.update_loop(status="todo", lease="", strikes=0, outcome=outcome)
        h.save()
        _log(f"CONTINUE {h.path.name}: {outcome}")
    elif status in {"needs-human", "blocked"}:
        h.update_loop(status=status, lease="", outcome=outcome)
        h.save()
        _log(f"{status.upper()} {h.path.name}: {outcome}")
    else:  # crash / no LOOP_RESULT
        strikes = int(lp.get("strikes", 0)) + 1
        if strikes >= config.MAX_STRIKES:
            h.update_loop(status="needs-human", lease="", strikes=strikes,
                          outcome="worker crashed repeatedly (no LOOP_RESULT)")
            _log(f"PARKED {h.path.name} after {strikes} strikes")
        else:
            h.update_loop(status="todo", lease="", strikes=strikes)
            _log(f"STRIKE {strikes} on {h.path.name}")
        h.save()


# --- Main loop --------------------------------------------------------------

_claim_mutex = threading.Lock()


def _claim_next():
    """Select + lease under a mutex so two threads never claim one task."""
    with _claim_mutex:
        h = queue.select_next()
        if h is None:
            return None
        h.update_loop(lease=make_lease(), status="in-progress")
        queue.mark_picked(h)
        h.save()
        return h


def _worker_thread(h: Handoff) -> None:
    _log(f"claimed {h.path.name}")
    result = worker.run_one(h)
    apply_verdict(h, result)


def drain_loop() -> int:
    config.ensure_dirs()
    if not acquire_lock():
        _log("another drainer is running (busy) — exiting")
        return 0
    try:
        reap_stale_leases()
        threads: list[threading.Thread] = []
        while True:
            threads = [t for t in threads if t.is_alive()]
            if len(threads) < config.MAX_CONCURRENCY:
                h = _claim_next()
                if h is not None:
                    t = threading.Thread(target=_worker_thread, args=(h,),
                                         daemon=False)
                    t.start()
                    threads.append(t)
                    time.sleep(config.SPAWN_STAGGER_S)
                    continue
                if not threads:
                    break  # queue drained and nothing in flight
            time.sleep(5)
        snap = queue.snapshot()
        _log(f"queue drained: {snap}")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(drain_loop())
