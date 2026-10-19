import unittest

import hand_offs


class HookThresholdTest(unittest.TestCase):
    def test_reads_a_threshold_that_the_hook_sets(self) -> None:
        hook_lines = hand_offs.HOOK.read_text().splitlines()
        self.assertIn(f"soon_tokens={hand_offs.hook_threshold('soon_tokens')}", hook_lines)

    def test_stops_on_a_threshold_that_the_hook_does_not_set(self) -> None:
        with self.assertRaisesRegex(ValueError, r"Can't find later_tokens=<tokens> in .*hand-off"):
            hand_offs.hook_threshold("later_tokens")
