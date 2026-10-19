import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import paths

# git reads none of the machine's settings.
GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Tester",
    "GIT_AUTHOR_EMAIL": "tester@example.com",
    "GIT_COMMITTER_NAME": "Tester",
    "GIT_COMMITTER_EMAIL": "tester@example.com",
}


class RepositoryTest(unittest.TestCase):
    """A test with a temporary folder, in which git reads none of the machine's settings."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        environment = mock.patch.dict(os.environ, GIT_ENVIRONMENT)
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, *arguments: str, folder: Path) -> None:
        subprocess.run(["git", *arguments], cwd=folder, check=True, capture_output=True)

    def checkout_with_worktree(self) -> tuple[Path, Path]:
        checkout, worktree = self.folder / "main", self.folder / "worktree"
        self.git("init", "--quiet", str(checkout), folder=self.folder)
        self.git("commit", "--quiet", "--allow-empty", "-m", "Start", folder=checkout)
        self.git("worktree", "add", "--quiet", "-b", "work", str(worktree), folder=checkout)
        return checkout, worktree


class MainCheckoutTest(RepositoryTest):
    def test_gives_the_main_checkout_from_a_worktree(self) -> None:
        checkout, worktree = self.checkout_with_worktree()
        self.assertEqual(paths.main_checkout(worktree), checkout)

    def test_gives_the_checkout_of_a_repository_whose_git_folder_is_elsewhere(self) -> None:
        checkout = self.folder / "work"
        self.git(
            "init",
            "--quiet",
            "--separate-git-dir",
            str(self.folder / "metadata.git"),
            str(checkout),
            folder=self.folder,
        )
        self.assertEqual(paths.main_checkout(checkout), checkout)


class LoopDirTest(RepositoryTest):
    def test_is_the_loop_folder_of_the_main_checkout(self) -> None:
        # The git folder that every worktree of this repository shares is in its main checkout.
        shared = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        with mock.patch.dict(os.environ, {"LOOP_DIR": ""}):
            self.assertEqual(paths.loop_dir(), Path(shared.strip()).parent / ".loop")

    def test_is_the_folder_that_loop_dir_names(self) -> None:
        with mock.patch.dict(os.environ, {"LOOP_DIR": "/elsewhere/loop"}):
            self.assertEqual(paths.loop_dir(), Path("/elsewhere/loop"))
