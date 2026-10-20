import datetime as dt
import json
import os
from typing import Any
from unittest import mock

import heavy_log
from test_report import SINCE, ReportTest, end, report, start
from test_transcripts import at, call_line, prompt_line


def heavy_entry(minute: float, event: str, command: str = "scripts/check", **fields: Any) -> dict[str, Any]:
    return {"at": at(minute), "event": event, "id": f"{command}-{minute}", "command": command, **fields}


def heavy_end(minute: float, seconds: float, cores: float, memory_gb: float, swap: float | None) -> dict[str, Any]:
    used = {"cores": cores, "memory_gb": memory_gb, "gpu_percent": None}
    return heavy_entry(minute, "end", exit_code=0, seconds=seconds, used=used, highest_swap_used_gb=swap)


class HeavyTest(ReportTest):
    def log_heavy(self, *entries: dict[str, Any]) -> None:
        (self.loop / "heavy.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries))

    def test_gives_the_runs_of_each_command_their_waits_and_the_swap_while_they_ran(self) -> None:
        self.log_heavy(
            heavy_entry(-5, "start", waited_seconds=600, waited_with_room_seconds=600),
            heavy_end(-4, 90, 3.0, 1.0, 9.0),
            heavy_entry(1, "start", waited_seconds=0, waited_with_room_seconds=0),
            heavy_entry(2, "wait"),
            heavy_entry(4, "start", waited_seconds=120, waited_with_room_seconds=30),
            heavy_entry(5, "wait"),
            heavy_entry(8, "start", waited_seconds=240, waited_with_room_seconds=60),
            heavy_end(3, 120, 3.4, 1.2, 0.5),
            heavy_end(6, 60, 2.0, 0.8, None),
            heavy_end(10, 90, 3.0, 1.0, 2.5),
            heavy_entry(9, "start", "make test", waited_seconds=0, waited_with_room_seconds=0),
        )
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        heavy = [line for line in lines if "heavy" in line.lower()]
        self.assertEqual(
            heavy,
            [
                (
                    "Heavy command make test: 1 started, 0 ended | 0 waited, 0.0 min in all, 0.0 min of it while the "
                    "readings showed room"
                ),
                (
                    "Heavy command scripts/check: 3 started, 3 ended, median 1.5 min, 3.0 cores and 1.0 GB | 2 waited, "
                    "6.0 min in all, 1.5 min of it while the readings showed room"
                ),
                "Swap while heavy commands ran, the highest in each run, GB: median 1.5, highest 2.5",
            ],
        )

    def test_says_when_no_run_gave_the_swap(self) -> None:
        self.log_heavy(
            heavy_entry(1, "start", waited_seconds=0, waited_with_room_seconds=0), heavy_end(2, 30, 1.0, 1.0, None)
        )
        self.assertEqual(self.line("Swap while heavy"), "Swap while heavy commands ran: no readings")

    def test_counts_the_wait_of_a_command_that_a_signal_stopped_while_it_waited(self) -> None:
        self.log_heavy(heavy_entry(1, "wait"), heavy_entry(3, "stop", waited_seconds=120, waited_with_room_seconds=90))
        self.assertEqual(
            self.line("Heavy command scripts/check"),
            (
                "Heavy command scripts/check: 0 started, 0 ended | 1 waited, 2.0 min in all, 1.5 min of it while the "
                "readings showed room"
            ),
        )

    def test_leaves_out_a_line_that_is_not_json_with_a_warning(self) -> None:
        start = heavy_entry(1, "start", waited_seconds=60, waited_with_room_seconds=0)
        log = self.loop / "heavy.jsonl"
        log.write_text(json.dumps(start) + "\n" + '{"at": "2026-10-11T14:02:00+00:00", "event": "en\n')
        # The warning comes once in a process, and the tests share one.
        heavy_log.warn.cache_clear()
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        self.assertIn(
            "Heavy command scripts/check: 1 started, 0 ended | 1 waited, 1.0 min in all, 0.0 min of it while the "
            "readings showed room",
            lines,
        )
        self.assertEqual(
            errors, f"warning: line 2 of {log} isn't JSON, so it doesn't count. Delete the line to end this warning.\n"
        )


def meter_reading(minute: float, cores: int = 10, **numbers: float | None) -> dict[str, Any]:
    """A line of the meter's log, with a machine that gives the numbers, and gives no others."""
    names = ("disk_free_gb", "memory_free_gb", "swap_used_gb", "gpu_percent", "gpu_memory_used_gb", "load")
    machine = {name: numbers.get(name) for name in names} | {"cores": cores, "gpu_shares_memory": True}
    return {"at": at(minute), "account": "claude", "meters": {}, "meters_at_stop": [], "machine": machine}


def worked(agent_id: str, *periods: tuple[float, float]) -> dict[str, Any]:
    """An end line of agents.jsonl for an agent that worked in the periods, each from one minute to another."""
    return {
        **end(agent_id, periods[-1][1]),
        "periods": [[at(first), at(last)] for first, last in periods],
    }


class MachineTest(ReportTest):
    def setUp(self) -> None:
        super().setUp()
        # From minute 0 to 40 these agents work, one, two, or four at once. The test build c has no end line, and its
        # transcript's last call is at minute 41.
        self.log_agents(
            start("d", -5),
            start("a", 0),
            start("b", 10),
            {**start("c", 15), "test": True},
            start("e", 16),
            worked("d", (-5, 5)),
            worked("e", (16, 18)),
            worked("b", (10, 20)),
            worked("a", (0, 30)),
        )
        transcript = self.transcript("agent-c", [prompt_line(15), call_line("x", 16), call_line("y", 41)], "session")
        transcript.with_suffix(".meta.json").write_text(json.dumps({"toolUseId": "c"}))

    def log_readings(self, *readings: dict[str, Any]) -> None:
        (self.loop / "meter.jsonl").write_text("".join(json.dumps(reading) + "\n" for reading in readings))

    def machine_lines(self) -> list[str]:
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        return lines[next(index for index, line in enumerate(lines) if line.startswith("Free disk")) :]

    def test_gives_the_median_and_the_worst_of_each_number_and_its_median_for_each_number_of_agents(self) -> None:
        disk = {0: 100, 8: 98, 12: 97, 17: 95, 20: 96, 40: 90}
        swap = {0: 1, 8: 2, 12: 3, 17: 6, 20: 4, 40: None}
        before_the_time = meter_reading(-10, disk_free_gb=500, swap_used_gb=50)
        self.log_readings(
            before_the_time,
            *[meter_reading(minute, disk_free_gb=disk[minute], swap_used_gb=swap[minute]) for minute in disk],
        )
        self.assertEqual(
            self.machine_lines(),
            [
                "Free disk, GB: median 96.5, lowest 90.0, net change -10.0",
                "Free memory, GB: no readings",
                "Swap in use, GB: median 3.0, highest 6.0, grew in 85% of the time between readings",
                "GPU use, %: no readings",
                "GPU memory in use, GB: no readings",
                "Load: no readings",
                (
                    "Median with 1 agent, 2 readings: free disk 94.0, free memory none, swap in use 2.0, GPU use none, "
                    "GPU memory in use none, load none"
                ),
                (
                    "Median with 2 agents, 3 readings: free disk 97.0, free memory none, swap in use 3.0, GPU use "
                    "none, GPU memory in use none, load none"
                ),
                "Median with 3 agents: no readings",
                (
                    "Median with 4 agents, 1 reading: free disk 95.0, free memory none, swap in use 6.0, GPU use none, "
                    "GPU memory in use none, load none"
                ),
            ],
        )

    def test_gives_the_share_of_the_time_in_which_swap_grew_and_of_the_readings_with_a_load_above_the_cores(
        self,
    ) -> None:
        # Swap grows in 78 of the 90 minutes that count. With a longest gap of 60 minutes, the gap of 61 minutes from
        # minute 80 doesn't count, and neither do the minutes next to the reading without swap.
        patcher = mock.patch.object(report, "LONGEST_READING_GAP", dt.timedelta(minutes=60))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log_readings(
            meter_reading(0, cores=10, swap_used_gb=1.0, load=12),
            meter_reading(10, cores=10, swap_used_gb=1.5, load=10),
            meter_reading(12, cores=8, swap_used_gb=1.5, load=4),
            meter_reading(20, cores=8, swap_used_gb=2.0, load=9),
            meter_reading(80, swap_used_gb=3.0),
            meter_reading(141, swap_used_gb=4.0),
            meter_reading(151, swap_used_gb=4.0),
            meter_reading(161, swap_used_gb=None),
            meter_reading(171, swap_used_gb=5.0),
        )
        lines = self.machine_lines()
        self.assertIn("Swap in use, GB: median 2.5, highest 5.0, grew in 87% of the time between readings", lines)
        self.assertIn("Load: median 9.5, highest 12.0, above the cores in 50% of the readings", lines)

    def test_says_when_no_two_readings_in_a_row_give_swap(self) -> None:
        self.log_readings(meter_reading(8, swap_used_gb=None), meter_reading(12, swap_used_gb=2.0))
        self.assertEqual(
            self.line("Swap"), "Swap in use, GB: median 2.0, highest 2.0, no two readings in a row to compare"
        )

    def test_says_none_for_a_number_that_no_reading_with_that_many_agents_gives(self) -> None:
        self.log_readings(meter_reading(8, load=2.5, gpu_percent=40), meter_reading(12, load=4.5, gpu_percent=None))
        self.assertEqual(
            self.machine_lines()[-2:],
            [
                (
                    "Median with 1 agent, 1 reading: free disk none, free memory none, swap in use none, GPU use 40.0, "
                    "GPU memory in use none, load 2.5"
                ),
                (
                    "Median with 2 agents, 1 reading: free disk none, free memory none, swap in use none, GPU use "
                    "none, GPU memory in use none, load 4.5"
                ),
            ],
        )

    def test_counts_an_agent_without_an_end_line_until_the_last_line_of_its_transcript(self) -> None:
        self.log_readings(meter_reading(40.5, memory_free_gb=12), meter_reading(41.5, memory_free_gb=20))
        self.assertEqual(
            self.machine_lines()[-1],
            (
                "Median with 1 agent, 1 reading: free disk none, free memory 12.0, swap in use none, GPU use none, "
                "GPU memory in use none, load none"
            ),
        )

    def test_counts_a_run_without_an_end_line_until_the_last_line_of_its_events(self) -> None:
        self.log_agents(start("issue-12-run", 0))
        events = self.loop / "runs" / "issue-12-run" / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text('{"type": "turn.started"}\n')
        last_line = (SINCE + dt.timedelta(minutes=10)).timestamp()
        os.utime(events, (last_line, last_line))
        self.log_readings(meter_reading(5, load=2.0), meter_reading(15, load=8.0))
        self.assertEqual(
            self.machine_lines()[-1],
            (
                "Median with 1 agent, 1 reading: free disk none, free memory none, swap in use none, GPU use none, "
                "GPU memory in use none, load 2.0"
            ),
        )

    def test_leaves_out_the_time_that_an_agent_waited_for_a_message(self) -> None:
        self.log_agents(start("f", 0), worked("f", (0, 5), (20, 25)))
        self.log_readings(*[meter_reading(minute, load=load) for minute, load in ((3, 1.0), (10, 9.0), (22, 3.0))])
        self.assertEqual(
            self.machine_lines()[-1],
            (
                "Median with 1 agent, 2 readings: free disk none, free memory none, swap in use none, GPU use none, "
                "GPU memory in use none, load 2.0"
            ),
        )

    def test_gives_no_line_for_a_number_of_agents_when_no_agent_worked_at_any_reading(self) -> None:
        self.log_readings(meter_reading(50, load=1.0))
        self.assertEqual(
            self.machine_lines()[-1], "Load: median 1.0, highest 1.0, above the cores in 0% of the readings"
        )

    def test_leaves_out_the_readings_from_before_the_meter_read_the_machine(self) -> None:
        reading = meter_reading(8, disk_free_gb=50)
        del reading["machine"]
        self.log_readings(reading)
        self.assertEqual(self.line("Machine"), "Machine: no readings")
