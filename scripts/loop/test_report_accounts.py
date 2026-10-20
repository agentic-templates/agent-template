import datetime as dt
import json
from typing import Any

from test_report import ReportTest, end, meter_reading, start
from test_transcripts import START, at

# The reset of the week that the readings start in
RESET = START + dt.timedelta(days=3)


def on(account: str | None, line: dict[str, Any]) -> dict[str, Any]:
    """A start line of agents.jsonl that names the account."""
    return {**line, "account": account}


def week_reading(minute: float, account: str, used: float, reset: dt.datetime | None = RESET) -> dict[str, Any]:
    """A line of the meter's log for a reading of the account, whose weekly meter has the use and the reset."""
    week = {"used": used, "resets": reset.isoformat() if reset else None}
    session = {"used": 1.0, "resets": at(minute + 60)}
    return {"at": at(minute), "account": account, "meters": {"session": session, "week": week}, "meters_at_stop": []}


class AccountTest(ReportTest):
    def log_readings(self, *readings: dict[str, Any]) -> None:
        (self.loop / "meter.jsonl").write_text("".join(json.dumps(reading) + "\n" for reading in readings))

    def account_lines(self) -> list[str]:
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        return [line for line in lines if line.startswith(("Account", "Agents whose"))]

    def test_gives_each_accounts_output_tokens_and_its_output_tokens_for_each_point_of_its_week(self) -> None:
        self.log_agents(
            on("claude", start("toolu_1", 1)),
            end("toolu_1", 10, requests={"req_a": 4}),
            on("claude", start("run-1", 2)),
            end("run-1", 10, output_tokens=800),
            on("codex", start("run-2", 3)),
            end("run-2", 10, output_tokens=5000),
        )
        self.log_requests(("req_a", 1200))
        self.log_readings(
            week_reading(0, "claude", 10),
            week_reading(0, "codex", 20.5),
            week_reading(30, "claude", 12),
            week_reading(60, "claude", 14),
            week_reading(60, "codex", 23),
        )
        self.assertEqual(
            self.account_lines(),
            [
                "Account claude: 2000 output tokens, 4 points of the weekly meter, 500 output tokens per point",
                "Account codex: 5000 output tokens, 2.5 points of the weekly meter, 2000 output tokens per point",
            ],
        )

    def test_counts_a_new_weeks_meter_from_0(self) -> None:
        self.log_agents(on("claude", start("run-1", 1)), end("run-1", 50, output_tokens=1400))
        # The reset moves by minutes from one reading to the next, and a week later in a new week, whether or not the
        # meter shows less than before.
        self.log_readings(
            week_reading(0, "claude", 96),
            week_reading(10, "claude", 98, RESET + dt.timedelta(minutes=3)),
            week_reading(20, "claude", 3, RESET + dt.timedelta(days=7)),
            week_reading(30, "claude", 5, RESET + dt.timedelta(days=7)),
            week_reading(40, "claude", 7, RESET + dt.timedelta(days=14)),
        )
        self.assertEqual(
            self.account_lines(),
            ["Account claude: 1400 output tokens, 14 points of the weekly meter, 100 output tokens per point"],
        )

    def test_counts_a_fall_of_the_meter_without_a_reset_as_a_new_week(self) -> None:
        # A Codex snapshot from before its week's reset gives 0% with no reset.
        self.log_agents(on("codex", start("run-1", 1)), end("run-1", 50, output_tokens=600))
        self.log_readings(
            week_reading(0, "codex", 40, None), week_reading(10, "codex", 0, None), week_reading(20, "codex", 6)
        )
        self.assertEqual(
            self.account_lines(),
            ["Account codex: 600 output tokens, 6 points of the weekly meter, 100 output tokens per point"],
        )

    def test_counts_the_agents_that_ended_after_the_time_test_builds_too_and_those_that_name_no_account(self) -> None:
        nameless = start("nameless", 3)
        self.log_agents(
            on("claude", start("old", -10)),
            end("old", 5, output_tokens=9999),
            on("claude", {**start("test", 1), "test": True}),
            end("test", 9, output_tokens=300),
            on("claude", start("open", 2)),
            nameless,
            end("nameless", 9, output_tokens=70),
            on(None, start("unmatched", 4)),
            end("unmatched", 9, output_tokens=30),
            on("codex", start("run-1", 5)),
            end("run-1", 9, output_tokens=50),
        )
        self.log_readings(
            week_reading(-20, "claude", 1),
            {"at": at(2), "machine": meter_reading(2, disk_free_gb=100)["machine"]},
            # A reading of the machine and of an account without a weekly meter
            meter_reading(3, disk_free_gb=100),
            week_reading(1, "claude", 50),
            week_reading(0, "spare", 7),
            week_reading(9, "spare", 7),
        )
        self.assertEqual(
            self.account_lines(),
            [
                "Account claude: 300 output tokens, no rise of the weekly meter",
                "Account codex: 50 output tokens, no rise of the weekly meter",
                "Account spare: 0 output tokens, no rise of the weekly meter",
                "Agents whose start line names no account: 100 output tokens",
            ],
        )
