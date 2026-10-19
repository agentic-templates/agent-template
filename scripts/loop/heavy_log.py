"""Reads the log of the heavy commands that scripts/loop/run-heavy runs, which scripts/loop/report reads too."""

import functools
import json
import sys
from pathlib import Path
from typing import Any

NAME = "heavy.jsonl"


def read(log: Path) -> list[dict[str, Any]]:
    """The lines of the log, oldest first, and none before anything writes it. A process that is killed while it writes
    can leave a line unfinished, so a line that isn't JSON doesn't count, and gives a warning."""
    if not log.exists():
        return []
    entries = []
    for number, line in enumerate(log.read_text().splitlines(), start=1):
        try:
            entries.append(json.loads(line))
        except ValueError:
            warn(f"line {number} of {log} isn't JSON, so it doesn't count. Delete the line to end this warning.")
    return entries


@functools.cache
def warn(message: str) -> None:
    """Prints a warning once in this process, however often its cause comes back."""
    print(f"warning: {message}", file=sys.stderr)
