"""Counts how much of each account's weekly meter the agents' output tokens took, for scripts/loop/report."""

import datetime as dt
import itertools
import json
from typing import Any

from agent_log import AgentRun, agent_output_tokens, started

# The meter for all models, as usage.py names it for Claude, and the weekly window of Codex
WEEK = "week"
# /usage rounds a reset time differently from one reading to the next, so only a reset that moved further than this
# starts a new week. A week's next reset is 7 days after the last.
NEW_WEEK_SHIFT = dt.timedelta(days=1)


def account_lines(runs: list[AgentRun], event_tokens: dict[str, int], meter_log: str, since: dt.datetime) -> list[str]:
    """A line for each account that the agents that started after the time ran on, or that the meter read after it:
    the agents' output tokens, the points that the account's weekly meter rose, and the tokens for each point. Then a
    line for the agents whose start line names no account."""
    tokens: dict[str | None, int] = {}
    for run in runs:
        if started(run) < since or run.end is None:
            continue
        account = run.start.get("account")
        tokens[account] = tokens.get(account, 0) + agent_output_tokens(run.end, event_tokens)[0]
    points = week_points(read_week_readings(meter_log, since))
    names = sorted({name for name in tokens if name is not None} | set(points))
    if not names and None not in tokens:
        return ["Accounts: no agent ended and the meter read no weekly meter"]
    lines = [account_line(name, tokens.get(name, 0), points.get(name, 0.0)) for name in names]
    if None in tokens:
        lines.append(f"Agents whose start line names no account: {tokens[None]} output tokens")
    return lines


def account_line(name: str, tokens: int, points: float) -> str:
    line = f"Account {name}: {tokens} output tokens"
    if not points:
        return f"{line}, no rise of the weekly meter"
    return f"{line}, {points:g} points of the weekly meter, {tokens / points:.0f} output tokens per point"


def read_week_readings(meter_log: str, since: dt.datetime) -> dict[str, list[dict[str, Any]]]:
    """The readings of each account's weekly meter in the meter's log after the time, in the order of their times. A
    machine reading and an account's reading without a weekly meter have none."""
    readings: dict[str, list[dict[str, Any]]] = {}
    for line in meter_log.splitlines():
        entry = json.loads(line)
        week = entry.get("meters", {}).get(WEEK)
        if "account" in entry and week and dt.datetime.fromisoformat(entry["at"]) >= since:
            readings.setdefault(entry["account"], []).append({"at": entry["at"], **week})
    for account_readings in readings.values():
        account_readings.sort(key=lambda reading: dt.datetime.fromisoformat(reading["at"]))
    return readings


def week_points(readings: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    """The percentage points that each account's weekly meter rose from its first reading to its last. In a new week,
    the meter starts again from 0, so the rise counts from 0. The rise of the old week after its last reading is
    unknown and doesn't count."""
    points: dict[str, float] = {}
    for account, account_readings in readings.items():
        rise = 0.0
        for previous, current in itertools.pairwise(account_readings):
            is_new_week = is_later_week(previous, current) or current["used"] < previous["used"]
            rise += current["used"] if is_new_week else current["used"] - previous["used"]
        points[account] = rise
    return points


def is_later_week(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if previous["resets"] is None or current["resets"] is None:
        return False
    shift = dt.datetime.fromisoformat(current["resets"]) - dt.datetime.fromisoformat(previous["resets"])
    return shift > NEW_WEEK_SHIFT
