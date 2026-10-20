import contextlib
import fcntl
import importlib.machinery
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any
from unittest import mock

import heavy_log
import paths
import room
from machine import CpuTicks, CpuTicksError
from test_room import reading

RUN_HEAVY = Path(__file__).with_name("run-heavy")
# The script's file name has no .py, so the tests load it by its path.
run_heavy = types.ModuleType("run_heavy")
importlib.machinery.SourceFileLoader("run_heavy", str(RUN_HEAVY)).exec_module(run_heavy)

NOW = 1_800_000_000.0
PYTHON = sys.executable


class FakeClock:
    """The time of day, which each sleep moves on. A test gives an action for each sleep that it expects, such as
    ending another command, so that a sleep it doesn't expect fails."""

    def __init__(self) -> None:
        self.now = NOW
        self.actions: list[Callable[[], None]] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.actions.pop(0)()


class FakeCpu:
    """The CPU ticks of a machine with 10 cores, 100 ticks a second each, whose cores are as busy as a test sets."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.busy_cores = 0.0
        self.busy_ticks = 0.0
        self.at = clock.now

    def ticks(self) -> CpuTicks:
        self.busy_ticks += self.busy_cores * (self.clock.now - self.at) * 100
        self.at = self.clock.now
        return CpuTicks(round(self.busy_ticks), round(self.clock.now * 1000))


class RunHeavyTest(unittest.TestCase):
    """A test with a loop folder, fixed readings of a machine with 10 cores, which are idle unless the test sets them
    busy, and a fake time of day."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.loop = self.root / "loop"
        self.heavy = self.loop / "heavy"
        self.heavy.mkdir(parents=True)
        self.machine = reading()
        self.clock = FakeClock()
        self.cpu = FakeCpu(self.clock)
        clock = types.SimpleNamespace(time=self.clock.time, monotonic=time.monotonic, sleep=self.clock.sleep)
        patchers: list[Any] = [
            mock.patch.dict(os.environ, {"LOOP_DIR": str(self.loop)}),
            mock.patch.object(run_heavy, "time", clock),
            mock.patch.object(run_heavy, "read_machine", lambda _folder: dict(self.machine)),
            mock.patch.object(run_heavy, "read_cpu_ticks", self.cpu.ticks),
            mock.patch.object(run_heavy, "SAMPLE_SECONDS", 2.0),
            mock.patch.object(room, "KEEP_FREE_CORES", 1.0),
            mock.patch.object(room, "KEEP_FREE_MEMORY_GB", 2.0),
            mock.patch.object(room, "KEEP_FREE_GPU_PERCENT", 10.0),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("CI", run_heavy.HEAVY_VARIABLE, paths.CODEX_SANDBOX_VARIABLE):
            os.environ.pop(name, None)
        # A warning comes once in a process, and the tests share one.
        heavy_log.warn.cache_clear()

    def run_heavy(self, *command: str) -> tuple[int, str]:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            status = run_heavy.main(list(command))
        return status, errors.getvalue()

    def log(self, skipped: int = 0) -> list[dict[str, Any]]:
        """The lines of the log, after the number of lines to skip."""
        log = self.loop / "heavy.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()[skipped:]] if log.exists() else []

    def state(self) -> dict[str, Any]:
        state: dict[str, Any] = json.loads((self.heavy / "state.json").read_text())
        return state

    def add_entry(self, name: str, need: dict[str, Any] | None, started: float | None, arrived: float = 0.0) -> None:
        """Adds a command of another process to the line."""
        state_file = self.heavy / "state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {"commands": []}
        entry = {"id": name, "command": name, "arrived": NOW + arrived, "need": need, "started": started}
        state["commands"].append({**entry, "using": room.NOTHING._asdict()})
        state_file.write_text(json.dumps(state))

    def hold(self, name: str) -> IO[str]:
        """Holds a command's lock file, as the process of a command that waits or runs does."""
        lock = (self.heavy / f"{name}.lock").open("w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return lock

    def write_log(self, *lines: dict[str, Any]) -> None:
        with (self.loop / "heavy.jsonl").open("a") as log:
            for line in lines:
                log.write(json.dumps(line) + "\n")


def need(cores: float, memory_gb: float = 1.0, gpu_percent: float | None = 0.0) -> dict[str, Any]:
    return {"cores": cores, "memory_gb": memory_gb, "gpu_percent": gpu_percent}


def end_line(command: list[str], used: dict[str, Any], exit_code: int = 0) -> dict[str, Any]:
    line = {"at": "2026-10-19T04:00:00-04:00", "event": "end", "command": shlex.join(command), "exit_code": exit_code}
    return {**line, "used": used}


class StartTest(RunHeavyTest):
    def test_starts_a_command_at_once_when_the_machine_has_room(self) -> None:
        status, errors = self.run_heavy(PYTHON, "-c", "raise SystemExit(3)")
        self.assertEqual((status, errors), (3, ""))
        start, end = self.log()
        self.assertEqual(start["event"], "start")
        self.assertEqual((start["waited_seconds"], start["waited_with_room_seconds"]), (0.0, 0.0))
        self.assertEqual(start["machine"], self.machine)
        self.assertEqual((end["event"], end["exit_code"]), ("end", 3))
        self.assertEqual(self.state()["commands"], [])
        self.assertEqual(list(self.heavy.glob("*.lock")), [])

    def test_waits_while_another_command_takes_the_room_and_starts_once_it_ends(self) -> None:
        self.add_entry("build", need(9.0), started=NOW - 100, arrived=-100)
        lock = self.hold("build")
        # The first look has one reading of the CPU, which gives no busy cores. The second shows 9.5 busy cores, the
        # third and fourth show room, and the build ends after the fourth.
        self.cpu.busy_cores = 9.5

        def show_room() -> None:
            self.cpu.busy_cores = 0.0

        self.clock.actions = [lambda: None, show_room, lambda: None, lock.close]
        self.write_log(end_line([PYTHON, "-c", "pass"], need(2.0)))
        self.assertEqual(self.run_heavy(PYTHON, "-c", "pass"), (0, ""))
        wait, start, _ = self.log()[1:]
        self.assertEqual((wait["event"], wait["need"], wait["machine"]), ("wait", need(2.0), self.machine))
        self.assertEqual((start["waited_seconds"], start["busy_cores"]), (8.0, 0.0))
        # The readings showed room from the third look on, two looks of 2 seconds before the fifth.
        self.assertEqual(start["waited_with_room_seconds"], 4.0)
        self.assertEqual(self.state()["commands"], [])

    def test_reserves_the_most_that_the_command_used_in_its_runs_that_ended_by_themselves(self) -> None:
        command = [PYTHON, "-c", "pass"]
        self.write_log(
            end_line(command, need(3.0, memory_gb=0.5)),
            end_line(command, need(1.0, memory_gb=2.0, gpu_percent=None)),
            end_line(command, need(8.0, memory_gb=9.0), exit_code=-9),
            end_line(["other"], need(7.0)),
        )
        self.run_heavy(*command)
        self.assertEqual(self.log()[4]["need"], need(3.0, memory_gb=2.0))

    def test_measures_a_run_and_reserves_what_it_used_for_the_next(self) -> None:
        busy = (
            "import time\n"
            "held = bytearray(150_000_000)\n"
            "start = time.monotonic()\n"
            "while time.monotonic() - start < 0.5:\n"
            "    pass"
        )
        with mock.patch.object(run_heavy, "SAMPLE_SECONDS", 0.05):
            self.run_heavy(PYTHON, "-c", busy)
            self.run_heavy(PYTHON, "-c", busy)
        first_start, first_end, second_start, _ = self.log()
        self.assertIsNone(first_start["need"])
        self.assertGreaterEqual(first_end["seconds"], 0.5)
        self.assertGreater(first_end["used"]["cores"], 0.0)
        self.assertGreaterEqual(first_end["used"]["memory_gb"], 0.15)
        self.assertEqual(
            second_start["need"], {**first_end["used"], "gpu_percent": first_end["used"]["gpu_percent"] or 0.0}
        )
        self.assertEqual(first_end["highest_swap_used_gb"], 0.0)

    def test_reserves_for_a_new_command_what_the_commands_that_share_its_leading_words_used(self) -> None:
        # As cargo test bar after cargo test foo
        self.write_log(end_line([PYTHON, "-c", "pass"], need(2.0)), end_line(["other"], need(7.0)))
        self.run_heavy(PYTHON, "-c", "x = 1")
        self.assertEqual(self.log()[2]["need"], need(2.0))

    def test_gives_the_commands_that_wait_the_reservations_that_the_run_changes(self) -> None:
        command = [PYTHON, "-c", "pass"]
        self.add_entry("running", need(1.0), started=NOW - 10, arrived=-10)
        for name, words in (("again", command), ("similar", [PYTHON, "-c", "x = 1"]), ("other", ["other"])):
            self.add_entry(name, None, started=None, arrived=10)
        state = self.state()
        for entry, words in zip(state["commands"], (command, command, [PYTHON, "-c", "x = 1"], ["other"]), strict=True):
            entry["command"] = shlex.join(words)
            self.hold(entry["id"])
        (self.heavy / "state.json").write_text(json.dumps(state))
        # The command starts at the second look, which has two readings of the CPU.
        self.clock.actions = [lambda: None]
        self.run_heavy(*command)
        used = self.log()[-1]["used"]
        reserved = {**used, "gpu_percent": used["gpu_percent"] or 0.0}
        needs = {entry["id"]: entry["need"] for entry in self.state()["commands"]}
        self.assertEqual(needs, {"running": need(1.0), "again": reserved, "similar": reserved, "other": None})

    def test_leaves_out_a_line_of_the_log_that_is_not_json_with_a_warning(self) -> None:
        # A writer that was killed left a line unfinished, and the next line went on from it.
        start = json.dumps({"at": "2026-10-19T04:00:00-04:00", "event": "start"})
        (self.loop / "heavy.jsonl").write_text('{"at": "2026-10-19T03:59' + start + "\n")
        self.write_log(end_line([PYTHON, "-c", "pass"], need(2.0)))
        status, errors = self.run_heavy(PYTHON, "-c", "pass")
        self.assertEqual(status, 0)
        log = self.loop / "heavy.jsonl"
        warning = f"warning: line 1 of {log} isn't JSON, so it doesn't count. Delete the line to end this warning.\n"
        self.assertEqual(errors, warning)
        self.assertEqual(self.log(skipped=1)[1]["need"], need(2.0))

    def test_counts_a_command_in_line_whose_use_has_no_numbers_as_using_nothing(self) -> None:
        # Another version of the script, such as on another branch, leaves the use of a waiting command empty.
        self.add_entry("waiting", need(1.0), started=None, arrived=-10)
        self.hold("waiting")
        state = self.state()
        state["commands"][0]["using"] = None
        (self.heavy / "state.json").write_text(json.dumps(state))
        self.write_log(end_line([PYTHON, "-c", "pass"], need(2.0)))
        # The first look starts the command ahead in line, and this one starts at the second, which has two readings
        # of the CPU.
        self.clock.actions = [lambda: None]
        self.assertEqual(self.run_heavy(PYTHON, "-c", "pass"), (0, ""))
        self.assertEqual(self.log()[2]["waited_seconds"], 2.0)
        self.assertEqual(self.state()["commands"][0]["started"], NOW)

    def test_starts_a_retrospectives_command_after_another_agents_that_came_later(self) -> None:
        worktree = self.root / "test-build"
        subprocess.run(["git", "init", "--quiet", str(worktree)], check=True)
        paths.retrospective_mark(worktree).touch()
        command = [PYTHON, "-c", "pass"]
        self.write_log(end_line(command, need(6.0)))
        self.add_entry("check", need(6.0), started=None, arrived=1)
        lock = self.hold("check")
        started_check: list[float] = []

        def end_check() -> None:
            [check] = self.state()["commands"][:1]
            started_check.append(check["started"])
            lock.close()

        # The first look starts the check, and the second, once the check ended, starts the command.
        self.clock.actions = [end_check]
        with contextlib.chdir(worktree):
            self.assertEqual(self.run_heavy(*command), (0, ""))
        self.assertEqual(started_check, [NOW])
        self.assertEqual(self.log()[2]["waited_seconds"], 2.0)

    def test_runs_a_command_from_a_folder_outside_any_git_checkout_as_another_agents(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        with contextlib.chdir(outside):
            self.assertEqual(self.run_heavy(PYTHON, "-c", "pass"), (0, ""))
        self.assertEqual(self.log()[0]["event"], "start")


class EndTest(RunHeavyTest):
    def test_releases_the_room_of_a_command_that_a_signal_ends_and_leaves_its_run_out_of_the_reservation(self) -> None:
        crash = [PYTHON, "-c", "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)"]
        self.assertEqual(self.run_heavy(*crash), (128 + signal.SIGKILL, ""))
        self.assertEqual(self.log()[-1]["exit_code"], -signal.SIGKILL)
        self.assertEqual(self.state()["commands"], [])
        self.run_heavy(*crash)
        self.assertIsNone(self.log()[2]["need"])

    def test_drops_a_command_whose_process_ended_without_taking_it_out_of_the_line(self) -> None:
        self.add_entry("crashed", need(9.0), started=NOW - 100, arrived=-100)
        (self.heavy / "crashed.lock").touch()
        self.add_entry("never-locked", None, started=None, arrived=-50)
        self.assertEqual(self.run_heavy(PYTHON, "-c", "pass"), (0, ""))
        self.assertEqual(self.log()[0]["waited_seconds"], 0.0)
        self.assertEqual(list(self.heavy.glob("*.lock")), [])
        self.assertEqual(self.state()["commands"], [])

    def test_passes_a_stop_signal_on_to_the_command_and_lets_ctrl_c_reach_it_from_the_terminal(self) -> None:
        # The command sends both signals to run-heavy, which runs in the tests' own process, and ends on SIGTERM alone.
        command = (
            "import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(5))\n"
            "os.kill(os.getppid(), signal.SIGINT)\n"
            "os.kill(os.getppid(), signal.SIGTERM)\n"
            "time.sleep(30)"
        )
        earlier = signal.getsignal(signal.SIGTERM)
        with mock.patch.object(run_heavy, "SAMPLE_SECONDS", 0.05):
            self.assertEqual(self.run_heavy(PYTHON, "-c", command), (5, ""))
        self.assertEqual(signal.getsignal(signal.SIGTERM), earlier)

    def test_says_when_it_cannot_run_the_command(self) -> None:
        status, errors = self.run_heavy(str(self.root / "missing"), "--flag")
        self.assertEqual(status, 127)
        self.assertEqual(errors, f"error: can't run {self.root / 'missing'}: No such file or directory.\n")
        self.assertEqual(self.state()["commands"], [])


class StopTest(RunHeavyTest):
    def test_logs_the_wait_of_a_command_that_a_signal_stops_while_it_waits(self) -> None:
        self.add_entry("build", need(9.0), started=NOW - 100, arrived=-100)
        self.hold("build")
        # The first look has one reading of the CPU, so it shows no room. The second shows room, and a Bash call
        # that times out sends SIGTERM 2 seconds later.
        self.clock.actions = [lambda: None, lambda: os.kill(os.getpid(), signal.SIGTERM)]
        earlier = signal.getsignal(signal.SIGTERM)
        self.assertEqual(self.run_heavy(PYTHON, "-c", "pass"), (128 + signal.SIGTERM, ""))
        wait, stop = self.log()
        self.assertEqual((wait["event"], stop["event"], stop["need"]), ("wait", "stop", None))
        self.assertEqual((stop["waited_seconds"], stop["waited_with_room_seconds"]), (4.0, 2.0))
        self.assertEqual([entry["id"] for entry in self.state()["commands"]], ["build"])
        self.assertEqual(signal.getsignal(signal.SIGTERM), earlier)

    def test_takes_a_command_that_a_signal_stops_before_its_first_look_out_of_the_line(self) -> None:
        # The signal comes while run-heavy reads the machine for its first look.
        with mock.patch.object(run_heavy, "read_machine", lambda _folder: os.kill(os.getpid(), signal.SIGHUP)):
            self.assertEqual(self.run_heavy(PYTHON, "-c", "pass"), (128 + signal.SIGHUP, ""))
        self.assertEqual(self.log(), [])
        self.assertEqual(self.state()["commands"], [])


class ReadingTest(RunHeavyTest):
    def write_reading(self, age: float, cpu_ticks: CpuTicks | None, **numbers: Any) -> None:
        last = {"at": NOW - age, "machine": reading(**numbers), "cpu_ticks": cpu_ticks, "busy_cores": 2.5}
        (self.heavy / "state.json").write_text(json.dumps({"commands": [], "reading": last}))

    def test_shares_a_reading_that_is_less_than_a_second_old(self) -> None:
        self.write_reading(0.5, None, load=1.5)
        self.run_heavy(PYTHON, "-c", "pass")
        self.assertEqual((self.log()[0]["machine"]["load"], self.log()[0]["busy_cores"]), (1.5, 2.5))
        self.write_reading(1.0, None, load=1.5)
        self.run_heavy(PYTHON, "-c", "pass")
        self.assertEqual((self.log()[2]["machine"]["load"], self.log()[2]["busy_cores"]), (0.0, None))

    def test_says_once_what_it_cannot_read_when_the_cpu_ticks_cannot_be_read(self) -> None:
        def unreadable() -> CpuTicks:
            raise CpuTicksError("can't read the machine's CPU ticks from /proc/stat")

        self.add_entry("build", need(9.0), started=NOW - 100, arrived=-100)
        lock = self.hold("build")
        self.clock.actions = [lambda: None, lock.close]
        with mock.patch.object(run_heavy, "read_cpu_ticks", unreadable):
            status, errors = self.run_heavy(PYTHON, "-c", "pass")
        self.assertEqual(status, 0)
        self.assertEqual(
            errors,
            "warning: can't read the machine's CPU ticks from /proc/stat, so heavy commands start one at a time.\n",
        )
        self.assertEqual([line["busy_cores"] for line in self.log()], [None, None, None])

    def test_gives_the_busy_cores_since_the_reading_before_unless_it_is_older_than_ten_seconds(self) -> None:
        self.cpu.busy_cores = 4.0
        for age, busy_cores in ((10.0, 4.0), (10.5, None)):
            with self.subTest(age=age):
                self.cpu.busy_ticks, self.cpu.at = 0.0, NOW - age
                self.write_reading(age, CpuTicks(0, round((NOW - age) * 1000)))
                self.run_heavy(PYTHON, "-c", "pass")
                self.assertEqual(self.log()[-2]["busy_cores"], busy_cores)


class AtOnceTest(RunHeavyTest):
    def test_runs_a_heavy_command_that_another_one_starts_at_once(self) -> None:
        seen = self.root / "seen"
        inner = f"import os, pathlib\npathlib.Path({str(seen)!r}).write_text(os.environ['{run_heavy.HEAVY_VARIABLE}'])"
        self.assertEqual(self.run_heavy(PYTHON, str(RUN_HEAVY), PYTHON, "-c", inner), (0, ""))
        start, end = self.log()
        self.assertEqual(seen.read_text(), start["id"])
        self.assertEqual(end["event"], "end")

    def test_runs_every_command_at_once_in_ci_with_the_default_action_for_sigpipe(self) -> None:
        # bash's trap -p prints a signal that bash ignored from its start, as trap -- '' SIGPIPE.
        show = f"trap -p PIPE; echo ${run_heavy.HEAVY_VARIABLE}"
        done = subprocess.run(
            [PYTHON, str(RUN_HEAVY), "bash", "-c", show],
            capture_output=True,
            text=True,
            env={**os.environ, "CI": "true"},
            check=False,
            timeout=60,
        )
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "ci\n", ""))
        self.assertEqual(self.log(), [])

    def test_runs_every_command_at_once_in_codex_sandbox_without_using_the_loop_folder(self) -> None:
        # The loop folder is in a file, so each read or write there fails.
        file = self.root / "file"
        file.write_text("")
        done = subprocess.run(
            [PYTHON, str(RUN_HEAVY), "bash", "-c", "echo ran"],
            capture_output=True,
            text=True,
            env={**os.environ, paths.CODEX_SANDBOX_VARIABLE: "1", "LOOP_DIR": str(file / "loop")},
            check=False,
            timeout=60,
        )
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "ran\n", ""))

    def test_says_when_it_cannot_run_a_command_at_once(self) -> None:
        missing = str(self.root / "missing")
        done = subprocess.run(
            [PYTHON, str(RUN_HEAVY), missing],
            capture_output=True,
            text=True,
            env={**os.environ, "CI": "true"},
            check=False,
            timeout=60,
        )
        self.assertEqual(
            (done.returncode, done.stderr), (127, f"error: can't run {missing}: No such file or directory.\n")
        )

    def test_asks_for_a_command(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
            run_heavy.main([])
        self.assertIn("give the command to run, as in scripts/loop/run-heavy make test", errors.getvalue())
