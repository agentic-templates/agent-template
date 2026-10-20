"""Tests of how scripts/loop/run_events.py tells whether a builder's run failed."""

import unittest

import run_events
from test_run_builder import CODEX_TURN, RESULT


class ClaudeProblemTest(unittest.TestCase):
    def test_is_none_for_a_success_and_exit_code_0(self) -> None:
        self.assertIsNone(run_events.claude_problem(RESULT, 0))

    def test_gives_each_error_of_a_result_that_is_not_a_success(self) -> None:
        result = {"subtype": "error_max_turns", "is_error": False, "errors": ["Too many turns", "Stopped"]}
        self.assertEqual(
            run_events.claude_problem(result, 0), "claude's result is error_max_turns: Too many turns, Stopped"
        )

    def test_gives_the_text_of_a_success_that_is_an_error(self) -> None:
        result = {**RESULT, "is_error": True, "result": "API Error: 529 overloaded"}
        self.assertEqual(run_events.claude_problem(result, 0), "claude's result is success: API Error: 529 overloaded")

    def test_counts_an_exit_code_other_than_0_after_a_success(self) -> None:
        self.assertEqual(
            run_events.claude_problem(RESULT, 143),
            "claude ended with code 143 after its result. stderr.log in the run folder says why.",
        )


class CodexProblemTest(unittest.TestCase):
    def test_is_none_for_a_completed_turn_and_exit_code_0(self) -> None:
        self.assertIsNone(run_events.codex_problem(CODEX_TURN, 0))

    def test_says_when_no_turn_started(self) -> None:
        self.assertEqual(
            run_events.codex_problem([{"type": "thread.started"}], 1),
            "codex ended with code 1 before its turn started. stderr.log in the run folder says why.",
        )

    def test_says_when_the_turn_did_not_complete(self) -> None:
        self.assertEqual(
            run_events.codex_problem(CODEX_TURN[:3], 0),
            "codex ended with code 0 before its turn completed. stderr.log in the run folder says why.",
        )

    def test_counts_an_exit_code_other_than_0_after_a_completed_turn(self) -> None:
        self.assertEqual(
            run_events.codex_problem(CODEX_TURN, 2),
            "codex ended with code 2 after its turn. stderr.log in the run folder says why.",
        )
