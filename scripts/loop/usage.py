"""Reads the usage meters that `claude -p /usage` prints, such as
"Current session: 8% used · resets Oct 18 at 4:30am (America/New_York)"."""

import datetime as dt
import re
from typing import TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Meter(TypedDict):
    used: float
    # The time of the meter's next reset in ISO format, or None when the output gives none or one that can't be read.
    resets: str | None


USAGE_LINE = re.compile(
    r"^Current (?P<kind>session|week)(?: \((?P<scope>[^)]*)\))?: (?P<used>\d+(?:\.\d+)?)% used"
    r"(?: · resets (?P<reset>.+))?$"
)
RESET_TIME = re.compile(
    r"^(?:(?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}) at )?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?(?P<half>am|pm)"
    r"(?: \((?P<zone>[^)]+)\))?$"
)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_usage(text: str, reading_time: dt.datetime) -> dict[str, Meter]:
    """The meters in the output, by name: "session", "week" for all models, and "week (<model>)" for one model."""
    meters: dict[str, Meter] = {}
    for line in text.splitlines():
        match = USAGE_LINE.match(line.strip())
        if not match:
            continue
        name = match["kind"]
        if match["scope"] and match["scope"] != "all models":
            name = f"{name} ({match['scope']})"
        reset = parse_reset(match["reset"], reading_time) if match["reset"] else None
        meters[name] = {"used": float(match["used"]), "resets": reset.isoformat() if reset else None}
    return meters


def parse_reset(text: str, reading_time: dt.datetime) -> dt.datetime | None:
    """The time that a reset text names, such as "4:30am (America/New_York)" or "Oct 24 at 5pm (UTC)", or None.
    A time without a date is less than a day away, so it's the next time the clock shows it. A time without a zone is
    in the zone of the reading time."""
    match = RESET_TIME.match(text.strip())
    if not match or (match["month"] and match["month"] not in MONTHS):
        return None
    try:
        zone = ZoneInfo(match["zone"]) if match["zone"] else reading_time.tzinfo
    except ZoneInfoNotFoundError, ValueError:
        return None
    local_time = reading_time.astimezone(zone)
    reset = local_time.replace(
        hour=int(match["hour"]) % 12 + (12 if match["half"] == "pm" else 0),
        minute=int(match["minute"] or 0),
        second=0,
        microsecond=0,
    )
    if not match["month"]:
        return reset if reset > local_time else reset + dt.timedelta(days=1)
    try:
        reset = reset.replace(month=MONTHS.index(match["month"]) + 1, day=int(match["day"]))
    except ValueError:
        return None
    # A date that has passed, such as Jan 2 read on Dec 30, is next year's.
    if reset < local_time - dt.timedelta(days=1):
        reset = reset.replace(year=reset.year + 1)
    return reset
