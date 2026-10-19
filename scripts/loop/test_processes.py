import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import processes
from room import Use

# Parts of what ps and ioreg printed on a Mac with Apple silicon
PROCESSES = """  PID  PPID    RSS
  100     1  10240
  200   100   2048
  300   200   1024
  400     1  99999

"""
GPU_CLIENTS = """+-o AGXDeviceUserClient  <class AGXDeviceUserClient, id 0x100000ef6, !registered, !matched, active, busy 0>
    {
      "AppUsage" = ()
      "IOUserClientCreator" = "pid 400, WindowServer"
    }

+-o AGXDeviceUserClient  <class AGXDeviceUserClient, id 0x100000ef8, !registered, !matched, active, busy 0>
    {
      "AppUsage" = ({"API"="Metal","lastSubmittedTime"=1176,"accumulatedGPUTime"=1000000000},{"API"="Metal",\
"lastSubmittedTime"=0,"accumulatedGPUTime"=250000000})
      "IOUserClientCreator" = "pid 200, train"
    }

+-o AGXDeviceUserClient  <class AGXDeviceUserClient, id 0x100000ef9, !registered, !matched, active, busy 0>
    {
      "AppUsage" = ({"API"="Metal","lastSubmittedTime"=1176,"accumulatedGPUTime"=250000000})
      "IOUserClientCreator" = "pid 200, train"
    }

+-o IOGPUSurfaceMTL  <class IOGPUSurfaceMTL, id 0x10003f4b4, !registered, !matched, active, busy 0>
    {
    }
"""


def stat_line(pid: int, name: str, utime: int, stime: int, cutime: int, cstime: int) -> str:
    """A line of /proc/<pid>/stat, with the CPU ticks of the process and of the children that it waited for."""
    fields = ["S", "1", str(pid), str(pid), "0", "-1", "4194560", "100", "0", "0", "0"]
    rest = ["20", "0", "1", "0", "12345", "1000000", "250"]
    return f"{pid} ({name}) {' '.join([*fields, str(utime), str(stime), str(cutime), str(cstime), *rest])}\n"


class LinuxTest(unittest.TestCase):
    """A test on Linux, with a /proc that gives the CPU ticks of the processes 100, 200 and 300."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.proc = Path(folder.name)
        self.ticks_per_second = os.sysconf("SC_CLK_TCK")
        self.write_stat(100, "bash", 10, 5, 300, 100)
        self.write_stat(200, "python3 (tests)", 0, 0, 0, 0)
        self.write_stat(300, "sleep", 1, 1, 0, 0)
        for patcher in (mock.patch.object(sys, "platform", "linux"), mock.patch.object(processes, "PROC", self.proc)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_stat(self, pid: int, name: str, utime: int, stime: int, cutime: int, cstime: int) -> None:
        (self.proc / str(pid)).mkdir(exist_ok=True)
        (self.proc / str(pid) / "stat").write_text(stat_line(pid, name, utime, stime, cutime, cstime))


class TreeTest(unittest.TestCase):
    def test_finds_a_command_and_each_process_that_it_started_with_their_memory(self) -> None:
        self.assertEqual(processes.tree_memory(PROCESSES, 100), ({100, 200, 300}, (10240 + 2048 + 1024) * 1024))

    def test_gives_a_command_that_ended_no_memory(self) -> None:
        self.assertEqual(processes.tree_memory(PROCESSES, 600), ({600}, 0))


class CpuTest(LinuxTest):
    def test_counts_the_cpu_of_each_process_and_of_the_children_that_it_waited_for(self) -> None:
        self.assertEqual(processes.cpu_seconds({100, 200, 300}), (10 + 5 + 300 + 100 + 1 + 1) / self.ticks_per_second)

    def test_counts_no_cpu_for_a_process_that_ended(self) -> None:
        self.assertEqual(processes.cpu_seconds({300, 999}), 2 / self.ticks_per_second)


def rusage(user: int, system: int, child_user: int, child_system: int) -> bytes:
    """A result of proc_pid_rusage with the times of a process and of the children that it waited for."""
    result = bytearray(processes.RUSAGE_BYTES)
    struct.pack_into("QQ", result, processes.OWN_TIMES_OFFSET, user, system)
    struct.pack_into("QQ", result, processes.CHILD_TIMES_OFFSET, child_user, child_system)
    return bytes(result)


class MacosCpuTest(unittest.TestCase):
    def test_counts_the_cpu_of_each_process_and_of_the_children_that_it_waited_for_in_mach_time(self) -> None:
        # On a Mac with Apple silicon a unit of mach time is 125/3 nanoseconds, so 24,000,000 units are a second.
        results = {100: rusage(24_000_000, 12_000_000, 48_000_000, 0), 200: rusage(0, 0, 0, 12_000_000), 300: None}
        with (
            mock.patch.object(sys, "platform", "darwin"),
            mock.patch.object(processes, "process_rusage", results.get),
            mock.patch.object(processes, "mach_timebase", lambda: (125, 3)),
        ):
            self.assertAlmostEqual(processes.cpu_seconds({100, 200, 300}), 4.0)


class GpuTest(unittest.TestCase):
    def test_reads_the_gpu_time_of_each_process(self) -> None:
        self.assertEqual(processes.gpu_nanoseconds(GPU_CLIENTS), {400: 0, 200: 1_500_000_000})


class SamplerTest(LinuxTest):
    def test_measures_the_cores_memory_and_share_of_the_gpu_since_the_sample_before(self) -> None:
        outputs = {"ps": PROCESSES, "ioreg": GPU_CLIENTS}
        cores = 4 * self.ticks_per_second
        with mock.patch.object(processes, "output_of", lambda command: outputs[command[0]]):
            sampler = processes.Sampler(100, reads_gpu=True, at=10.0)
            self.write_stat(100, "bash", 10, 5, 300 + cores, 100 + cores // 2)
            used = sampler.sample(13.0)
            # The process 300 ended, so ps and ioreg no longer give it. Its CPU time moves to its parent, which waited
            # for it, and its GPU time stays counted.
            outputs["ioreg"] = ""
            outputs["ps"] = PROCESSES.replace("  300   200   1024\n", "")
            self.write_stat(200, "python3 (tests)", 0, 0, 1, 1)
            self.assertEqual(sampler.sample(15.0), Use(0.0, (10240 + 2048) * 1024 / 1e9, 0.0))
        total_seconds = (10 + 5 + 300 + 100 + 1 + 1 + cores + cores // 2) / self.ticks_per_second
        self.assertEqual(used, Use(total_seconds / 3, (10240 + 2048 + 1024) * 1024 / 1e9, 50.0))
        self.assertEqual(sampler.gpu_total(), 1_500_000_000)
        self.assertEqual(sampler.peak_memory_bytes, (10240 + 2048 + 1024) * 1024)

    def test_measures_no_gpu_on_a_machine_that_does_not_give_the_gpu_time_of_each_process(self) -> None:
        with mock.patch.object(
            processes, "output_of", lambda command: PROCESSES if command[0] == "ps" else GPU_CLIENTS
        ):
            sampler = processes.Sampler(100, reads_gpu=False, at=10.0)
            self.assertEqual(sampler.sample(13.0).gpu_percent, 0.0)
        self.assertEqual(sampler.gpu_total(), 0)
