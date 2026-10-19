"""Measures how the context of an agent or a session grows. A call's context is its input, cached tokens too, which
holds everything that the calls before it read and wrote. So the context that the results of a call's tool calls added
is the next call's context, less the call's own context and its output. That also counts the messages that arrived
while the tool calls ran, such as a task notification. The tool calls of one call share what they added equally. A
compaction, which Claude Code marks with a line of its own, shrinks the context, and a step in which the context shrank
adds none. Only the event of a call's request in .loop/requests.jsonl gives its output tokens, because a transcript
gives them as of the reply's start. Without the event, the model's own output, such as a file that it writes, would
count as what the tool calls added, so a step whose call has no event stays out."""

import itertools
from pathlib import Path
from typing import NamedTuple

import agent_time
import transcripts

# The subtype of the line that Claude Code writes in a transcript where it compacted the context
COMPACTION = "compact_boundary"


class CallContext(NamedTuple):
    call: transcripts.Call
    # The kind of each of its tool calls, as scripts/loop/agent_time.py names them
    tool_kinds: list[str]
    follows_compaction: bool


class Growth(NamedTuple):
    # The largest context of a call
    peak: int
    # The tool calls whose results a later call read, in the steps whose call has an event
    tool_calls: int
    # The context that the results of each kind of tool call added
    added: dict[str, float]
    compactions: int
    # The steps with tool calls whose call has no event, which stay out of tool_calls and added
    steps_without_event: int


def read_calls(file: Path) -> list[CallContext]:
    """The calls in a transcript, in the order that they started, with the kinds of their tool calls, and whether a
    compaction came before each and after the call before it."""
    entries = transcripts.timed_entries(file)
    calls, _ = transcripts.entry_calls([entry for entry in entries if transcripts.has_message(entry)])
    tool_kinds: dict[str, list[str]] = {}
    after_compaction: set[str] = set()
    has_compacted = False
    for entry in entries:
        if entry.get("subtype") == COMPACTION:
            has_compacted = True
            continue
        if not transcripts.has_message(entry) or not transcripts.is_call(entry):
            continue
        call = entry["message"]["id"]
        if call not in tool_kinds and has_compacted:
            after_compaction.add(call)
            has_compacted = False
        uses = transcripts.blocks(entry, "tool_use")
        tool_kinds.setdefault(call, []).extend(agent_time.tool_kind(use) for use in uses)
    return [CallContext(call, tool_kinds[call.id], call.id in after_compaction) for call in calls]


def growth(calls: list[CallContext], counted: set[str], event_tokens: dict[str, int]) -> Growth:
    """How the context grew in the calls whose ids are counted, with the output tokens of each request's event in
    event_tokens, by request id."""
    peak = max((context.call.input_tokens for context in calls if context.call.id in counted), default=0)
    compactions = sum(context.follows_compaction for context in calls if context.call.id in counted)
    added: dict[str, float] = {}
    tool_calls = 0
    steps_without_event = 0
    for previous, following in itertools.pairwise(calls):
        kinds = previous.tool_kinds
        if following.call.id not in counted or not kinds:
            continue
        output_tokens = event_tokens.get(previous.call.request_id or "")
        if output_tokens is None:
            steps_without_event += 1
            continue
        tool_calls += len(kinds)
        step = following.call.input_tokens - previous.call.input_tokens - output_tokens
        for kind in kinds:
            added[kind] = added.get(kind, 0) + max(0, step) / len(kinds)
    return Growth(peak, tool_calls, added, compactions, steps_without_event)
