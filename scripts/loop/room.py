"""Decides which heavy commands start, from the machine's readings and the commands that wait and run, as
scripts/loop/run-heavy describes."""

import math
from collections.abc import Iterable
from typing import NamedTuple

from machine import Machine

# The scheduler's own numbers. A retrospective changes them from the lines on heavy commands that scripts/loop/report
# gives.
# The room that the scheduler leaves to the agents and to everything else that runs outside it
KEEP_FREE_CORES = 0.5
KEEP_FREE_MEMORY_GB = 2.0
KEEP_FREE_GPU_PERCENT = 10.0
# A command reserves the most that it used in this many of its last runs.
RUNS_PER_NEED = 3


class Use(NamedTuple):
    """What a command uses or needs, or the room that the machine has for it."""

    cores: float
    memory_gb: float
    gpu_percent: float


NO_ROOM = Use(-math.inf, -math.inf, -math.inf)
NOTHING = Use(0.0, 0.0, 0.0)


class Command(NamedTuple):
    """A heavy command that waits or runs."""

    id: str
    arrived: float
    # What the command reserves, or None for a first run
    need: Use | None
    # When it started, or None while it waits
    started: float | None = None
    # What its processes used at their last sample
    using: Use = NOTHING
    # Whether a retrospective's test build or auditor runs it
    is_retrospective: bool = False


class Run(NamedTuple):
    """A run of a command that ended, with the command's words and what it used."""

    command: list[str]
    used: Use


def need_of(command: list[str], runs: list[Run]) -> Use | None:
    """What a command reserves, from the runs that ended before it starts, oldest first: the most that it used in its
    last runs. A command that has no run of its own takes the runs of the commands that share the most leading words
    with it, the program at least, so that cargo test bar reserves what cargo test foo used. None when no run shares
    its program, which makes the command's run a first run."""
    own = [run.used for run in runs if run.command == command]
    if own:
        return most_of(own)
    closest = max((shared_words(run.command, command) for run in runs), default=0)
    if closest == 0:
        return None
    return most_of([run.used for run in runs if shared_words(run.command, command) == closest])


def shared_words(first: list[str], second: list[str]) -> int:
    """How many leading words two commands share."""
    count = 0
    for first_word, second_word in zip(first, second):
        if first_word != second_word:
            break
        count += 1
    return count


def most_of(uses: list[Use]) -> Use:
    """The most of each number in the last uses. A run gets fewer cores while other programs crowd the machine, so the
    most of several runs is the closest to what the command uses when it has room."""
    recent = uses[-RUNS_PER_NEED:]
    return Use(
        max(use.cores for use in recent),
        max(use.memory_gb for use in recent),
        max(use.gpu_percent for use in recent),
    )


def free_room(machine: Machine, busy_cores: float | None) -> Use:
    """The room that the readings show: the idle cores, the free memory and the idle share of the GPU. A number that
    the machine doesn't give leaves unlimited room, but the cores have no room until two readings of the CPU ticks give
    how many are busy."""
    memory = machine["memory_free_gb"]
    gpu = machine["gpu_percent"]
    return Use(
        -math.inf if busy_cores is None else machine["cores"] - busy_cores,
        math.inf if memory is None else memory,
        math.inf if gpu is None else 100 - gpu,
    )


def has_room(need: Use | None, room: Use) -> bool:
    """Whether a command fits in the room. A number that the command doesn't need never stops it, even when other
    programs take more than the room kept free. A first run, whose need is unknown, fits once some cores and memory are
    left, whatever the GPU's room, because few commands use the GPU."""
    if need is None:
        return room.cores > 0 and room.memory_gb > 0
    return all(wanted <= free for wanted, free in zip(need, room, strict=True) if wanted > 0)


def room_alone(free: Use, running: list[Command]) -> Use:
    """The room that the machine would have with no heavy command running, from the room that the readings show, less
    the room that the scheduler keeps free."""
    keep_free = Use(KEEP_FREE_CORES, KEEP_FREE_MEMORY_GB, KEEP_FREE_GPU_PERCENT)
    return subtract(free, [keep_free, *(negative(command.using) for command in running)])


def commands_to_start(waiting: list[Command], running: list[Command], alone: Use) -> list[str]:
    """The ids of the waiting commands that start now, given the commands that run and the room that the machine would
    have with none of them. A retrospective's command starts only when no other agent's command waits, because each
    minute that a builder's command waits is a minute of its run. Its test builds all wait alike, so its comparison of
    them stays fair."""
    others = [command for command in waiting if not command.is_retrospective]
    started = starting_in_line(others, running, alone)
    if len(started) < len(others):
        return [command.id for command in started]
    retrospectives = [command for command in waiting if command.is_retrospective]
    started += starting_in_line(retrospectives, [*running, *started], alone)
    return [command.id for command in started]


def starting_in_line(waiting: list[Command], running: list[Command], alone: Use) -> list[Command]:
    """The waiting commands that start now, in the order in which they came.

    The first in line starts when it fits. When no heavy command runs, it starts even if it doesn't fit, so that a
    command that is too big for the machine still runs. A command behind it starts ahead of it only if both fit once
    the commands that run now end, so that it can't keep the first in line waiting longer."""
    room = room_now(alone, running)
    started: list[Command] = []
    head: Command | None = None
    room_behind_head = NO_ROOM
    for command in sorted(waiting, key=lambda command: (command.arrived, command.id)):
        if head is None:
            if has_room(command.need, room) or not (running or started):
                started.append(command)
                room = taken(room, command.need)
            else:
                head = command
                room_behind_head = room_behind(head, running, alone)
            continue
        if command.need is not None and has_room(command.need, room) and has_room(command.need, room_behind_head):
            started.append(command)
            room = taken(room, command.need)
            room_behind_head = taken(room_behind_head, command.need)
    return started


def room_now(alone: Use, running: list[Command]) -> Use:
    """The room that the running commands leave. A first run takes all of it, so that it measures what it uses."""
    if any(command.need is None for command in running):
        return NO_ROOM
    return subtract(alone, [counted(command) for command in running])


def room_behind(head: Command, running: list[Command], alone: Use) -> Use:
    """The room that the commands behind the first in line may take: what the first leaves once the commands ahead of
    it end, less the room of the commands that already started ahead of it."""
    if head.need is None:
        return NO_ROOM
    started_ahead = [counted(command) for command in running if command.arrived > head.arrived]
    return subtract(alone, [head.need, *started_ahead])


def counted(command: Command) -> Use:
    """The room that a running command takes: its need, or what it uses now where that is more. So a command that just
    started counts at its need until the readings show its use."""
    need = NOTHING if command.need is None else command.need
    return Use(*(max(needed, used) for needed, used in zip(need, command.using, strict=True)))


def taken(room: Use, need: Use | None) -> Use:
    """The room that is left after a command starts in it."""
    return NO_ROOM if need is None else subtract(room, [need])


def negative(use: Use) -> Use:
    return Use(*(-number for number in use))


def subtract(room: Use, uses: Iterable[Use]) -> Use:
    left = list(room)
    for use in uses:
        left = [free - used for free, used in zip(left, use, strict=True)]
    return Use(*left)
