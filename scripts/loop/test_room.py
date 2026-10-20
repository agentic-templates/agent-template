import math
import unittest
from unittest import mock

import room
from machine import Machine
from room import Command, Use

NOW = 1_800_000_000.0


def reading(load: float = 0.0, memory_free_gb: float | None = 30.0, gpu_percent: float | None = 0.0) -> Machine:
    return {
        "disk_free_gb": 100.0,
        "load": load,
        "cores": 10,
        "memory_free_gb": memory_free_gb,
        "swap_used_gb": 0.0,
        "gpu_percent": gpu_percent,
        "gpu_memory_used_gb": None,
        "gpu_shares_memory": None,
    }


def waiting(name: str, cores: float | None, arrived: float = 0.0, memory_gb: float = 1.0) -> Command:
    need = None if cores is None else Use(cores, memory_gb, 0.0)
    return Command(name, NOW + arrived, need)


def running(name: str, cores: float | None, arrived: float = -100.0, memory_gb: float = 1.0) -> Command:
    return waiting(name, cores, arrived, memory_gb)._replace(started=NOW)


class RoomTest(unittest.TestCase):
    """Keeps 1 core, 2 GB and 10% of the GPU free, whatever numbers a retrospective sets."""

    def setUp(self) -> None:
        for name, value in (("KEEP_FREE_CORES", 1.0), ("KEEP_FREE_MEMORY_GB", 2.0), ("KEEP_FREE_GPU_PERCENT", 10.0)):
            patcher = mock.patch.object(room, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


CHECK = ["scripts/check"]


def runs_of(command: str, *uses: Use) -> list[room.Run]:
    return [room.Run(command.split(), use) for use in uses]


class NeedTest(unittest.TestCase):
    def test_reserves_nothing_known_before_the_first_run_ends(self) -> None:
        self.assertIsNone(room.need_of(CHECK, []))

    def test_reserves_the_most_of_each_number_that_a_measured_run_used(self) -> None:
        runs = runs_of("scripts/check", Use(2.0, 1.0, 0.0), Use(3.4, 0.5, 12.0), Use(1.0, 2.5, 0.0))
        self.assertEqual(room.need_of(CHECK, runs), Use(3.4, 2.5, 12.0))

    def test_leaves_out_the_runs_before_the_last_five(self) -> None:
        runs = runs_of("scripts/check", Use(9.0, 9.0, 90.0), *[Use(1.0, 1.0, 0.0)] * 5)
        with mock.patch.object(room, "RUNS_PER_NEED", 5):
            self.assertEqual(room.need_of(CHECK, runs), Use(1.0, 1.0, 0.0))

    def test_reserves_from_the_runs_of_the_command_itself_over_those_of_others(self) -> None:
        runs = [*runs_of("cargo test foo", Use(2.0, 1.0, 0.0)), *runs_of("cargo test", Use(4.0, 2.0, 0.0))]
        self.assertEqual(room.need_of(["cargo", "test"], runs), Use(4.0, 2.0, 0.0))

    def test_reserves_for_a_new_command_from_the_runs_of_the_commands_that_share_the_most_leading_words(self) -> None:
        runs = [
            *runs_of("cargo test foo", Use(4.0, 2.0, 0.0)),
            *runs_of("cargo test baz", Use(3.0, 3.0, 0.0)),
            *runs_of("cargo build", Use(8.0, 6.0, 0.0)),
            *runs_of("make test", Use(9.0, 9.0, 0.0)),
        ]
        self.assertEqual(room.need_of(["cargo", "test", "bar"], runs), Use(4.0, 3.0, 0.0))
        self.assertEqual(room.need_of(["cargo", "test"], runs), Use(4.0, 3.0, 0.0))
        self.assertEqual(room.need_of(["cargo", "doc"], runs), Use(8.0, 6.0, 0.0))

    def test_reserves_nothing_for_a_command_whose_program_no_run_shares(self) -> None:
        self.assertIsNone(room.need_of(["cargo", "test"], runs_of("make test", Use(2.0, 1.0, 0.0))))


class FreeRoomTest(unittest.TestCase):
    def test_gives_the_idle_cores_the_free_memory_and_the_idle_share_of_the_gpu(self) -> None:
        self.assertEqual(room.free_room(reading(memory_free_gb=20.0, gpu_percent=30.0), 3.0), Use(7.0, 20.0, 70.0))

    def test_counts_the_idle_cores_whatever_the_load(self) -> None:
        # A load of 69 on 10 cores, with processes that wait for the disk, while the cores were 54% idle
        self.assertEqual(room.free_room(reading(load=69.0), 4.6).cores, 5.4)

    def test_leaves_unlimited_room_for_a_number_that_the_machine_does_not_give(self) -> None:
        self.assertEqual(
            room.free_room(reading(memory_free_gb=None, gpu_percent=None), 0.0), Use(10.0, math.inf, math.inf)
        )

    def test_leaves_no_room_on_the_cores_before_two_readings_of_the_cpu_give_their_use(self) -> None:
        self.assertEqual(room.free_room(reading(), None).cores, -math.inf)


class HasRoomTest(unittest.TestCase):
    def test_fits_a_need_up_to_the_room_in_each_number(self) -> None:
        self.assertTrue(room.has_room(Use(4.0, 8.0, 50.0), Use(4.0, 8.0, 50.0)))
        for too_much in (Use(4.1, 1.0, 0.0), Use(1.0, 8.1, 0.0), Use(1.0, 1.0, 50.1)):
            with self.subTest(need=too_much):
                self.assertFalse(room.has_room(too_much, Use(4.0, 8.0, 50.0)))

    def test_never_lets_a_number_that_the_command_does_not_need_stop_it(self) -> None:
        # Another program keeps the GPU 95% busy, so its room is 5 less the 10 kept free.
        self.assertTrue(room.has_room(Use(3.4, 1.0, 0.0), Use(8.0, 20.0, -5.0)))
        self.assertFalse(room.has_room(Use(3.4, 1.0, 1.0), Use(8.0, 20.0, -5.0)))

    def test_fits_a_first_run_once_some_cores_and_memory_are_left_whatever_the_gpu(self) -> None:
        self.assertTrue(room.has_room(None, Use(0.5, 0.5, -5.0)))
        for no_room in (Use(0.0, 1.0, 1.0), Use(1.0, -1.0, 1.0)):
            with self.subTest(room=no_room):
                self.assertFalse(room.has_room(None, no_room))


class RoomAloneTest(RoomTest):
    def test_keeps_room_free_from_what_the_readings_show(self) -> None:
        self.assertEqual(room.room_alone(Use(8.0, 20.0, 70.0), []), Use(7.0, 18.0, 60.0))

    def test_gives_back_what_the_running_commands_use_now(self) -> None:
        command = running("train", 1.0)._replace(using=Use(3.0, 5.0, 20.0))
        self.assertEqual(room.room_alone(Use(4.0, 20.0, 70.0), [command]), Use(6.0, 23.0, 80.0))

    def test_counts_a_running_command_with_no_sample_yet_at_its_reservation(self) -> None:
        just_started = running("check", 4.0, memory_gb=8.0)
        alone = room.room_alone(Use(8.0, 30.0, 100.0), [just_started])
        self.assertEqual(room.room_now(alone, [just_started]), Use(3.0, 20.0, 90.0))
        self.assertEqual(room.commands_to_start([waiting("build", 3.1)], [just_started], alone), [])
        self.assertEqual(room.commands_to_start([waiting("test", 3.0)], [just_started], alone), ["test"])

    def test_counts_a_running_command_that_uses_less_than_it_reserves_at_what_it_uses(self) -> None:
        # check reserves 6 cores and 8 GB but uses 1 core and 2 GB in a light step. Other programs use 1 core.
        command = running("check", 6.0, memory_gb=8.0)._replace(using=Use(1.0, 2.0, 0.0))
        alone = room.room_alone(Use(8.0, 26.0, 100.0), [command])
        self.assertEqual(room.room_now(alone, [command]), Use(7.0, 24.0, 90.0))
        self.assertEqual(room.commands_to_start([waiting("build", 7.1)], [command], alone), [])
        self.assertEqual(room.commands_to_start([waiting("test", 7.0)], [command], alone), ["test"])

    def test_counts_a_running_command_that_uses_more_than_it_reserves_at_what_it_uses(self) -> None:
        command = running("train", 1.0, memory_gb=8.0)._replace(using=Use(3.0, 12.0, 20.0))
        alone = room.room_alone(Use(4.0, 20.0, 70.0), [command])
        self.assertEqual(room.room_now(alone, [command]), Use(3.0, 18.0, 60.0))


class CommandsToStartTest(unittest.TestCase):
    def test_starts_a_command_that_fits_at_once(self) -> None:
        self.assertEqual(room.commands_to_start([waiting("check", 3.4)], [], Use(9.0, 28.0, 90.0)), ["check"])

    def test_keeps_a_command_waiting_while_the_running_ones_take_its_room(self) -> None:
        self.assertEqual(
            room.commands_to_start([waiting("check", 3.4)], [running("build", 6.0)], Use(9.0, 28.0, 90.0)), []
        )

    def test_starts_commands_together_only_as_far_as_they_fit(self) -> None:
        line = [waiting("first", 4.0, arrived=1), waiting("second", 4.0, arrived=2), waiting("third", 4.0, arrived=3)]
        self.assertEqual(room.commands_to_start(line, [], Use(9.0, 28.0, 90.0)), ["first", "second"])

    def test_takes_the_commands_in_the_order_in_which_they_came(self) -> None:
        line = [waiting("later", 5.0, arrived=2), waiting("earlier", 5.0, arrived=1)]
        self.assertEqual(room.commands_to_start(line, [], Use(9.0, 28.0, 90.0)), ["earlier"])

    def test_counts_the_memory_that_a_command_reserves(self) -> None:
        line = [waiting("big", 1.0, memory_gb=20.0, arrived=1), waiting("small", 1.0, memory_gb=9.0, arrived=2)]
        self.assertEqual(room.commands_to_start(line, [], Use(9.0, 28.0, 90.0)), ["big"])

    def test_starts_commands_that_need_no_gpu_while_other_programs_keep_it_busy(self) -> None:
        alone = Use(9.0, 28.0, -5.0)
        self.assertEqual(room.commands_to_start([waiting("check", 3.4)], [running("build", 1.0)], alone), ["check"])
        self.assertEqual(room.commands_to_start([waiting("new", None)], [running("build", 1.0)], alone), ["new"])

    def test_starts_a_first_run_once_the_readings_show_room(self) -> None:
        self.assertEqual(
            room.commands_to_start([waiting("new", None)], [running("check", 3.0)], Use(9.0, 28.0, 90.0)), ["new"]
        )
        self.assertEqual(
            room.commands_to_start([waiting("new", None)], [running("check", 9.0)], Use(9.0, 28.0, 90.0)), []
        )

    def test_starts_nothing_else_while_a_first_run_runs(self) -> None:
        self.assertEqual(
            room.commands_to_start([waiting("check", 0.1)], [running("new", None)], Use(9.0, 28.0, 90.0)), []
        )

    def test_starts_nothing_after_a_first_run_that_starts(self) -> None:
        line = [waiting("new", None, arrived=1), waiting("check", 0.1, arrived=2)]
        self.assertEqual(room.commands_to_start(line, [], Use(9.0, 28.0, 90.0)), ["new"])

    def test_starts_a_command_too_big_for_the_machine_once_no_heavy_command_runs(self) -> None:
        line = [waiting("huge", 16.0, arrived=1), waiting("check", 2.0, arrived=2)]
        self.assertEqual(room.commands_to_start(line, [running("build", 2.0)], Use(9.0, 28.0, 90.0)), [])
        self.assertEqual(room.commands_to_start(line, [], Use(9.0, 28.0, 90.0)), ["huge"])

    def test_starts_the_first_in_line_while_other_programs_leave_no_room_once_no_heavy_command_runs(self) -> None:
        self.assertEqual(room.commands_to_start([waiting("check", 3.4)], [], Use(-5.0, 28.0, 90.0)), ["check"])

    def test_starts_a_small_command_ahead_of_the_line_when_the_first_still_gets_its_room(self) -> None:
        # 5 cores run, so the first in line, which needs 6, waits. Once the 5 end, 9 are free: 6 for it and 2 for small.
        line = [waiting("large", 6.0, arrived=1), waiting("small", 2.0, arrived=2)]
        self.assertEqual(room.commands_to_start(line, [running("build", 5.0)], Use(9.0, 28.0, 90.0)), ["small"])

    def test_keeps_a_command_behind_the_first_when_it_would_delay_it(self) -> None:
        line = [waiting("large", 6.0, arrived=1), waiting("medium", 4.0, arrived=2)]
        self.assertEqual(room.commands_to_start(line, [running("build", 5.0)], Use(9.0, 28.0, 90.0)), [])

    def test_counts_the_commands_that_started_ahead_of_the_first_against_the_room_behind_it(self) -> None:
        # small started ahead of large and takes 2 of the 3 cores that large leaves, so another can't take 2 more.
        line = [waiting("large", 6.0, arrived=1), waiting("another", 2.0, arrived=3)]
        ahead = [running("build", 5.0, arrived=0), running("small", 2.0, arrived=2)]
        self.assertEqual(room.commands_to_start(line, ahead, Use(9.0, 28.0, 90.0)), [])

    def test_gives_a_large_command_its_turn_once_the_commands_ahead_of_it_end(self) -> None:
        line = [waiting("large", 6.0, arrived=1), waiting("small", 2.0, arrived=3)]
        started_ahead = [running("ahead", 2.0, arrived=2)]
        self.assertEqual(room.commands_to_start(line, started_ahead, Use(9.0, 28.0, 90.0)), ["large"])

    def test_starts_nothing_ahead_of_a_first_run(self) -> None:
        line = [waiting("new", None, arrived=1), waiting("small", 0.5, arrived=2)]
        self.assertEqual(room.commands_to_start(line, [running("build", 9.0)], Use(9.0, 28.0, 90.0)), [])

    def test_starts_no_first_run_ahead_of_the_line(self) -> None:
        line = [waiting("large", 6.0, arrived=1), waiting("new", None, arrived=2)]
        self.assertEqual(room.commands_to_start(line, [running("build", 5.0)], Use(9.0, 28.0, 90.0)), [])
