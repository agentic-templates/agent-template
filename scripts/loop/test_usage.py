import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from usage import parse_reset, parse_usage

NEW_YORK = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 10, 18, 3, 8, tzinfo=NEW_YORK)

# The lines that claude -p /usage printed on 2026-10-18, among its other output
USAGE = """Warning: claude.ai MCP server blocked by enterprise policy: claude.ai Claude Docs
You are currently using your subscription to power your Claude Code usage

Current session: 8% used · resets Oct 18 at 4:30am (America/New_York)
Current week (all models): 29% used · resets Oct 24 at 5pm (America/New_York)
Current week (Fable): 0% used · resets Oct 24 at 5pm (America/New_York)

What's contributing to your limits usage?
"""


class ParseUsageTest(unittest.TestCase):
    def test_reads_each_meter_of_an_output(self) -> None:
        self.assertEqual(
            parse_usage(USAGE, NOW),
            {
                "session": {"used": 8.0, "resets": "2026-10-18T04:30:00-04:00"},
                "week": {"used": 29.0, "resets": "2026-10-24T17:00:00-04:00"},
                "week (Fable)": {"used": 0.0, "resets": "2026-10-24T17:00:00-04:00"},
            },
        )

    def test_reads_a_meter_without_a_reset_time(self) -> None:
        self.assertEqual(parse_usage("Current session: 0.5% used", NOW), {"session": {"used": 0.5, "resets": None}})

    def test_gives_no_reset_time_for_one_it_cannot_read(self) -> None:
        self.assertEqual(
            parse_usage("Current session: 3% used · resets soon", NOW), {"session": {"used": 3.0, "resets": None}}
        )

    def test_gives_no_meters_for_an_output_without_any(self) -> None:
        self.assertEqual(parse_usage("Not logged in · Please run /login", NOW), {})


class ParseResetTest(unittest.TestCase):
    def test_reads_a_time_with_a_date(self) -> None:
        self.assertEqual(
            parse_reset("Oct 24 at 5pm (America/New_York)", NOW), dt.datetime(2026, 10, 24, 17, 0, tzinfo=NEW_YORK)
        )

    def test_reads_a_time_without_a_date_as_the_next_time_the_clock_shows_it(self) -> None:
        self.assertEqual(
            parse_reset("4:30am (America/New_York)", NOW), dt.datetime(2026, 10, 18, 4, 30, tzinfo=NEW_YORK)
        )
        self.assertEqual(
            parse_reset("1:34am (America/New_York)", NOW), dt.datetime(2026, 10, 19, 1, 34, tzinfo=NEW_YORK)
        )

    def test_reads_a_time_in_another_zone(self) -> None:
        self.assertEqual(parse_reset("9am (UTC)", NOW), dt.datetime(2026, 10, 18, 9, 0, tzinfo=dt.UTC))

    def test_reads_a_time_without_a_zone_in_the_zone_of_the_reading_time(self) -> None:
        self.assertEqual(parse_reset("4:30am", NOW), dt.datetime(2026, 10, 18, 4, 30, tzinfo=NEW_YORK))

    def test_reads_noon_and_midnight(self) -> None:
        self.assertEqual(parse_reset("12pm (UTC)", NOW), dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC))
        self.assertEqual(parse_reset("12am (UTC)", NOW), dt.datetime(2026, 10, 19, 0, 0, tzinfo=dt.UTC))

    def test_puts_a_date_that_has_passed_in_the_next_year(self) -> None:
        december = dt.datetime(2026, 12, 30, 10, 0, tzinfo=NEW_YORK)
        self.assertEqual(
            parse_reset("Jan 2 at 9am (America/New_York)", december), dt.datetime(2027, 1, 2, 9, 0, tzinfo=NEW_YORK)
        )

    def test_keeps_a_date_of_today_in_this_year(self) -> None:
        self.assertEqual(
            parse_reset("Oct 18 at 1am (America/New_York)", NOW), dt.datetime(2026, 10, 18, 1, 0, tzinfo=NEW_YORK)
        )

    def test_gives_none_for_a_text_it_cannot_read(self) -> None:
        for text in ["soon", "Foo 3 at 5pm (UTC)", "Feb 30 at 5pm (UTC)", "4:30am (Mars/Base)", "4:30am (../etc)"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_reset(text, NOW))
