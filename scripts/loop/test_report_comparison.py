from typing import Any

from test_report import SINCE, ReportTest, end, start
from test_transcripts import at


class ComparisonTest(ReportTest):
    def builders(
        self, first: int, minute: float, opened: int, failed: int, agent_type: str = "builder"
    ) -> list[dict[str, Any]]:
        """The lines of agents that started at the minute and opened pull requests from the number first on, of which
        the first ones failed a check right away."""
        lines = []
        for number in range(first, first + opened):
            agent = f"{agent_type}-{number}"
            lines += [start(agent, minute, agent_type), end(agent, minute + 1, pull_request=number)]
            failures = ("FAILURE",) if number < first + failed else ()
            self.add_pull_request(number, commit_minutes=(minute + 0.5,), conclusions=failures)
        return lines

    def counts_part(self, agent_type: str = "builder") -> str:
        """The Type line from the counts of the pull requests on."""
        line = self.line(f"Type {agent_type}:")
        return line[line.index("| pull requests:") :]

    def test_names_the_counts_whose_difference_from_the_period_before_is_larger_than_chance(self) -> None:
        self.retrospectives.append(SINCE.isoformat())
        # Fisher's exact test gives 0.73 for 7 of 20 against 5 of 18, and 2/252 for 5 of 5 against 0 of 5. Holm's
        # correction for the 4 counts of one type asks for less than 0.05 / 4.
        cases = {
            (20, 7, 18, 5): "no difference larger than chance",
            (5, 5, 5, 0): "larger than chance: failed a check",
        }
        for (opened, failed, opened_before, failed_before), verdict in cases.items():
            with self.subTest(now=(failed, opened), before=(failed_before, opened_before)):
                self.pull_requests.clear()
                before = self.builders(1, -10, opened_before, failed_before)
                self.log_agents(*before, *self.builders(100, 1, opened, failed))
                self.assertEqual(
                    self.counts_part(),
                    f"| pull requests: {opened}, 0 needed a fix after review, {failed} failed a check, 0 left the merge "
                    f"queue unmerged, 0 got a second builder | before: pull requests: {opened_before}, 0 needed a fix "
                    f"after review, {failed_before} failed a check, 0 left the merge queue unmerged, 0 got a second "
                    f"builder | {verdict}",
                )

    def test_corrects_for_the_number_of_counts_that_the_report_compares(self) -> None:
        self.retrospectives.append(SINCE.isoformat())
        # 2/70 for 4 of 4 against 0 of 4 is below 0.05 but not below 0.05 / 4.
        self.log_agents(*self.builders(1, -10, 4, 0), *self.builders(10, 1, 4, 4))
        self.assertTrue(self.counts_part().endswith("| no difference larger than chance"))
        # 2/252 for 5 of 5 against 0 of 5 is below 0.05 / 4, but not below 0.05 / 8 once another type is compared.
        self.pull_requests.clear()
        self.log_agents(
            *self.builders(1, -10, 5, 0),
            *self.builders(10, 1, 5, 5),
            *self.builders(20, -10, 4, 2, "builder-deep"),
            *self.builders(30, 1, 4, 2, "builder-deep"),
        )
        self.assertTrue(self.counts_part().endswith("| no difference larger than chance"))

    def test_counts_what_happened_in_the_period_before_by_its_end(self) -> None:
        self.retrospectives.append(SINCE.isoformat())
        self.log_agents(
            start("a", -10, issue=12),
            end("a", -9, pull_request=50),
            start("ra", 5, agent_type="reviewer", issue=50),
            start("a2", 6, issue=12),
            *self.builders(100, 1, 1, 0),
        )
        # The reviewer, the fix, the failed check, the exit from the merge queue and the second builder came after the
        # time.
        self.add_pull_request(
            50, commit_minutes=(10,), conclusions=("FAILURE",), removals=("merge_conflict",), removal_minute=12
        )
        self.assertIn(
            "| before: pull requests: 1, 0 needed a fix after review, 0 failed a check, 0 left the merge queue "
            "unmerged, 0 got a second builder |",
            self.counts_part(),
        )

    def test_compares_with_the_period_from_the_last_retrospective_that_merged_before_the_time(self) -> None:
        self.retrospectives += [at(-100), at(-30), at(5)]
        self.log_agents(*self.builders(1, -50, 4, 0), *self.builders(10, -20, 4, 4), *self.builders(20, 1, 4, 0))
        self.assertEqual(
            self.line("Before"),
            "Before: from 2026-10-11T13:30:00+00:00, when the last retrospective before then merged",
        )
        self.assertIn("| before: pull requests: 4, 0 needed a fix after review, 4 failed a check", self.counts_part())

    def test_compares_with_every_agent_before_the_time_when_the_only_retrospective_merged_at_the_time(self) -> None:
        self.retrospectives.append(SINCE.isoformat())
        self.log_agents(*self.builders(1, -5000, 2, 0), *self.builders(10, -20, 2, 2), *self.builders(20, 1, 4, 0))
        self.assertEqual(
            self.line("Before"), "Before: from the first agent, because no retrospective merged before then"
        )
        self.assertIn("| before: pull requests: 4, 0 needed a fix after review, 2 failed a check", self.counts_part())

    def test_compares_nothing_before_the_first_retrospective(self) -> None:
        self.retrospectives.append(at(5))
        self.log_agents(*self.builders(1, -20, 2, 2), *self.builders(20, 1, 4, 0))
        status, lines, errors = self.run_report()
        self.assertEqual((status, errors), (0, ""))
        self.assertFalse([line for line in lines if line.startswith("Before")])
        self.assertEqual(
            self.counts_part(),
            "| pull requests: 4, 0 needed a fix after review, 0 failed a check, 0 left the merge queue unmerged, "
            "0 got a second builder",
        )

    def test_gives_the_counts_before_without_a_comparison_when_a_type_opened_no_pull_requests_since(self) -> None:
        self.retrospectives.append(SINCE.isoformat())
        self.log_agents(
            *self.builders(1, -10, 2, 1),
            *self.builders(10, -10, 1, 0, "builder-small"),
            start("builder-new", 1),
            start("old builder", -10, "builder-deep"),
            end("old builder", -9),
        )
        status, lines, errors = self.run_report()
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            [line for line in lines if line.startswith("Type")],
            [
                (
                    "Type builder: 1 started, 0 ended | pull requests: 0 | before: pull requests: 2, 0 needed a fix "
                    "after review, 1 failed a check, 0 left the merge queue unmerged, 0 got a second builder"
                ),
                (
                    "Type builder-small: 0 started, 0 ended | pull requests: 0 | before: pull requests: 1, 0 needed a "
                    "fix after review, 0 failed a check, 0 left the merge queue unmerged, 0 got a second builder"
                ),
            ],
        )
