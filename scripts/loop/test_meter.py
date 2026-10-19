import contextlib
import datetime as dt
import importlib.machinery
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import paths
from test_transcripts import call_line, prompt_line, write_transcript

METER = Path(__file__).with_name("meter")
# The meter's file name has no .py, so the tests load it by its path.
meter = types.ModuleType("meter")
importlib.machinery.SourceFileLoader("meter", str(METER)).exec_module(meter)

NEW_YORK = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 10, 18, 3, 8, tzinfo=NEW_YORK)

# A stand-in for claude and gh. It writes each call to calls.jsonl in STANDIN_DIR, with the folder it runs in, the files
# there and some variables. claude gives the next answer in claude.json there, and the last one again when they run out.
# gh gives the answer in gh.json. An answer can sleep, print to stdout and stderr, and exit with a status. -S keeps
# coverage, which starts in each Python process of the tests, out of the stand-ins, where it made the tests five times
# slower.
STANDIN = f"""#!{sys.executable} -S
import json, os, sys, time
folder, tool = os.environ["STANDIN_DIR"], os.path.basename(sys.argv[0])
with open(os.path.join(folder, "calls.jsonl"), "a") as log:
    seen = {{name: os.environ.get(name) for name in ("CLAUDECODE", "CLAUDE_CODE_TEST", "CLAUDE_CONFIG_DIR")}}
    call = {{"tool": tool, "args": sys.argv[1:], "cwd": os.getcwd(), "files": os.listdir("."), "env": seen}}
    log.write(json.dumps(call) + "\\n")
answers = json.load(open(os.path.join(folder, tool + ".json")))
if tool == "gh":
    answer = answers
else:
    calls = [json.loads(line) for line in open(os.path.join(folder, "calls.jsonl"))]
    answer = answers[min(sum(call["tool"] == tool for call in calls), len(answers)) - 1]
time.sleep(answer.get("sleep", 0))
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("status", 0))
"""


def usage_output(
    session: float, session_reset: str = "4:30am", week: float = 30.0, model_week: float | None = None
) -> dict[str, str]:
    """An answer of claude -p /usage, with reset times in New York."""
    output = (
        f"Current session: {session:g}% used · resets {session_reset} (America/New_York)\n"
        f"Current week (all models): {week:g}% used · resets Oct 24 at 5pm (America/New_York)\n"
    )
    if model_week is not None:
        output += f"Current week (Fable): {model_week:g}% used · resets Oct 24 at 5pm (America/New_York)\n"
    return {"stdout": output}


def github_answer(merges: tuple[str, ...] = ("2026-10-18T05:00:00Z",), **more: Any) -> dict[str, Any]:
    """gh's answer, with the times when the retrospectives' pull requests merged, and any more fields of the
    repository. The default time is 1:00 in New York on the day of NOW."""
    repository = {"pullRequests": {"nodes": [{"mergedAt": merge} for merge in merges]}, **more}
    return {"stdout": json.dumps({"data": {"repository": repository}})}


def meters(
    session: float | None = 40.0,
    week: float = 30.0,
    session_reset: dt.datetime | None = NOW + dt.timedelta(hours=2),
    week_reset: dt.datetime | None = NOW + dt.timedelta(days=3),
    model_week: float | None = None,
) -> dict[str, Any]:
    found = {"week": {"used": week, "resets": week_reset.isoformat() if week_reset else None}}
    if session is not None:
        found["session"] = {"used": session, "resets": session_reset.isoformat() if session_reset else None}
    if model_week is not None:
        found["week (Fable)"] = {"used": model_week, "resets": (NOW + dt.timedelta(days=3)).isoformat()}
    return found


def reading(at: dt.datetime, used: float, resets: str | None = "2026-10-18T04:30:00-04:00") -> dict[str, Any]:
    return {"at": at.isoformat(), "account": "claude", "meters": {"session": {"used": used, "resets": resets}}}


class FakeClock:
    """The clock for the meter's waits: a sleep moves the time on at once."""

    def __init__(self) -> None:
        self.time = NOW
        self.sleeps: list[float] = []

    def now(self) -> dt.datetime:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += dt.timedelta(seconds=seconds)


class MeterTest(unittest.TestCase):
    """A test with a loop folder, stand-ins for claude and gh first on PATH, and a fake clock."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.loop = self.root / "loop"
        self.standins = self.root / "standins"
        self.bin = self.root / "bin"
        for created in (self.loop, self.standins, self.bin):
            created.mkdir()
        for tool in ("claude", "gh"):
            (self.bin / tool).write_text(STANDIN)
            (self.bin / tool).chmod(0o755)
        self.environment = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "STANDIN_DIR": str(self.standins),
            "LOOP_DIR": str(self.loop),
            "HOME": str(self.root / "person"),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude-config"),
        }
        patched = mock.patch.dict(os.environ, self.environment)
        patched.start()
        self.addCleanup(patched.stop)
        self.clock = FakeClock()
        # Only the meter's own name for the time module gets the fake sleep, because subprocess sleeps too while it
        # waits for a process.
        for name, replacement in (("now", self.clock.now), ("time", types.SimpleNamespace(sleep=self.clock.sleep))):
            patcher = mock.patch.object(meter, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.answer("claude", [usage_output(8)])
        self.answer("gh", github_answer())

    def answer(self, tool: str, answers: Any) -> None:
        (self.standins / f"{tool}.json").write_text(json.dumps(answers))

    def accounts(self, *accounts: dict[str, Any]) -> None:
        (self.loop / "accounts.json").write_text(json.dumps({"accounts": list(accounts)}))

    def calls(self, tool: str) -> list[dict[str, Any]]:
        log = self.standins / "calls.jsonl"
        return [call for call in map(json.loads, log.read_text().splitlines()) if call["tool"] == tool]

    def without_standin(self, tool: str) -> None:
        """Takes the stand-in for a tool away, and leaves only git on PATH with the other stand-in."""
        (self.bin / tool).unlink()
        only_git = self.root / "only-git"
        only_git.mkdir()
        git = shutil.which("git")
        assert git
        (only_git / "git").symlink_to(git)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{only_git}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_meter(self, *arguments: str) -> tuple[int, list[str], str]:
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            status = meter.main(list(arguments))
        return status, printed.getvalue().splitlines(), errors.getvalue()

    def codex_home(self, *snapshots: tuple[str, dict[str, Any] | None], age: dt.timedelta = dt.timedelta()) -> Path:
        """A Codex home with a session file that holds a line for each snapshot: (timestamp, rate_limits)."""
        home = self.root / "codex"
        rollout = home / "sessions" / "2026" / "10" / "18" / f"rollout-{len(list(home.rglob('*.jsonl')))}.jsonl"
        rollout.parent.mkdir(parents=True, exist_ok=True)
        lines = ['{"type": "session_meta"}', '{"type": "event_msg", "used_percent": "not JSON']
        for timestamp, limits in snapshots:
            payload = {"type": "token_count", "rate_limits": limits, "used_percent": None}
            lines.append(json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": payload}))
        rollout.write_text("\n".join(lines) + "\n")
        modified = (NOW - age).timestamp()
        os.utime(rollout, (modified, modified))
        return home


class AdviseTest(unittest.TestCase):
    def test_goes_and_reads_every_30_minutes_below_80_percent(self) -> None:
        self.assertEqual(meter.advise(meters(40.0), 0.2, NOW, False), ("go", 98.0, 1800, []))

    def test_goes_on_an_account_without_a_session_meter(self) -> None:
        self.assertEqual(meter.advise(meters(None), None, NOW, False)[0::2], ("go", 1800))

    def test_watches_and_reads_every_10_minutes_past_80_percent(self) -> None:
        self.assertEqual(meter.advise(meters(81.0), 0.2, NOW, False)[0::2], ("watch", 600))
        self.assertEqual(meter.advise(meters(10.0, week=85.0), 0.2, NOW, False)[0::2], ("watch", 600))
        self.assertEqual(meter.advise(meters(79.0), 0.2, NOW, False)[0], "go")

    def test_keeps_8_minutes_of_usage_and_at_least_2_percent_before_the_session_stop_point(self) -> None:
        self.assertEqual(meter.advise(meters(40.0), 1.0, NOW, False)[1], 92.0)
        self.assertEqual(meter.advise(meters(40.0), 0.1, NOW, False)[1], 98.0)
        self.assertAlmostEqual(meter.advise(meters(40.0), None, NOW, False)[1], 97.6)

    def test_is_near_within_3_percent_of_the_session_stop_point(self) -> None:
        self.assertEqual(meter.advise(meters(95.0), 0.2, NOW, False)[0::2], ("near", 150))
        self.assertEqual(meter.advise(meters(94.9), 0.2, NOW, False)[0], "watch")

    def test_is_near_within_1_percent_of_the_weekly_stop(self) -> None:
        self.assertEqual(meter.advise(meters(10.0, week=98.0), 0.2, NOW, False)[0], "near")
        self.assertEqual(meter.advise(meters(10.0, week=97.9), 0.2, NOW, False)[0], "watch")

    def test_stops_at_the_session_stop_point_until_a_minute_after_the_reset(self) -> None:
        self.assertEqual(meter.advise(meters(98.0), 0.2, NOW, False)[0::2], ("stop", 2 * 3600 + 60))
        self.assertEqual(meter.advise(meters(98.0, session_reset=None), 0.2, NOW, False)[0::2], ("stop", 600))

    def test_stops_at_the_weekly_stop_less_than_10_hours_before_its_reset(self) -> None:
        week_reset = NOW + dt.timedelta(hours=10)
        self.assertEqual(
            meter.advise(meters(10.0, 99.0, week_reset=week_reset), 0.2, NOW, False)[0::2], ("stop", 36060)
        )

    def test_parks_at_the_weekly_stop_more_than_10_hours_before_its_reset(self) -> None:
        week_reset = NOW + dt.timedelta(hours=10, seconds=1)
        self.assertEqual(
            meter.advise(meters(10.0, 99.0, week_reset=week_reset), 0.2, NOW, False)[0::2], ("parked", 36061)
        )
        self.assertEqual(meter.advise(meters(10.0, 99.0, week_reset=None), 0.2, NOW, False)[0::2], ("parked", 600))

    def test_counts_the_weekly_meter_of_each_model(self) -> None:
        advice = meter.advise(meters(8.0, 40.0, model_week=100.0), None, NOW, False)
        self.assertEqual(
            (advice.state, advice.wait_seconds, advice.meters_at_stop), ("parked", 3 * 86400 + 60, ["week (Fable)"])
        )
        self.assertEqual(meter.advise(meters(8.0, 40.0, model_week=98.0), None, NOW, False)[0], "near")
        self.assertEqual(meter.advise(meters(8.0, 40.0, model_week=85.0), None, NOW, False)[0], "watch")

    def test_stays_at_the_session_stop_point_that_it_reached_until_the_reset(self) -> None:
        self.assertEqual(meter.advise(meters(93.0), 0.02, NOW, True)[0::2], ("stop", 2 * 3600 + 60))
        self.assertEqual(meter.advise(meters(93.0), 0.02, NOW, False)[0], "watch")

    def test_waits_for_the_last_reset_of_the_meters_at_their_stop(self) -> None:
        both = meters(98.0, 99.0, week_reset=NOW + dt.timedelta(hours=1))
        self.assertEqual(meter.advise(both, 0.2, NOW, False)[0::2], ("stop", 2 * 3600 + 60))
        self.assertEqual(sorted(meter.advise(both, 0.2, NOW, False).meters_at_stop), ["session", "week"])
        self.assertEqual(meter.advise(meters(98.0, 99.0), 0.2, NOW, False)[0::2], ("parked", 3 * 86400 + 60))

    def test_reads_again_before_a_fast_rate_reaches_the_stop_point(self) -> None:
        # At 1 % a minute, 79 % reaches the stop point of 92 % in 13 minutes. The next reading comes after half of them.
        self.assertEqual(meter.advise(meters(79.0), 1.0, NOW, False)[0::2], ("go", 390))

    def test_reads_at_most_once_a_minute(self) -> None:
        self.assertEqual(meter.advise(meters(91.5), 1.0, NOW, False)[2], 60)

    def test_reads_at_the_default_rate_when_the_meter_did_not_rise(self) -> None:
        # At the default rate of 0.3 % a minute, 97 % reaches the stop point of 98 % in 200 seconds.
        self.assertEqual(meter.advise(meters(97.0), 0.0, NOW, False)[0::2], ("near", 100))


class RateTest(unittest.TestCase):
    def test_is_the_rise_per_minute_in_one_window(self) -> None:
        rate = meter.rate_per_minute(reading(NOW, 10.0), reading(NOW + dt.timedelta(minutes=30), 16.0))
        self.assertAlmostEqual(rate, 0.2)

    def test_counts_a_reset_time_that_moved_by_a_minute_as_the_same_window(self) -> None:
        later = reading(NOW + dt.timedelta(minutes=30), 16.0, "2026-10-18T04:29:00-04:00")
        self.assertAlmostEqual(meter.rate_per_minute(reading(NOW, 10.0), later), 0.2)

    def test_is_0_when_the_meter_fell(self) -> None:
        self.assertEqual(meter.rate_per_minute(reading(NOW, 10.0), reading(NOW + dt.timedelta(minutes=5), 9.0)), 0.0)

    def test_is_unknown_without_two_readings_of_one_window(self) -> None:
        later = NOW + dt.timedelta(minutes=90)
        cases = {
            "no earlier reading": (None, reading(later, 3.0)),
            "another window": (reading(NOW, 90.0), reading(later, 3.0, "2026-10-18T09:30:00-04:00")),
            "no reset time": (reading(NOW, 10.0, None), reading(later, 16.0)),
            "no session meter": ({"at": NOW.isoformat(), "meters": {}}, reading(later, 16.0)),
            "less than half a minute apart": (reading(NOW, 10.0), reading(NOW + dt.timedelta(seconds=29), 11.0)),
        }
        for case, (before, after) in cases.items():
            with self.subTest(case=case):
                self.assertIsNone(meter.rate_per_minute(before, after))


class AccountLineTest(MeterTest):
    def test_gives_each_meter_the_rate_the_stop_point_and_the_advice(self) -> None:
        self.assertEqual(
            self.run_meter()[1][0],
            "claude 03:08 | session 8% resets 04:30 | week 30% resets Sat 17:00 | rate unknown | stop at 98% | go",
        )
        self.clock.time += dt.timedelta(minutes=20)
        self.answer("claude", [usage_output(14)])
        self.assertEqual(
            self.run_meter()[1][0],
            "claude 03:28 | session 14% resets 04:30 | week 30% resets Sat 17:00 | rate 0.30%/min | stop at 98% | go",
        )

    def test_gives_the_weekly_meter_of_each_model(self) -> None:
        self.answer("claude", [usage_output(8, week=40, model_week=100)])
        self.assertEqual(
            self.run_meter()[1][0],
            "claude 03:08 | session 8% resets 04:30 | week 40% resets Sat 17:00 | week (Fable) 100% resets Sat 17:00 | "
            "rate unknown | stop at 98% | parked",
        )

    def test_stays_at_stop_until_the_reset_when_the_rate_falls(self) -> None:
        readings = [
            (dt.timedelta(minutes=2), 90, "watch"),
            (dt.timedelta(minutes=4), 92, "stop"),
            (dt.timedelta(minutes=54), 93, "stop"),
        ]
        for since_now, used, advice in readings:
            self.clock.time = NOW + since_now
            self.answer("claude", [usage_output(used, "5:12am")])
            self.assertTrue(self.run_meter()[1][0].endswith(f" | {advice}"), (since_now, advice))
        self.answer("claude", [usage_output(94, "5:12am")])
        self.assertTrue(self.run_meter("--wake")[1][0].endswith(" | stop"))
        self.assertEqual(self.clock.sleeps, [3000])
        self.clock.time = dt.datetime(2026, 10, 18, 5, 13, tzinfo=NEW_YORK)
        self.answer("claude", [usage_output(1, "10:12am")])
        self.assertTrue(self.run_meter()[1][0].endswith(" | go"))

    def test_adds_each_reading_to_the_log(self) -> None:
        self.run_meter()
        self.assertEqual(
            json.loads((self.loop / "meter.jsonl").read_text()),
            {
                "at": "2026-10-18T03:08:00-04:00",
                "account": "claude",
                "meters": {
                    "session": {"used": 8.0, "resets": "2026-10-18T04:30:00-04:00"},
                    "week": {"used": 30.0, "resets": "2026-10-24T17:00:00-04:00"},
                },
                "meters_at_stop": [],
            },
        )

    def test_takes_the_rate_from_the_last_reading_of_the_same_account(self) -> None:
        log = [reading(NOW - dt.timedelta(minutes=10), 5.0), {**reading(NOW, 1.0), "account": "other"}]
        (self.loop / "meter.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in log))
        self.assertIn("| rate 0.30%/min |", self.run_meter()[1][0])

    def test_gives_a_reset_without_its_day_when_it_is_less_than_20_hours_away(self) -> None:
        session = {"used": 5.0, "resets": (NOW + dt.timedelta(hours=20)).isoformat()}
        self.assertEqual(meter.describe(session, NOW), "5% resets 23:08")
        session["resets"] = (NOW + dt.timedelta(hours=20, minutes=1)).isoformat()
        self.assertEqual(meter.describe(session, NOW), "5% resets Sun 23:09")
        self.assertEqual(meter.describe({"used": 5.5, "resets": None}, NOW), "5.5%")


class ClaudeTest(MeterTest):
    def test_runs_usage_in_an_empty_folder_without_the_variables_of_the_session(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_TEST": "session"}):
            self.run_meter()
        [call] = self.calls("claude")
        self.assertEqual(call["args"], ["-p", "/usage"])
        self.assertEqual(call["files"], [])
        self.assertFalse(Path(call["cwd"]).exists())
        self.assertEqual(call["env"]["CLAUDECODE"], None)
        self.assertEqual(call["env"]["CLAUDE_CODE_TEST"], None)

    def test_gives_claude_the_variables_of_the_account(self) -> None:
        self.accounts({"name": "work", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "~/.claude-work"}})
        status, lines, _ = self.run_meter()
        self.assertEqual(status, 0)
        self.assertTrue(lines[0].startswith("work 03:08 | session 8%"))
        self.assertEqual(self.calls("claude")[0]["env"]["CLAUDE_CONFIG_DIR"], f"{self.root}/person/.claude-work")

    def test_says_why_an_output_has_no_meters(self) -> None:
        self.answer("claude", [{"stdout": "\nNot logged in · Please run /login\n"}])
        status, lines, _ = self.run_meter()
        self.assertEqual(status, 1)
        self.assertEqual(
            lines[0],
            'claude | no reading: claude -p /usage printed no usage meters, and began with "Not logged in · Please run '
            '/login". Run it in an empty folder to see why.',
        )
        self.answer("claude", [{"status": 1}])
        self.assertIn('and began with "nothing"', self.run_meter()[1][0])

    def test_says_when_claude_is_not_installed(self) -> None:
        self.without_standin("claude")
        status, lines, _ = self.run_meter()
        self.assertEqual(status, 1)
        self.assertEqual(
            lines[0], "claude | no reading: claude isn't installed. Install Claude Code, or remove the account."
        )

    def test_says_when_claude_does_not_answer_in_time(self) -> None:
        self.answer("claude", [{"sleep": 10}])
        with mock.patch.object(meter, "CLAUDE_TIMEOUT_SECONDS", 0.2):
            status, lines, _ = self.run_meter()
        self.assertEqual(status, 1)
        self.assertEqual(
            lines[0],
            "claude | no reading: claude -p /usage didn't end within 0.2 seconds. Run it in an empty folder to see why.",
        )


class CodexTest(MeterTest):
    def week(self, used: float, resets_at: dt.datetime) -> dict[str, Any]:
        return {"used_percent": used, "window_minutes": 10080, "resets_at": int(resets_at.timestamp())}

    def test_reads_the_newest_snapshot_in_the_session_files(self) -> None:
        week_reset = dt.datetime(2026, 10, 21, 9, 0, tzinfo=NEW_YORK)
        session = {
            "used_percent": 12.0,
            "window_minutes": 300,
            "resets_at": int((NOW + dt.timedelta(hours=1)).timestamp()),
        }
        self.codex_home(("2026-10-18T06:00:00Z", {"primary": self.week(40.0, week_reset)}))
        self.codex_home()
        home = self.codex_home(
            ("2026-10-18T06:30:00Z", {"primary": self.week(44.0, week_reset)}),
            ("2026-10-18T06:50:00.500Z", {"primary": session, "secondary": self.week(45.0, week_reset)}),
            ("2026-10-18T06:55:00Z", None),
        )
        self.accounts({"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}})
        status, lines, _ = self.run_meter()
        self.assertEqual(status, 0)
        self.assertEqual(
            lines[0],
            "codex 03:08 | session 12% resets 04:08 | week 45% resets Wed 09:00 | rate unknown | stop at 98% | "
            "snapshot 18 min old | go",
        )

    def test_reads_a_meter_whose_window_reset_after_its_snapshot_as_unused(self) -> None:
        home = self.codex_home(("2026-10-18T06:00:00Z", {"primary": self.week(97.0, NOW - dt.timedelta(minutes=1))}))
        self.accounts({"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}})
        self.assertEqual(self.run_meter()[1][0], "codex 03:08 | week 0% | snapshot 68 min old | go")

    def test_reads_a_meter_without_a_reset_time(self) -> None:
        home = self.codex_home(("2026-10-18T06:00:00Z", {"primary": {"used_percent": 3.0, "window_minutes": 10080}}))
        self.accounts({"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}})
        self.assertEqual(self.run_meter()[1][0], "codex 03:08 | week 3% | snapshot 68 min old | go")

    def test_says_when_codex_wrote_no_snapshot_in_the_last_8_days(self) -> None:
        home = self.codex_home(
            ("2026-10-10T06:00:00Z", {"primary": self.week(10.0, NOW)}), age=dt.timedelta(days=8, seconds=1)
        )
        self.accounts({"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}})
        status, lines, _ = self.run_meter()
        self.assertEqual(status, 1)
        self.assertEqual(
            lines[0],
            f"codex | no reading: Codex wrote no usage snapshot in {home}/sessions in the last 8 days. Run codex once, "
            "and read again.",
        )

    def test_reads_codex_in_its_default_home(self) -> None:
        self.accounts({"name": "codex", "tool": "codex"})
        self.assertIn(f"snapshot in {self.root}/person/.codex/sessions in", self.run_meter()[1][0])


class AccountsTest(MeterTest):
    def test_reads_each_account_in_the_file(self) -> None:
        self.accounts({"name": "one", "tool": "claude"}, {"name": "two", "tool": "claude"})
        status, lines, _ = self.run_meter()
        self.assertEqual((status, [line.split(" ")[0] for line in lines]), (0, ["one", "two", "machine"]))

    def test_stops_on_a_file_that_is_wrong(self) -> None:
        example = '{"accounts": [{"name": "claude", "tool": "claude"}]}'
        file = self.loop / "accounts.json"
        cases = {
            "{": f"error: {file} isn't valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 "
            f"(char 1). Fix it, as in {example}.",
            "[]": f'error: {file} has no list of accounts under "accounts". Add one, as in {example}.',
            '{"accounts": []}': f'error: {file} has no list of accounts under "accounts". Add one, as in {example}.',
            '{"accounts": [{"name": "codex"}]}': f'error: {file} lists the account {{"name": "codex"}}, which needs a '
            '"name" and a "tool" that is "claude" or "codex". Fix the account.',
            '{"accounts": [{"name": "x", "tool": "claude", "env": {"A": 1}}]}': f"error: {file} gives the account x an "
            '"env" that isn\'t an object of strings, such as {"CODEX_HOME": "~/.codex"}. Fix the account.',
        }
        for content, error in cases.items():
            with self.subTest(content=content):
                file.write_text(content)
                self.assertEqual(self.run_meter(), (1, [], error + "\n"))


class WakeTest(MeterTest):
    def test_wakes_when_the_advice_changes(self) -> None:
        self.answer("claude", [usage_output(8), usage_output(9), usage_output(10, week=85)])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.clock.sleeps), (0, [1800, 1800]))
        self.assertTrue(lines[0].startswith("claude 04:08 | session 10% ") and lines[0].endswith(" | watch"))

    def test_wakes_every_50_minutes_while_an_account_waits_for_its_reset(self) -> None:
        self.answer("claude", [usage_output(99, "7:30am")])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.clock.sleeps), (0, [3000]))
        self.assertTrue(lines[0].startswith("claude 03:58 | session 99% ") and lines[0].endswith(" | stop"))

    def test_counts_the_50_minutes_from_the_start_while_another_account_reads_more_often(self) -> None:
        home = self.codex_home(("2026-10-18T07:00:00Z", {"primary": {"used_percent": 3.0, "window_minutes": 10080}}))
        self.accounts(
            {"name": "claude", "tool": "claude"}, {"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}}
        )
        self.answer("claude", [usage_output(99, "7:30am")])
        self.assertEqual(self.run_meter("--wake")[0], 0)
        self.assertEqual(self.clock.sleeps, [1800, 1200])

    def test_wakes_just_after_a_reset_that_comes_sooner(self) -> None:
        self.answer("claude", [usage_output(99, "3:18am"), usage_output(1, "8:18am")])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.clock.sleeps), (0, [660]))
        self.assertTrue(lines[0].endswith(" | go"))

    def test_waits_for_the_reset_of_a_parked_account_without_waking(self) -> None:
        week_full = usage_output(10, week=99)
        self.answer("claude", [week_full, usage_output(1, week=0)])
        status, lines, _ = self.run_meter("--wake")
        reset = dt.datetime(2026, 10, 24, 17, 0, tzinfo=NEW_YORK)
        self.assertEqual((status, self.clock.sleeps), (0, [(reset - NOW).total_seconds() + 60]))
        self.assertTrue(lines[0].endswith(" | go"))

    def test_ends_when_an_account_gives_no_reading(self) -> None:
        self.answer("claude", [usage_output(8), {"stdout": "Not logged in"}])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.clock.sleeps), (1, [1800]))
        self.assertTrue(lines[0].startswith("claude | no reading: "))

    def test_ends_at_once_when_the_first_reading_fails(self) -> None:
        self.answer("claude", [{"stdout": "Not logged in"}])
        self.assertEqual((self.run_meter("--wake")[0], self.clock.sleeps), (1, []))


class MachineLineTest(MeterTest):
    def test_gives_the_disk_the_load_the_context_and_the_retrospective(self) -> None:
        self.assertEqual(
            meter.machine_line(
                94.4, 3.25, 10, 210_500, "retrospective due: 31.5 hours of agents' work since the last one"
            ),
            "machine | disk 94 GB free | load 3.2 on 10 cores | orchestrator context 210k | "
            "retrospective due: 31.5 hours of agents' work since the last one",
        )

    def test_is_the_last_line(self) -> None:
        self.assertRegex(
            self.run_meter()[1][-1],
            r"^machine \| disk \d+ GB free \| load \d+\.\d on \d+ cores \| orchestrator context unknown \| "
            r"retrospective not due: 0\.0 of 30 hours of agents' work since the last one$",
        )


class ContextTest(MeterTest):
    def transcript(self, session: str) -> Path:
        project = "".join(character if character.isalnum() else "-" for character in str(paths.main_checkout()))
        transcript = self.root / "claude-config" / "projects" / project / f"{session}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        return transcript

    def context(self) -> str:
        return self.run_meter()[1][-1].split(" | ")[3]

    def test_reads_the_input_of_the_last_call_in_the_transcript_of_the_orchestrator(self) -> None:
        (self.loop / "state.md").write_text("# Loop state\nsession: abc123\npid: 42\n")
        calls = [
            {"type": "assistant", "message": {"usage": {"input_tokens": 3, "cache_read_input_tokens": 1000}}},
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 5,
                        "cache_read_input_tokens": 200_000,
                        "cache_creation_input_tokens": 10_500,
                    }
                },
            },
        ]
        lines = [json.dumps(call) for call in calls]
        lines += ['{"type": "user", "usage": broken', '{"usage": 1}', '{"message": "text", "usage": 1}', "{}"]
        self.transcript("abc123").write_text("\n".join(lines) + "\n")
        self.assertEqual(self.context(), "orchestrator context 210k")

    def test_reads_the_last_call_of_a_transcript_longer_than_the_end_that_it_reads(self) -> None:
        (self.loop / "state.md").write_text("session: long\n")
        start = json.dumps({"type": "user", "message": {"content": "x" * 500_000}})
        call = json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 99_000}}})
        self.transcript("long").write_text(f"{start}\n{call}\n")
        self.assertEqual(self.context(), "orchestrator context 99k")

    def test_is_unknown_without_a_session_or_its_transcript(self) -> None:
        cases = {
            "no state file": None,
            "no session line": "pid: 42\n",
            "an empty session line": "session:\n",
            "no transcript": "session: missing\n",
            "no call": "session: empty\n",
        }
        self.transcript("empty").write_text('{"type": "user"}\n')
        for case, state in cases.items():
            with self.subTest(case=case):
                if state is not None:
                    (self.loop / "state.md").write_text(state)
                self.assertEqual(self.context(), "orchestrator context unknown")


def new_york(time: str) -> dt.datetime:
    return dt.datetime.fromisoformat(time).replace(tzinfo=NEW_YORK)


def agent(
    agent_id: str,
    started: str,
    minutes: float | None,
    agent_type: str = "builder",
    is_test: bool = False,
    periods: list[tuple[str, str]] | None = None,
) -> list[str]:
    """The lines of .loop/agents.jsonl for an agent that started at a time in New York, with an end line unless
    minutes is None. The agent worked from its start for the minutes, unless the periods say otherwise."""
    start = {"at": new_york(started).isoformat(), "event": "start", "id": agent_id, "type": agent_type, "test": is_test}
    lines = [json.dumps(start)]
    if minutes is None:
        return lines
    if periods is None:
        periods = [(started, (new_york(started) + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S"))]
    end = {
        "at": new_york(started).isoformat(),
        "event": "end",
        "id": agent_id,
        "minutes": minutes,
        "periods": [[new_york(begin).isoformat(), new_york(finish).isoformat()] for begin, finish in periods],
    }
    lines.append(json.dumps(end))
    return lines


class RetrospectiveTest(MeterTest):
    """The last retrospective merged at 1:00 in New York, unless a test says otherwise."""

    def retrospective(self, *agents: list[str]) -> str:
        lines = [line for lines in agents for line in lines]
        (self.loop / "agents.jsonl").write_text("".join(f"{line}\n" for line in lines))
        return self.run_meter()[1][-1].split(" | ")[4]

    def test_is_due_once_one_agent_worked_30_hours_since_the_last_one(self) -> None:
        cases = {
            1800: "retrospective due: 30.0 hours of agents' work since the last one",
            1799: "retrospective not due: 29.9 of 30 hours of agents' work since the last one",
        }
        for minutes, note in cases.items():
            with self.subTest(minutes=minutes):
                self.assertEqual(self.retrospective(agent("a", "2026-10-18T01:30:00", minutes)), note)

    def test_adds_up_the_work_of_every_type_of_agent(self) -> None:
        agents = [
            agent("a", "2026-10-18T01:00:00", 600),
            agent("b", "2026-10-18T01:30:00", 600, "researcher"),
            agent("c", "2026-10-18T02:00:00", 600, "reviewer"),
            agent("d", "2026-10-18T02:00:00", 1, "codex"),
        ]
        self.assertEqual(
            self.retrospective(*agents), "retrospective due: 30.0 hours of agents' work since the last one"
        )

    def test_counts_the_last_end_of_an_agent_that_ended_again_after_a_message(self) -> None:
        first_end = agent("a", "2026-10-18T01:30:00", 1000)
        again = agent("a", "2026-10-18T01:30:00", 1200)[1]
        self.assertEqual(
            self.retrospective([*first_end, again]),
            "retrospective not due: 20.0 of 30 hours of agents' work since the last one",
        )

    def test_leaves_out_test_builds_auditors_and_work_before_the_last_one(self) -> None:
        agents = [
            agent("test", "2026-10-18T01:30:00", 600, is_test=True),
            agent("auditor", "2026-10-18T01:30:00", 600, "auditor"),
            agent("before", "2026-10-17T20:00:00", 300),
            agent("counted", "2026-10-18T01:00:00", 60),
        ]
        self.assertEqual(
            self.retrospective(*agents), "retrospective not due: 1.0 of 30 hours of agents' work since the last one"
        )

    def test_counts_the_work_after_the_last_one_of_an_agent_that_started_before_it(self) -> None:
        periods = [
            ("2026-10-17T23:00:00", "2026-10-18T00:30:00"),
            ("2026-10-18T00:40:00", "2026-10-18T01:30:00"),
            ("2026-10-18T02:00:00", "2026-10-18T02:15:00"),
        ]
        self.assertEqual(
            self.retrospective(agent("a", "2026-10-17T23:00:00", 155, periods=periods)),
            "retrospective not due: 0.7 of 30 hours of agents' work since the last one",
        )

    def test_counts_an_agent_of_a_session_without_an_end_line_until_the_last_line_of_its_transcript(self) -> None:
        project = "".join(character if character.isalnum() else "-" for character in str(paths.main_checkout()))
        agents = self.root / "claude-config" / "projects" / project / "session-1" / "subagents"
        start = new_york("2026-10-18T00:30:00").astimezone(dt.UTC)
        write_transcript(
            agents / "agent-a1.jsonl",
            [
                {**prompt_line(0), "timestamp": start.isoformat()},
                {**call_line("a", 0), "timestamp": (start + dt.timedelta(minutes=50)).isoformat()},
                {**call_line("b", 0), "timestamp": (start + dt.timedelta(minutes=90)).isoformat()},
            ],
        )
        (agents / "agent-a1.meta.json").write_text(json.dumps({"agentType": "builder", "toolUseId": "toolu_1"}))
        (agents / "agent-a2.meta.json").write_text(json.dumps({"agentType": "builder"}))
        self.assertEqual(
            self.retrospective(agent("toolu_1", "2026-10-18T00:30:00", None)),
            "retrospective not due: 1.0 of 30 hours of agents' work since the last one",
        )

    def test_counts_a_run_without_an_end_line_until_the_last_line_of_its_events(self) -> None:
        events = self.loop / "runs" / "issue-12-run" / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text('{"type": "turn.started"}\n')
        last_line = new_york("2026-10-18T02:30:00").timestamp()
        os.utime(events, (last_line, last_line))
        self.assertEqual(
            self.retrospective(
                agent("issue-12-run", "2026-10-18T00:00:00", None), agent("other-run", "2026-10-18T01:00:00", None)
            ),
            "retrospective not due: 1.5 of 30 hours of agents' work since the last one",
        )

    def test_counts_the_minutes_of_an_end_line_without_periods_from_the_agents_start(self) -> None:
        start = json.dumps(
            {"at": "2026-10-18T00:30:00-04:00", "event": "start", "id": "a", "type": "builder", "test": False}
        )
        end = json.dumps({"at": "2026-10-18T03:00:00-04:00", "event": "end", "id": "a", "minutes": 120})
        self.assertEqual(
            self.retrospective([start, end]),
            "retrospective not due: 1.5 of 30 hours of agents' work since the last one",
        )

    def test_starts_at_the_newest_retrospective(self) -> None:
        self.answer(
            "gh", github_answer(merges=("2026-10-11T09:00:00Z", "2026-10-18T05:00:00Z", "2026-10-15T09:00:00Z"))
        )
        agents = [agent("a", "2026-10-16T12:00:00", 1800), agent("b", "2026-10-18T02:00:00", 90)]
        self.assertEqual(
            self.retrospective(*agents), "retrospective not due: 1.5 of 30 hours of agents' work since the last one"
        )
        (repository,) = self.calls("gh")
        self.assertEqual(repository["args"][:4], ["api", "graphql", "-f", f"query={meter.REPOSITORY_QUERY}"])
        self.assertEqual(
            repository["args"][4:], ["-F", "owner={owner}", "-F", "name={repo}", "-F", "label=retrospective"]
        )
        self.assertEqual(Path(repository["cwd"]), paths.main_checkout())

    def test_counts_the_whole_log_before_the_first_retrospective(self) -> None:
        self.answer("gh", github_answer(merges=()))
        agents = [agent("a", "2026-09-01T12:00:00", 1500), agent("b", "2026-10-18T02:00:00", 300)]
        self.assertEqual(
            self.retrospective(*agents), "retrospective due: 30.0 hours of agents' work with no retrospective yet"
        )
        self.assertEqual(
            self.retrospective(), "retrospective not due: 0.0 of 30 hours of agents' work with no retrospective yet"
        )

    def test_is_not_due_after_a_release_alone(self) -> None:
        release = {"tagName": "v1.2.0", "publishedAt": "2026-10-18T06:00:00Z"}
        self.answer("gh", github_answer(latestRelease=release))
        self.assertEqual(
            self.retrospective(agent("a", "2026-10-18T01:30:00", 60)),
            "retrospective not due: 1.0 of 30 hours of agents' work since the last one",
        )

    def test_is_unknown_when_gh_fails(self) -> None:
        self.answer("gh", {"status": 1, "stderr": "HTTP 401: Bad credentials\n(https://api.github.com)\n"})
        self.assertEqual(
            self.retrospective(),
            "retrospective unknown: gh api graphql failed: HTTP 401: Bad credentials (https://api.github.com). Check the "
            "network, and that gh auth status shows a login.",
        )

    def test_is_unknown_when_gh_is_not_installed(self) -> None:
        self.without_standin("gh")
        self.assertEqual(self.retrospective(), "retrospective unknown: gh isn't installed. Install the GitHub CLI.")


class HelpTest(unittest.TestCase):
    def test_gives_the_hours_of_work_that_make_a_retrospective_due(self) -> None:
        printed = io.StringIO()
        with (
            mock.patch.object(meter, "HOURS_PER_RETROSPECTIVE", 12.5),
            contextlib.redirect_stdout(printed),
            self.assertRaises(SystemExit),
        ):
            meter.main(["--help"])
        self.assertIn("once the agents have worked 12.5 hours since", " ".join(printed.getvalue().split()))


class ScriptTest(MeterTest):
    def test_runs_as_a_script(self) -> None:
        done = subprocess.run(
            [sys.executable, str(METER)], capture_output=True, text=True, env=dict(os.environ), check=False, timeout=60
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stdout, r"^claude \d\d:\d\d \| session 8% .* \| go\nmachine \| ")
