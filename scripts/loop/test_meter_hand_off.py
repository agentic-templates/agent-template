import datetime as dt
import json
import os
from typing import Any

from test_meter import NOW, MeterTest, meters


class HandOffTest(MeterTest):
    """At the reading, the builder a has run 1 minute of a builder's 30, so it still needs 29, or 7.25 points at 0.25
    points a minute, and the builder b has run 20, so it still needs 2.5 points. Until an agent with a note of
    scripts/hooks/hand-off ends, a hand-off takes 10 minutes, or 2.5 points."""

    def setUp(self) -> None:
        super().setUp()
        self.log_agent("earlier", NOW - dt.timedelta(hours=3), end=NOW - dt.timedelta(hours=2, minutes=30))
        self.log_agent("a", NOW - dt.timedelta(minutes=1))
        self.log_agent("b", NOW - dt.timedelta(minutes=20))
        self.clock.time = NOW + dt.timedelta(minutes=5)

    def log_agent(self, agent_id: str, start: dt.datetime, end: dt.datetime | None = None, **start_fields: Any) -> None:
        lines = [
            {"at": start.isoformat(), "event": "start", "id": agent_id, "type": "builder", "account": "claude"}
            | start_fields
        ]
        if end is not None:
            lines.append({"at": end.isoformat(), "event": "end", "id": agent_id, "minutes": 30.0})
        with (self.loop / "agents.jsonl").open("a") as log:
            log.writelines(json.dumps(line) + "\n" for line in lines)

    def reading(self, session: float, at: dt.datetime = NOW, **fields: Any) -> None:
        entry = {
            "at": at.isoformat(),
            "account": "claude",
            "meters": meters(session),
            "per_agent_minute": {"session": 0.25, "week": 0.01},
        } | fields
        with (self.loop / "meter.jsonl").open("a") as log:
            log.write(json.dumps(entry) + "\n")

    def note(self, name: str, agent_id: str, at: dt.datetime) -> None:
        hand_offs = self.loop / "hand-offs"
        hand_offs.mkdir(exist_ok=True)
        (hand_offs / name).write_text(agent_id)
        os.utime(hand_offs / name, (at.timestamp(), at.timestamp()))

    def hand_off(self, agent_id: str) -> str:
        status, lines, errors = self.run_meter("--hand-off", agent_id)
        self.assertEqual((status, errors), (0, ""))
        return " ".join(lines)

    def notes(self) -> list[str]:
        return [self.hand_off("a"), self.hand_off("b")]

    def test_gives_the_notes_from_the_newest_reading_of_the_agents_account(self) -> None:
        self.reading(99.0, at=NOW - dt.timedelta(minutes=10))
        self.reading(90.25)
        self.assertEqual(self.notes(), ["none", "none"])
        self.reading(90.5)
        self.assertEqual(self.notes(), ["soon", "none"])
        self.reading(95.0)
        self.assertEqual(self.notes(), ["now", "now"])
        self.assertFalse((self.standins / "calls.jsonl").exists())

    def test_gives_no_note_from_a_reading_more_than_60_minutes_old(self) -> None:
        self.reading(95.0)
        self.clock.time = NOW + dt.timedelta(minutes=60)
        self.assertEqual(self.hand_off("a"), "now")
        self.clock.time = NOW + dt.timedelta(minutes=60, seconds=1)
        self.assertEqual(self.hand_off("a"), "none")

    def test_gives_no_note_without_a_reading_of_the_agents_account(self) -> None:
        self.log_agent("c", NOW - dt.timedelta(minutes=1), account="other")
        self.log_agent("d", NOW - dt.timedelta(minutes=1), account=None)
        self.assertEqual(self.hand_off("a"), "none")
        self.reading(95.0)
        self.assertEqual([self.hand_off(agent) for agent in ("c", "d", "unknown")], ["none", "none", "none"])

    def test_counts_what_the_orchestrator_needs_at_the_share_in_the_reading(self) -> None:
        # At 0.1 of its time, the orchestrator needs 0.1 * 0.25 * 82 minutes until the 5-hour reset, 2.05 points.
        self.reading(88.25)
        self.assertEqual(self.notes(), ["none", "none"])
        self.reading(88.25, orchestrator_share=0.1)
        self.assertEqual(self.notes(), ["soon", "none"])

    def test_counts_an_agent_that_started_on_the_account_after_the_reading(self) -> None:
        # The builder c still needs all 30 minutes, 7.5 points.
        self.log_agent("c", NOW + dt.timedelta(minutes=2))
        self.reading(83.0)
        self.assertEqual([*self.notes(), self.hand_off("c")], ["none", "none", "soon"])

    def test_counts_an_agent_with_a_note_at_the_time_of_a_hand_off(self) -> None:
        self.note("agent-a.400000", "a", NOW)
        self.reading(90.5)
        self.assertEqual(self.notes(), ["none", "none"])

    def test_takes_the_time_of_a_hand_off_from_the_notes_and_the_end_lines(self) -> None:
        # The earlier agent ended 20 minutes after its latest note, so the hand-offs of a and b take 10 points.
        self.note("agent-earlier.400000", "earlier", NOW - dt.timedelta(hours=3))
        self.note("agent-earlier.500000", "earlier", NOW - dt.timedelta(hours=2, minutes=50))
        self.reading(90.0)
        self.assertEqual(self.notes(), ["now", "now"])
        self.reading(89.75)
        self.assertEqual(self.notes(), ["none", "none"])
