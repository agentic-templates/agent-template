import datetime as dt
import os
from typing import Any

from test_report import SINCE, ReportTest
from test_transcripts import at, call_line


def stopped_line(minute: float) -> dict[str, Any]:
    """A transcript's line for Claude Code's note that the safety classifier stopped a response, and it went on once."""
    note = "Opus 5.5's safeguards stopped the response above · continuing once with that noted"
    return {"type": "system", "subtype": "informational", "content": note, "timestamp": at(minute)}


def refused_line(minute: float) -> dict[str, Any]:
    """A transcript's line for Claude Code's note that the safety classifier refused a request."""
    return {"type": "system", "subtype": "model_refusal_no_fallback", "content": "", "timestamp": at(minute)}


class SafetyStopTest(ReportTest):
    def test_counts_the_stopped_responses_and_the_refused_requests_of_the_sessions_and_of_their_agents(self) -> None:
        self.transcript("session", [stopped_line(1), call_line("s", 2), refused_line(3)])
        self.transcript("builder", [refused_line(4)], session="session", agent_type="builder")
        self.assertEqual(
            self.line("Safety classifier"),
            "Safety classifier stops: 2 in the sessions in the main checkout, 1 in their agents",
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
