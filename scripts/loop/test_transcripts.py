import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import transcripts

# The time that the lines of a test's transcript count their minutes from
START = dt.datetime(2026, 10, 11, 14, 0, tzinfo=dt.UTC)


def at(minutes: float) -> str:
    """The time that many minutes after START."""
    return (START + dt.timedelta(minutes=minutes)).isoformat()


def prompt_line(minute: float, content: Any = "Build the issue.") -> dict[str, Any]:
    """A transcript's line for a prompt or a message that a session or an agent received."""
    return {"type": "user", "timestamp": at(minute), "message": {"role": "user", "content": content}}


def call_line(call: str, minute: float, tokens: int = 0, tool_calls: int = 0) -> dict[str, Any]:
    """A transcript's line for a call, with the tokens as its input."""
    content: list[dict[str, Any]] = [{"type": "text"}] + [{"type": "tool_use", "id": "", "input": {}}] * tool_calls
    usage = {"input_tokens": tokens, "output_tokens": None}
    return {"type": "assistant", "timestamp": at(minute), "message": {"id": call, "usage": usage, "content": content}}


def command_line(call: str, minute: float, tool_use: str, command: str) -> dict[str, Any]:
    """A transcript's line for a call that runs a command with the Bash tool."""
    use = {"type": "tool_use", "id": tool_use, "name": "Bash", "input": {"command": command}}
    return {"type": "assistant", "timestamp": at(minute), "message": {"id": call, "content": [use]}}


def result_line(minute: float, tool_use: str, output: Any) -> dict[str, Any]:
    """A transcript's line for the result of a tool call."""
    result = {"type": "tool_result", "tool_use_id": tool_use, "content": output}
    return {"type": "user", "timestamp": at(minute), "message": {"role": "user", "content": [result]}}


def write_transcript(file: Path, lines: list[Any]) -> Path:
    """Writes the lines, each an entry or the text of a line, as a transcript."""
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines))
    return file


class TranscriptTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "agent.jsonl"

    def write(self, *lines: Any) -> Path:
        return write_transcript(self.file, list(lines))


class WorkingMinutesTest(TranscriptTest):
    def minutes(self) -> float:
        return transcripts.working_minutes(*transcripts.read_transcript(self.file))

    def test_counts_the_minutes_from_each_message_to_the_last_call_before_the_next(self) -> None:
        self.write(
            prompt_line(0),
            call_line("a", 1),
            {"type": "user", "timestamp": at(2), "message": {"content": [{"type": "tool_result"}]}},
            call_line("b", 10),
            prompt_line(70, [{"type": "text", "text": "Fix the review's findings."}]),
            call_line("c", 71),
            call_line("d", 75),
        )
        self.assertEqual(self.minutes(), 15.0)

    def test_counts_from_the_first_call_without_a_prompt(self) -> None:
        self.write(call_line("a", 3), call_line("b", 5))
        self.assertEqual(self.minutes(), 2.0)

    def test_is_0_without_a_call(self) -> None:
        self.write(prompt_line(0))
        self.assertEqual(self.minutes(), 0.0)


class ToolCallTest(TranscriptTest):
    def test_adds_up_the_tool_calls_on_each_line_of_a_response(self) -> None:
        self.write(call_line("a", 1, tool_calls=1), call_line("a", 2, tool_calls=2), call_line("b", 3, tool_calls=1))
        calls, _ = transcripts.read_transcript(self.file)
        self.assertEqual({call.id: call.tool_calls for call in calls}, {"a": 3, "b": 1})


class OpenedPullRequestTest(TranscriptTest):
    def test_is_the_number_in_the_url_that_gh_pr_create_printed(self) -> None:
        self.write(
            command_line("a", 1, "toolu_1", "cd app && gh pr create --draft --fill"),
            result_line(2, "toolu_1", "Warning: 1 uncommitted change\nhttps://github.com/owner/repo/pull/57\n"),
        )
        self.assertEqual(transcripts.opened_pull_request(self.file), 57)

    def test_reads_a_result_in_blocks_and_the_command_gh_pr_new(self) -> None:
        self.write(
            command_line("a", 1, "toolu_1", "gh pr new --fill"),
            result_line(2, "toolu_1", [{"type": "text", "text": "https://github.com/owner/repo/pull/8"}]),
        )
        self.assertEqual(transcripts.opened_pull_request(self.file), 8)

    def test_is_the_last_pull_request_when_the_agent_opened_two(self) -> None:
        self.write(
            command_line("a", 1, "toolu_1", "gh pr create --fill"),
            result_line(2, "toolu_1", "https://github.com/owner/repo/pull/8"),
            command_line("b", 3, "toolu_2", "gh pr create --fill"),
            result_line(4, "toolu_2", "https://github.com/owner/repo/pull/9"),
        )
        self.assertEqual(transcripts.opened_pull_request(self.file), 9)

    def test_is_none_when_only_other_commands_print_a_pull_request(self) -> None:
        self.write(
            command_line("a", 1, "toolu_1", "gh pr view 8 --json url"),
            result_line(2, "toolu_1", "https://github.com/owner/repo/pull/8"),
            command_line("b", 3, "toolu_2", "gh pr create --fill"),
            result_line(4, "toolu_2", "pull request create failed: GraphQL: No commits between main and work"),
        )
        self.assertIsNone(transcripts.opened_pull_request(self.file))
