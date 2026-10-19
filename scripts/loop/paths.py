"""Finds the loop folder, where the loop keeps the files that stay on this machine: the .loop folder of the main
checkout, which every worktree shares. The environment variable LOOP_DIR names another folder."""

import os
import subprocess
from pathlib import Path


def main_checkout(folder: Path | None = None) -> Path:
    """The main checkout of the repository that holds a folder, by default this file's folder."""
    folder = folder or Path(__file__).resolve().parent
    # git lists the main checkout first. For a repository whose git folder is outside its checkout, it lists the git
    # folder instead, and then the folder's own checkout counts as the main one.
    first_line = git(["worktree", "list", "--porcelain"], folder).splitlines()[0]
    listed = Path(first_line.removeprefix("worktree "))
    if (listed / ".git").exists():
        return listed
    return Path(git(["rev-parse", "--show-toplevel"], folder).strip())


def loop_dir() -> Path:
    override = os.environ.get("LOOP_DIR")
    if override:
        return Path(override)
    return main_checkout() / ".loop"


def git(arguments: list[str], folder: Path) -> str:
    return subprocess.run(["git", *arguments], cwd=folder, capture_output=True, text=True, check=True).stdout
