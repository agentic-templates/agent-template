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
