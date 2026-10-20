import datetime as dt
import unittest
from typing import Any

import account_room
from account_room import Candidate, Room

NOW = dt.datetime(2026, 10, 18, 3, 8, tzinfo=dt.UTC)
TYPES = ["builder", "reviewer"]
RUNS = {"builder": [30.0], "reviewer": [10.0]}


def meters(session: float, week: float = 30.0, week_reset: dt.timedelta = dt.timedelta(days=3)) -> dict[str, Any]:
    return {
        "session": {"used": session, "resets": (NOW + dt.timedelta(hours=2)).isoformat()},
        "week": {"used": week, "resets": (NOW + week_reset).isoformat()},
    }


def candidate(name: str, session: float, **options: Any) -> Candidate:
    is_orchestrators = options.pop("is_orchestrators", False)
    return Candidate(name, TYPES, meters(session, **options), is_orchestrators)


class AccountForTest(unittest.TestCase):
    def test_names_the_account_with_the_lowest_5_hour_meter(self) -> None:
        candidates = [candidate("one", 40.0, week=10.0), candidate("two", 30.0, week=80.0)]
        self.assertEqual(account_room.account_for("builder", candidates), "two")

    def test_names_the_account_whose_week_resets_first_between_equal_5_hour_meters(self) -> None:
        candidates = [
            candidate("one", 30.0, week_reset=dt.timedelta(days=4)),
            candidate("two", 30.0, week_reset=dt.timedelta(days=2)),
            candidate("three", 30.0, week_reset=dt.timedelta(days=3)),
        ]
        self.assertEqual(account_room.account_for("builder", candidates), "two")

    def test_names_an_account_whose_week_reset_is_known_before_one_whose_is_not(self) -> None:
        unknown = candidate("unknown", 30.0)
        unknown.meters["week"]["resets"] = None
        self.assertEqual(account_room.account_for("builder", [unknown, candidate("known", 30.0)]), "known")

    def test_names_an_account_other_than_the_orchestrators_when_the_rest_is_equal(self) -> None:
        candidates = [candidate("own", 30.0, is_orchestrators=True), candidate("other", 30.0)]
        self.assertEqual(account_room.account_for("builder", candidates), "other")

    def test_names_only_an_account_where_the_type_fits(self) -> None:
        candidates = [Candidate("low", ["reviewer"], meters(10.0), False), candidate("high", 60.0)]
        self.assertEqual(account_room.account_for("builder", candidates), "high")
        self.assertEqual(account_room.account_for("reviewer", candidates), "low")
        self.assertIsNone(account_room.account_for("builder-deep", candidates))

    def test_names_another_account_when_the_orchestrators_is_down_to_the_orchestrators_own_need(self) -> None:
        # Working all its time at 0.005 points of the week a minute, the orchestrator needs 4 of the 4.5 points left
        # before its account's week resets in 800 minutes. A builder's run would take 0.15 more, and a reviewer's 0.05.
        rates = {"session": 0.05, "week": 0.005}
        own_meters = meters(10.0, 94.5, week_reset=dt.timedelta(minutes=800))
        own = Room(own_meters, rates, [], 1.0)
        other_meters = meters(50.0, 40.0)
        other_fitting = account_room.fitting_types(Room(other_meters, rates, [], 0.0), TYPES, RUNS, NOW)
        own_fitting = account_room.fitting_types(own, TYPES, RUNS, NOW)
        self.assertEqual((own_fitting, other_fitting), (TYPES, TYPES))
        candidates = [
            Candidate("own", own_fitting, own_meters, True),
            Candidate("other", other_fitting, other_meters, False),
        ]
        self.assertEqual(account_room.account_for("builder", candidates), "own")

        own_meters = meters(10.0, 95.0, week_reset=dt.timedelta(minutes=800))
        own = Room(own_meters, rates, [], 1.0)
        self.assertEqual(account_room.fitting_types(own, TYPES, RUNS, NOW), [])
        candidates = [Candidate("own", [], own_meters, True), Candidate("other", other_fitting, other_meters, False)]
        self.assertEqual(account_room.account_for("builder", candidates), "other")


class MeasuredTest(unittest.TestCase):
    def test_is_the_rise_of_each_meter_over_the_agent_minutes(self) -> None:
        measured = account_room.measured_per_agent_minute(meters(16.0, 31.0), meters(10.0, 30.0), 60.0)
        self.assertEqual(measured, {"session": 0.1, "week": 1 / 60})

    def test_measures_nothing_for_a_meter_that_did_not_rise_or_reset_since(self) -> None:
        later_window = meters(16.0, 30.0)
        later_window["session"]["resets"] = (NOW + dt.timedelta(hours=7)).isoformat()
        self.assertEqual(account_room.measured_per_agent_minute(later_window, meters(10.0, 30.0), 60.0), {})

    def test_counts_a_reset_time_that_moved_by_a_minute_as_the_same_window(self) -> None:
        later = meters(16.0)
        later["session"]["resets"] = (NOW + dt.timedelta(hours=2, minutes=-1)).isoformat()
        self.assertEqual(account_room.measured_per_agent_minute(later, meters(10.0), 60.0), {"session": 0.1})

    def test_measures_nothing_without_agent_minutes(self) -> None:
        self.assertEqual(account_room.measured_per_agent_minute(meters(16.0), meters(10.0), 0.0), {})

    def test_measures_nothing_for_a_meter_without_a_reset_time(self) -> None:
        before, after = meters(10.0), meters(16.0)
        before["session"]["resets"] = after["session"]["resets"] = None
        self.assertNotIn("session", account_room.measured_per_agent_minute(after, before, 60.0))


class WorkingShareTest(unittest.TestCase):
    def test_is_the_share_of_the_time_since_the_first_period_that_the_periods_take(self) -> None:
        periods = [(NOW, NOW + dt.timedelta(minutes=10)), (NOW + dt.timedelta(minutes=30), NOW + dt.timedelta(hours=1))]
        self.assertEqual(account_room.working_share(periods, NOW + dt.timedelta(hours=2)), 1 / 3)

    def test_is_0_without_periods(self) -> None:
        self.assertEqual(account_room.working_share([], NOW), 0.0)


class LongRunTest(unittest.TestCase):
    def test_is_the_90th_percentile_by_nearest_rank(self) -> None:
        self.assertEqual(account_room.long_run([float(minutes) for minutes in range(10, 0, -1)]), 9.0)
        self.assertEqual(account_room.long_run([float(minutes) for minutes in range(1, 12)]), 10.0)
        self.assertEqual(account_room.long_run([4.0]), 4.0)

    def test_is_the_first_run_until_any_run_ended(self) -> None:
        self.assertEqual(account_room.run_minutes("builder", {}), account_room.FIRST_RUN_MINUTES)


# The builder a1 has run 0 minutes, so it still needs 30, or 7.5 points at 0.25 points a minute, the builder a2 still
# needs 10, or 2.5 points, and the reviewer a3 5, or 1.25 points. A hand-off takes 4 minutes, or 1 point.
HAND_OFF_RATES = {"session": 0.25, "week": 0.001}
HAND_OFF_AGENTS = [
    account_room.Running("builder", 0.0, "a1"),
    account_room.Running("builder", 20.0, "a2"),
    account_room.Running("reviewer", 5.0, "a3"),
]
HAND_OFF_MINUTES = 4.0


def hand_off_room(session: float, share: float = 0.0, running: list[account_room.Running] | None = None) -> Room:
    return Room(meters(session), HAND_OFF_RATES, HAND_OFF_AGENTS if running is None else running, share)


def notes(
    room: Room, told: frozenset[str] = frozenset(), running: frozenset[str] = frozenset({"a1", "a2", "a3"})
) -> dict[str, str | None]:
    """The note that each agent gets."""
    known = account_room.HandOffs(set(running), set(told), HAND_OFF_MINUTES)
    return {
        agent.id: account_room.hand_off_note(agent.id, room, RUNS, NOW, known) for agent in room.running if agent.id
    }


class HandOffNoteTest(unittest.TestCase):
    def test_gives_no_note_while_the_room_covers_what_every_agent_still_needs(self) -> None:
        self.assertEqual(notes(hand_off_room(88.75)), {"a1": None, "a2": None, "a3": None})

    def test_tells_the_agent_that_still_needs_the_most_to_hand_off_soon_once_the_room_does_not_cover_them_all(
        self,
    ) -> None:
        self.assertEqual(notes(hand_off_room(89.0)), {"a1": "soon", "a2": None, "a3": None})
        # With a1 handing off, the agents still need 1 + 2.5 + 1.25 points.
        self.assertEqual(notes(hand_off_room(95.25)), {"a1": "soon", "a2": None, "a3": None})

    def test_tells_the_next_agent_once_the_room_does_not_cover_the_rest(self) -> None:
        self.assertEqual(notes(hand_off_room(95.5)), {"a1": "soon", "a2": "soon", "a3": None})

    def test_tells_every_agent_to_hand_off_now_once_the_room_is_down_to_their_hand_offs(self) -> None:
        self.assertEqual(notes(hand_off_room(96.75)), {"a1": "soon", "a2": "soon", "a3": None})
        self.assertEqual(notes(hand_off_room(97.0)), {"a1": "now", "a2": "now", "a3": "now"})

    def test_counts_an_agent_that_was_told_to_hand_off_at_the_time_of_a_hand_off(self) -> None:
        self.assertEqual(notes(hand_off_room(89.0), told=frozenset({"a1"})), {"a1": None, "a2": None, "a3": None})
        self.assertEqual(notes(hand_off_room(95.5), told=frozenset({"a1"})), {"a1": None, "a2": "soon", "a3": None})

    def test_tells_only_agents_that_still_run_and_counts_the_rest_at_what_they_need(self) -> None:
        # a1 ended after the reading, so it can't hand off, and still counts 7.5 points.
        self.assertEqual(
            notes(hand_off_room(89.0), running=frozenset({"a2", "a3"})), {"a1": None, "a2": "soon", "a3": None}
        )
        self.assertEqual(
            notes(hand_off_room(90.5), running=frozenset({"a2", "a3"})), {"a1": None, "a2": "soon", "a3": "soon"}
        )
        # An agent that the meter named the account for, and that hasn't started, still needs 7.5 points.
        named = [*HAND_OFF_AGENTS, account_room.Running("builder", 0.0)]
        self.assertEqual(notes(hand_off_room(81.5, running=named)), {"a1": "soon", "a2": None, "a3": None})

    def test_counts_what_the_orchestrator_needs_until_its_weekly_reset(self) -> None:
        # At 0.1 of its time, the orchestrator needs 0.1 * 0.25 * 120 minutes until the 5-hour reset, 3 points.
        self.assertEqual(notes(hand_off_room(85.75, share=0.1)), {"a1": None, "a2": None, "a3": None})
        self.assertEqual(notes(hand_off_room(86.0, share=0.1)), {"a1": "soon", "a2": None, "a3": None})
        # The hand-offs of the 3 agents and of the orchestrator at its share take 3.1 points.
        self.assertEqual(notes(hand_off_room(96.9, share=0.1)), {"a1": "now", "a2": "now", "a3": "now"})

    def test_tells_first_the_agent_that_has_run_for_less_time_of_two_that_need_the_same(self) -> None:
        # The builder has run 25 of its 30 minutes, and the reviewer 5 of its 10, so each still needs 5 minutes.
        running = [account_room.Running("builder", 25.0, "a1"), account_room.Running("reviewer", 5.0, "a2")]
        self.assertEqual(notes(hand_off_room(97.75, running=running)), {"a1": None, "a2": "soon"})

    def test_gives_no_note_to_an_agent_that_would_finish_no_later_than_a_hand_off_would_end(self) -> None:
        # At 0.1 of its time, the orchestrator needs 3 points, so a room of 4 points can't cover the agents even when a1
        # hands off. A builder that has run 26 minutes needs 4 more, as long as a hand-off, and one that has run 25
        # needs 5.
        finishing = [account_room.Running("builder", 0.0, "a1"), account_room.Running("builder", 26.0, "a2")]
        self.assertEqual(notes(hand_off_room(96.0, share=0.1, running=finishing)), {"a1": "soon", "a2": None})
        not_finishing = [account_room.Running("builder", 0.0, "a1"), account_room.Running("builder", 25.0, "a2")]
        self.assertEqual(notes(hand_off_room(96.0, share=0.1, running=not_finishing)), {"a1": "soon", "a2": "soon"})
        # The hand-offs of the 2 agents and of the orchestrator at its share take 2.1 points.
        self.assertEqual(notes(hand_off_room(98.0, share=0.1, running=finishing)), {"a1": "now", "a2": None})

    def test_counts_only_the_usage_before_the_reset(self) -> None:
        room = hand_off_room(89.0)
        room.meters["session"]["resets"] = (NOW + dt.timedelta(minutes=20)).isoformat()
        # Until the reset, a1 needs 20 minutes, a2 10 and a3 5, so 8.75 points in all.
        self.assertEqual(notes(room), {"a1": None, "a2": None, "a3": None})


class HandOffMinutesTest(unittest.TestCase):
    def test_is_the_90th_percentile_of_the_minutes_from_each_agents_latest_note_to_its_end(self) -> None:
        noted = {f"a{minutes}": NOW for minutes in range(1, 11)}
        ends = {agent: NOW + dt.timedelta(minutes=int(agent[1:])) for agent in noted}
        self.assertEqual(account_room.hand_off_minutes(noted, ends), 9.0)

    def test_leaves_out_an_agent_that_ended_before_its_note_or_has_not_ended(self) -> None:
        noted = {"a1": NOW, "a2": NOW, "a3": NOW}
        ends = {"a1": NOW + dt.timedelta(minutes=4), "a2": NOW - dt.timedelta(minutes=1)}
        self.assertEqual(account_room.hand_off_minutes(noted, ends), 4.0)

    def test_is_the_first_hand_off_until_an_agent_with_a_note_ended(self) -> None:
        self.assertEqual(account_room.hand_off_minutes({"a1": NOW}, {}), account_room.FIRST_HAND_OFF_MINUTES)
