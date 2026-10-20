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
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import account_room
import paths
from machine import CpuTicks, CpuTicksError, Machine
from test_transcripts import write_transcript

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


def meters_of(output: dict[str, str]) -> dict[str, Any]:
    """The meters of an answer of claude -p /usage, read at NOW."""
    found: dict[str, Any] = meter.parse_usage(output["stdout"], NOW)
    return found


class FakeClock:
    """The clock for the meter's waits: a sleep moves the time on at once, and then runs each alarm whose time came."""

    def __init__(self) -> None:
        self.time = NOW
        self.sleeps: list[float] = []
        self.alarms: list[tuple[dt.datetime, Callable[[], None]]] = []

    def now(self) -> dt.datetime:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += dt.timedelta(seconds=seconds)
        for alarm in [alarm for alarm in self.alarms if alarm[0] <= self.time]:
            self.alarms.remove(alarm)
            alarm[1]()


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
        # A reading of the models from now, so that the meter sends no requests for them
        (self.loop / "models.json").write_text(json.dumps({"at": NOW.isoformat(), "models": {}, "problems": {}}))

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


# The minutes of earlier runs: a long run of a reviewer takes 10 minutes, of a builder 30 and of a builder-deep 60.
RUNS = {"reviewer": [10.0], "builder": [20.0, 30.0], "builder-deep": [60.0]}
TYPES = ["builder", "builder-deep", "reviewer"]
SLOW = {"session": 0.05, "week": 0.005}
FAST = {"session": 0.2, "week": 0.05}


def room(
    session: float | None = 40.0,
    week: float = 30.0,
    per_agent_minute: dict[str, float] = SLOW,
    running: tuple[tuple[str, float], ...] = (),
    orchestrator_share: float = 0.0,
    **meter_options: Any,
) -> account_room.Room:
    found = meters(session, week, **meter_options)
    rates = {name: per_agent_minute[name] for name in found}
    agents = [account_room.Running(agent_type, minutes) for agent_type, minutes in running]
    return account_room.Room(found, rates, agents, orchestrator_share)


def advice_text(account: account_room.Room, types: list[str] = TYPES) -> str:
    text: str = meter.advise(account, types, RUNS, NOW).text
    return text


class AdviseTest(unittest.TestCase):
    def test_starts_every_type_whose_run_fits_in_the_room_at_a_slow_rate(self) -> None:
        # Each run takes at most 3 points, and 5 are left.
        self.assertEqual(advice_text(room(95.0)), "go")

    def test_starts_only_the_shorter_types_at_a_fast_rate(self) -> None:
        # A reviewer's run takes 2 points, a builder's 6 and a builder-deep's 12.
        self.assertEqual(advice_text(room(80.0, per_agent_minute=FAST)), "go")
        self.assertEqual(advice_text(room(89.0, per_agent_minute=FAST)), "builder, reviewer")
        self.assertEqual(advice_text(room(95.0, per_agent_minute=FAST)), "reviewer")
        self.assertEqual(advice_text(room(98.5, per_agent_minute=FAST)), "stop")

    def test_keeps_the_room_that_the_running_agents_still_need(self) -> None:
        # A builder that just started needs 30 more minutes, 6 points. A builder-deep at 50 minutes needs 10, 2 points.
        running = (("builder", 0.0), ("builder-deep", 50.0))
        self.assertEqual(advice_text(room(79.5, per_agent_minute=FAST, running=running)), "go")
        self.assertEqual(advice_text(room(82.0, per_agent_minute=FAST, running=running)), "builder, reviewer")

    def test_counts_how_much_longer_the_runs_that_ran_as_long_as_an_agent_went_on(self) -> None:
        # A builder at 25 minutes needs 5 more, 1 point, because only the run of 30 minutes ran that long.
        running = (("builder", 25.0),)
        self.assertEqual(advice_text(room(86.5, per_agent_minute=FAST, running=running)), "go")
        self.assertEqual(advice_text(room(87.1, per_agent_minute=FAST, running=running)), "builder, reviewer")

    def test_counts_nothing_more_for_an_agent_that_ran_longer_than_every_earlier_run(self) -> None:
        running = (("builder", 45.0),)
        self.assertEqual(advice_text(room(87.5, per_agent_minute=FAST, running=running)), "go")

    def test_counts_only_the_usage_before_the_5_hour_reset(self) -> None:
        # 5 minutes before the reset, each run takes 1 point.
        close = room(95.0, per_agent_minute=FAST, session_reset=NOW + dt.timedelta(minutes=5))
        self.assertEqual(advice_text(close), "go")
        self.assertEqual(advice_text(room(95.0, per_agent_minute=FAST)), "reviewer")

    def test_starts_by_the_room_of_a_weekly_meter_at_93_percent(self) -> None:
        # Of the week, a builder's run takes 1.5 points and a builder-deep's 3. A builder that just started needs 1.5,
        # and a builder-deep 3.
        self.assertEqual(advice_text(room(10.0, 93.0, FAST)), "go")
        running = (("builder", 0.0), ("builder-deep", 0.0))
        self.assertEqual(advice_text(room(10.0, 92.9, FAST, running)), "builder, reviewer")

    def test_never_lets_a_weekly_meter_pass_99_percent(self) -> None:
        # Of the week, a reviewer's run takes 0.05 points, a builder's 0.15 and a builder-deep's 0.3.
        self.assertEqual(advice_text(room(10.0, 98.65)), "go")
        self.assertEqual(advice_text(room(10.0, 98.8)), "builder, reviewer")
        self.assertEqual(advice_text(room(10.0, 98.9)), "reviewer")
        self.assertEqual(advice_text(room(10.0, 98.96)), "stop")

    def test_starts_nothing_on_a_meter_at_its_limit_even_when_it_measured_no_usage(self) -> None:
        unused = {"session": 0.0, "week": 0.0}
        self.assertEqual(advice_text(room(100.0, per_agent_minute=unused)), "stop")

    def test_advises_an_account_with_only_a_weekly_meter(self) -> None:
        runs = {"codex": [40.0]}
        self.assertEqual(meter.advise(room(None, 96.9, FAST), ["codex"], runs, NOW).text, "go")
        self.assertEqual(meter.advise(room(None, 97.1, FAST), ["codex"], runs, NOW).text, "stop")

    def test_keeps_the_orchestrators_own_need_until_the_weekly_reset(self) -> None:
        # Working half its time, the orchestrator needs 0.5 * 0.005 * 2000 minutes = 5 points of the week.
        week_reset = NOW + dt.timedelta(minutes=2000)
        self.assertEqual(advice_text(room(10.0, 93.65, week_reset=week_reset, orchestrator_share=0.5)), "go")
        self.assertEqual(
            advice_text(room(10.0, 93.8, week_reset=week_reset, orchestrator_share=0.5)), "builder, reviewer"
        )
        self.assertEqual(advice_text(room(10.0, 94.0, week_reset=week_reset, orchestrator_share=0.5)), "stop")
        self.assertEqual(advice_text(room(10.0, 94.0, week_reset=week_reset)), "go")

    def test_takes_the_runs_of_all_types_for_a_type_without_one(self) -> None:
        # A long run of all runs takes 60 minutes, 3 points.
        self.assertEqual(advice_text(room(96.9), ["researcher"]), "go")
        self.assertEqual(advice_text(room(97.1), ["researcher"]), "stop")

    def test_reads_every_30_minutes_while_every_type_fits_and_every_10_while_some_do(self) -> None:
        self.assertEqual(meter.advise(room(40.0), TYPES, RUNS, NOW).wait_seconds, 1800)
        self.assertEqual(meter.advise(room(95.0, per_agent_minute=FAST), TYPES, RUNS, NOW).wait_seconds, 600)

    def test_reads_again_before_half_the_time_until_the_longest_type_that_fits_may_no_longer_fit(self) -> None:
        # Two agents use 0.4 points a minute. Of the 20 points left, a builder that just started needs 6, and a
        # builder-deep's run takes 12. The 2 points left last 5 minutes, and the meter reads again after half of them.
        running = (("reviewer", 10.0), ("builder", 0.0))
        self.assertEqual(meter.advise(room(80.0, 30.0, FAST, running), TYPES, RUNS, NOW).wait_seconds, 150)

    def test_stops_until_the_reset_of_the_meter_that_no_type_fits_or_10_minutes_while_agents_run(self) -> None:
        self.assertEqual(meter.advise(room(99.6), TYPES, RUNS, NOW)[0::2], ("stop", []))
        self.assertEqual(meter.advise(room(99.6), TYPES, RUNS, NOW).wait_seconds, 2 * 3600 + 60)
        unknown = room(99.6, session_reset=None)
        self.assertEqual(meter.advise(unknown, TYPES, RUNS, NOW).wait_seconds, 600)
        running = room(99.6, running=(("builder", 5.0),))
        self.assertEqual(meter.advise(running, TYPES, RUNS, NOW).wait_seconds, 600)

    def test_stops_at_99_percent_of_the_week_less_than_10_hours_before_its_reset(self) -> None:
        week_reset = NOW + dt.timedelta(hours=10)
        self.assertEqual(meter.advise(room(10.0, 99.0, week_reset=week_reset), TYPES, RUNS, NOW)[0:2], ("stop", 36060))

    def test_parks_at_99_percent_of_the_week_more_than_10_hours_before_its_reset(self) -> None:
        week_reset = NOW + dt.timedelta(hours=10, seconds=1)
        parked = meter.advise(room(10.0, 99.0, week_reset=week_reset), TYPES, RUNS, NOW)
        self.assertEqual(parked[0:2], ("parked", 36061))
        self.assertEqual(meter.advise(room(10.0, 99.0, week_reset=None), TYPES, RUNS, NOW)[0:2], ("parked", 600))

    def test_parks_at_99_percent_of_the_weekly_meter_of_a_model(self) -> None:
        found = meters(8.0, 40.0, model_week=99.0)
        rates = {name: 0.01 for name in found}
        parked = meter.advise(account_room.Room(found, rates, [], 0.0), TYPES, RUNS, NOW)
        self.assertEqual(parked[0:2], ("parked", 3 * 86400 + 60))


class AccountLineTest(MeterTest):
    def log_agent(self, account: str, start: dt.datetime, end: dt.datetime) -> None:
        """Logs an agent of the account that worked from the start to the end."""
        lines = [
            {
                "at": start.isoformat(),
                "event": "start",
                "id": "a1",
                "type": "builder",
                "test": False,
                "account": account,
            },
            {
                "at": end.isoformat(),
                "event": "end",
                "id": "a1",
                "minutes": (end - start).total_seconds() / 60,
                "periods": [[start.isoformat(), end.isoformat()]],
            },
        ]
        (self.loop / "agents.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))

    def test_gives_each_meter_its_usage_per_agent_minute_and_the_advice(self) -> None:
        self.assertEqual(
            self.run_meter()[1][0],
            "claude 03:08 | session 8% resets 04:30 | week 30% resets Sat 17:00 | "
            "per agent-minute session 0.12%, week 0.02% | go",
        )

    def test_measures_the_rise_of_each_meter_over_the_minutes_that_the_accounts_agents_worked(self) -> None:
        self.run_meter()
        self.log_agent("claude", NOW, NOW + dt.timedelta(minutes=30))
        self.clock.time = NOW + dt.timedelta(minutes=30)
        self.answer("claude", [usage_output(14)])
        self.assertIn(" | per agent-minute session 0.2%, week 0.02% | ", self.run_meter()[1][0])
        self.assertEqual(json.loads(self.log_lines()[-1])["per_agent_minute"], {"session": 0.2})

    def test_takes_the_last_usage_per_agent_minute_that_the_account_measured_while_no_agent_works(self) -> None:
        self.run_meter()
        self.log_agent("claude", NOW, NOW + dt.timedelta(minutes=30))
        self.clock.time = NOW + dt.timedelta(minutes=30)
        self.answer("claude", [usage_output(14)])
        self.run_meter()
        self.clock.time = NOW + dt.timedelta(minutes=65)
        self.answer("claude", [usage_output(20)])
        self.assertIn(" | per agent-minute session 0.2%, week 0.02% | ", self.run_meter()[1][0])

    def test_counts_the_minutes_of_an_agent_that_runs_on_the_account_and_not_of_one_whose_process_ended(self) -> None:
        self.run_meter()
        ended = subprocess.Popen([sys.executable, "-S", "-c", ""])
        ended.wait()
        starts = [
            {"at": NOW.isoformat(), "event": "start", "id": agent_id, "type": "builder", "test": False}
            | {"account": "claude", "pid": pid}
            for agent_id, pid in (("a1", os.getpid()), ("a2", ended.pid))
        ]
        (self.loop / "agents.jsonl").write_text("".join(json.dumps(start) + "\n" for start in starts))
        self.clock.time = NOW + dt.timedelta(minutes=30)
        self.answer("claude", [usage_output(14)])
        self.assertIn(" | per agent-minute session 0.2%, week 0.02% | ", self.run_meter()[1][0])

    def test_measures_nothing_from_a_reading_more_than_60_minutes_old(self) -> None:
        self.run_meter()
        self.log_agent("claude", NOW, NOW + dt.timedelta(minutes=61))
        self.clock.time = NOW + dt.timedelta(minutes=61)
        self.answer("claude", [usage_output(14)])
        self.assertIn(" | per agent-minute session 0.12%, week 0.02% | ", self.run_meter()[1][0])

    def test_counts_the_agents_of_the_account_only(self) -> None:
        self.run_meter()
        self.log_agent("other", NOW, NOW + dt.timedelta(minutes=30))
        self.clock.time = NOW + dt.timedelta(minutes=30)
        self.answer("claude", [usage_output(14)])
        self.assertIn(" | per agent-minute session 0.12%, week 0.02% | ", self.run_meter()[1][0])

    def test_counts_the_orchestrators_work_and_its_need_until_the_weekly_reset_on_its_own_account(self) -> None:
        project = "".join(character if character.isalnum() else "-" for character in str(paths.main_checkout()))
        transcript = self.root / "claude-config" / "projects" / project / "orchestrator.jsonl"
        call = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1000}}}
        write_transcript(
            transcript,
            [
                {
                    "type": "user",
                    "timestamp": (NOW - dt.timedelta(minutes=60)).isoformat(),
                    "message": {"content": "go"},
                },
                call | {"timestamp": (NOW - dt.timedelta(minutes=59)).isoformat()},
                call | {"timestamp": (NOW - dt.timedelta(minutes=15)).isoformat()},
            ],
        )
        (self.loop / "state.md").write_text("session: orchestrator\n")
        earlier = {
            "at": (NOW - dt.timedelta(minutes=30)).isoformat(),
            "account": "claude",
            "meters": meters_of(usage_output(2)),
        }
        (self.loop / "meter.jsonl").write_text(json.dumps(earlier) + "\n")
        # The orchestrator worked 15 of the last 30 minutes, while the 5-hour meter rose 6 points, and 45 of the last
        # 60. At 0.02 points of the week a minute, it needs 0.75 * 0.02 * 9472 minutes until the weekly reset, 142
        # points, which is more than the week has left.
        line = self.run_meter()[1][0]
        self.assertIn(" | per agent-minute session 0.4%, week 0.02% | ", line)
        self.assertTrue(line.endswith(" | stop"))

    def test_gives_the_weekly_meter_of_each_model(self) -> None:
        self.answer("claude", [usage_output(8, week=40, model_week=100)])
        self.assertEqual(
            self.run_meter()[1][0],
            "claude 03:08 | session 8% resets 04:30 | week 40% resets Sat 17:00 | week (Fable) 100% resets Sat 17:00 | "
            "per agent-minute session 0.12%, week 0.02%, week (Fable) 0.02% | parked",
        )

    def test_stops_until_the_5_hour_reset_and_goes_after_it(self) -> None:
        self.answer("claude", [usage_output(98, "5:12am")])
        self.assertTrue(self.run_meter("--wake")[1][0].endswith(" | stop"))
        self.assertEqual(sum(self.clock.sleeps), 3000)
        self.clock.time = dt.datetime(2026, 10, 18, 5, 13, tzinfo=NEW_YORK)
        self.answer("claude", [usage_output(1, "10:12am")])
        self.assertTrue(self.run_meter()[1][0].endswith(" | go"))

    def test_adds_each_reading_to_the_log(self) -> None:
        self.run_meter()
        machine, entry = map(json.loads, self.log_lines())
        self.assertEqual(set(machine.pop("machine")), set(Machine.__annotations__))
        self.assertEqual(machine, {"at": "2026-10-18T03:08:00-04:00"})
        self.assertEqual(
            entry,
            {
                "at": "2026-10-18T03:08:00-04:00",
                "account": "claude",
                "meters": {
                    "session": {"used": 8.0, "resets": "2026-10-18T04:30:00-04:00"},
                    "week": {"used": 30.0, "resets": "2026-10-24T17:00:00-04:00"},
                },
                "per_agent_minute": {},
            },
        )

    def log_lines(self) -> list[str]:
        return (self.loop / "meter.jsonl").read_text().splitlines()

    def test_takes_the_last_usage_per_agent_minute_of_the_same_account(self) -> None:
        log = [
            {"at": (NOW - dt.timedelta(hours=3)).isoformat(), "account": "claude", "meters": {}},
            {"at": (NOW - dt.timedelta(hours=2)).isoformat(), "account": "claude", "per_agent_minute": {"week": 0.01}},
            {"at": (NOW - dt.timedelta(hours=1)).isoformat(), "account": "other", "per_agent_minute": {"week": 0.3}},
        ]
        (self.loop / "meter.jsonl").write_text("".join(json.dumps(entry | {"meters": {}}) + "\n" for entry in log))
        self.assertIn(" | per agent-minute session 0.12%, week 0.01% | ", self.run_meter()[1][0])

    def test_names_the_account_with_the_lowest_5_hour_meter_for_each_type_other_than_the_auditor(self) -> None:
        self.accounts({"name": "one", "tool": "claude"}, {"name": "two", "tool": "claude"})
        self.answer("claude", [usage_output(40), usage_output(20)])
        self.assertEqual(
            self.run_meter()[1][2],
            "start on | builder two | builder-deep two | builder-small two | researcher two | reviewer two",
        )

    def test_names_no_account_for_a_type_that_fits_on_none(self) -> None:
        self.answer("claude", [usage_output(99)])
        self.assertEqual(
            self.run_meter()[1][1],
            "start on | builder no account | builder-deep no account | builder-small no account | "
            "researcher no account | reviewer no account",
        )

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
            "codex 03:08 | session 12% resets 04:08 | week 45% resets Wed 09:00 | "
            "per agent-minute session 0.12%, week 0.02% | snapshot 18 min old | go",
        )

    def test_reads_a_meter_whose_window_reset_after_its_snapshot_as_unused(self) -> None:
        home = self.codex_home(("2026-10-18T06:00:00Z", {"primary": self.week(97.0, NOW - dt.timedelta(minutes=1))}))
        self.accounts({"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}})
        self.assertEqual(
            self.run_meter()[1][0], "codex 03:08 | week 0% | per agent-minute week 0.02% | snapshot 68 min old | go"
        )

    def test_reads_a_meter_without_a_reset_time(self) -> None:
        home = self.codex_home(("2026-10-18T06:00:00Z", {"primary": {"used_percent": 3.0, "window_minutes": 10080}}))
        self.accounts({"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}})
        self.assertEqual(
            self.run_meter()[1][0], "codex 03:08 | week 3% | per agent-minute week 0.02% | snapshot 68 min old | go"
        )

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
        self.assertEqual(
            (status, [line.split(" ")[0] for line in lines]), (0, ["one", "two", "start", "agents", "machine"])
        )

    def test_logs_one_reading_of_the_machine_for_all_the_accounts(self) -> None:
        self.accounts({"name": "one", "tool": "claude"}, {"name": "two", "tool": "claude"})
        self.run_meter()
        entries = [json.loads(line) for line in (self.loop / "meter.jsonl").read_text().splitlines()]
        self.assertEqual([entry.get("account") for entry in entries], [None, "one", "two"])
        self.assertEqual(["machine" in entry for entry in entries], [True, False, False])

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
    """The meter waits in steps of HAND_OFF_CHECK_SECONDS, so the tests check the seconds after the start at which it
    reads the accounts."""

    def setUp(self) -> None:
        super().setUp()
        self.readings: list[float] = []
        read_accounts = meter.read_accounts

        def read_and_record(*arguments: Any) -> Any:
            self.readings.append((self.clock.time - NOW).total_seconds())
            return read_accounts(*arguments)

        patcher = mock.patch.object(meter, "read_accounts", read_and_record)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_wakes_when_the_advice_changes(self) -> None:
        self.answer("claude", [usage_output(8), usage_output(9), usage_output(98)])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.readings), (0, [0, 1800, 3600]))
        self.assertTrue(lines[0].startswith("claude 04:08 | session 98% ") and lines[0].endswith(" | stop"))

    def test_wakes_every_50_minutes_while_an_account_waits_for_its_reset(self) -> None:
        self.answer("claude", [usage_output(99, "7:30am")])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.readings), (0, [0, 3000]))
        self.assertTrue(lines[0].startswith("claude 03:58 | session 99% ") and lines[0].endswith(" | stop"))

    def test_wakes_within_a_minute_when_the_hook_tells_an_agent_to_hand_off(self) -> None:
        hand_offs = self.loop / "hand-offs"
        hand_offs.mkdir()
        (hand_offs / "a1.400000").touch()
        self.clock.alarms.append((NOW + dt.timedelta(seconds=610), (hand_offs / "a2.400000").touch))
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.readings, self.clock.time - NOW), (0, [0], dt.timedelta(seconds=660)))
        self.assertTrue(lines[0].startswith("claude 03:08 | session 8% ") and lines[0].endswith(" | go"))

    def test_does_not_wake_for_a_note_of_the_hook_from_before_it_started(self) -> None:
        (self.loop / "hand-offs").mkdir()
        (self.loop / "hand-offs" / "a1.500000").touch()
        self.answer("claude", [usage_output(8), usage_output(9), usage_output(98)])
        self.assertEqual((self.run_meter("--wake")[0], self.readings), (0, [0, 1800, 3600]))

    def test_counts_the_50_minutes_from_the_start_while_another_account_reads_more_often(self) -> None:
        home = self.codex_home(("2026-10-18T07:00:00Z", {"primary": {"used_percent": 3.0, "window_minutes": 10080}}))
        self.accounts(
            {"name": "claude", "tool": "claude"}, {"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(home)}}
        )
        self.answer("claude", [usage_output(99, "7:30am")])
        self.assertEqual(self.run_meter("--wake")[0], 0)
        self.assertEqual(self.readings, [0, 1800, 3000])

    def test_wakes_just_after_a_reset_that_comes_sooner(self) -> None:
        self.answer("claude", [usage_output(99, "3:18am"), usage_output(1, "8:18am")])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.readings), (0, [0, 660]))
        self.assertTrue(lines[0].endswith(" | go"))

    def test_waits_for_the_reset_of_a_parked_account_without_waking(self) -> None:
        week_full = usage_output(10, week=99)
        self.answer("claude", [week_full, usage_output(1, week=0)])
        status, lines, _ = self.run_meter("--wake")
        reset = dt.datetime(2026, 10, 24, 17, 0, tzinfo=NEW_YORK)
        self.assertEqual((status, self.readings), (0, [0, (reset - NOW).total_seconds() + 60]))
        self.assertTrue(lines[0].endswith(" | go"))

    def test_ends_when_an_account_gives_no_reading(self) -> None:
        self.answer("claude", [usage_output(8), {"stdout": "Not logged in"}])
        status, lines, _ = self.run_meter("--wake")
        self.assertEqual((status, self.readings), (1, [0, 1800]))
        self.assertTrue(lines[0].startswith("claude | no reading: "))

    def test_ends_at_once_when_the_first_reading_fails(self) -> None:
        self.answer("claude", [{"stdout": "Not logged in"}])
        self.assertEqual((self.run_meter("--wake")[0], self.readings), (1, [0]))


# A reading of a Mac with Apple silicon
MAC: Machine = {
    "disk_free_gb": 94.4,
    "load": 3.25,
    "cores": 10,
    "memory_free_gb": 30.03,
    "swap_used_gb": 1.88,
    "gpu_percent": 23.0,
    "gpu_memory_used_gb": 6.62,
    "gpu_shares_memory": True,
}


class MachineLineTest(MeterTest):
    def test_gives_the_disk_the_memory_the_swap_the_gpu_the_busy_cores_the_context_and_the_retrospective(self) -> None:
        retrospective = "retrospective due: 31.5 hours of agents' work since the last one"
        self.assertEqual(
            meter.machine_line(MAC, 4.2, 0, 210_500, retrospective, []),
            "machine | disk 94 GB free | memory 30 GB free | swap 1.9 GB used | GPU 23% busy, 6.6 GB of the main memory"
            " | 4.2 of 10 cores busy | orchestrator context 210k | "
            "retrospective due: 31.5 hours of agents' work since the last one",
        )

    def test_gives_the_memory_of_a_gpu_with_its_own_memory(self) -> None:
        line = meter.machine_line({**MAC, "gpu_shares_memory": False}, 4.2, 0, None, "", [])
        self.assertIn(" | GPU 23% busy, 6.6 GB of its own memory | ", line)

    def test_says_which_numbers_the_machine_does_not_give(self) -> None:
        missing = {**MAC, "memory_free_gb": None, "swap_used_gb": None, "gpu_percent": None, "gpu_memory_used_gb": None}
        line = meter.machine_line(missing, None, 0, None, "", [])
        self.assertIn(" | memory unknown | swap unknown | GPU unknown | busy cores of 10 unknown | ", line)
        line = meter.machine_line({**MAC, "gpu_percent": None}, 4.2, 0, None, "", [])
        self.assertIn(" | GPU use unknown, 6.6 GB of the main memory | ", line)
        line = meter.machine_line({**MAC, "gpu_memory_used_gb": None}, 4.2, 0, None, "", [])
        self.assertIn(" | GPU 23% busy, memory unknown | ", line)

    def test_gives_the_cores_that_were_busy_while_it_read_the_accounts(self) -> None:
        ticks = iter([CpuTicks(1000, 5000), CpuTicks(1420, 6000)])
        with mock.patch.object(meter, "read_cpu_ticks", lambda: next(ticks)), mock.patch("os.cpu_count", lambda: 10):
            self.assertIn(" | 4.2 of 10 cores busy | ", self.run_meter()[1][-1])

    def test_gives_no_busy_cores_when_the_cpu_ticks_cannot_be_read(self) -> None:
        def unreadable() -> CpuTicks:
            raise CpuTicksError("can't read the machine's CPU ticks from /proc/stat")

        with mock.patch.object(meter, "read_cpu_ticks", unreadable), mock.patch("os.cpu_count", lambda: 10):
            _, lines, errors = self.run_meter()
        self.assertIn(" | busy cores of 10 unknown | ", lines[-1])
        warning = "warning: can't read the machine's CPU ticks from /proc/stat, so the busy cores are unknown.\n"
        self.assertEqual(errors, warning)

    def test_is_the_last_line(self) -> None:
        with mock.patch.object(meter, "OUTPUT_TOKENS_PER_RETROSPECTIVE", None):
            last_line = self.run_meter()[1][-1]
        self.assertRegex(
            last_line,
            r"^machine \| disk \d+ GB free \| memory .* \| (\d+\.\d of \d+ cores busy|busy cores of \d+ unknown) \| "
            r"orchestrator context unknown \| "
            r"retrospective not due: 0\.0 of 30 hours of agents' work since the last one$",
        )


class ContextTest(MeterTest):
    def transcript(self, session: str) -> Path:
        project = "".join(character if character.isalnum() else "-" for character in str(paths.main_checkout()))
        transcript = self.root / "claude-config" / "projects" / project / f"{session}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        return transcript

    def context(self) -> str:
        return self.run_meter()[1][-1].split(" | ")[-2]

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

    def test_keeps_each_line_within_120_characters(self) -> None:
        longer = [line for line in meter.help_text().splitlines() if len(line) > 120]
        self.assertEqual(longer, [])


class ScriptTest(MeterTest):
    def test_runs_as_a_script(self) -> None:
        done = subprocess.run(
            [sys.executable, str(METER)], capture_output=True, text=True, env=dict(os.environ), check=False, timeout=60
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(
            done.stdout,
            r"^claude \d\d:\d\d \| session 8% .* \| go\nstart on \| .*\nagents \| none running\nmachine \| ",
        )
