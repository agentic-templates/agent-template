"""Reads how much of the machine is in use: its free disk, load, free memory, swap, GPU and CPU. It needs no root. A
number that the machine doesn't give is None, never 0."""

import ctypes
import functools
import os
import re
import resource
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, TypedDict

GIGABYTE = 1e9
MEBIBYTE = 1024 * 1024
KIBIBYTE = 1024
MEMINFO = Path("/proc/meminfo")
PROC_STAT = Path("/proc/stat")
# The flavor of host_statistics that gives the ticks that all the cores spent in user, system, idle and nice time
HOST_CPU_LOAD_INFO = 3
MACOS_MEMORY = ["sysctl", "-n", "hw.memsize", "vm.swapusage"]
VM_STAT = ["vm_stat"]
# Prints 1 on a Mac with Apple silicon. An Intel Mac doesn't know the name, so sysctl fails there.
APPLE_SILICON = ["sysctl", "-n", "hw.optional.arm64"]
IOREG = ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"]
COMMAND_TIMEOUT_SECONDS = 10
NVIDIA_SMI = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"]


class Machine(TypedDict):
    # The free disk of the disk that holds the folder that read_machine gets
    disk_free_gb: float
    # The load over the last minute
    load: float
    cores: int
    # The memory that programs can take without swapping: free memory, and memory that the system can take back, such
    # as the cache of files
    memory_free_gb: float | None
    swap_used_gb: float | None
    # The highest use of the machine's GPUs
    gpu_percent: float | None
    # The memory in use by the machine's GPUs
    gpu_memory_used_gb: float | None
    # Whether the GPU's memory is part of the main memory, as on a Mac with Apple silicon, so that memory_free_gb
    # already leaves it out. None when the reading can't tell, as on an Intel Mac, or has no GPU.
    gpu_shares_memory: bool | None


class CpuTicks(NamedTuple):
    """The ticks of the machine's clock that all its cores together spent busy, and in all, since the machine started."""

    busy: int
    total: int


class CpuTime(NamedTuple):
    """The CPU time that this process and the children that it waited for used, and the time on the clock, in seconds
    since a fixed point in the past."""

    used_seconds: float
    elapsed_seconds: float


class CpuTicksError(Exception):
    """The machine's CPU ticks can't be read. The message says from where."""


class Memory(NamedTuple):
    free_bytes: int | None
    swap_used_bytes: int | None


class Gpu(NamedTuple):
    percent: float | None
    memory_used_bytes: int | None
    shares_memory: bool | None


NO_GPU = Gpu(None, None, None)


def read_machine(folder: Path) -> Machine:
    """A reading of the machine now."""
    if sys.platform == "darwin":
        memory = macos_memory(output_of(MACOS_MEMORY), output_of(VM_STAT))
        gpu = macos_gpu(output_of(IOREG), is_apple_silicon=is_apple_silicon())
    else:
        memory = linux_memory(MEMINFO.read_text() if MEMINFO.exists() else "")
        gpu = nvidia_gpu(output_of(NVIDIA_SMI))
    return {
        "disk_free_gb": round(shutil.disk_usage(folder).free / GIGABYTE, 2),
        "load": round(os.getloadavg()[0], 2),
        "cores": os.cpu_count() or 1,
        "memory_free_gb": gigabytes(memory.free_bytes),
        "swap_used_gb": gigabytes(memory.swap_used_bytes),
        "gpu_percent": gpu.percent,
        "gpu_memory_used_gb": gigabytes(gpu.memory_used_bytes),
        "gpu_shares_memory": gpu.shares_memory,
    }


def read_cpu_ticks() -> CpuTicks:
    """The machine's CPU ticks now, which busy_cores compares with an earlier reading. The load can't show the idle
    cores, because it counts the processes that wait for the disk too: macOS gave a load of 69 on 10 cores while they
    were 54% idle."""
    if sys.platform == "darwin":
        load = host_cpu_load()
        ticks = None if load is None else macos_cpu_ticks(load)
        source = "host_statistics"
    else:
        ticks = linux_cpu_ticks(PROC_STAT.read_text() if PROC_STAT.exists() else "")
        source = str(PROC_STAT)
    if ticks is None:
        raise CpuTicksError(f"can't read the machine's CPU ticks from {source}")
    return ticks


def busy_cores(earlier: CpuTicks | None, later: CpuTicks | None, cores: int) -> float | None:
    """The cores that were busy on average between two readings of the CPU ticks. None without both, or when the
    counters started again from 0, as macOS's do after 49 days on 10 cores."""
    if earlier is None or later is None or later.total <= earlier.total or later.busy < earlier.busy:
        return None
    return round(cores * (later.busy - earlier.busy) / (later.total - earlier.total), 2)


def read_cpu_time() -> CpuTime:
    """The CPU time that this process and the children that it waited for used until now."""
    times = os.times()
    return CpuTime(times.user + times.system + times.children_user + times.children_system, times.elapsed)


class CpuReading(NamedTuple):
    """The machine's CPU ticks and this process's CPU time at the same moment."""

    ticks: CpuTicks
    time: CpuTime


def read_cpu() -> CpuReading:
    """The CPU ticks and this process's CPU time now, which busy_cores_besides compares with an earlier reading."""
    return CpuReading(read_cpu_ticks(), read_cpu_time())


def busy_cores_besides(earlier: CpuReading | None, later: CpuReading | None, cores: int) -> float | None:
    """The cores that were busy on average between two readings, other than the cores that this process and its
    children kept busy. None when busy_cores gives none, or without time between the readings."""
    if earlier is None or later is None:
        return None
    busy = busy_cores(earlier.ticks, later.ticks, cores)
    elapsed = later.time.elapsed_seconds - earlier.time.elapsed_seconds
    if busy is None or elapsed <= 0:
        return None
    own = (later.time.used_seconds - earlier.time.used_seconds) / elapsed
    # The busy cores and the CPU time come from separate counters, so the process can seem to use more than all.
    return round(max(busy - own, 0.0), 2)


@functools.cache
def system_library() -> ctypes.CDLL:  # pragma: no cover, because only macOS has the library
    library = ctypes.CDLL("/usr/lib/libSystem.dylib")
    library.mach_host_self.restype = ctypes.c_uint32
    return library


@functools.cache
def host_port() -> int:  # pragma: no cover, because only macOS has the library
    """The port of the machine, which each call of mach_host_self adds a reference to, so the process asks once."""
    port: int = system_library().mach_host_self()
    return port


def host_cpu_load() -> list[int] | None:  # pragma: no cover, because only macOS has host_statistics
    """The ticks that all the cores spent in user, system, idle and nice time, or None if host_statistics fails."""
    ticks = (ctypes.c_uint32 * 4)()
    count = ctypes.c_uint32(len(ticks))
    if system_library().host_statistics(host_port(), HOST_CPU_LOAD_INFO, ticks, ctypes.byref(count)) != 0:
        return None
    return list(ticks)


def macos_cpu_ticks(load: list[int]) -> CpuTicks:
    """The CPU ticks in the ticks of user, system, idle and nice time that host_statistics gives."""
    user, system, idle, nice = load
    return CpuTicks(user + system + nice, user + system + idle + nice)


def linux_cpu_ticks(stat: str) -> CpuTicks | None:
    """The CPU ticks in the first line of /proc/stat: user, nice, system, idle, iowait, irq, softirq and steal. The
    time that waits for the disk, iowait, counts as idle."""
    fields = stat.split("\n", 1)[0].split()
    if not fields or fields[0] != "cpu":
        return None
    ticks = [int(field) for field in fields[1:9]]
    user, nice, system, idle, iowait, irq, softirq, steal = ticks + [0] * (8 - len(ticks))
    busy = user + nice + system + irq + softirq + steal
    return CpuTicks(busy, busy + idle + iowait)


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and output_of(APPLE_SILICON).strip() == "1"


def peak_memory_bytes(usage: resource.struct_rusage) -> int:
    """The most memory that one of the processes that a resource usage covers used."""
    # macOS gives the memory in bytes, and Linux in kibibytes.
    return usage.ru_maxrss * (1 if sys.platform == "darwin" else KIBIBYTE)


def output_of(command: list[str]) -> str:
    """What a command prints, or nothing if it isn't installed, can't start, fails or doesn't end in time. nvidia-smi
    fails on a machine without an NVIDIA driver, and macOS doesn't start ps in Codex's sandbox, because ps is setuid."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except OSError, subprocess.TimeoutExpired:
        return ""
    return done.stdout if done.returncode == 0 else ""


def gigabytes(size: int | None) -> float | None:
    return None if size is None else round(size / GIGABYTE, 2)


def macos_memory(sysctl: str, vm_stat: str) -> Memory:
    """The memory in the output of sysctl -n hw.memsize vm.swapusage and of vm_stat. The free memory is the memory
    that Activity Monitor doesn't count as used: all of it but the memory of programs, the wired memory and the
    compressed memory. memory_pressure -Q gives no such number: its "free percentage" counts the memory of programs
    as free, and stayed the same when a program took 8 GB."""
    lines = sysctl.splitlines()
    total = int(lines[0]) if lines and lines[0].isdigit() else None
    swap = re.search(r"used = (\d+(?:\.\d+)?)M", sysctl)
    swap_used = round(float(swap[1]) * MEBIBYTE) if swap else None
    page_size = re.search(r"page size of (\d+) bytes", vm_stat)
    pages = {match[1]: int(match[2]) for match in re.finditer(r"^(.+?):\s+(\d+)\.$", vm_stat, re.MULTILINE)}
    used_names = ("Anonymous pages", "Pages wired down", "Pages occupied by compressor", "Pages purgeable")
    if total is None or not page_size or any(name not in pages for name in used_names):
        return Memory(None, swap_used)
    # The system can empty purgeable pages, which the anonymous pages include.
    used_pages = (
        pages["Anonymous pages"]
        - pages["Pages purgeable"]
        + pages["Pages wired down"]
        + pages["Pages occupied by compressor"]
    )
    return Memory(total - used_pages * int(page_size[1]), swap_used)


def macos_gpu(ioreg: str, is_apple_silicon: bool) -> Gpu:
    """The GPUs in the output of ioreg for the class IOAccelerator. The GPU of a Mac with Apple silicon takes its memory
    from the main memory. "Alloc system memory" rose by 8 GB when a program put 8 GB in a Metal buffer, while "In use
    system memory" rose by 50 MB, so the reading takes the first. An Intel Mac can have a GPU with its own memory, which
    ioreg doesn't give, so its reading has no GPU memory."""
    percents, memory = [], []
    for statistics in re.findall(r'"PerformanceStatistics" = \{(.*)\}', ioreg):
        percent = re.search(r'"Device Utilization %"=(\d+)', statistics)
        allocated = re.search(r'"Alloc system memory"=(\d+)', statistics)
        if percent:
            percents.append(float(percent[1]))
        if allocated:
            memory.append(int(allocated[1]))
    if not is_apple_silicon:
        return Gpu(max(percents), None, None) if percents else NO_GPU
    if not percents and not memory:
        return NO_GPU
    return Gpu(max(percents, default=None), sum(memory) if memory else None, True)


def linux_memory(meminfo: str) -> Memory:
    """The memory in the text of /proc/meminfo, which gives sizes in kB that are kibibytes."""
    sizes = {match[1]: int(match[2]) * KIBIBYTE for match in re.finditer(r"^(\w+):\s+(\d+) kB$", meminfo, re.MULTILINE)}
    free = sizes.get("MemAvailable")
    swap_used = sizes["SwapTotal"] - sizes["SwapFree"] if "SwapTotal" in sizes and "SwapFree" in sizes else None
    return Memory(free, swap_used)


def nvidia_gpu(nvidia_smi: str) -> Gpu:
    """The GPUs in the output of nvidia-smi, a line for each GPU with its use in percent and its memory in use in MiB.
    A GPU that can't give a number prints [N/A] in its place."""
    percents, memory = [], []
    for line in nvidia_smi.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 2:
            continue
        percent, used = fields
        if percent.isdigit():
            percents.append(float(percent))
        if used.isdigit():
            memory.append(int(used) * MEBIBYTE)
    if not percents and not memory:
        return NO_GPU
    return Gpu(max(percents, default=None), sum(memory) if memory else None, False)
