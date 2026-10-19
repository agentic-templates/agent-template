import tempfile
import unittest
from pathlib import Path
from typing import Any

import agent_time
from test_transcripts import at, call_line, command_line, prompt_line, result_line, write_transcript


def tool_line(call: str, minute: float, tool_use: str, name: str) -> dict[str, Any]:
    """A transcript's line for a call that uses a tool other than Bash."""
    use = {"type": "tool_use", "id": tool_use, "name": name, "input": {}}
    return {"type": "assistant", "timestamp": at(minute), "message": {"id": call, "content": [use]}}


def notification_line(minute: float) -> dict[str, Any]:
    """A transcript's line that tells an agent that one of its background tasks ended."""
    return prompt_line(minute, "<task-notification>done</task-notification>") | {
        "origin": {"kind": "task-notification"}
    }


def message_line(minute: float) -> dict[str, Any]:
    """A transcript's line for a message from the orchestrator."""
    return prompt_line(minute, "Fix the review's findings.") | {"origin": {"kind": "coordinator"}}


class SplitTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "agent.jsonl"

    def minutes(self, *lines: Any) -> dict[str, float]:
        write_transcript(self.file, list(lines))
        return {part: seconds / 60 for part, seconds in agent_time.read_split(self.file).items() if seconds}

    def test_splits_the_time_into_the_model_each_kind_of_tool_call_and_each_kind_of_wait(self) -> None:
        self.assertEqual(
            self.minutes(
                prompt_line(0),
                command_line("a", 1, "toolu_1", "scripts/check"),
                result_line(6, "toolu_1", "passed"),
                command_line("b", 7, "toolu_2", "scripts/start-issue.test"),
                result_line(9, "toolu_2", "passed"),
                command_line("c", 10, "toolu_3", "gh pr checks 57 --watch"),
                result_line(20, "toolu_3", "passed"),
                command_line("d", 20.5, "toolu_4", "sleep 60"),
                result_line(21.5, "toolu_4", ""),
                command_line("e", 22, "toolu_5", "git status"),
                result_line(22.5, "toolu_5", "clean"),
                tool_line("f", 23, "toolu_6", "Read"),
                result_line(23.25, "toolu_6", "text"),
                command_line("g", 24, "toolu_7", "scripts/check > check.log"),
                result_line(24, "toolu_7", "Started in the background."),
                call_line("h", 25),
                notification_line(30),
                tool_line("i", 31, "toolu_8", "SubagentHandback"),
                result_line(31, "toolu_8", "delivered"),
                message_line(45),
                call_line("j", 47),
            ),
            {
                agent_time.MODEL: 1 + 1 + 1 + 0.5 + 0.5 + 0.5 + 0.75 + 1 + 1 + 2,
                agent_time.CHECK: 5,
                agent_time.TEST_FILE: 2,
                agent_time.CI: 10,
                agent_time.SLEEP: 1,
                agent_time.COMMAND: 0.5,
                agent_time.TOOL: 0.25,
                agent_time.BACKGROUND_WAIT: 5,
                agent_time.ORCHESTRATOR_WAIT: 14,
            },
        )

    def test_counts_the_call_that_started_first_while_several_run(self) -> None:
        self.assertEqual(
            self.minutes(
                command_line("a", 0, "toolu_1", "sleep 120"),
                command_line("a", 0, "toolu_2", "scripts/check"),
                result_line(2, "toolu_1", ""),
                result_line(5, "toolu_2", "passed"),
            ),
            {agent_time.SLEEP: 2, agent_time.CHECK: 3},
        )

    def test_counts_a_wait_that_a_line_other_than_a_task_notification_ends_as_a_wait_for_the_orchestrator(
        self,
    ) -> None:
        self.assertEqual(
            self.minutes(prompt_line(0), call_line("a", 1), prompt_line(10, "Rebase on main.")),
            {agent_time.MODEL: 1, agent_time.ORCHESTRATOR_WAIT: 9},
        )

    def test_is_empty_for_a_transcript_of_one_line_or_none(self) -> None:
        self.assertEqual(self.minutes(prompt_line(0)), {})
        self.assertEqual(self.minutes(), {})


class CommandKindTest(unittest.TestCase):
    def test_tells_the_kind_from_the_programs_that_a_command_runs(self) -> None:
        cases = {
            "scripts/check": agent_time.CHECK,
            "cd ~/app && ./scripts/check 2>&1 | tail -30": agent_time.CHECK,
            "X=1 time scripts/check": agent_time.CHECK,
            "scripts/check-pull-request origin/main HEAD": agent_time.COMMAND,
            "bash scripts/hooks/settings.test": agent_time.TEST_FILE,
            "for t in a b; do scripts/hooks/$t.test; done": agent_time.TEST_FILE,
            "uv run --locked python -m unittest test_report": agent_time.TEST_FILE,
            "python3 scripts/loop/test_report.py": agent_time.TEST_FILE,
            "uv run --locked python -m unittest discover -s scripts/loop -t scripts/loop": agent_time.COMMAND,
            "cat scripts/loop/test_report.py": agent_time.COMMAND,
            "gh pr checks 57 --watch --fail-fast": agent_time.CI,
            "gh run watch 123 --exit-status": agent_time.CI,
            "scripts/wait-for-pull-requests": agent_time.CI,
            "until gh pr view 57 --json state | grep -q MERGED; do sleep 30; done": agent_time.CI,
            "sleep 60; gh run view 123": agent_time.CI,
            "gh pr view 57": agent_time.COMMAND,
            "sleep 5": agent_time.SLEEP,
            "until [ -s check.log ]; do sleep 10; done; cat check.log": agent_time.SLEEP,
            "": agent_time.COMMAND,
        }
        for command, kind in cases.items():
            with self.subTest(command=command):
                self.assertEqual(agent_time.command_kind(command), kind)

    def test_leaves_out_the_body_of_a_here_document(self) -> None:
        cases = {
            "cat > body.md <<'EOF'\nscripts/check passes.\nEOF": agent_time.COMMAND,
            'gh pr create --body "$(cat <<EOF\nsleep 5\nEOF\n)"': agent_time.COMMAND,
            "cat > empty.txt <<EOF\nEOF\nscripts/check": agent_time.CHECK,
            "python3 - <<'EOF' && scripts/start-issue.test\nprint('scripts/check')\nEOF": agent_time.TEST_FILE,
        }
        for command, kind in cases.items():
            with self.subTest(command=command):
                self.assertEqual(agent_time.command_kind(command), kind)
