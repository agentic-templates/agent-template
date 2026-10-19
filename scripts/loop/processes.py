"""Measures what the processes of a running command use: its own process, and each that it started."""

import ctypes
import functools
import os
import re
import struct
import sys
from pathlib import Path

from machine import GIGABYTE, KIBIBYTE, output_of, system_library
from room import Use

PROCESSES = ["ps", "-A", "-o", "pid=,ppid=,rss="]
GPU_CLIENTS = ["ioreg", "-r", "-c", "AGXDeviceUserClient", "-w", "0", "-d", "1"]
PROC = Path("/proc")
NANOSECONDS = 1e9
# The flavor of proc_pid_rusage that first gives the CPU time of the children that a process waited for, the offsets
# of the times in its result, and room for more than the 160 bytes of that result
RUSAGE_INFO_V2 = 2
OWN_TIMES_OFFSET = 16
CHILD_TIMES_OFFSET = 96
RUSAGE_BYTES = 512


class Sampler:
    """Measures the processes of a running command. Its samples give what the processes use now: their memory, and
    their cores and share of the GPU since the sample before."""

    def __init__(self, pid: int, reads_gpu: bool, at: float) -> None:
        self.pid = pid
        self.reads_gpu = reads_gpu
        self.peak_memory_bytes = 0
        self.gpu_nanoseconds: dict[int, int] = {}
        self.last_sample = (at, 0.0, 0)

    def sample(self, at: float) -> Use:
        pids, memory = tree_memory(output_of(PROCESSES), self.pid)
        self.peak_memory_bytes = max(self.peak_memory_bytes, memory)
        if self.reads_gpu:
            for pid, used in gpu_nanoseconds(output_of(GPU_CLIENTS)).items():
                if pid in pids:
                    self.gpu_nanoseconds[pid] = max(used, self.gpu_nanoseconds.get(pid, 0))
        cpu = cpu_seconds(pids)
        last_at, last_cpu, last_gpu = self.last_sample
        self.last_sample = (at, cpu, self.gpu_total())
        seconds = at - last_at
        return Use((cpu - last_cpu) / seconds, memory / GIGABYTE, gpu_percent(self.gpu_total() - last_gpu, seconds))

    def gpu_total(self) -> int:
        return sum(self.gpu_nanoseconds.values())


def tree_memory(processes: str, root: int) -> tuple[set[int], int]:
    """The ids of a process and of each process that it started, and the memory in bytes that they use, from the
    output of ps -A -o pid=,ppid=,rss=, which gives the memory in kibibytes."""
    children: dict[int, list[int]] = {}
    memory: dict[int, int] = {}
    for line in processes.splitlines():
        fields = line.split()
        if len(fields) != 3 or not all(field.isdigit() for field in fields):
            continue
        pid, parent, kibibytes = map(int, fields)
        children.setdefault(parent, []).append(pid)
        memory[pid] = kibibytes * KIBIBYTE
    tree: set[int] = set()
    unvisited = [root]
    while unvisited:
        pid = unvisited.pop()
        tree.add(pid)
        unvisited += children.get(pid, [])
    return tree, sum(memory.get(pid, 0) for pid in tree)


def cpu_seconds(pids: set[int]) -> float:
    """The CPU time of the processes, with that of each child that they waited for, so that the time of a process that
    ended stays counted in its parent. A process that ended since its id was read gives none."""
    if sys.platform == "darwin":
        seconds = sum(macos_cpu_seconds(process_rusage(pid), mach_timebase()) for pid in pids)
    else:
        seconds = sum(linux_cpu_seconds(pid) for pid in pids)
    return seconds


def linux_cpu_seconds(pid: int) -> float:
    try:
        stat = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return 0.0
    return process_ticks(stat) / os.sysconf("SC_CLK_TCK")


def process_ticks(stat: str) -> int:
    """The ticks of the process and of the children that it waited for, the fields utime, stime, cutime and cstime,
    which are the 14th to the 17th of /proc/<pid>/stat. The second field, the command's name in parentheses, can hold
    spaces, so the fields count from the last parenthesis, which the third follows."""
    fields = stat.rsplit(")", 1)[1].split()
    return sum(int(field) for field in fields[11:15])


@functools.cache
def process_library() -> ctypes.CDLL:  # pragma: no cover, because only macOS has the library
    return ctypes.CDLL("/usr/lib/libproc.dylib")


@functools.cache
def mach_timebase() -> tuple[int, int]:  # pragma: no cover, because only macOS has the library
    """The nanoseconds in a unit of mach time, as a fraction: 125/3 on a Mac with Apple silicon."""

    class Timebase(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

    timebase = Timebase()
    system_library().mach_timebase_info(ctypes.byref(timebase))
    return timebase.numer, timebase.denom


def process_rusage(pid: int) -> bytes | None:  # pragma: no cover, because only macOS has proc_pid_rusage
    """The result of proc_pid_rusage for a process, or None for a process that ended."""
    usage = ctypes.create_string_buffer(RUSAGE_BYTES)
    if process_library().proc_pid_rusage(pid, RUSAGE_INFO_V2, usage) != 0:
        return None
    return usage.raw


def macos_cpu_seconds(rusage: bytes | None, timebase: tuple[int, int]) -> float:
    """The CPU time of a process and of the children that it waited for, from the result of proc_pid_rusage, which
    gives the times in units of mach time."""
    if rusage is None:
        return 0.0
    user, system = struct.unpack_from("QQ", rusage, OWN_TIMES_OFFSET)
    child_user, child_system = struct.unpack_from("QQ", rusage, CHILD_TIMES_OFFSET)
    numerator, denominator = timebase
    return float(user + system + child_user + child_system) * numerator / denominator / NANOSECONDS


def gpu_nanoseconds(clients: str) -> dict[int, int]:
    """The GPU time in nanoseconds that each process has used, from the output of ioreg for the clients of the GPU of a
    Mac with Apple silicon. A process's clients, and their times, go away when it ends."""
    used: dict[int, int] = {}
    for client in clients.split("+-o ")[1:]:
        creator = re.search(r'"IOUserClientCreator" = "pid (\d+),', client)
        if creator:
            times = re.findall(r'"accumulatedGPUTime"=(\d+)', client)
            used[int(creator[1])] = used.get(int(creator[1]), 0) + sum(map(int, times))
    return used


def gpu_percent(nanoseconds: int, seconds: float) -> float:
    return 100 * nanoseconds / (seconds * NANOSECONDS)
