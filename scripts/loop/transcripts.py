"""Reads the transcripts that Claude Code keeps of each session and agent, one JSON entry to a line."""

import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
# gh pr new is another name for gh pr create, which prints the new pull request's URL. The URL ends in its number.
PR_CREATE = re.compile(r"\bgh pr (create|new)\b")
PULL_REQUEST_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/([1-9][0-9]*)")


class Call(NamedTuple):
    id: str
    start: dt.datetime
    end: dt.datetime
    tokens: int
    tool_calls: int


def read_transcript(file: Path) -> tuple[list[Call], list[dt.datetime]]:
    """The calls in a transcript, and the times of the prompts and messages in it. A response that spans several lines
    repeats its message's id and usage on each line, so its tokens count once. Each line holds other blocks of the
    response, so its tool calls add up."""
    times: dict[str, list[dt.datetime]] = {}
    usage: dict[str, dict[str, int]] = {}
    tool_calls: Counter[str] = Counter()
    prompts = []
    for entry in transcript_entries(file):
        if is_prompt(entry):
            prompts.append(dt.datetime.fromisoformat(entry["timestamp"]))
        if not is_call(entry):
            continue
        message = entry["message"]
        call = message["id"]
        times.setdefault(call, []).append(dt.datetime.fromisoformat(entry["timestamp"]))
        most = usage.setdefault(call, dict.fromkeys(USAGE_KEYS, 0))
        for key in USAGE_KEYS:
            most[key] = max(most[key], message.get("usage", {}).get(key) or 0)
        tool_calls[call] += len(blocks(entry, "tool_use"))
    calls = [
        Call(call, min(moments), max(moments), sum(usage[call].values()), tool_calls[call])
        for call, moments in times.items()
    ]
    return calls, prompts


def transcript_entries(file: Path) -> list[dict[str, Any]]:
    """The entries of a transcript that hold a message and its time."""
    entries = []
    with file.open(errors="replace") as handle:
        for line in handle:
            entry = transcript_entry(line)
            if entry is not None:
                entries.append(entry)
    return entries


def transcript_entry(line: str) -> dict[str, Any] | None:
    """The entry on a transcript's line if it holds a message and its time, or None."""
    try:
        entry = json.loads(line)
    except ValueError:
        # Claude Code may be writing the last line of a running session's transcript.
        return None
    if not isinstance(entry.get("message"), dict) or not entry.get("timestamp"):
        return None
    return dict(entry)


def is_call(entry: dict[str, Any]) -> bool:
    return entry.get("type") == "assistant" and bool(entry["message"].get("id"))


def is_prompt(entry: dict[str, Any]) -> bool:
    """Whether the entry is a prompt or a message that the session or agent received, rather than a tool's result."""
    if entry.get("type") != "user":
        return False
    content = entry["message"].get("content")
    return not isinstance(content, list) or any(block.get("type") != "tool_result" for block in content)


def blocks(entry: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """The blocks of a kind in an entry's message, such as tool_use for the tool calls in a call, or tool_result for
    the tools' results that a prompt holds."""
    content = entry["message"].get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if block.get("type") == kind]


def working_minutes(calls: list[Call], prompts: list[dt.datetime]) -> float:
    """The minutes from each prompt or message that the session or agent received to its last call before the next
    one. An agent that gets a message after it finished adds to the same transcript, so the time it waited doesn't
    count."""
    if not calls:
        return 0.0
    first_start = min(call.start for call in calls)
    ends: dict[dt.datetime, dt.datetime] = {}
    for call in calls:
        start = max((prompt for prompt in prompts if prompt <= call.start), default=first_start)
        ends[start] = max(ends.get(start, call.end), call.end)
    return sum((end - start).total_seconds() for start, end in ends.items()) / 60


def opened_pull_request(file: Path) -> int | None:
    """The number of the last pull request that a gh pr create command in the transcript opened, or None."""
    creates: set[str] = set()
    opened = None
    for entry in transcript_entries(file):
        creates.update(
            use["id"] for use in blocks(entry, "tool_use") if PR_CREATE.search(str(use["input"].get("command")))
        )
        for result in blocks(entry, "tool_result"):
            found = PULL_REQUEST_URL.findall(str(result.get("content")))
            if result.get("tool_use_id") in creates and found:
                opened = int(found[-1])
    return opened
