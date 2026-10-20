import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

import hand_offs

NOW = dt.datetime(2026, 10, 18, 7, 8, tzinfo=dt.UTC)


class HookThresholdTest(unittest.TestCase):
    def test_reads_a_threshold_that_the_hook_sets(self) -> None:
        hook_lines = hand_offs.HOOK.read_text().splitlines()
        self.assertIn(f"soon_tokens={hand_offs.hook_threshold('soon_tokens')}", hook_lines)

    def test_stops_on_a_threshold_that_the_hook_does_not_set(self) -> None:
        with self.assertRaisesRegex(ValueError, r"Can't find later_tokens=<tokens> in .*hand-off"):
            hand_offs.hook_threshold("later_tokens")


class ToldForRoomTest(unittest.TestCase):
    def test_lists_the_agents_with_a_note_about_their_accounts_room(self) -> None:
        notes = {"a1.room-soon", "a2.room-now", "a3.400000", "a4.room", "notes.txt"}
        self.assertEqual(hand_offs.told_for_room(notes), {"a1", "a2"})


class NoteTimesTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def note(self, name: str, text: str, minutes: int) -> None:
        file = self.folder / name
        file.write_text(text)
        noted = (NOW + dt.timedelta(minutes=minutes)).timestamp()
        os.utime(file, (noted, noted))

    def test_gives_the_time_of_the_latest_note_of_each_agent_by_the_id_in_its_files(self) -> None:
        self.note("a1.400000", "toolu_01", 0)
        self.note("a1.room-now", "toolu_01", 5)
        self.note("s2.room-soon", "12-20261018-070000", 3)
        self.assertEqual(
            hand_offs.note_times(self.folder),
            {"toolu_01": NOW + dt.timedelta(minutes=5), "12-20261018-070000": NOW + dt.timedelta(minutes=3)},
        )

    def test_leaves_out_a_note_without_an_id(self) -> None:
        self.note("a1.400000", "", 0)
        self.assertEqual(hand_offs.note_times(self.folder), {})

    def test_gives_nothing_without_the_folder(self) -> None:
        self.assertEqual(hand_offs.note_times(self.folder / "hand-offs"), {})
