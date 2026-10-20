"""Reads what scripts/hooks/hand-off leaves: its thresholds, and the notes in which it records that it told an agent to
hand off its work."""

import datetime as dt
import re
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "hand-off"
FOLDER_NAME = "hand-offs"
# The names that the hook gives the notes for the account's room, after the agent: <agent>.<name>
ROOM_NOTES = ("room-soon", "room-now")


def note_files(hand_offs: Path) -> set[str]:
    """The names of the files in which scripts/hooks/hand-off notes that it told an agent to hand off its work, each
    <agent id or session id>.<threshold> for a note about the agent's context, and <agent id or session id>.room-soon
    or .room-now for one about its account's room."""
    return {file.name for file in hand_offs.glob("*.*")}


def hand_off_thresholds(notes: set[str]) -> dict[str, int]:
    """The highest threshold at which scripts/hooks/hand-off told each agent to hand off its work."""
    thresholds: dict[str, int] = {}
    for note in notes:
        agent, _, threshold = note.rpartition(".")
        if threshold.isdigit():
            thresholds[agent] = max(thresholds.get(agent, 0), int(threshold))
    return thresholds


def told_for_room(notes: set[str]) -> set[str]:
    """The agents that scripts/hooks/hand-off told to hand off their work for their account's room."""
    return {agent for agent, _, name in (note.rpartition(".") for note in notes) if name in ROOM_NOTES}


def note_times(hand_offs: Path) -> dict[str, dt.datetime]:
    """The time of the latest note of each agent, by the id of its start line in agents.jsonl, which the hook writes in
    each note's file. A file without an id, as the hook wrote them before, counts for no agent."""
    times: dict[str, dt.datetime] = {}
    for file in hand_offs.glob("*.*"):
        agent_id = file.read_text().strip()
        if not agent_id:
            continue
        noted = dt.datetime.fromtimestamp(file.stat().st_mtime, dt.UTC)
        times[agent_id] = max(times.get(agent_id, noted), noted)
    return times


def hook_threshold(name: str) -> int:
    """A threshold that scripts/hooks/hand-off sets, such as soon_tokens, in tokens."""
    found = re.search(rf"^{name}=([0-9]+)$", HOOK.read_text(), re.MULTILINE)
    if found is None:
        raise ValueError(f"Can't find {name}=<tokens> in {HOOK}. Give the threshold's name as the hook sets it.")
    return int(found[1])
