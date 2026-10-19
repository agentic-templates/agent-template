"""Writes and reads the lines of agents.jsonl in the loop folder: one when an agent starts, and one each time it ends.
scripts/loop/agent-log writes them for the agents of a Claude Code session, and scripts/loop/run-builder for its runs.
Nothing trims the file, because Claude Code deletes the transcripts that hold some of the numbers after 30 days.

Each line is a JSON object. A start line holds:

  at              the time
  event           "start"
  id              the id of the Agent call that started the agent, or the name of the run's folder in .loop/runs
  type            the agent's type, such as builder, or codex for a run of Codex
  model, effort   the model and effort that the Agent call gave, or else those in the type's agent file, or null
  agent_file      git's hash of the type's agent file, .claude/agents/<type>.md, or null if the type has none
  issue           the number at the start of the prompt's first line, of an issue or a pull request, or null
  label           the issue's type label, such as feature, or null
  kind            the kind after the number, as scripts/take-next-issue prints it, or null
  test            true for a test build of a retrospective
  pid             the id of the Claude Code process whose session started the agent, which writes the agent's end
                  line, or null for a run of scripts/loop/run-builder. A start line written before start lines held
                  the id has none.

An end line holds at, "end" as its event, the start line's id, and:

  model, effort   for an agent of a session, the model and effort in the .meta.json file next to its transcript, or
                  null. They count when the start line has none, as for a type without an agent file.
  minutes         the agent's minutes, from each prompt or message that it received to its last call before the next
                  one, or a Codex run's minutes
  periods         the periods that make up those minutes, each a list of its start and end time
  tool_calls      the agent's tool calls
  input_tokens    the input tokens that the agent used, cached tokens too
  output_tokens   the output tokens that the agent used, reasoning tokens included, because Claude and Codex both
                  count them among the output tokens. For an agent of a session, the count leaves out most of
                  them, because its transcript gives each reply's output tokens as of the reply's start. A run of
                  scripts/loop/run-builder counts the output tokens of the agents that it starts too.
  requests        for an agent of a session, the output tokens of each of its requests to the API, as its transcript
                  gives them, by request id, and null for a run. scripts/loop/report takes each request's exact count
                  from requests.jsonl, which scripts/loop/request_events.py describes.
  pull_request    the number of the pull request that the agent opened, or null
  worktree_mb     the size of the agent's worktree in megabytes, or null if it had none
  cpu_seconds     a run's CPU time, which only scripts/loop/run-builder gives
  peak_memory_mb  the most memory that a process of the run used, which only scripts/loop/run-builder gives

An agent that gets a message after it ended ends again, so its last end line counts.
"""

import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

import paths
import transcripts

# The checkout that holds this file. Its agent files are those that the agents run with.
CHECKOUT = Path(__file__).resolve().parents[2]
AGENTS_FOLDER = CHECKOUT / ".claude" / "agents"
# The kinds that scripts/take-next-issue prints.
KIND = re.compile(r"research|parent|other|left behind [1-9][0-9]*")
# The line that scripts/take-next-issue printed, such as 12 other, or a pull request's number alone.
SUBJECT = re.compile(rf"([1-9][0-9]*)(?: ({KIND.pattern}))?")
BYTES_IN_A_MEGABYTE = 1_000_000
# The environment variable that scripts/loop/run-builder sets for the builders that it runs
RUN_VARIABLE = "LOOP_RUN"


class AgentSettings(NamedTuple):
    model: str | None
    effort: str | None
    file_hash: str | None
    # The text after the front matter
    instructions: str = ""
    # The tools and disallowed tools, separated by commas, and the limit on turns
    tools: str | None = None
    disallowed_tools: str | None = None
    max_turns: str | None = None


class AgentRun(NamedTuple):
    """An agent in agents.jsonl: its start line, and its last end line, or None if it has none."""

    start: dict[str, Any]
    end: dict[str, Any] | None


Period = tuple[dt.datetime, dt.datetime]


class Numbers(NamedTuple):
    minutes: float
    # The start and end times of each period, in ISO format
    periods: list[tuple[str, str]]
    tool_calls: int
    input_tokens: int
    output_tokens: int
    requests: dict[str, int] | None
    pull_request: int | None


def agent_settings(agent_type: str) -> AgentSettings:
    """The settings in the type's agent file, and git's hash of the file. A type without a file, such as Claude Code's
    Explore, has none of them."""
    file = AGENTS_FOLDER / f"{agent_type}.md"
    if not file.is_file():
        return AgentSettings(None, None, None)
    fields, instructions = split_agent_file(file.read_text())
    file_hash = subprocess.run(
        ["git", "hash-object", str(file)], cwd=file.parent, capture_output=True, text=True, check=True
    ).stdout.strip()
    return AgentSettings(
        model=fields.get("model"),
        effort=fields.get("effort"),
        file_hash=file_hash,
        instructions=instructions,
        tools=fields.get("tools"),
        disallowed_tools=fields.get("disallowedTools"),
        max_turns=fields.get("maxTurns"),
    )


def split_agent_file(text: str) -> tuple[dict[str, str], str]:
    """The fields of the front matter at the start of an agent file, the lines of name: value between two lines of
    ---, and the text after it. A field can also list its values on the lines after it, each after a dash, as tools
    can, and then its value is the list, separated by commas."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}, text.strip()
    fields: dict[str, str] = {}
    name = ""
    for index, line in enumerate(lines[1:], start=2):
        if line == "---":
            return fields, "\n".join(lines[index:]).strip()
        if line.strip().startswith("- ") and name:
            listed = line.strip().removeprefix("- ").strip()
            fields[name] = f"{fields[name]}, {listed}" if fields[name] else listed
            continue
        name, _, value = line.partition(":")
        name = name.strip()
        fields[name] = value.strip()
    return fields, ""


def prompt_subject(prompt: str) -> tuple[int | None, str | None]:
    """The number and the kind that the first line of an agent's prompt gives, or None for each that it lacks."""
    found = SUBJECT.fullmatch(prompt.split("\n", 1)[0].strip())
    if not found:
        return None, None
    return int(found[1]), found[2]


def type_label(labels: list[str]) -> str | None:
    """The first of an issue's labels that is a type label, as scripts/issue-labels lists them, or None."""
    listed = subprocess.run(
        ["bash", "-c", 'source "$0" && printf "%s\\n" "${type_labels[@]}"', CHECKOUT / "scripts" / "issue-labels"],
        capture_output=True,
        text=True,
        check=True,
    )
    type_labels = listed.stdout.split()
    return next((label for label in labels if label in type_labels), None)


def transcript_numbers(transcript: Path) -> Numbers:
    calls, prompts = transcripts.read_transcript(transcript)
    return Numbers(
        minutes=round(transcripts.working_minutes(calls, prompts), 1),
        periods=period_texts(transcripts.working_periods(calls, prompts)),
        tool_calls=sum(call.tool_calls for call in calls),
        input_tokens=sum(call.input_tokens for call in calls),
        output_tokens=sum(call.output_tokens for call in calls),
        requests=request_tokens(calls),
        pull_request=transcripts.opened_pull_request(transcript),
    )


def request_tokens(calls: list[transcripts.Call]) -> dict[str, int]:
    """The output tokens of each call that has a request id, as the transcript gives them, by request id."""
    return {call.request_id: call.output_tokens for call in calls if call.request_id}


def transcript_requests(transcript: Path) -> dict[str, int]:
    return request_tokens(transcripts.read_transcript(transcript)[0])


def folder_megabytes(folder: Path) -> float | None:
    """The disk space that a folder takes, or None if it doesn't exist."""
    if not folder.is_dir():
        return None
    done = subprocess.run(["du", "-sk", str(folder)], capture_output=True, text=True, check=True)
    return megabytes(int(done.stdout.split()[0]) * 1024)


def megabytes(size: int) -> float:
    return round(size / BYTES_IN_A_MEGABYTE, 1)


def start_line(
    agent_id: str,
    agent_type: str,
    settings: AgentSettings,
    issue: int | None,
    label: str | None,
    kind: str | None,
    is_test: bool,
    pid: int | None = None,
) -> dict[str, Any]:
    return {
        "at": now(),
        "event": "start",
        "id": agent_id,
        "type": agent_type,
        "model": settings.model,
        "effort": settings.effort,
        "agent_file": settings.file_hash,
        "issue": issue,
        "label": label,
        "kind": kind,
        "test": is_test,
        "pid": pid,
    }


def end_line(
    agent_id: str,
    numbers: Numbers,
    worktree_mb: float | None,
    model: str | None = None,
    effort: str | None = None,
    run_usage: dict[str, float] | None = None,
) -> dict[str, Any]:
    """The end line, with a run's CPU time and peak memory when they're given."""
    return {
        "at": now(),
        "event": "end",
        "id": agent_id,
        "model": model,
        "effort": effort,
        **numbers._asdict(),
        "worktree_mb": worktree_mb,
        **(run_usage or {}),
    }


def append(line: dict[str, Any]) -> None:
    folder = paths.loop_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "agents.jsonl").open("a") as handle:
        handle.write(json.dumps(line) + "\n")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def period_texts(periods: list[Period]) -> list[tuple[str, str]]:
    return [(start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")) for start, end in periods]


def read_runs(log: Path) -> list[AgentRun]:
    """The agents in a log, in the order they started. A log that nothing has written yet holds none. An agent ends
    again each time that it gets a message after it ended, so its last end line counts."""
    if not log.exists():
        return []
    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, dict[str, Any]] = {}
    for line in log.read_text().splitlines():
        entry: dict[str, Any] = json.loads(line)
        (starts if entry["event"] == "start" else ends)[entry["id"]] = entry
    return [AgentRun(start, ends.get(agent_id)) for agent_id, start in starts.items()]


def agent_output_tokens(end: dict[str, Any], event_tokens: dict[str, int]) -> tuple[int, int]:
    """The output tokens, and how many requests have no event. A run's own count is exact. An agent of a session gets
    each request's count from its event, or else from its transcript. An end line written before agents.jsonl had
    requests gives none, so its own count counts, as for an agent with no events."""
    requests: dict[str, int] | None = end.get("requests")
    if requests is None:
        return end["output_tokens"], 0
    tokens = sum(event_tokens.get(request, transcript_count) for request, transcript_count in requests.items())
    return tokens, sum(request not in event_tokens for request in requests)


def started(run: AgentRun) -> dt.datetime:
    return dt.datetime.fromisoformat(run.start["at"])


def agent_transcripts(project_folder: Path) -> dict[str, Path]:
    """The transcripts of the agents of the sessions in a Claude Code project folder, by the id of the Agent call that
    started each. Claude Code writes the id in the .meta.json file next to the transcript when the agent starts."""
    found = {}
    for meta in project_folder.glob("*/subagents/*.meta.json"):
        agent_id = json.loads(meta.read_text()).get("toolUseId")
        if agent_id:
            found[agent_id] = meta.with_name(meta.name.removesuffix(".meta.json") + ".jsonl")
    return found


def working_periods(run: AgentRun, transcripts_by_id: dict[str, Path], runs_folder: Path) -> list[Period]:
    """When an agent worked: the periods on its last end line. An agent without an end line, such as one that still
    runs or one whose session ended first, worked until the last line of its transcript, or for a run of
    scripts/loop/run-builder, of its events in its run folder."""
    if run.end is not None:
        if "periods" not in run.end:
            # An end line from before end lines held periods gives only the minutes.
            return [(started(run), started(run) + dt.timedelta(minutes=run.end["minutes"]))]
        return [(dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)) for start, end in run.end["periods"]]
    transcript = transcripts_by_id.get(run.start["id"])
    if transcript is not None and transcript.exists():
        return transcripts.working_periods(*transcripts.read_transcript(transcript))
    events = runs_folder / run.start["id"] / "events.jsonl"
    if events.exists():
        return [(started(run), dt.datetime.fromtimestamp(events.stat().st_mtime, dt.UTC))]
    return []


def minutes_after(periods: list[Period], moment: dt.datetime | None) -> float:
    """The minutes of the periods after a moment, or all of them without one."""
    minutes = 0.0
    for start, end in periods:
        start = max(start, moment) if moment else start
        minutes += max(0.0, (end - start).total_seconds() / 60)
    return minutes
