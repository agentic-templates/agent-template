"""What scripts/loop/run-builder and codex_finish.py share: how a run ends, its folders, and the commands that they
run."""

import json
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

# The checkout that holds this script. The script runs this checkout's checks, such as scripts/hooks/before-tool,
# because the worktree's copies are files that the builder can change, and they would run outside Codex's sandbox.
TRUSTED_ROOT = Path(__file__).resolve().parents[2]


class Stop(Exception):
    """Ends the run, with the reason."""


class Outcome(NamedTuple):
    """How a run ended, as its last line says, and what the end line in agents.jsonl takes from it."""

    text: str
    is_ok: bool = True
    # The pull request that the script opened for a Codex builder
    pull_request: int | None = None
    worktree_mb: float | None = None


class Folders(NamedTuple):
    checkout: Path
    worktree: Path
    run: Path
    # The main checkout's git hooks folder, as an absolute path. A relative core.hooksPath would name the worktree's
    # copies of the hooks.
    hooks: str


def label_names(issue: dict[str, Any]) -> list[str]:
    return [label["name"] for label in issue["labels"]]


def own_pull_request(branch: str, checkout: Path) -> dict[str, Any] | None:
    """The branch's open pull request, if it has one. A pull request from a fork can come from a branch of the same
    name, and a push doesn't change it."""
    fields = "number,isCrossRepository,isDraft,autoMergeRequest"
    listed = json.loads(run_ok(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", fields], checkout))
    own = [pull_request for pull_request in listed if not pull_request["isCrossRepository"]]
    return own[0] if own else None


def hold_for_review(pull_request: dict[str, Any], checkout: Path) -> None:
    """Turns off the pull request's auto-merge and makes it a draft, so that the change that the push adds waits for a
    reviewer."""
    number = str(pull_request["number"])
    if pull_request["autoMergeRequest"]:
        run_ok(["gh", "pr", "merge", number, "--disable-auto"], checkout)
    if not pull_request["isDraft"]:
        run_ok(["gh", "pr", "ready", number, "--undo"], checkout)


def shell_function(file: str, call: str, *arguments: str) -> str:
    """The output of a call of the functions in a file in scripts/ of the checkout that holds this script, such as
    scripts/needs-triage."""
    done = subprocess.run(
        ["bash", "-c", f'source "$0" && {call}', TRUSTED_ROOT / "scripts" / file, *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def git(arguments: list[str], folder: Path, hooks: str) -> str:
    """The output of a git command that must succeed. Git runs the hooks in the folder hooks, as an absolute path."""
    return run_ok(["git", "-c", f"core.hooksPath={hooks}", *arguments], folder)


def run(command: list[str], folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=folder, capture_output=True, text=True, check=False)


def run_ok(command: list[str], folder: Path) -> str:
    """The output of a command that must succeed. A failure ends the run with the command's error."""
    done = run(command, folder)
    if done.returncode != 0:
        raise Stop(f"{command[0]} failed: {one_line(done.stderr or done.stdout)}")
    return done.stdout


def one_line(text: str) -> str:
    return " ".join(text.split())
