import os
import unittest
from pathlib import Path
from unittest import mock

from accounts import SIGNED_IN, Account, claude_project_folders, transcript_account


class ClaudeProjectFoldersTest(unittest.TestCase):
    def test_gives_the_folder_of_this_process_and_of_each_claude_account_once(self) -> None:
        accounts: list[Account] = [
            {"name": "signed-in", "tool": "claude", "env": {}},
            {"name": "codex", "tool": "codex", "env": {"CODEX_HOME": "/codex"}},
            {"name": "second", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "/second"}},
            {"name": "same", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "/process", "ANTHROPIC_MODEL": "opus"}},
        ]
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/process", "HOME": "/person"}):
            folders = claude_project_folders(Path("/work/my.project"), accounts)
        self.assertEqual(
            folders,
            [
                Path("/process/projects/-work-my-project"),
                Path("/person/.claude/projects/-work-my-project"),
                Path("/second/projects/-work-my-project"),
            ],
        )

    def test_gives_the_folder_of_this_process_without_a_claude_account(self) -> None:
        codex: list[Account] = [{"name": "codex", "tool": "codex", "env": {"CODEX_HOME": "/codex"}}]
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/process"}):
            folders = claude_project_folders(Path("/work"), codex)
        self.assertEqual(folders, [Path("/process/projects/-work")])


class TranscriptAccountTest(unittest.TestCase):
    def account(self, accounts: list[Account], transcript: str) -> str | None:
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/second", "HOME": "/person"}):
            found = transcript_account(Path("/work"), accounts, Path(transcript))
        return found and found["name"]

    def test_gives_the_claude_account_whose_folder_holds_the_transcript(self) -> None:
        codex: Account = {"name": "codex", "tool": "codex", "env": {}}
        second: Account = {"name": "second", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "/second"}}
        accounts = [codex, SIGNED_IN, second]
        self.assertEqual(self.account(accounts, "/person/.claude/projects/-work/s.jsonl"), "claude")
        self.assertEqual(self.account(accounts, "/second/projects/-work/s.jsonl"), "second")
        self.assertIsNone(self.account(accounts, "/other/projects/-work/s.jsonl"))

    def test_gives_the_account_of_this_process_for_a_transcript_in_its_folder(self) -> None:
        # No account sets CLAUDE_CONFIG_DIR, so this process runs on the signed-in account, with its own folder.
        self.assertEqual(self.account([SIGNED_IN], "/second/projects/-work/s.jsonl"), "claude")
