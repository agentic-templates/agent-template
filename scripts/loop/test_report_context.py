import datetime as dt
import os
from unittest import mock

import hand_offs
from test_context_growth import bash, compaction_line, sized_call_line, tool
from test_report import SINCE, ReportTest, end, start
from test_transcripts import prompt_line, result_line


class ContextTest(ReportTest):
    def setUp(self) -> None:
        super().setUp()
        hook = self.root / "hand-off"
        hook.write_text("compact_tokens=50000\nsoon_tokens=100000\nnow_tokens=150000\n")
        patched = mock.patch.object(hand_offs, "HOOK", hook)
        patched.start()
        self.addCleanup(patched.stop)

    def test_gives_how_the_context_of_each_type_of_agent_and_of_the_orchestrator_grew(self) -> None:
        self.log_agents(
            start("a", 0, issue=12, kind="other", label="feature"),
            end("a", 4, minutes=4),
            start("b", 0),
            start("r", 0, agent_type="reviewer"),
            start("no transcript", 0, agent_type="reviewer"),
        )
        self.agent_transcript(
            "a",
            [
                prompt_line(0),
                sized_call_line("x1", 1, 20_000, bash("toolu_a1", "scripts/check"), output_tokens=1_000),
                result_line(3, "toolu_a1", "passed"),
                sized_call_line("x2", 4, 120_000),
            ],
        )
        (self.loop / "hand-offs").mkdir()
        (self.loop / "hand-offs" / "a.100000").touch()
        self.agent_transcript(
            "b",
            [
                prompt_line(0),
                sized_call_line("y1", 1, 30_000, tool("toolu_b1", "Read")),
                result_line(2, "toolu_b1", "text"),
                sized_call_line("y2", 3, 42_000, bash("toolu_b2", "git log")),
                result_line(4, "toolu_b2", "log"),
                sized_call_line("y3", 5, 50_000, bash("toolu_b3", "scripts/check")),
                result_line(6, "toolu_b3", "passed"),
                sized_call_line("y4", 7, 61_000),
            ],
        )
        self.agent_transcript("r", [prompt_line(0), sized_call_line("z1", 1, 5_000)])
        self.transcript(
            "session",
            [
                prompt_line(0),
                sized_call_line("s1", 1, 40_000, tool("toolu_s1", "Agent")),
                result_line(2, "toolu_s1", "report"),
                sized_call_line("s2", 3, 70_000, bash("toolu_s2", "gh pr checks 57 --watch")),
                result_line(4, "toolu_s2", "passed"),
                compaction_line(5),
                sized_call_line("s3", 6, 10_000, tool("toolu_s3", "Read")),
                result_line(7, "toolu_s3", "text"),
                sized_call_line("s4", 8, 14_000),
            ],
        )
        # The orchestrator's last step with a tool call has no event.
        self.log_requests(*[(f"req_{call}", 0) for call in ("y1", "y2", "y3", "s1", "s2")], ("req_x1", 1_000))
        status, lines, errors = self.run_report("--since", SINCE.isoformat(), "--issues")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            [line for line in lines if line.startswith("Context")],
            [
                (
                    "Context of type builder: 2 agents, peak median 90.5k, largest 120.0k, 50% past the hand-off "
                    "threshold of 100.0k | 32.5k added per tool call, 0 steps without an event left out | 1 told to hand off, 0 compactions | added most "
                    "by scripts/check 85%, other tools 9%, other commands 6%"
                ),
                (
                    "Context of type reviewer: 1 agents, peak median 5.0k, largest 5.0k, 0% past the hand-off "
                    "threshold of 100.0k | no tool call measured, 0 steps without an event left out | 0 told to hand "
                    "off, 0 compactions"
                ),
                (
                    "Context of the orchestrator: 1 sessions, peak median 70.0k, largest 70.0k, 100% past the "
                    "compaction threshold of 50.0k | 15.0k added per tool call, 1 steps without an event left out | 1 "
                    "compactions | added most by other tools 100%"
                ),
            ],
        )
        self.assertEqual(
            next(line for line in lines if line.startswith("Issue")),
            (
                "Issue #12 feature other: builder, 4.0 min, 1 tool calls, 1 input tokens, 1 output tokens | no pull "
                "request | context: peak 120.0k, 99.0k added per tool call | time: model 2.0 min, scripts/check "
                "2.0 min"
            ),
        )

    def test_counts_the_orchestrators_calls_after_the_time_and_a_call_that_a_resumed_session_copied_once(self) -> None:
        earlier = self.transcript(
            "earlier",
            [
                sized_call_line("before", -30, 20_000, tool("toolu_1", "Read")),
                result_line(-29, "toolu_1", "text"),
                sized_call_line("s1", 1, 30_000, tool("toolu_2", "Read")),
            ],
        )
        modified = (SINCE + dt.timedelta(minutes=5)).timestamp()
        os.utime(earlier, (modified, modified))
        self.transcript(
            "resumed",
            [
                sized_call_line("s1", 1, 30_000, tool("toolu_2", "Read")),
                result_line(2, "toolu_2", "text"),
                sized_call_line("s2", 6, 36_000),
            ],
        )
        self.log_requests(("req_before", 0), ("req_s1", 0))
        self.assertEqual(
            self.line("Context of the orchestrator"),
            (
                "Context of the orchestrator: 2 sessions, peak median 33.0k, largest 36.0k, 0% past the compaction "
                "threshold of 50.0k | 8.0k added per tool call, 0 steps without an event left out | 0 compactions | "
                "added most by other tools 100%"
            ),
        )
