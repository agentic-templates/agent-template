"""Reads the events that a builder's run of claude or codex printed: whether the run failed, and the numbers that its
end line in agents.jsonl takes from them."""

import json
from pathlib import Path
from typing import Any

import agent_log
import paths
from accounts import Account, claude_environment

# The fields of each model's tokens in the modelUsage of claude's result event
CLAUDE_INPUT_FIELDS = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
CLAUDE_OUTPUT_FIELD = "outputTokens"
# The items of a Codex run that are tool calls, as codex exec --json lists them
CODEX_TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list")


def run_numbers(
    tool: str, account: Account, run: agent_log.Period, run_folder: Path, worktree: Path, pull_request: int | None
) -> agent_log.Numbers:
    """The builder's numbers for the end line in agents.jsonl: a Codex builder's from its events and the run's period,
    and a Claude builder's from its transcript, as for an agent of a session, but with the tokens from its events. A
    Claude builder without a transcript, such as one that never started, made no calls. The pull request is the one
    that the script opened for a Codex builder."""
    events = read_events(run_folder / "events.jsonl")
    if tool == "codex":
        return codex_numbers(events, run, pull_request)
    input_tokens, output_tokens = claude_tokens(events)
    sessions = [event["session_id"] for event in events if "session_id" in event]
    project = paths.claude_project_folder(worktree, claude_environment(account))
    transcript = project / f"{sessions[0]}.jsonl" if sessions else None
    if transcript is None or not transcript.exists():
        return agent_log.Numbers(
            run_minutes(run), agent_log.period_texts([run]), 0, input_tokens, output_tokens, None, None
        )
    numbers = agent_log.transcript_numbers(transcript)
    return numbers._replace(input_tokens=input_tokens, output_tokens=output_tokens, requests=None)


def claude_tokens(events: list[dict[str, Any]]) -> tuple[int, int]:
    """The input tokens, cached ones too, and the output tokens of a Claude run, from its last result event. The
    modelUsage of a result counts the whole run until then, with the agents that the builder started, while its usage
    counts only the builder's own replies to the last prompt."""
    results = [event for event in events if event.get("type") == "result"]
    models = results[-1].get("modelUsage", {}).values() if results else []
    input_tokens = sum(model.get(field) or 0 for model in models for field in CLAUDE_INPUT_FIELDS)
    return input_tokens, sum(model.get(CLAUDE_OUTPUT_FIELD) or 0 for model in models)


def codex_numbers(events: list[dict[str, Any]], run: agent_log.Period, pull_request: int | None) -> agent_log.Numbers:
    tool_calls = sum(
        event.get("type") == "item.completed" and event.get("item", {}).get("type") in CODEX_TOOL_ITEMS
        for event in events
    )
    # Codex counts the cached input tokens among the input tokens, and the reasoning tokens among the output tokens.
    usages = [event.get("usage", {}) for event in events if event.get("type") == "turn.completed"]
    input_tokens = sum(usage.get("input_tokens", 0) for usage in usages)
    output_tokens = sum(usage.get("output_tokens", 0) for usage in usages)
    periods = agent_log.period_texts([run])
    return agent_log.Numbers(run_minutes(run), periods, tool_calls, input_tokens, output_tokens, None, pull_request)


def run_minutes(run: agent_log.Period) -> float:
    start, end = run
    return round((end - start).total_seconds() / 60, 1)


def read_events(file: Path) -> list[dict[str, Any]]:
    return list(map(parse_event, file.read_text().splitlines())) if file.exists() else []


def claude_problem(result: dict[str, Any], exit_code: int) -> str | None:
    """Why a Claude run with this result event failed, or None."""
    if result.get("subtype") != "success" or result.get("is_error"):
        errors = result.get("errors") or [result.get("result") or "no message"]
        return f"claude's result is {result.get('subtype')}: {', '.join(map(str, errors))}"
    if exit_code != 0:
        return f"claude ended with code {exit_code} after its result. stderr.log in the run folder says why."
    return None


def codex_problem(events: list[dict[str, Any]], exit_code: int) -> str | None:
    """Why a Codex run with these events failed, or None. Its last event about its turn must say that the turn
    completed."""
    turns = [event for event in events if event.get("type") in ("turn.started", "turn.completed", "turn.failed")]
    if not turns:
        return f"codex ended with code {exit_code} before its turn started. stderr.log in the run folder says why."
    last = turns[-1]
    if last["type"] == "turn.failed":
        return f"codex's turn failed: {last.get('error', {}).get('message', 'no message')}"
    if last["type"] == "turn.started":
        return f"codex ended with code {exit_code} before its turn completed. stderr.log in the run folder says why."
    if exit_code != 0:
        return f"codex ended with code {exit_code} after its turn. stderr.log in the run folder says why."
    return None


def parse_event(line: str) -> dict[str, Any]:
    """The event on a line of a builder's output, or an empty one when the line holds none."""
    try:
        event = json.loads(line)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}
