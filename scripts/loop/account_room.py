"""Decides which agent types an account can start, and the account to start each type on, as scripts/loop/meter --help
describes, from the account's meters, its usage per agent-minute, the agents that run on it and the minutes of the
agents' earlier runs."""

import datetime as dt
import math
from typing import NamedTuple

from usage import Meter

SESSION = "session"
# A type's run, and the time that a running agent will still run, cover this share of the earlier runs of its type.
LONG_RUN_SHARE = 0.9
# The usage at which each meter's room ends. A weekly meter keeps a point, so that the account never waits days for
# its reset.
SESSION_LIMIT_PERCENT = 100.0
WEEK_LIMIT_PERCENT = 99.0
# How far away the reset of a meter whose reset time is unknown can be
SESSION_WINDOW = dt.timedelta(hours=5)
WEEK_WINDOW = dt.timedelta(days=7)
# The orchestrator works until the reset of this meter.
WEEK = "week"
# /usage rounds a reset time differently from one reading to the next, such as 4:30am and then 4:29am.
SAME_WINDOW_DRIFT = dt.timedelta(minutes=10)
# A type's run until agents.jsonl has an earlier run that ended
FIRST_RUN_MINUTES = 30.0
# The usage per agent-minute, in percent of the meter, until the meter measures one on the account: about the most
# that the loop's Claude accounts used over 30 minutes in October 2026, so that the first advice starts too little
# rather than too much.
FIRST_SESSION_PER_AGENT_MINUTE = 0.12
FIRST_WEEK_PER_AGENT_MINUTE = 0.02


class Running(NamedTuple):
    """An agent that runs on the account: its type, and the minutes that it has run."""

    type: str
    minutes: float


class Room(NamedTuple):
    """What an account's advice depends on."""

    meters: dict[str, Meter]
    # The usage per agent-minute of each meter, in percent
    per_agent_minute: dict[str, float]
    running: list[Running]
    # The share of its time that the orchestrator worked so far, on its own account, and 0 on any other
    orchestrator_share: float


class Candidate(NamedTuple):
    """An account that can be named for an agent."""

    name: str
    fitting: list[str]
    meters: dict[str, Meter]
    is_orchestrators: bool


def first_per_agent_minute(meter_name: str) -> float:
    return FIRST_SESSION_PER_AGENT_MINUTE if meter_name == SESSION else FIRST_WEEK_PER_AGENT_MINUTE


def measured_per_agent_minute(
    meters: dict[str, Meter], earlier: dict[str, Meter], agent_minutes: float
) -> dict[str, float]:
    """The usage per agent-minute of each meter that rose since an earlier reading of the same window, while the
    account's agents worked the minutes. A meter that didn't rise measures nothing, because /usage gives whole percents,
    so a meter can stay put while the agents use it."""
    if agent_minutes <= 0:
        return {}
    measured = {}
    for name, meter in meters.items():
        before = earlier.get(name)
        if before is None or not is_same_window(before, meter):
            continue
        rise = meter["used"] - before["used"]
        if rise > 0:
            measured[name] = rise / agent_minutes
    return measured


def is_same_window(before: Meter, after: Meter) -> bool:
    """Whether two readings of a meter are of one window."""
    if before["resets"] is None or after["resets"] is None:
        return False
    drift = dt.datetime.fromisoformat(after["resets"]) - dt.datetime.fromisoformat(before["resets"])
    return abs(drift) <= SAME_WINDOW_DRIFT


def working_share(periods: list[tuple[dt.datetime, dt.datetime]], reading_time: dt.datetime) -> float:
    """The share of the time from the start of the first period to the reading that the periods take."""
    if not periods:
        return 0.0
    worked = sum((end - start).total_seconds() for start, end in periods)
    return min(1.0, worked / max(1.0, (reading_time - periods[0][0]).total_seconds()))


def long_run(minutes: list[float]) -> float:
    """The LONG_RUN_SHARE percentile of the minutes, by nearest rank."""
    ranked = sorted(minutes)
    return ranked[math.ceil(LONG_RUN_SHARE * len(ranked)) - 1]


def earlier_runs(agent_type: str, runs: dict[str, list[float]]) -> list[float]:
    """The minutes of the earlier runs of a type. A type without one takes the runs of all types."""
    every_run = [minutes for type_runs in runs.values() for minutes in type_runs]
    return runs.get(agent_type) or every_run or [FIRST_RUN_MINUTES]


def run_minutes(agent_type: str, runs: dict[str, list[float]]) -> float:
    return long_run(earlier_runs(agent_type, runs))


def minutes_left(agent: Running, runs: dict[str, list[float]]) -> float:
    """How much longer a running agent will likely run, from how much longer the earlier runs of its type that ran as
    long as it has went on. An agent that outran them all needs nothing more than the meter shows."""
    longer = [minutes - agent.minutes for minutes in earlier_runs(agent.type, runs) if minutes >= agent.minutes]
    return long_run(longer) if longer else 0.0


def minutes_to_reset(name: str, meter: Meter, reading_time: dt.datetime) -> float:
    if meter["resets"] is None:
        window = SESSION_WINDOW if name == SESSION else WEEK_WINDOW
        return window.total_seconds() / 60
    return max(0.0, (dt.datetime.fromisoformat(meter["resets"]) - reading_time).total_seconds() / 60)


def usage_of(minutes: float, name: str, room: Room, reading_time: dt.datetime) -> float:
    """The usage of an agent's minutes on a meter. Only the minutes before the meter's reset count, because the room
    comes back then."""
    counted = min(minutes, minutes_to_reset(name, room.meters[name], reading_time))
    return room.per_agent_minute[name] * counted


def free_room(name: str, room: Room, runs: dict[str, list[float]], reading_time: dt.datetime) -> float:
    """The room on a meter, less what the running agents still need."""
    meter = room.meters[name]
    limit = SESSION_LIMIT_PERCENT if name == SESSION else WEEK_LIMIT_PERCENT
    needs = sum(usage_of(minutes_left(agent, runs), name, room, reading_time) for agent in room.running)
    week = room.meters.get(WEEK)
    until_week_reset = minutes_to_reset(WEEK, week, reading_time) if week else WEEK_WINDOW.total_seconds() / 60
    needs += room.orchestrator_share * usage_of(until_week_reset, name, room, reading_time)
    return limit - meter["used"] - needs


def fits(agent_type: str, name: str, room: Room, runs: dict[str, list[float]], reading_time: dt.datetime) -> bool:
    """Whether a type's run fits on a meter. A meter without room fits nothing, even at a usage of 0."""
    free = free_room(name, room, runs, reading_time)
    return free > 0 and free >= usage_of(run_minutes(agent_type, runs), name, room, reading_time)


def fitting_types(room: Room, types: list[str], runs: dict[str, list[float]], reading_time: dt.datetime) -> list[str]:
    """The types whose run fits on each of the account's meters, in the order of types."""
    return [
        agent_type
        for agent_type in types
        if all(fits(agent_type, name, room, runs, reading_time) for name in room.meters)
    ]


def full_meters(room: Room, types: list[str], runs: dict[str, list[float]], reading_time: dt.datetime) -> list[str]:
    """The meters on which no type fits."""
    return [
        name
        for name in room.meters
        if not any(fits(agent_type, name, room, runs, reading_time) for agent_type in types)
    ]


def minutes_until_change(
    room: Room, fitting: list[str], runs: dict[str, list[float]], reading_time: dt.datetime
) -> float:
    """The minutes until the longest type that fits may no longer fit, as the agents that run use the room, or
    infinity when nothing uses it."""
    agents = len(room.running) + room.orchestrator_share
    soonest = math.inf
    for name in room.meters:
        rate = room.per_agent_minute[name] * agents
        if rate <= 0:
            continue
        longest_run = max(usage_of(run_minutes(agent_type, runs), name, room, reading_time) for agent_type in fitting)
        soonest = min(soonest, (free_room(name, room, runs, reading_time) - longest_run) / rate)
    return max(0.0, soonest)


def account_for(agent_type: str, candidates: list[Candidate]) -> str | None:
    """The account to start an agent of the type on, among those where the type fits: the one with the lowest 5-hour
    meter, because a percentage is of the account's own limit, so a larger plan takes more agents. Then the one whose
    week resets first, because its unused week is lost first, and then one other than the orchestrator's."""
    fitting = [candidate for candidate in candidates if agent_type in candidate.fitting]
    if not fitting:
        return None
    return min(fitting, key=account_order).name


def account_order(candidate: Candidate) -> tuple[float, dt.datetime, bool]:
    session = candidate.meters.get(SESSION)
    week = candidate.meters.get(WEEK)
    week_reset = dt.datetime.max.replace(tzinfo=dt.UTC)
    if week and week["resets"]:
        week_reset = dt.datetime.fromisoformat(week["resets"])
    return (session["used"] if session else 0.0, week_reset, candidate.is_orchestrators)
