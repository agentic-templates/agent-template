import tempfile
import unittest
from pathlib import Path
from typing import Any

import context_growth
from context_growth import Growth
from test_transcripts import at, prompt_line, result_line, write_transcript


def bash(tool_use: str, command: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use, "name": "Bash", "input": {"command": command}}


def tool(tool_use: str, name: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use, "name": name, "input": {}}


def sized_call_line(
    call: str,
    minute: float,
    context: int,
    *uses: dict[str, Any],
    output_tokens: int = 0,
    request_id: str | None = None,
) -> dict[str, Any]:
    """A transcript's line for a call whose context is the tokens, split between new and cached input as Claude Code
    gives them, with the tool calls, and with the request id, or else req_<call>."""
    usage = {
        "input_tokens": 1,
        "cache_creation_input_tokens": 9,
        "cache_read_input_tokens": context - 10,
        "output_tokens": output_tokens,
    }
    content = [{"type": "text", "text": "Working."}, *uses]
    line = {"type": "assistant", "timestamp": at(minute), "message": {"id": call, "usage": usage, "content": content}}
    return line | {"requestId": request_id or f"req_{call}"}


def compaction_line(minute: float) -> dict[str, Any]:
    """The line that Claude Code writes where it compacted the context."""
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "timestamp": at(minute),
        "content": "Conversation compacted",
    }


class GrowthTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "agent.jsonl"

    def growth(
        self, *lines: Any, counted: set[str] | None = None, event_tokens: dict[str, int] | None = None
    ) -> Growth:
        """The growth in the transcript of the lines. Without event_tokens, each request has an event that gives the
        output tokens that the transcript gives."""
        write_transcript(self.file, list(lines))
        if event_tokens is None:
            event_tokens = {
                line["requestId"]: line["message"]["usage"]["output_tokens"] for line in lines if "requestId" in line
            }
        calls = context_growth.read_calls(self.file)
        every_call = {context.call.id for context in calls}
        return context_growth.growth(calls, every_call if counted is None else counted, event_tokens)

    def test_gives_the_peak_and_the_context_that_the_results_of_each_kind_of_tool_call_added(self) -> None:
        self.assertEqual(
            self.growth(
                prompt_line(0),
                sized_call_line("a", 1, 20_000, bash("toolu_1", "scripts/check"), output_tokens=500),
                result_line(2, "toolu_1", "passed"),
                sized_call_line("b", 3, 30_500, tool("toolu_2", "Read"), output_tokens=200),
                result_line(4, "toolu_2", "text"),
                sized_call_line("c", 5, 33_700, bash("toolu_3", "git status")),
                result_line(6, "toolu_3", "clean"),
                sized_call_line("d", 7, 34_000),
                prompt_line(8, "Fix the review's findings."),
                sized_call_line("e", 9, 40_000),
            ),
            Growth(
                peak=40_000,
                tool_calls=3,
                added={"scripts/check": 10_000, "other tools": 3_000, "other commands": 300},
                compactions=0,
                steps_without_event=0,
            ),
        )

    def test_shares_what_the_tool_calls_of_one_call_added_equally(self) -> None:
        self.assertEqual(
            self.growth(
                sized_call_line("a", 1, 10_000, bash("toolu_1", "git log"), tool("toolu_2", "Grep")),
                result_line(2, "toolu_1", "log"),
                result_line(2, "toolu_2", "found"),
                sized_call_line("b", 3, 13_000),
            ),
            Growth(
                peak=13_000,
                tool_calls=2,
                added={"other commands": 1_500, "other tools": 1_500},
                compactions=0,
                steps_without_event=0,
            ),
        )

    def test_counts_the_tool_calls_of_a_response_on_several_lines_once_each(self) -> None:
        self.assertEqual(
            self.growth(
                sized_call_line("a", 1, 10_000, bash("toolu_1", "git log")),
                sized_call_line("a", 1, 10_000, tool("toolu_2", "Grep")),
                result_line(2, "toolu_1", "log"),
                result_line(2, "toolu_2", "found"),
                sized_call_line("b", 3, 13_000),
            ),
            Growth(
                peak=13_000,
                tool_calls=2,
                added={"other commands": 1_500, "other tools": 1_500},
                compactions=0,
                steps_without_event=0,
            ),
        )

    def test_counts_each_compaction_and_no_context_added_in_the_step_that_shrank(self) -> None:
        self.assertEqual(
            self.growth(
                sized_call_line("a", 1, 290_000, bash("toolu_1", "gh pr checks 57 --watch")),
                result_line(2, "toolu_1", "passed"),
                compaction_line(3),
                sized_call_line("b", 4, 15_000, tool("toolu_2", "Read")),
                result_line(5, "toolu_2", "text"),
                sized_call_line("c", 6, 18_000),
                compaction_line(7),
                sized_call_line("d", 8, 12_000),
            ),
            Growth(
                peak=290_000,
                tool_calls=2,
                added={"waiting for CI": 0, "other tools": 3_000},
                compactions=2,
                steps_without_event=0,
            ),
        )

    def test_takes_a_calls_output_tokens_from_its_event_and_leaves_out_a_step_whose_call_has_none(self) -> None:
        self.assertEqual(
            self.growth(
                sized_call_line("a", 1, 10_000, tool("toolu_1", "Write"), output_tokens=5, request_id="req_a"),
                result_line(2, "toolu_1", "written"),
                sized_call_line("b", 3, 15_000, tool("toolu_2", "Write"), output_tokens=5, request_id="req_b"),
                result_line(4, "toolu_2", "written"),
                sized_call_line("c", 5, 20_000),
                event_tokens={"req_a": 4_000},
            ),
            Growth(peak=20_000, tool_calls=1, added={"other tools": 1_000}, compactions=0, steps_without_event=1),
        )

    def test_measures_only_the_counted_calls_and_the_steps_that_lead_to_them(self) -> None:
        self.assertEqual(
            self.growth(
                sized_call_line("a", 1, 80_000, tool("toolu_1", "Read")),
                result_line(2, "toolu_1", "text"),
                compaction_line(3),
                sized_call_line("b", 4, 10_000, tool("toolu_2", "Read")),
                result_line(5, "toolu_2", "text"),
                sized_call_line("c", 6, 12_000, bash("toolu_3", "scripts/check")),
                result_line(7, "toolu_3", "passed"),
                sized_call_line("d", 8, 20_000),
                counted={"c", "d"},
            ),
            Growth(
                peak=20_000,
                tool_calls=2,
                added={"other tools": 2_000, "scripts/check": 8_000},
                compactions=0,
                steps_without_event=0,
            ),
        )

    def test_gives_nothing_for_a_transcript_without_calls(self) -> None:
        self.assertEqual(
            self.growth(prompt_line(0)), Growth(peak=0, tool_calls=0, added={}, compactions=0, steps_without_event=0)
        )
