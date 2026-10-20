"""Tests of the message from the orchestrator that scripts/loop/run-builder adds to a builder's prompt."""

from test_run_builder import RunBuilderTest

MESSAGE = "Fix the errors that the reviewer found in pull request #7."
# The message follows the issue's text, and comes before the rest of the brief.
MESSAGE_START = (
    "Issue #12: Fix the importer\n\nIt drops rows.\n\n---\n\nThe orchestrator adds this message for you:\n\n"
    f"{MESSAGE}\n\n---\n\nYou build this issue, of the kind `other`"
)


class MessageTest(RunBuilderTest):
    def message_file(self) -> str:
        message = self.root / "message.md"
        message.write_text(f"{MESSAGE}\n")
        return str(message)

    def test_claude_builder_gets_the_message_after_the_text_of_its_issue(self) -> None:
        status, _, _ = self.run_builder("--tool", "claude", "--message", self.message_file())
        self.assertEqual(status, 0)
        brief = (self.run_folder() / "brief.md").read_text()
        self.assertTrue(brief.startswith(MESSAGE_START))
        self.assertEqual(self.calls("claude")[0]["args"][-1], brief)

    def test_codex_builder_gets_the_message_after_the_text_of_its_issue(self) -> None:
        status, _, _ = self.run_builder("--tool", "codex", "--message", self.message_file())
        self.assertEqual(status, 0)
        brief = (self.run_folder() / "brief.md").read_text()
        self.assertTrue(brief.startswith(MESSAGE_START))
        self.assertEqual(self.calls("codex")[0]["args"][-1], brief)

    def test_stops_when_it_cannot_read_the_message_file(self) -> None:
        missing = self.root / "missing.md"
        status, _, errors = self.run_builder("--tool", "claude", "--message", str(missing))
        self.assertEqual(status, 1)
        self.assertEqual(
            errors,
            f"error: can't read the message file {missing}: No such file or directory. Give the path of a file that "
            "holds the message.\n",
        )
        self.assertEqual(self.calls("gh"), [])
