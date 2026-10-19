"""Finds the loop folder, where the loop keeps the files that stay on this machine: the .loop folder of the main
checkout, which every worktree shares. The environment variable LOOP_DIR names another folder. Tells whether a process
runs in Codex's sandbox, which can't write the loop folder."""

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

CODEX_SANDBOX_VARIABLE = "CODEX_SANDBOX_NETWORK_DISABLED"


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


def is_in_codex_sandbox() -> bool:
    """Whether this process runs in Codex's sandbox, which sets CODEX_SANDBOX_VARIABLE to 1, as scripts/check checks
    too."""
    return os.environ.get(CODEX_SANDBOX_VARIABLE) == "1"


def claude_project_folder(checkout: Path, environment: Mapping[str, str] = os.environ) -> Path:
    """The folder where Claude Code keeps the transcripts of the sessions that run in a checkout, or in a folder, with
    this process's environment or another, such as one that selects another account."""
    config = Path(environment.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")))
    # Claude Code names a project's folder after its path, with a dash for each character other than a letter or digit.
    return config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(checkout))


def git(arguments: list[str], folder: Path) -> str:
    return subprocess.run(["git", *arguments], cwd=folder, capture_output=True, text=True, check=True).stdout
