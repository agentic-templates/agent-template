import contextlib
import importlib.machinery
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import test_paths

SCRIPT = Path(__file__).with_name("consequential")
# The script's file name has no .py, so the tests load it by its path.
consequential = types.ModuleType("consequential")
consequential.__file__ = str(SCRIPT)
importlib.machinery.SourceFileLoader("consequential", str(SCRIPT)).exec_module(consequential)
PATTERNS = consequential.read_patterns(consequential.PATTERNS_FILE)


class ReadPatternsTest(unittest.TestCase):
    def assert_reads(self, text: str, patterns: tuple[str, ...]) -> None:
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "consequential-paths"
        path.write_text(text)
        self.assertEqual(consequential.read_patterns(path), patterns)

    def test_reads_each_line_but_the_comments(self) -> None:
        self.assert_reads("# The rules\nAGENTS.md\n# The scripts\nscripts/*\n", ("AGENTS.md", "scripts/*"))

    def test_reads_a_last_line_without_a_line_break(self) -> None:
        self.assert_reads("AGENTS.md\nuv.lock", ("AGENTS.md", "uv.lock"))


class PatternsFileTest(unittest.TestCase):
    def test_holds_only_the_characters_that_bash_and_fnmatch_read_alike(self) -> None:
        # Bash's [[ == ]] and Python's fnmatch read other characters differently, such as [^a] and \*.
        for pattern in PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertRegex(pattern, r"\A[A-Za-z0-9._/*-]+\Z")


class ConsequentialFilesTest(unittest.TestCase):
    def test_lists_a_listed_file(self) -> None:
        self.assertEqual(consequential.consequential_files(["AGENTS.md"], PATTERNS), ["AGENTS.md"])

    def test_leaves_out_an_unlisted_file(self) -> None:
        self.assertEqual(consequential.consequential_files(["README.md"], PATTERNS), [])

    def test_lists_each_file_under_a_pattern_that_ends_in_a_star(self) -> None:
        for file in ["scripts/hooks/before-tool", "scripts/loop/test_meter.py", "docs/code.md", ".claude/agents/a.md"]:
            with self.subTest(file=file):
                self.assertEqual(consequential.consequential_files([file], PATTERNS), [file])

    def test_lists_an_agents_file_in_any_folder(self) -> None:
        for file in ["docs/AGENTS.md", "src/app/AGENTS.md"]:
            with self.subTest(file=file):
                self.assertEqual(consequential.consequential_files([file], PATTERNS), [file])

    def test_matches_a_pattern_without_a_star_only_as_the_whole_path(self) -> None:
        for file in ["pyproject.toml.old", "src/uv.lock", "AGENTS.md.old", "xAGENTS.md", "src/xAGENTS.md"]:
            with self.subTest(file=file):
                self.assertEqual(consequential.consequential_files([file], PATTERNS), [])

    def test_matches_a_folder_pattern_only_at_the_start_of_the_path(self) -> None:
        self.assertEqual(consequential.consequential_files(["src/scripts/check", "src/docs/guide.md"], PATTERNS), [])

    def test_lists_only_the_listed_files_of_a_change(self) -> None:
        files = ["README.md", ".claude/settings.json", ".github/ISSUE_TEMPLATE/bug.yml"]
        self.assertEqual(consequential.consequential_files(files, PATTERNS), [".claude/settings.json"])

    def test_lists_a_file_once(self) -> None:
        self.assertEqual(consequential.consequential_files(["AGENTS.md", "AGENTS.md"], PATTERNS), ["AGENTS.md"])

    def test_lists_nothing_for_a_change_without_files(self) -> None:
        self.assertEqual(consequential.consequential_files([], PATTERNS), [])


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

    def test_exits_2_outside_a_checkout(self) -> None:
        outside = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(contextlib.chdir(outside))
        status, printed, errors = self.run_script()
        self.assertEqual((status, printed), (2, ""))
        self.assertRegex(errors, r"^error: git rev-parse --show-toplevel failed: fatal: not a git repository")

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

    def test_counts_new_files_from_the_top_of_the_checkout_when_run_from_a_folder(self) -> None:
        self.commit("src/app.py")
        self.write("scripts/hooks/new-hook")
        self.enterContext(contextlib.chdir(self.folder / "src"))
        self.assertEqual(self.run_script(), (0, "scripts/hooks/new-hook\n", ""))

    def test_counts_uncommitted_changes_and_new_files_for_a_single_commit(self) -> None:
        self.write("AGENTS.md", "scripts/hooks/new-hook")
        self.assertEqual(self.run_script("origin/main"), (0, "AGENTS.md\nscripts/hooks/new-hook\n", ""))

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

    def test_exits_2_and_names_the_list_of_paths_when_it_is_missing(self) -> None:
        missing = self.folder / "scripts" / "consequential-paths"
        self.enterContext(mock.patch.object(consequential, "PATTERNS_FILE", missing))
        self.commit("AGENTS.md")
        error = (
            f"error: can't read {missing}, which lists the paths of the files that a reviewer reads: No such file or "
            "directory. Restore it from git, as with git restore scripts/consequential-paths.\n"
        )
        self.assertEqual(self.run_script(), (2, "", error))

    def test_runs_as_a_script(self) -> None:
        self.commit("scripts/check")
        done = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True, env=dict(os.environ), check=False, timeout=60
        )
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "scripts/check\n", ""))
