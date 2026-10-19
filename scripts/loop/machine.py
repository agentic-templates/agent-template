"""Reads how much of the machine is in use: its free disk, load, free memory, swap, and GPU. It needs no root. A number
that the machine doesn't give is None, never 0."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, TypedDict

GIGABYTE = 1e9
MEBIBYTE = 1024 * 1024
KIBIBYTE = 1024
MEMINFO = Path("/proc/meminfo")
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
    # already leaves it out. None without a GPU reading.
    gpu_shares_memory: bool | None


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
        memory = macos_memory(output_of(["sysctl", "-n", "hw.memsize", "vm.swapusage"]), output_of(["vm_stat"]))
        gpu = macos_gpu(output_of(IOREG))
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


def output_of(command: list[str]) -> str:
    """What a command prints, or nothing if it isn't installed, fails or doesn't end in time. nvidia-smi fails on a
    machine without an NVIDIA driver."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except FileNotFoundError, subprocess.TimeoutExpired:
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


def macos_gpu(ioreg: str) -> Gpu:
    """The GPUs in the output of ioreg for the class IOAccelerator. A Mac's GPU takes its memory from the main memory.
    "Alloc system memory" rose by 8 GB when a program put 8 GB in a Metal buffer, while "In use system memory" rose by
    50 MB, so the reading takes the first."""
    percents, memory = [], []
    for statistics in re.findall(r'"PerformanceStatistics" = \{(.*)\}', ioreg):
        percent = re.search(r'"Device Utilization %"=(\d+)', statistics)
        allocated = re.search(r'"Alloc system memory"=(\d+)', statistics)
        if percent:
            percents.append(float(percent[1]))
        if allocated:
            memory.append(int(allocated[1]))
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
