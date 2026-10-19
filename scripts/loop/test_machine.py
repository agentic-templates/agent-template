import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import machine

# Parts of what a Mac with Apple silicon printed, with round numbers
SYSCTL = "68719476736\ntotal = 3072.00M  used = 1797.00M  free = 1275.00M  (encrypted)\n"
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              539229.
Pages wired down:                       1000000.
Pages purgeable:                         100000.
"Translation faults":               58622274640.
File-backed pages:                      1238162.
Anonymous pages:                        1100000.
Pages occupied by compressor:             94304.
"""
IOREG = """+-o AGXAcceleratorG13X  <class AGXAcceleratorG13X, id 0x100000adb, registered, matched, active, busy 0>
    {
      "PerformanceStatistics" = {"In use system memory (driver)"=0,"Alloc system memory"=6624411648,"Tiler \
Utilization %"=27,"Renderer Utilization %"=26,"Device Utilization %"=27,"In use system memory"=715227136}
      "model" = "Apple M1 Max"
    }
"""
MEMINFO = """MemTotal:       16303428 kB
MemFree:         1209212 kB
MemAvailable:    9765624 kB
SwapCached:        10240 kB
SwapTotal:       4194300 kB
SwapFree:        3170300 kB
HugePages_Total:       0
"""
NVIDIA_SMI = "27, 3100\n81, 12000\n"

# A stand-in for a command, which prints the file in STANDIN_DIR named after the command and its arguments, or fails
# if there's none.
STANDIN = f"""#!{sys.executable} -S
import os, sys
answer = os.path.join(os.environ["STANDIN_DIR"], " ".join([os.path.basename(sys.argv[0]), *sys.argv[1:]]))
if not os.path.exists(answer):
    sys.exit(1)
sys.stdout.write(open(answer).read())
"""


class MacosMemoryTest(unittest.TestCase):
    def test_counts_all_memory_free_but_that_of_programs_the_wired_and_the_compressed(self) -> None:
        # 4,194,304 pages of 16,384 bytes, less 1,100,000 - 100,000 + 1,000,000 + 94,304 in use, leaves 2,100,000.
        self.assertEqual(machine.macos_memory(SYSCTL, VM_STAT), machine.Memory(2_100_000 * 16384, 1797 * 1024 * 1024))

    def test_gives_no_free_memory_without_vm_stat(self) -> None:
        self.assertEqual(machine.macos_memory(SYSCTL, ""), machine.Memory(None, 1797 * 1024 * 1024))

    def test_gives_no_free_memory_when_vm_stat_leaves_out_a_count(self) -> None:
        without_compressor = VM_STAT.replace("Pages occupied by compressor:             94304.\n", "")
        self.assertEqual(machine.macos_memory(SYSCTL, without_compressor).free_bytes, None)

    def test_gives_nothing_without_sysctl(self) -> None:
        self.assertEqual(machine.macos_memory("", VM_STAT), machine.Memory(None, None))


class MacosGpuTest(unittest.TestCase):
    def test_reads_the_use_and_the_memory_that_the_gpu_takes_of_the_main_memory(self) -> None:
        self.assertEqual(machine.macos_gpu(IOREG, is_apple_silicon=True), machine.Gpu(27.0, 6624411648, True))

    def test_gives_only_the_use_of_the_gpu_of_an_intel_mac(self) -> None:
        self.assertEqual(machine.macos_gpu(IOREG, is_apple_silicon=False), machine.Gpu(27.0, None, None))
        no_use = '"PerformanceStatistics" = {"Alloc system memory"=1000}'
        self.assertEqual(machine.macos_gpu(no_use, is_apple_silicon=False), machine.Gpu(None, None, None))

    def test_takes_the_busiest_gpu_and_the_memory_of_all(self) -> None:
        second = IOREG.replace('"Device Utilization %"=27', '"Device Utilization %"=60')
        self.assertEqual(
            machine.macos_gpu(IOREG + second, is_apple_silicon=True), machine.Gpu(60.0, 2 * 6624411648, True)
        )

    def test_gives_no_gpu_reading_without_statistics(self) -> None:
        self.assertEqual(machine.macos_gpu("", is_apple_silicon=True), machine.Gpu(None, None, None))
        no_numbers = '"PerformanceStatistics" = {"recoveryCount"=3}'
        self.assertEqual(machine.macos_gpu(no_numbers, is_apple_silicon=True), machine.Gpu(None, None, None))

    def test_gives_the_numbers_that_the_statistics_hold(self) -> None:
        only_memory = '"PerformanceStatistics" = {"Alloc system memory"=1000}'
        self.assertEqual(machine.macos_gpu(only_memory, is_apple_silicon=True), machine.Gpu(None, 1000, True))
        only_use = '"PerformanceStatistics" = {"Device Utilization %"=5}'
        self.assertEqual(machine.macos_gpu(only_use, is_apple_silicon=True), machine.Gpu(5.0, None, True))


class LinuxMemoryTest(unittest.TestCase):
    def test_reads_the_available_memory_and_the_swap_in_use(self) -> None:
        self.assertEqual(machine.linux_memory(MEMINFO), machine.Memory(9765624 * 1024, 1024000 * 1024))

    def test_gives_nothing_that_meminfo_leaves_out(self) -> None:
        self.assertEqual(machine.linux_memory("MemTotal:       16303428 kB\n"), machine.Memory(None, None))
        self.assertEqual(machine.linux_memory(""), machine.Memory(None, None))


class CpuTicksTest(unittest.TestCase):
    def test_counts_the_time_that_waits_for_the_disk_as_idle(self) -> None:
        # user, nice, system, idle, iowait, irq, softirq, steal, guest and guest_nice
        stat = "cpu  100 20 30 400 500 6 7 8 90 10\ncpu0 50 10 15 200 250 3 3 4 45 5\nintr 12345\n"
        self.assertEqual(machine.linux_cpu_ticks(stat), machine.CpuTicks(171, 1071))

    def test_reads_the_ticks_of_an_older_kernel_that_gives_fewer(self) -> None:
        self.assertEqual(machine.linux_cpu_ticks("cpu 100 20 30 400\n"), machine.CpuTicks(150, 550))

    def test_gives_no_ticks_without_the_line_of_all_the_cores(self) -> None:
        self.assertIsNone(machine.linux_cpu_ticks(""))
        self.assertIsNone(machine.linux_cpu_ticks("intr 12345\n"))

    def test_gives_the_cores_that_were_busy_between_two_readings(self) -> None:
        earlier, later = machine.CpuTicks(1000, 5000), machine.CpuTicks(1460, 6000)
        self.assertEqual(machine.busy_cores(earlier, later, 10), 4.6)

    def test_gives_no_busy_cores_without_two_readings_in_order(self) -> None:
        later = machine.CpuTicks(1460, 6000)
        self.assertIsNone(machine.busy_cores(None, later, 10))
        self.assertIsNone(machine.busy_cores(later, None, 10))
        self.assertIsNone(machine.busy_cores(later, later, 10))
        # The counters of macOS start again from 0 after 49 days on 10 cores.
        self.assertIsNone(machine.busy_cores(later, machine.CpuTicks(10, 7000), 10))

    def test_reads_the_ticks_of_a_linux_machine_and_says_when_proc_stat_gives_none(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(sys, "platform", "linux"):
            stat = Path(folder) / "stat"
            with mock.patch.object(machine, "PROC_STAT", stat):
                for text in (None, "intr 12345\n"):
                    if text is not None:
                        stat.write_text(text)
                    with self.subTest(stat=text), self.assertRaises(machine.CpuTicksError) as raised:
                        machine.read_cpu_ticks()
                    self.assertEqual(str(raised.exception), f"can't read the machine's CPU ticks from {stat}")
                stat.write_text("cpu 100 20 30 400\n")
                self.assertEqual(machine.read_cpu_ticks(), machine.CpuTicks(150, 550))

    def test_counts_user_system_and_nice_time_of_a_mac_as_busy(self) -> None:
        # user, system, idle and nice, in the order of host_statistics
        load = [100, 50, 800, 10]
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(machine, "host_cpu_load", lambda: load):
            self.assertEqual(machine.read_cpu_ticks(), machine.CpuTicks(160, 960))

    def test_says_when_host_statistics_gives_no_ticks(self) -> None:
        with (
            mock.patch.object(sys, "platform", "darwin"),
            mock.patch.object(machine, "host_cpu_load", lambda: None),
            self.assertRaises(machine.CpuTicksError) as raised,
        ):
            machine.read_cpu_ticks()
        self.assertEqual(str(raised.exception), "can't read the machine's CPU ticks from host_statistics")


class NvidiaGpuTest(unittest.TestCase):
    def test_takes_the_busiest_gpu_and_the_memory_of_all_as_their_own(self) -> None:
        self.assertEqual(machine.nvidia_gpu(NVIDIA_SMI), machine.Gpu(81.0, 15100 * 1024 * 1024, False))

    def test_gives_no_number_that_the_gpu_does_not_give(self) -> None:
        self.assertEqual(machine.nvidia_gpu("[N/A], 512\n"), machine.Gpu(None, 512 * 1024 * 1024, False))
        self.assertEqual(machine.nvidia_gpu("40, [N/A]\n"), machine.Gpu(40.0, None, False))

    def test_gives_no_gpu_reading_without_a_gpu(self) -> None:
        self.assertEqual(machine.nvidia_gpu(""), machine.Gpu(None, None, None))
        self.assertEqual(machine.nvidia_gpu("[N/A], [N/A]\nNo devices were found\n"), machine.Gpu(None, None, None))


class ReadMachineTest(unittest.TestCase):
    """read_machine with stand-ins for the commands that it runs, alone on PATH, so that the machine's own commands
    don't answer."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.answers = self.root / "answers"
        self.bin = self.root / "bin"
        self.answers.mkdir()
        self.bin.mkdir()
        for command in ("sysctl", "vm_stat", "ioreg", "nvidia-smi"):
            (self.bin / command).write_text(STANDIN)
            (self.bin / command).chmod(0o755)
        environment = {"PATH": str(self.bin), "STANDIN_DIR": str(self.answers)}
        for patcher in (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(machine, "MEMINFO", self.root / "meminfo"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def answer(self, command: list[str], text: str) -> None:
        (self.answers / " ".join(command)).write_text(text)

    def answer_mac(self) -> None:
        for command, text in ((machine.MACOS_MEMORY, SYSCTL), (machine.VM_STAT, VM_STAT), (machine.IOREG, IOREG)):
            self.answer(command, text)

    def read(self, platform: str) -> machine.Machine:
        with mock.patch.object(sys, "platform", platform):
            return machine.read_machine(self.root)

    def test_reads_each_number_on_a_mac_whose_gpu_shares_the_main_memory(self) -> None:
        self.answer_mac()
        self.answer(machine.APPLE_SILICON, "1\n")
        reading = self.read("darwin")
        self.assertGreater(reading["disk_free_gb"], 0)
        self.assertGreaterEqual(reading["load"], 0)
        self.assertGreater(reading["cores"], 0)
        self.assertEqual(
            {name: value for name, value in reading.items() if name not in ("disk_free_gb", "load", "cores")},
            {
                "memory_free_gb": 34.41,
                "swap_used_gb": 1.88,
                "gpu_percent": 27.0,
                "gpu_memory_used_gb": 6.62,
                "gpu_shares_memory": True,
            },
        )

    def test_reads_each_number_on_linux_with_an_nvidia_gpu(self) -> None:
        (self.root / "meminfo").write_text(MEMINFO)
        self.answer(machine.NVIDIA_SMI, NVIDIA_SMI)
        reading = self.read("linux")
        self.assertEqual(
            (reading["memory_free_gb"], reading["swap_used_gb"], reading["gpu_percent"]), (10.0, 1.05, 81.0)
        )
        self.assertEqual((reading["gpu_memory_used_gb"], reading["gpu_shares_memory"]), (15.83, False))

    def test_reads_no_gpu_memory_on_an_intel_mac(self) -> None:
        self.answer_mac()
        reading = self.read("darwin")
        self.assertEqual(
            (reading["gpu_percent"], reading["gpu_memory_used_gb"], reading["gpu_shares_memory"]), (27.0, None, None)
        )
        self.assertEqual(reading["memory_free_gb"], 34.41)

    def test_records_the_numbers_that_a_failing_command_does_not_give_as_missing(self) -> None:
        self.answer(machine.MACOS_MEMORY, SYSCTL)
        reading = self.read("darwin")
        self.assertEqual((reading["memory_free_gb"], reading["swap_used_gb"]), (None, 1.88))
        self.assertEqual(
            (reading["gpu_percent"], reading["gpu_memory_used_gb"], reading["gpu_shares_memory"]), (None,) * 3
        )

    def test_records_the_numbers_of_a_linux_machine_without_meminfo_or_nvidia_smi_as_missing(self) -> None:
        (self.bin / "nvidia-smi").unlink()
        reading = self.read("linux")
        self.assertEqual(
            [reading[name] for name in ("memory_free_gb", "swap_used_gb", "gpu_percent", "gpu_memory_used_gb")],
            [None] * 4,
        )
        self.assertIsNone(reading["gpu_shares_memory"])

    def test_records_the_numbers_of_a_command_that_does_not_end_in_time_as_missing(self) -> None:
        self.answer(machine.NVIDIA_SMI, NVIDIA_SMI)
        (self.bin / "nvidia-smi").write_text("#!/bin/sh\nexec /bin/sleep 30\n")
        with mock.patch.object(machine, "COMMAND_TIMEOUT_SECONDS", 0.2):
            self.assertIsNone(self.read("linux")["gpu_percent"])
