"""Reads the usage meters of a Codex account from the newest rate-limit snapshot that Codex wrote in its session files,
because Codex has no command that prints its usage."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

from usage import Meter

# A snapshot from before this is from an earlier weekly window, so older session files aren't read.
SNAPSHOT_AGE = dt.timedelta(days=8)


def read_meters(sessions: Path, reading_time: dt.datetime) -> tuple[dict[str, Meter], dt.datetime] | None:
    """The meters in the newest snapshot in the session files of the last SNAPSHOT_AGE, and the snapshot's time, or None
    without such a snapshot."""
    snapshot = newest_snapshot(sessions, reading_time - SNAPSHOT_AGE)
    if snapshot is None:
        return None
    snapshot_time, limits = snapshot
    meters: dict[str, Meter] = {}
    for window in (limits.get("primary"), limits.get("secondary")):
        if isinstance(window, dict):
            name = "session" if window.get("window_minutes", 0) <= 5 * 60 else "week"
            meters[name] = window_meter(window, reading_time)
    return meters, snapshot_time


def newest_snapshot(sessions: Path, oldest: dt.datetime) -> tuple[dt.datetime, dict[str, Any]] | None:
    rollouts = [
        rollout for rollout in sessions.glob("*/*/*/rollout-*.jsonl") if rollout.stat().st_mtime >= oldest.timestamp()
    ]
    snapshots = [snapshot for snapshot in map(last_snapshot, rollouts) if snapshot]
    return max(snapshots, key=lambda snapshot: snapshot[0], default=None)


def last_snapshot(rollout: Path) -> tuple[dt.datetime, dict[str, Any]] | None:
    for line in reversed(rollout.read_text(errors="replace").splitlines()):
        if '"used_percent"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        limits = entry.get("payload", {}).get("rate_limits")
        if isinstance(limits, dict):
            return dt.datetime.fromisoformat(entry["timestamp"]), limits
    return None


def window_meter(window: dict[str, Any], reading_time: dt.datetime) -> Meter:
    used = float(window.get("used_percent", 0))
    reset = dt.datetime.fromtimestamp(window["resets_at"], dt.UTC) if window.get("resets_at") else None
    # Codex writes a snapshot only while it runs, so a snapshot from before its window's reset is out of date.
    if reset and reset < reading_time:
        return {"used": 0.0, "resets": None}
    return {"used": used, "resets": reset.isoformat() if reset else None}
