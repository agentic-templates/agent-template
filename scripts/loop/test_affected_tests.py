"""Tests scripts/affected-tests, on texts that each case gives, in small repositories and on this repository's files."""

import contextlib
import importlib.machinery
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "affected-tests"
# The script's file name has no .py, so the tests load it by its path.
loader = importlib.machinery.SourceFileLoader("affected_tests", str(SCRIPT))
affected_tests = types.ModuleType(loader.name)
affected_tests.__file__ = loader.path
loader.exec_module(affected_tests)


def affected(texts: dict[str, str], *changed: str) -> list[str]:
    """The test files among the texts that a change to the changed files affects."""
    test_files = [path for path in texts if path.endswith(".test") or path == "scripts/test-check"]
    selected: list[str] = affected_tests.affected_tests(
        test_files, set(changed), {path: textwrap.dedent(text) for path, text in texts.items()}
    )
    return selected


class AffectedTestsTest(unittest.TestCase):
    def test_runs_a_test_file_that_the_branch_changes(self) -> None:
        texts = {"scripts/tool.test": "", "scripts/other.test": ""}
        self.assertEqual(affected(texts, "scripts/tool.test"), ["scripts/tool.test"])

    def test_runs_no_test_file_when_the_branch_changes_none_of_their_files(self) -> None:
        texts = {"scripts/tool.test": "", "scripts/tool": "", "docs/tool.md": ""}
        self.assertEqual(affected(texts, "docs/tool.md"), [])

    def test_runs_the_test_file_of_a_script_its_check_or_its_bash_file(self) -> None:
        texts = {
            "scripts/tool.test": "",
            "scripts/tool": "",
            "scripts/hooks/rule.test": "",
            "scripts/hooks/rule.check": "",
            "scripts/hooks/words.test": "",
            "scripts/hooks/words.bash": "",
        }
        self.assertEqual(affected(texts, "scripts/tool"), ["scripts/tool.test"])
        self.assertEqual(affected(texts, "scripts/hooks/rule.check"), ["scripts/hooks/rule.test"])
        self.assertEqual(affected(texts, "scripts/hooks/words.bash"), ["scripts/hooks/words.test"])

    def test_runs_the_test_files_that_tested_files_gives(self) -> None:
        texts = {
            "scripts/test-check": "",
            "scripts/check": "",
            "scripts/hooks/settings.test": "",
            ".codex/hooks.json": "",
        }
        self.assertEqual(affected(texts, "scripts/check"), ["scripts/test-check"])
        self.assertEqual(affected(texts, ".codex/hooks.json"), ["scripts/hooks/settings.test"])

    def test_follows_paths_from_the_root_through_every_file_that_a_tested_file_names(self) -> None:
        texts = {
            "scripts/tool.test": "",
            "scripts/tool": "source scripts/library\n",
            "scripts/library": 'x=$(.github/run "$1")\n',
            ".github/run": "",
            "scripts/other.test": "",
        }
        self.assertEqual(affected(texts, ".github/run"), ["scripts/tool.test"])

    def test_follows_paths_from_the_files_own_folder_after_a_variable_or_an_expansion(self) -> None:
        texts = {
            "scripts/hooks/a.test": "",
            "scripts/hooks/a": 'source "${BASH_SOURCE[0]%/*}/../library"\n',
            "scripts/hooks/b.test": "",
            "scripts/hooks/b": '"$hooks_folder/helper"\n',
            "scripts/hooks/c.test": "",
            "scripts/hooks/c": '"$(dirname "$0")/helper" --check\n',
            "scripts/library": "",
            "scripts/hooks/helper": "",
        }
        self.assertEqual(affected(texts, "scripts/library"), ["scripts/hooks/a.test"])
        self.assertEqual(affected(texts, "scripts/hooks/helper"), ["scripts/hooks/b.test", "scripts/hooks/c.test"])

    def test_follows_the_python_modules_that_a_file_imports_from_its_folder(self) -> None:
        texts = {
            "scripts/hooks/a.test": "",
            "scripts/hooks/a": "scripts/loop/tool\n",
            "scripts/loop/tool": "import json\nimport paths\n",
            "scripts/loop/paths.py": "from accounts import load\n",
            "scripts/loop/accounts.py": "",
        }
        self.assertEqual(affected(texts, "scripts/loop/accounts.py"), ["scripts/hooks/a.test"])

    def test_takes_a_path_that_ends_a_sentence_without_its_period(self) -> None:
        texts = {"scripts/tool.test": "", "scripts/tool": 'echo "error: run scripts/other."\n', "scripts/other": ""}
        self.assertEqual(affected(texts, "scripts/other"), ["scripts/tool.test"])

    def test_runs_a_test_file_whose_script_names_a_file_that_the_branch_deletes(self) -> None:
        texts = {"scripts/tool.test": "", "scripts/tool": "scripts/gone\n"}
        self.assertEqual(affected(texts, "scripts/gone"), ["scripts/tool.test"])

    def test_takes_no_name_from_comments_here_documents_triple_quotes_or_home_folders(self) -> None:
        texts = {
            "scripts/tool.test": "",
            "scripts/tool": """\
                # Runs scripts/commented.
                  # scripts/commented
                cat <<'TEXT'
                scripts/in-here-document
                TEXT
                cat <<<"scripts/in-here-string"
                cp a ~/scripts/in-home "$HOME/scripts/in-home" "$test_home/scripts/in-home" "${HOME}/scripts/in-home"
                x = \"\"\"scripts/in-triple-quotes\"\"\"
                y = \"\"\"Runs
                scripts/in-triple-quotes
                \"\"\"
                """,
            "scripts/commented": "",
            "scripts/in-here-document": "",
            "scripts/in-here-string": "",
            "scripts/in-home": "",
            "scripts/in-triple-quotes": "",
        }
        for name in ("commented", "in-here-document", "in-home", "in-triple-quotes"):
            with self.subTest(name):
                self.assertEqual(affected(texts, f"scripts/{name}"), [])
        self.assertEqual(affected(texts, "scripts/in-here-string"), ["scripts/tool.test"])

    def test_takes_no_name_from_the_test_file_itself(self) -> None:
        texts = {"scripts/tool.test": "scripts/sample\n", "scripts/tool": "", "scripts/sample": ""}
        self.assertEqual(affected(texts, "scripts/sample"), [])

    def test_runs_every_test_file_when_the_branch_changes_a_file_that_every_test_uses(self) -> None:
        texts = {"scripts/tool.test": "", "scripts/hooks/rule.test": "", "scripts/test-check": ""}
        for shared in ("scripts/test-scripts", "scripts/hooks/testing.bash", "scripts/hooks/helpers.bash"):
            with self.subTest(shared):
                self.assertEqual(affected(texts, shared), list(texts))

    def test_follows_no_name_in_a_file_that_every_test_uses(self) -> None:
        texts = {
            "scripts/hooks/before-tool.test": "",
            "scripts/hooks/before-tool": "scripts/loop/agent-log start\n",
            "scripts/loop/agent-log": "",
        }
        self.assertEqual(affected(texts, "scripts/loop/agent-log"), [])


class ThisRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        with contextlib.chdir(ROOT):
            self.texts = affected_tests.repository_texts()
        self.hook_tests = sorted(
            path for path in self.texts if path.startswith("scripts/hooks/") and path.endswith(".test")
        )
        script_tests = sorted(path for path in self.texts if path.count("/") == 1 and path.endswith(".test"))
        self.test_files = [*self.hook_tests, "scripts/test-check", *script_tests]

    def test_runs_only_the_settings_test_for_a_change_to_the_agent_log(self) -> None:
        selected = affected_tests.affected_tests(self.test_files, {"scripts/loop/agent-log"}, self.texts)
        self.assertEqual(selected, ["scripts/hooks/settings.test"])

    def test_runs_every_test_of_the_hooks_for_a_change_to_their_helpers(self) -> None:
        selected = affected_tests.affected_tests(self.test_files, {"scripts/hooks/helpers.bash"}, self.texts)
        self.assertEqual(selected, self.test_files)


class BranchTest(unittest.TestCase):
    """Runs the script in a repository whose origin/main has the test file scripts/tool.test, and scripts/tool."""

    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git("init", "--quiet", "--initial-branch=main")
        (self.folder / "scripts").mkdir()
        for name in ("tool.test", "tool", "other.test", "other"):
            (self.folder / "scripts" / name).write_text("")
        self.commit("chore: start")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("switch", "--quiet", "--create", "12-change")

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.folder), *arguments], check=True)

    def commit(self, message: str) -> None:
        self.git("add", "--all")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "--quiet", "-m", message)

    def run_script(self, *other_test_files: str) -> list[str]:
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "scripts/tool.test", "scripts/other.test", *other_test_files],
            cwd=self.folder,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.splitlines()

    def test_runs_no_test_file_when_the_branch_changes_nothing(self) -> None:
        self.assertEqual(self.run_script(), [])

    def test_runs_the_test_file_of_a_script_that_the_branch_commits_a_change_to(self) -> None:
        (self.folder / "scripts" / "tool").write_text("more\n")
        self.commit("fix: more")
        self.assertEqual(self.run_script(), ["scripts/tool.test"])

    def test_runs_the_test_file_of_a_script_with_a_change_that_isnt_committed(self) -> None:
        (self.folder / "scripts" / "other").write_text("more\n")
        self.assertEqual(self.run_script(), ["scripts/other.test"])

    def test_runs_the_test_file_of_a_new_script_that_isnt_added_to_git(self) -> None:
        (self.folder / "scripts" / "new").write_text("")
        self.assertEqual(self.run_script("scripts/new.test"), ["scripts/new.test"])

    def test_runs_every_test_file_when_the_repository_has_no_origin_main(self) -> None:
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.run_script(), ["scripts/tool.test", "scripts/other.test"])
