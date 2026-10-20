import datetime as dt
import json
import unittest
from typing import Any

import waste
from test_meter import NOW, MeterTest, github_answer, usage_output
from test_meter_retrospective import new_york


def build(
    agent_id: str,
    ended: str,
    minutes: float,
    agent_type: str = "builder",
    issue: int | None = None,
    is_test: bool = False,
) -> list[str]:
    """The lines of .loop/agents.jsonl for an agent that worked for the minutes until it ended, at a time in New York
    on the day of the meter's reading."""
    end = new_york(f"2026-10-18T{ended}")
    start = end - dt.timedelta(minutes=minutes)
    start_line = {
        "at": start.isoformat(),
        "event": "start",
        "id": agent_id,
        "type": agent_type,
        "issue": issue,
        "test": is_test,
    }
    end_line = {
        "at": end.isoformat(),
        "event": "end",
        "id": agent_id,
        "minutes": minutes,
        "periods": [[start.isoformat(), end.isoformat()]],
        "output_tokens": 0,
        "requests": None,
    }
    return [json.dumps(start_line), json.dumps(end_line)]


def earlier_builds(minutes: float, agent_type: str = "builder", count: int = 5) -> list[list[str]]:
    """Builds of a type that ended at 00:10, 00:11 and so on."""
    return [build(f"{agent_type}-{index}", f"00:{10 + index}:00", minutes, agent_type) for index in range(count)]


def heavy(event: str, at: str, command: str = "scripts/check", **fields: Any) -> dict[str, Any]:
    """A line of .loop/heavy.jsonl at a time in New York on the day of the meter's reading."""
    return {"at": new_york(f"2026-10-18T{at}").isoformat(), "event": event, "id": at, "command": command, **fields}


def check_runs(before: list[float], since: list[float]) -> list[dict[str, Any]]:
    """End lines of passing runs of scripts/check that took the seconds, before the last retrospective at 01:00 and
    after it."""
    return [heavy("end", "00:30:00", exit_code=0, seconds=seconds) for seconds in before] + [
        heavy("end", "02:30:00", exit_code=0, seconds=seconds) for seconds in since
    ]


class AuditTest(MeterTest):
    """The meter reads at 03:08 in New York, and the last retrospective merged at 1:00."""

    def write_logs(self, agents: list[list[str]], heavy_lines: list[dict[str, Any]] | None = None) -> None:
        lines = [line for lines in agents for line in lines]
        (self.loop / "agents.jsonl").write_text("".join(f"{line}\n" for line in lines))
        (self.loop / "heavy.jsonl").write_text("".join(json.dumps(line) + "\n" for line in heavy_lines or []))

    def audits(
        self, agents: list[list[str]], heavy_lines: list[dict[str, Any]] | None = None, *arguments: str
    ) -> list[str]:
        """The parts of the machine line that name a finding for an audit."""
        self.write_logs(agents, heavy_lines)
        machine_line = self.run_meter(*arguments)[1][-1]
        self.assertTrue(machine_line.startswith("machine | "))
        return [part for part in machine_line.split(" | ") if part.startswith("audit due")]


class LongBuildTest(AuditTest):
    def test_names_a_build_that_took_more_than_4_times_the_median_of_its_types_earlier_builds(self) -> None:
        cases = {
            41: [
                "audit due: builder #208 took 41 min, more than 4 times the median of 10 min of its type's earlier builds"
            ],
            40: [],
        }
        for minutes, audits in cases.items():
            with self.subTest(minutes=minutes):
                agents = [*earlier_builds(10), build("long", "02:30:00", minutes, issue=208)]
                self.assertEqual(self.audits(agents), audits)

    def test_names_a_long_build_of_each_builder_type(self) -> None:
        for agent_type in ("builder-deep", "builder-small"):
            with self.subTest(agent_type=agent_type):
                agents = [*earlier_builds(10, agent_type), build("long", "02:30:00", 41, agent_type, issue=7)]
                self.assertEqual(
                    self.audits(agents),
                    [
                        (
                            f"audit due: {agent_type} #7 took 41 min, more than 4 times the median of 10 min of its "
                            "type's earlier builds"
                        )
                    ],
                )

    def test_waits_for_5_earlier_builds_of_the_type_that_ended(self) -> None:
        cases = {5: 1, 4: 0}
        for count, found in cases.items():
            with self.subTest(count=count):
                agents = [*earlier_builds(10, count=count), build("long", "02:30:00", 100)]
                self.assertEqual(len(self.audits(agents)), found)

    def test_compares_only_with_the_builds_of_the_same_type_that_ended_before(self) -> None:
        agents = [
            *earlier_builds(10, "builder-small"),
            *earlier_builds(10, count=4),
            build("later", "02:40:00", 10),
            build("long", "02:30:00", 100),
        ]
        self.assertEqual(self.audits(agents), [])

    def test_names_only_a_build_that_ended_in_the_last_hour(self) -> None:
        cases = {"02:08:00": 1, "02:07:59": 0}
        for ended, found in cases.items():
            with self.subTest(ended=ended):
                agents = [*earlier_builds(10), build("long", ended, 100)]
                self.assertEqual(len(self.audits(agents)), found)

    def test_leaves_out_test_builds_and_other_types(self) -> None:
        agents = [
            *earlier_builds(10, "reviewer"),
            build("long review", "02:30:00", 100, "reviewer"),
            *earlier_builds(10, count=4),
            build("test", "00:20:00", 10, is_test=True),
            build("long test", "02:30:00", 100, is_test=True),
            build("long", "02:40:00", 100),
        ]
        self.assertEqual(self.audits(agents), [])


class LongWaitTest(AuditTest):
    def test_names_waits_of_more_than_10_minutes_per_hour_of_builder_work_in_the_last_hour(self) -> None:
        cases = {
            601: [
                (
                    "audit due: heavy commands waited 10.0 min in run-heavy's line per hour of builder work in the "
                    "last hour, more than 10"
                )
            ],
            600: [],
        }
        for waited, audits in cases.items():
            with self.subTest(waited=waited):
                waits = [
                    heavy("start", "03:00:00", waited_seconds=waited - 100),
                    heavy("stop", "03:05:00", waited_seconds=100),
                ]
                self.assertEqual(self.audits([build("a", "03:08:00", 60)], waits), audits)

    def test_divides_by_the_hours_of_builder_work_alone(self) -> None:
        agents = [
            build("a", "03:08:00", 20),
            build("b", "03:00:00", 10, "builder-small"),
            build("c", "03:08:00", 60, "reviewer"),
        ]
        audits = self.audits(agents, [heavy("start", "03:00:00", waited_seconds=450)])
        self.assertEqual(len(audits), 1)
        self.assertIn("waited 15.0 min", audits[0])

    def test_counts_only_the_part_of_each_wait_and_of_the_work_in_the_last_hour(self) -> None:
        agents = [build("a", "03:08:00", 120)]
        audits = self.audits(agents, [heavy("start", "02:20:00", waited_seconds=3000)])
        self.assertEqual(len(audits), 1)
        self.assertIn("waited 12.0 min", audits[0])

    def test_counts_only_the_waits_that_ended(self) -> None:
        waits = [heavy("wait", "02:30:00"), heavy("end", "03:00:00", exit_code=0, seconds=1800)]
        self.assertEqual(self.audits([build("a", "03:08:00", 60)], waits), [])

    def test_names_nothing_without_builder_work_in_the_last_hour(self) -> None:
        agents = [build("a", "02:00:00", 60), build("b", "03:08:00", 60, "reviewer")]
        self.assertEqual(self.audits(agents, [heavy("start", "03:00:00", waited_seconds=1800)]), [])


class SlowerCheckTest(AuditTest):
    def test_names_a_median_20_percent_longer_since_the_last_retrospective_at_p_below_one_percent(self) -> None:
        cases = {
            "longer": (
                check_runs([60, 61, 62, 63, 64], [80, 81, 82, 83, 84]),
                [
                    (
                        "audit due: scripts/check's passing runs took a median of 82 s since the last retrospective, "
                        "against 62 s before it (p = 0.006)"
                    )
                ],
            ),
            "20% longer": (check_runs([98, 99, 100, 101, 102], [118, 119, 120, 121, 122]), 1),
            "19% longer": (check_runs([98, 99, 100, 101, 102], [117, 118, 119, 120, 121]), 0),
            "too few runs": (check_runs([60, 61, 62], [80, 81, 82]), 0),
        }
        for name, (runs, audits) in cases.items():
            with self.subTest(name):
                found = self.audits([], runs)
                if isinstance(audits, int):
                    self.assertEqual(len(found), audits)
                else:
                    self.assertEqual(found, audits)

    def test_counts_only_the_passing_runs_of_scripts_check(self) -> None:
        runs = [
            *check_runs([60, 61, 62, 63, 64], []),
            *[heavy("end", "02:30:00", exit_code=1, seconds=200) for _ in range(5)],
            *[heavy("end", "02:30:00", "scripts/test-scripts", exit_code=0, seconds=200) for _ in range(5)],
        ]
        self.assertEqual(self.audits([], runs), [])

    def test_names_nothing_before_the_first_retrospective(self) -> None:
        self.answer("gh", github_answer(merges=()))
        self.assertEqual(self.audits([], check_runs([60, 61, 62, 63, 64], [80, 81, 82, 83, 84])), [])


class WakeTest(AuditTest):
    """While the account's advice is go, meter --wake reads every 30 minutes. The advice turns to stop at the fifth
    reading of the account, so a wake that misses a finding ends then."""

    LONG_BUILD = (
        "audit due: builder #208 took 100 min, more than 4 times the median of 10 min of its type's earlier builds"
    )

    def setUp(self) -> None:
        super().setUp()
        self.answer("claude", [*[usage_output(8)] * 4, usage_output(98)])

    def test_wakes_at_the_reading_with_a_finding_that_the_meter_had_not_printed(self) -> None:
        self.write_logs(earlier_builds(10))
        later = [*earlier_builds(10), build("long", "03:20:00", 100, issue=208)]
        self.clock.alarms.append((NOW + dt.timedelta(minutes=10), lambda: self.write_logs(later)))
        self.assertEqual(self.run_meter("--wake")[1][-1].split(" | ")[-1], self.LONG_BUILD)
        self.assertEqual(self.clock.time - NOW, dt.timedelta(minutes=30))

    def test_wakes_at_once_for_a_finding_from_before_it_started(self) -> None:
        agents = [*earlier_builds(10), build("long", "03:00:00", 100, issue=208)]
        self.assertEqual(self.audits(agents, None, "--wake"), [self.LONG_BUILD])
        self.assertEqual(self.clock.time, NOW)

    def test_wakes_for_the_other_findings_when_github_does_not_say_when_the_last_retrospective_merged(self) -> None:
        self.answer("gh", {"status": 1, "stderr": "HTTP 401: Bad credentials\n"})
        agents = [*earlier_builds(10), build("long", "03:00:00", 100, issue=208)]
        slower = check_runs([60, 61, 62, 63, 64], [80, 81, 82, 83, 84])
        self.assertEqual(self.audits(agents, slower, "--wake"), [self.LONG_BUILD])
        self.assertEqual(self.clock.time, NOW)

    def test_does_not_wake_for_a_finding_that_the_meter_printed_last_time(self) -> None:
        agents = [*earlier_builds(10), build("long", "03:00:00", 100, issue=208)]
        self.assertEqual(self.audits(agents), [self.LONG_BUILD])
        self.assertEqual(self.audits(agents, None, "--wake"), [])
        self.assertEqual(self.clock.time - NOW, dt.timedelta(minutes=90))

    def test_does_not_wake_again_while_the_numbers_of_a_finding_change(self) -> None:
        waits = [heavy("start", "03:00:00", waited_seconds=900)]
        self.assertEqual(len(self.audits([build("a", "03:08:00", 60)], waits)), 1)
        self.write_logs([build("a", "03:08:00", 60)], [*waits, heavy("start", "03:05:00", waited_seconds=300)])
        self.run_meter("--wake")
        self.assertEqual(self.clock.time - NOW, dt.timedelta(minutes=90))


class PValueTest(unittest.TestCase):
    def test_gives_the_p_value_of_scipys_mannwhitneyu_with_the_normal_approximation(self) -> None:
        # From scipy.stats.mannwhitneyu(larger, smaller, alternative="greater", method="asymptotic")
        cases: list[tuple[list[float], list[float], float]] = [
            ([6, 7, 8, 9, 10], [1, 2, 3, 4, 5], 0.006092890177672405),
            ([3, 3, 4, 5, 5, 6], [1, 2, 3, 3, 4], 0.030213227253196463),
            ([65, 70, 84, 90, 88, 84, 100, 79], [60, 65, 62, 70, 66, 64, 61, 63, 65], 0.0009988136199159789),
            ([2, 2, 2], [2, 2], 1.0),
        ]
        for larger, smaller, p_value in cases:
            with self.subTest(larger=larger, smaller=smaller):
                self.assertAlmostEqual(waste.p_of_larger(larger, smaller), p_value, places=12)
