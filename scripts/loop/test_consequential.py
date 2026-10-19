import contextlib
import importlib.machinery
import io
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path

import test_paths

SCRIPT = Path(__file__).with_name("consequential")
# The script's file name has no .py, so the tests load it by its path.
consequential = types.ModuleType("consequential")
importlib.machinery.SourceFileLoader("consequential", str(SCRIPT)).exec_module(consequential)


class ConsequentialFilesTest(unittest.TestCase):
    def test_lists_a_listed_file(self) -> None:
        self.assertEqual(consequential.consequential_files(["AGENTS.md"]), ["AGENTS.md"])

    def test_leaves_out_an_unlisted_file(self) -> None:
        self.assertEqual(consequential.consequential_files(["README.md"]), [])

    def test_lists_each_file_under_a_pattern_that_ends_in_a_star(self) -> None:
        for file in ["scripts/hooks/before-tool", "scripts/loop/test_meter.py", "scripts/configure-github.test"]:
            with self.subTest(file=file):
                self.assertEqual(consequential.consequential_files([file]), [file])

    def test_matches_a_pattern_without_a_star_only_as_the_whole_path(self) -> None:
        for file in ["scripts/checker", "scripts/check.test", "docs/rules.md.old", "docs/AGENTS.md", "xAGENTS.md"]:
            with self.subTest(file=file):
                self.assertEqual(consequential.consequential_files([file]), [])

    def test_matches_a_pattern_with_a_star_only_at_the_start_of_the_path(self) -> None:
        self.assertEqual(consequential.consequential_files(["docs/scripts/hooks/before-tool"]), [])

    def test_lists_only_the_listed_files_of_a_change(self) -> None:
        files = ["README.md", ".claude/settings.json", "docs/code.md"]
        self.assertEqual(consequential.consequential_files(files), [".claude/settings.json"])

    def test_lists_a_file_once(self) -> None:
        self.assertEqual(consequential.consequential_files(["AGENTS.md", "AGENTS.md"]), ["AGENTS.md"])

    def test_lists_nothing_for_a_change_without_files(self) -> None:
        self.assertEqual(consequential.consequential_files([]), [])


class ScriptTest(test_paths.RepositoryTest):
    """A test in a repository whose branch work starts where origin/main is, and which has AGENTS.md and README.md."""

    def setUp(self) -> None:
        super().setUp()
        self.git("init", "--quiet", "--initial-branch", "main", folder=self.folder)
        self.commit("AGENTS.md", "README.md")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD", folder=self.folder)
        self.git("switch", "--quiet", "--create", "work", folder=self.folder)
        self.enterContext(contextlib.chdir(self.folder))

    def write(self, *files: str) -> None:
        for file in files:
            path = self.folder / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{file} changed\n" if path.exists() else f"{file}\n")

    def commit(self, *files: str) -> None:
        self.write(*files)
        self.git("add", "--all", folder=self.folder)
        self.git("commit", "--quiet", "--message", "Change", folder=self.folder)

    def run_script(self, *arguments: str) -> tuple[int, str, str]:
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            status = consequential.main(list(arguments))
        return status, printed.getvalue(), errors.getvalue()

    def test_says_not_consequential_and_exits_1_for_a_change_to_unlisted_files(self) -> None:
        self.commit("README.md", "src/app.py")
        self.assertEqual(self.run_script(), (1, "Not consequential.\n", ""))

    def test_prints_the_listed_files_and_exits_0(self) -> None:
        self.commit("README.md", "AGENTS.md", "scripts/hooks/before-tool")
        self.assertEqual(self.run_script(), (0, "AGENTS.md\nscripts/hooks/before-tool\n", ""))

    def test_counts_staged_unstaged_and_new_files_for_a_range_that_ends_at_head(self) -> None:
        self.write("AGENTS.md", ".codex/hooks.json", "scripts/hooks/before-tool")
        self.git("add", ".codex/hooks.json", folder=self.folder)
        self.assertEqual(self.run_script(), (0, ".codex/hooks.json\nAGENTS.md\nscripts/hooks/before-tool\n", ""))

    def test_leaves_out_uncommitted_changes_for_a_range_that_ends_elsewhere(self) -> None:
        self.commit("docs/rules.md")
        self.write("AGENTS.md")
        self.assertEqual(self.run_script("main...work"), (0, "docs/rules.md\n", ""))

    def test_lists_a_file_that_moves_away_from_a_listed_path(self) -> None:
        self.git("mv", "AGENTS.md", "notes.md", folder=self.folder)
        self.git("commit", "--quiet", "--message", "Move", folder=self.folder)
        self.assertEqual(self.run_script(), (0, "AGENTS.md\n", ""))

    def test_exits_2_with_the_cause_for_a_range_that_names_no_commit(self) -> None:
        status, printed, errors = self.run_script("nowhere...HEAD")
        self.assertEqual((status, printed), (2, ""))
        self.assertRegex(errors, r"^error: git diff .* failed: fatal: .*'nowhere\.\.\.HEAD'.* Run the script in a ")

    def test_exits_2_when_origin_main_is_missing(self) -> None:
        self.git("update-ref", "-d", "refs/remotes/origin/main", folder=self.folder)
        status, printed, errors = self.run_script()
        self.assertEqual((status, printed), (2, ""))
        self.assertIn("If origin/main is missing, run git fetch origin main.", errors)

    def test_passes_a_range_that_starts_with_a_dash_to_git_as_a_range(self) -> None:
        status, _, errors = self.run_script("--", "--output=out.txt")
        self.assertEqual(status, 2, errors)
        self.assertFalse((self.folder / "out.txt").exists())

    def test_runs_as_a_script(self) -> None:
        self.commit("scripts/check")
        done = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True, env=dict(os.environ), check=False, timeout=60
        )
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "scripts/check\n", ""))
