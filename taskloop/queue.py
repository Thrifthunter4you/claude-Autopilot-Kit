"""Queue eligibility + selection over the active task directory."""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from . import config
from .handoff import Handoff


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_not_before(raw) -> Optional[datetime.datetime]:
    """Fail-open ISO-8601 parse: None on missing/malformed input, so callers
    treat None as "no time gate" rather than erroring.

    Accepts three shapes:
      - str: parsed via fromisoformat (trailing 'Z' normalized for pre-3.11).
      - datetime.datetime: PyYAML resolves an UNQUOTED ISO-8601 scalar in
        frontmatter (`not_before: 2026-01-01T10:00:00Z`) to a native datetime,
        not a str — accept it directly or the gate silently no-ops on
        hand-edited YAML.
      - datetime.date (checked AFTER datetime, its subclass): a bare unquoted
        date resolves to a date; normalize to UTC midnight.

    Naive datetimes are forced to UTC to avoid naive-vs-aware comparison
    crashes.
    """
    if isinstance(raw, datetime.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=datetime.timezone.utc)
    if isinstance(raw, datetime.date):
        return datetime.datetime(raw.year, raw.month, raw.day,
                                 tzinfo=datetime.timezone.utc)
    if not raw or not isinstance(raw, str):
        return None
    try:
        s = raw.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        parsed = datetime.datetime.fromisoformat(s)
        return parsed if parsed.tzinfo else parsed.replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def eligible(h: Handoff) -> bool:
    """Can the drainer claim this task right now?"""
    lp = h.loop
    if not lp:
        return False
    nb = _parse_not_before(lp.get("not_before"))
    return (
        lp.get("status") == config.AUTO_STATUS
        and lp.get("class") not in config.GATED_CLASSES
        and not lp.get("lease")
        and int(lp.get("strikes", 0)) < config.MAX_STRIKES
        and (nb is None or nb <= _now_utc())
    )


def _key(h: Handoff):
    """Queue order: strict priority, then least-recently picked, then name.

    A task that yields ``continue`` becomes eligible again on its next pass.
    Without a persistent second key, filename ordering lets a small set of
    churners monopolize a priority band.  ``last_picked_at`` is stamped when a
    worker claims a task, so never-picked and longest-waiting tasks get the
    next slot within that band.  Priority remains the dominant ordering.
    """
    last_picked = _parse_not_before((h.loop or {}).get("last_picked_at"))
    if last_picked is None:
        # Missing/malformed stamps count as never picked: fail open towards
        # fairness, with a deterministic filename tie-break.
        last_picked = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return (int((h.loop or {}).get("priority", config.DEFAULT_PRIORITY)),
            last_picked, h.path.name)


def mark_picked(h: Handoff) -> None:
    """Persist the claim time used by _key's same-priority aging order."""
    h.update_loop(last_picked_at=_now_utc().isoformat())


def select_next(active_dir=None) -> Optional[Handoff]:
    """Top-priority eligible task, or None when the queue is drained."""
    active_dir = Path(active_dir or config.ACTIVE_DIR)
    cands = []
    for p in sorted(active_dir.glob("*.md")):
        # Skip a file that fails to parse rather than crashing selection.
        # Under concurrency another worker may be mid-rewrite of its own task
        # file while this scan runs; an unreadable/partial file is transient
        # and must not abort the whole drain.  A durably malformed file is
        # simply invisible to selection until fixed.
        try:
            h = Handoff.load(p)
        except Exception:
            continue
        if eligible(h):
            cands.append(h)
    if not cands:
        return None
    cands.sort(key=_key)
    return cands[0]


def snapshot(active_dir=None) -> dict:
    """Read-only classification of the queue: how many tasks are runnable vs
    why each todo-status task is currently blocked.  Reuses eligible()'s own
    predicate so the count can never drift from actual selection."""
    active_dir = Path(active_dir or config.ACTIVE_DIR)
    out = {"eligible": 0, "total_todo": 0, "lease_held": 0,
           "class_locked": 0, "strikes_exhausted": 0, "not_before_pending": 0,
           "parked": 0}
    for p in sorted(active_dir.glob("*.md")):
        try:
            h = Handoff.load(p)
        except Exception:
            continue
        lp = h.loop or {}
        status = lp.get("status")
        if status in {"needs-human", "blocked", "needs-triage"}:
            out["parked"] += 1
        if status != config.AUTO_STATUS:
            continue
        out["total_todo"] += 1
        if eligible(h):
            out["eligible"] += 1
        elif lp.get("lease"):
            out["lease_held"] += 1
        elif lp.get("class") in config.GATED_CLASSES:
            out["class_locked"] += 1
        elif int(lp.get("strikes", 0)) >= config.MAX_STRIKES:
            out["strikes_exhausted"] += 1
        else:
            nb = _parse_not_before(lp.get("not_before"))
            if nb is not None and nb > _now_utc():
                out["not_before_pending"] += 1
    return out
