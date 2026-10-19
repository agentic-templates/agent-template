"""Reads what scripts/hooks/hand-off leaves: its thresholds, and the notes in which it records that it told an agent to
hand off its work."""

import re
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "hand-off"
FOLDER_NAME = "hand-offs"


def note_files(hand_offs: Path) -> set[str]:
    """The names of the files in which scripts/hooks/hand-off notes that it told an agent to hand off its work, each
    <agent id or session id>.<threshold>."""
    return {file.name for file in hand_offs.glob("*.*")}


def hand_off_thresholds(notes: set[str]) -> dict[str, int]:
    """The highest threshold at which scripts/hooks/hand-off told each agent to hand off its work."""
    thresholds: dict[str, int] = {}
    for note in notes:
        agent, _, threshold = note.rpartition(".")
        if threshold.isdigit():
            thresholds[agent] = max(thresholds.get(agent, 0), int(threshold))
    return thresholds


def hook_threshold(name: str) -> int:
    """A threshold that scripts/hooks/hand-off sets, such as soon_tokens, in tokens."""
    found = re.search(rf"^{name}=([0-9]+)$", HOOK.read_text(), re.MULTILINE)
    if found is None:
        raise ValueError(f"Can't find {name}=<tokens> in {HOOK}. Give the threshold's name as the hook sets it.")
    return int(found[1])
