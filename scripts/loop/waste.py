"""Finds clear signs of waste in the loop's logs, which scripts/loop/meter names on its machine line, so that the
orchestrator starts a focused audit without waiting for the next retrospective. The answer to issue #299 gives the
evidence for each threshold."""

import datetime as dt
import itertools
import math
import shlex
import statistics
from typing import Any, NamedTuple

from agent_log import AgentRun

BUILDER_TYPES = frozenset(("builder", "builder-deep", "builder-small"))
# A build is long when it took more than this many times the median minutes of the earlier builds of its type.
LONG_BUILD_TIMES = 4
# The earlier builds of a type that ended, which its median needs
EARLIER_BUILDS = 5
# While an account isn't parked, scripts/loop/meter --wake reads at least every 50 minutes, and ends when a reading
# has a finding that the meter hadn't printed. So the meter prints each finding of this time at least once.
RECENT = dt.timedelta(hours=1)
WAIT_MINUTES_PER_BUILDER_HOUR = 10.0
CHECK = "scripts/check"
# How much longer the median passing run of scripts/check has to take since the last retrospective. A test alone
# finds any growth, however small, among the hundreds of runs between two retrospectives.
CHECK_GROWTH = 1.2
CHECK_P_VALUE = 0.01


class Finding(NamedTuple):
    # What the finding is about, which stays the same from one reading to the next while the numbers in text change
    subject: str
    text: str


def long_builds(runs: list[AgentRun], now: dt.datetime) -> list[Finding]:
    """A finding for each build that ended in the last hour after more than 4 times the median minutes of the earlier
    builds of its type that ended, once there are 5 of them. A test build of a retrospective doesn't count."""
    builds = [
        (run.start, run.end) for run in runs if run.end and run.start["type"] in BUILDER_TYPES and not run.start["test"]
    ]
    builds.sort(key=lambda build: ended(build[1]))
    findings = []
    for index, (start, end) in enumerate(builds):
        if now - ended(end) > RECENT:
            continue
        earlier = [
            earlier_end["minutes"]
            for earlier_start, earlier_end in builds[:index]
            if earlier_start["type"] == start["type"]
        ]
        if len(earlier) < EARLIER_BUILDS:
            continue
        median = statistics.median(earlier)
        if end["minutes"] > LONG_BUILD_TIMES * median:
            text = (
                f"{build_name(start)} took {end['minutes']:.0f} min, more than {LONG_BUILD_TIMES} times the median of "
                f"{median:.0f} min of its type's earlier builds"
            )
            findings.append(Finding(f"build {start['id']}", text))
    return findings


def ended(end: dict[str, Any]) -> dt.datetime:
    return dt.datetime.fromisoformat(end["at"])


def build_name(start: dict[str, Any]) -> str:
    issue = start.get("issue")
    return f"{start['type']} #{issue}" if issue else start["type"]


def long_waits(heavy_entries: list[dict[str, Any]], builder_minutes: float, now: dt.datetime) -> Finding | None:
    """A finding when the waits of all heavy commands in scripts/loop/run-heavy's line that ended came to more than 10
    minutes per hour of builder work over the last hour. A wait ends on a start line or a stop line of
    .loop/heavy.jsonl, which doesn't say which agent waited."""
    if builder_minutes <= 0:
        return None
    since = now - RECENT
    waited_seconds = 0.0
    for entry in heavy_entries:
        if entry["event"] not in ("start", "stop"):
            continue
        wait_end = dt.datetime.fromisoformat(entry["at"])
        wait_start = max(wait_end - dt.timedelta(seconds=entry["waited_seconds"]), since)
        waited_seconds += max(0.0, (min(wait_end, now) - wait_start).total_seconds())
    per_hour = waited_seconds / 60 / (builder_minutes / 60)
    if per_hour <= WAIT_MINUTES_PER_BUILDER_HOUR:
        return None
    text = (
        f"heavy commands waited {per_hour:.1f} min in run-heavy's line per hour of builder work in the last hour, more "
        f"than {WAIT_MINUTES_PER_BUILDER_HOUR:g}"
    )
    return Finding("waits", text)


def slower_check(heavy_entries: list[dict[str, Any]], last_retrospective: dt.datetime | None) -> Finding | None:
    """A finding when the median time of scripts/check's passing runs since the last retrospective is at least 20%
    above its median before it, and a one-sided Mann-Whitney test gives p < 0.01."""
    if last_retrospective is None:
        return None
    before: list[float] = []
    since: list[float] = []
    for entry in heavy_entries:
        if entry["event"] != "end" or entry["exit_code"] != 0 or shlex.split(entry["command"])[0] != CHECK:
            continue
        is_since = dt.datetime.fromisoformat(entry["at"]) >= last_retrospective
        (since if is_since else before).append(entry["seconds"])
    if not before or not since:
        return None
    median_before, median_since = statistics.median(before), statistics.median(since)
    if median_since < CHECK_GROWTH * median_before:
        return None
    p_value = p_of_larger(since, before)
    if p_value >= CHECK_P_VALUE:
        return None
    text = (
        f"{CHECK}'s passing runs took a median of {median_since:.0f} s since the last retrospective, against "
        f"{median_before:.0f} s before it (p = {p_value:.1g})"
    )
    return Finding("check", text)


def p_of_larger(larger: list[float], smaller: list[float]) -> float:
    """The p-value of a one-sided Mann-Whitney U test that the values in larger tend to be larger than those in
    smaller. It takes the normal approximation, with the corrections for ties and for continuity, which is close for
    the hundreds of runs of scripts/check between two retrospectives."""
    ranks: dict[float, float] = {}
    ties = 0
    position = 0
    for value, group in itertools.groupby(sorted([*larger, *smaller])):
        count = len(list(group))
        ranks[value] = position + (count + 1) / 2
        ties += count**3 - count
        position += count
    count_larger, count_smaller, count_all = len(larger), len(smaller), position
    u = sum(ranks[value] for value in larger) - count_larger * (count_larger + 1) / 2
    variance = count_larger * count_smaller / 12 * ((count_all + 1) - ties / (count_all * (count_all - 1)))
    if variance <= 0:
        return 1.0
    z = (u - count_larger * count_smaller / 2 - 0.5) / math.sqrt(variance)
    return 0.5 * math.erfc(z / math.sqrt(2))
