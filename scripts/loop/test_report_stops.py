import datetime as dt
import os
from collections import Counter
from pathlib import Path
from typing import Any
from unittest import mock

from test_report import SINCE, ReportTest, report
from test_transcripts import at, call_line, prompt_line


def stopped_line(minute: float, **links: str) -> dict[str, Any]:
    """A transcript's line for Claude Code's note that the safety classifier stopped a response, and that Claude Code
    asks once for the rest of it. The links are the line's uuid and parentUuid, which name it and the line before it."""
    note = "Opus 5.5's safeguards stopped the response above · continuing once with that noted"
    return {"type": "system", "subtype": "informational", "content": note, "timestamp": at(minute)} | links


def refused_line(minute: float, **links: str) -> dict[str, Any]:
    """A transcript's line for Claude Code's note that the safety classifier refused a request."""
    return {"type": "system", "subtype": "model_refusal_no_fallback", "content": "", "timestamp": at(minute)} | links


def asking_line(minute: float, **links: str) -> dict[str, Any]:
    """A transcript's line for the message in which Claude Code asks for the rest of a stopped response."""
    message = {"role": "user", "content": "Your response above was stopped by a safety classifier."}
    return {"type": "user", "isMeta": True, "timestamp": at(minute), "message": message} | links


class SafetyStopTest(ReportTest):
    def test_counts_the_stopped_responses_of_the_sessions_and_of_their_agents(self) -> None:
        self.transcript("session", [stopped_line(1), call_line("s", 2), refused_line(3)])
        self.transcript("builder", [refused_line(4)], session="session", agent_type="builder")
        self.assertEqual(
            self.line("Safety classifier"),
            "Safety classifier stops: 2 in the sessions in the main checkout, 1 in their agents",
        )

    def test_counts_a_stop_of_the_request_that_asks_for_the_rest_of_a_stopped_response_with_that_response(
        self,
    ) -> None:
        self.transcript(
            "session",
            [
                call_line("a", 1) | {"uuid": "a", "parentUuid": "prompt"},
                stopped_line(1, uuid="note", parentUuid="a"),
                asking_line(1, uuid="asking", parentUuid="note"),
                call_line("b", 2) | {"uuid": "b", "parentUuid": "asking"},
                call_line("b", 2) | {"uuid": "b-tool", "parentUuid": "b"},
                refused_line(2, uuid="refused", parentUuid="b-tool"),
                call_line("c", 3) | {"uuid": "c", "parentUuid": "result"},
                refused_line(3, uuid="refused-later", parentUuid="c"),
            ],
        )
        self.assertEqual(
            self.line("Safety classifier"),
            "Safety classifier stops: 2 in the sessions in the main checkout, 0 in their agents",
        )

    def test_counts_a_refusal_after_a_new_prompt_that_follows_a_refusal_as_another_stop(self) -> None:
        self.transcript(
            "session",
            [
                call_line("a", 1) | {"uuid": "a", "parentUuid": "start"},
                refused_line(1, uuid="refused", parentUuid="a"),
                prompt_line(2, "Go on with the next issue.") | {"uuid": "prompt", "parentUuid": "refused"},
                call_line("b", 3) | {"uuid": "b", "parentUuid": "prompt"},
                refused_line(3, uuid="refused-again", parentUuid="b"),
            ],
        )
        self.assertEqual(
            self.line("Safety classifier"),
            "Safety classifier stops: 2 in the sessions in the main checkout, 0 in their agents",
        )

    def test_counts_only_the_stops_after_the_time_and_no_other_note(self) -> None:
        compacted = {"type": "system", "subtype": "informational", "content": "Compacted", "timestamp": at(2)}
        self.transcript("session", [stopped_line(-1), compacted, stopped_line(1)])
        self.assertEqual(
            self.line("Safety classifier"),
            "Safety classifier stops: 1 in the sessions in the main checkout, 0 in their agents",
        )

    def test_counts_a_stop_that_a_resumed_session_copied_once(self) -> None:
        earlier = self.transcript("earlier", [stopped_line(1)])
        modified = (SINCE + dt.timedelta(minutes=5)).timestamp()
        os.utime(earlier, (modified, modified))
        self.transcript("resumed", [stopped_line(1), refused_line(6)])
        self.assertEqual(
            self.line("Safety classifier"),
            "Safety classifier stops: 2 in the sessions in the main checkout, 0 in their agents",
        )

    def test_names_the_versions_of_claude_code_that_wrote_the_transcripts_after_the_time(self) -> None:
        self.transcript(
            "session", [call_line("a", -1) | {"version": "2.1.290"}, call_line("b", 1) | {"version": "2.1.303"}]
        )
        self.transcript("builder", [refused_line(2, version="2.1.310")], session="session", agent_type="builder")
        self.assertEqual(
            self.line("Safety classifier"),
            "Safety classifier stops: 0 in the sessions in the main checkout, 1 in their agents, "
            "in transcripts of Claude Code 2.1.303, 2.1.310",
        )

    def test_reads_each_transcript_once(self) -> None:
        session = self.transcript("session", [stopped_line(1), call_line("s", 2)])
        agent = self.transcript("builder", [refused_line(3)], session="session", agent_type="builder")
        opened: Counter[Path] = Counter()
        open_file = Path.open

        def counted_open(file: Path, *arguments: Any, **options: Any) -> Any:
            opened[file] += 1
            return open_file(file, *arguments, **options)

        with mock.patch.object(Path, "open", counted_open):
            report.read_transcripts(self.project, self.root, SINCE)
        self.assertEqual(opened, Counter({session: 1, agent: 1}))
