"""Reads the transcripts that Claude Code keeps of each session and agent, one JSON entry to a line."""

import datetime as dt
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

# The fields of a call's usage that count its input tokens, cached tokens too
INPUT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
OUTPUT_KEY = "output_tokens"
# gh pr new is another name for gh pr create, which prints the new pull request's URL. The URL ends in its number.
PR_CREATE = re.compile(r"\bgh pr (create|new)\b")
PULL_REQUEST_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/([1-9][0-9]*)")
# Claude Code gives each message that a person typed this origin, on a user line, or on an attachment line of the kind
# QUEUED_MESSAGE if the message arrived while a turn ran. A task notification, an agent's message, a scheduled prompt
# such as the loop's heartbeat, a hook's output and a compaction's summary have another origin or none.
PERSON_ORIGIN = "human"
QUEUED_MESSAGE = "queued_command"
# Claude Code writes a system line when Claude's safety classifier stops a response. When Claude Code then asks once
# for the rest of the response, the line's content holds these words. When the classifier refused the request, the
# line's subtype starts with these words, as in model_refusal_no_fallback. Claude Code 2.1.303 writes them so.
STOPPED_RESPONSE = "safeguards stopped the response"
REFUSED_REQUEST = "model_refusal"
# The text of the line that Claude Code writes when the user interrupts a session or an agent, which isn't a message
# for it
INTERRUPTION = "[Request interrupted by user"
# The end of a transcript that holds its last call and its last message. An orchestrator's transcript of 218 MB took
# 0.25 seconds and about 0.5 GB of memory to read whole.
TAIL_BYTES = 400_000


class Call(NamedTuple):
    id: str
    start: dt.datetime
    end: dt.datetime
    # Cached tokens too
    input_tokens: int
    # The output tokens as of the reply's start, which leave out most of them
    output_tokens: int
    tool_calls: int
    # The id of the request to the API, which Claude Code's events give too, or None if the transcript has none
    request_id: str | None = None


class Message(NamedTuple):
    at: dt.datetime
    text: str


def read_transcript(file: Path) -> tuple[list[Call], list[dt.datetime]]:
    """The calls in a transcript, and the times of the prompts and messages in it."""
    return entry_calls(transcript_entries(file))


def entry_calls(entries: Iterable[dict[str, Any]]) -> tuple[list[Call], list[dt.datetime]]:
    """The calls in a transcript's entries that hold a message, in the order that they started, and the times of the
    prompts and messages in them. A response that spans several lines repeats its message's id and usage on each line,
    so its tokens count once. Each line holds other blocks of the response, so its tool calls add up."""
    times: dict[str, list[dt.datetime]] = {}
    usage: dict[str, dict[str, int]] = {}
    request_ids: dict[str, str] = {}
    tool_calls: Counter[str] = Counter()
    prompts = []
    for entry in entries:
        if is_prompt(entry):
            prompts.append(dt.datetime.fromisoformat(entry["timestamp"]))
        if not is_call(entry):
            continue
        message = entry["message"]
        call = message["id"]
        times.setdefault(call, []).append(dt.datetime.fromisoformat(entry["timestamp"]))
        most = usage.setdefault(call, dict.fromkeys((*INPUT_KEYS, OUTPUT_KEY), 0))
        for key in most:
            most[key] = max(most[key], message.get("usage", {}).get(key) or 0)
        tool_calls[call] += len(blocks(entry, "tool_use"))
        if entry.get("requestId"):
            request_ids[call] = entry["requestId"]
    calls = [
        Call(
            call,
            min(moments),
            max(moments),
            sum(usage[call][key] for key in INPUT_KEYS),
            usage[call][OUTPUT_KEY],
            tool_calls[call],
            request_ids.get(call),
        )
        for call, moments in times.items()
    ]
    return calls, prompts


def transcript_entries(file: Path) -> list[dict[str, Any]]:
    """The entries of a transcript that hold a message and its time."""
    return [entry for entry in timed_entries(file) if has_message(entry)]


def has_message(entry: dict[str, Any]) -> bool:
    return isinstance(entry.get("message"), dict)


def timed_entries(file: Path) -> list[dict[str, Any]]:
    """The entries of a transcript that hold their time."""
    entries = []
    with file.open(errors="replace") as handle:
        for line in handle:
            entry = timed_entry(line)
            if entry is not None:
                entries.append(entry)
    return entries


def timed_entry(line: str) -> dict[str, Any] | None:
    """The entry on a transcript's line if it holds its time, or None."""
    try:
        entry = json.loads(line)
    except ValueError:
        # Claude Code may be writing the last line of a running session's transcript.
        return None
    if not entry.get("timestamp"):
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


def tail_lines(file: Path) -> list[str]:
    """The lines in the last TAIL_BYTES of a transcript. The first of them may be cut off."""
    with file.open("rb") as handle:
        handle.seek(max(0, file.stat().st_size - TAIL_BYTES))
        return handle.read().decode(errors="replace").splitlines()


def is_interrupted(file: Path) -> bool:
    """Whether the last message in an agent's transcript is the line that says that the user interrupted the agent.
    Claude Code runs no hook then, so the agent gets no end line. A message that the agent gets later comes after that
    line, and the agent runs again."""
    if not file.exists():
        return False
    for line in reversed(tail_lines(file)):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if has_message(entry):
            return entry.get("type") == "user" and is_interruption(entry)
    return False


def is_interruption(entry: dict[str, Any]) -> bool:
    """Whether a prompt is the line that Claude Code writes when the user interrupts a session or an agent."""
    content = entry["message"].get("content")
    texts = [content] if isinstance(content, str) else [block.get("text", "") for block in content]
    return any(str(text).startswith(INTERRUPTION) for text in texts)


def safety_stops(entries: list[dict[str, Any]]) -> list[dt.datetime]:
    """The times in a transcript's entries at which Claude's safety classifier stopped a response. A stop in the
    request that asks for the rest of a stopped response stops the same response, so it doesn't count again."""
    by_uuid = {entry["uuid"]: entry for entry in entries if entry.get("uuid")}
    return [
        dt.datetime.fromisoformat(entry["timestamp"])
        for entry in entries
        if is_safety_stop(entry) and not asks_for_the_rest(entry, by_uuid)
    ]


def is_safety_stop(entry: dict[str, Any]) -> bool:
    if is_stopped_response(entry):
        return True
    return entry.get("type") == "system" and str(entry.get("subtype")).startswith(REFUSED_REQUEST)


def is_stopped_response(entry: dict[str, Any]) -> bool:
    """Whether the entry is Claude Code's note that the classifier stopped a response, after which it asks for the rest
    of the response."""
    return entry.get("type") == "system" and STOPPED_RESPONSE in str(entry.get("content"))


def asks_for_the_rest(entry: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> bool:
    """Whether the entry is in the request that Claude Code sends after a stopped response to ask for the rest of it.
    Each entry names the entry before it in the conversation. That request answers the message that Claude Code writes
    right after its note that the classifier stopped the response. A refused request gets no such message."""
    answered = entry_before(entry, by_uuid)
    while answered is not None and answered.get("type") == "assistant":
        answered = entry_before(answered, by_uuid)
    if answered is None or answered.get("type") != "user":
        return False
    return is_stopped_response(entry_before(answered, by_uuid) or {})


def entry_before(entry: dict[str, Any], by_uuid: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The entry that the entry names as the one before it in the conversation, or None if it names none."""
    before = entry.get("parentUuid")
    return by_uuid.get(before) if isinstance(before, str) else None


def claude_code_versions(entries: list[dict[str, Any]], since: dt.datetime) -> set[str]:
    """The versions of Claude Code that wrote a transcript's entries from the time on."""
    return {
        str(entry["version"])
        for entry in entries
        if entry.get("version") and dt.datetime.fromisoformat(entry["timestamp"]) >= since
    }


def person_messages(file: Path) -> list[Message]:
    """The messages that a person typed in a session, in the transcript's order."""
    messages = []
    for entry in timed_entries(file):
        text = person_text(entry)
        if text is not None:
            messages.append(Message(dt.datetime.fromisoformat(entry["timestamp"]), text))
    return messages


def person_text(entry: dict[str, Any]) -> str | None:
    """The text of the message that a person typed on a transcript's line, or None if the line holds no such message."""
    if entry.get("type") == "user" and isinstance(entry.get("message"), dict):
        origin, content = entry.get("origin"), entry["message"].get("content")
    elif entry.get("type") == "attachment" and (entry.get("attachment") or {}).get("type") == QUEUED_MESSAGE:
        origin, content = entry["attachment"].get("origin"), entry["attachment"].get("prompt")
    else:
        return None
    if (origin or {}).get("kind") != PERSON_ORIGIN:
        return None
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content if block.get("type") == "text")


def blocks(entry: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """The blocks of a kind in an entry's message, such as tool_use for the tool calls in a call, or tool_result for
    the tools' results that a prompt holds."""
    content = entry["message"].get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if block.get("type") == kind]


def working_minutes(calls: list[Call], prompts: list[dt.datetime]) -> float:
    return sum((end - start).total_seconds() for start, end in working_periods(calls, prompts)) / 60


def working_periods(calls: list[Call], prompts: list[dt.datetime]) -> list[tuple[dt.datetime, dt.datetime]]:
    """The periods from each prompt or message that the session or agent received to its last call before the next
    one, in order. An agent that gets a message after it finished adds to the same transcript, so the time it waited
    doesn't count."""
    if not calls:
        return []
    first_start = min(call.start for call in calls)
    ends: dict[dt.datetime, dt.datetime] = {}
    for call in calls:
        start = max((prompt for prompt in prompts if prompt <= call.start), default=first_start)
        ends[start] = max(ends.get(start, call.end), call.end)
    return sorted(ends.items())


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
