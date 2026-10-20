"""Reads a pull request that scripts/loop/run-builder starts a reviewer for, and refuses one whose code the reviewer
mustn't run. Claude Code runs the hooks of the .claude/settings.json at the pull request's head, and AGENTS.md lets
only a maintainer's code run."""

import json
import subprocess
from pathlib import Path
from typing import Any

# The permissions from GitHub's permission API that let a person write to the repository. The API reports the Maintain
# role as write.
WRITE_PERMISSIONS = ("admin", "write")
OWN_SESSION = "Start the reviewer in your own session, which reads the diff without running its code."


class PullRequestError(Exception):
    """Why the reviewer can't start on the pull request, with what to do about it."""


def read_pull_request(number: int, checkout: Path) -> dict[str, Any]:
    """The pull request's number, head commit and labels. Stops when its head is a branch in another repository or its
    author lacks write access to the repository."""
    fields = "number,headRefOid,isCrossRepository,author,labels"
    pull_request: dict[str, Any] = json.loads(gh(["pr", "view", str(number), "--json", fields], checkout, "gh failed"))
    # A maintainer may open a pull request from a fork whose commits someone else wrote.
    if pull_request["isCrossRepository"]:
        raise PullRequestError(
            f"the head of pull request #{number} is a branch in another repository, whose commits anyone may have "
            f"written, and the reviewer would run hooks from them. {OWN_SESSION}"
        )
    author = pull_request["author"]["login"]
    endpoint = f"repos/{{owner}}/{{repo}}/collaborators/{author}/permission"
    cannot_tell = f"can't tell whether {author}, who opened pull request #{number}, may write to the repository"
    permission = json.loads(gh(["api", endpoint], checkout, f"{cannot_tell}, because gh failed"))["permission"]
    if permission not in WRITE_PERMISSIONS:
        raise PullRequestError(
            f"{author}, who opened pull request #{number}, has the permission {permission}, not write, and the reviewer "
            f"would run hooks from the pull request. {OWN_SESSION}"
        )
    return pull_request


def gh(arguments: list[str], checkout: Path, failure: str) -> str:
    """What gh prints. When gh fails, stops with the failure followed by gh's error."""
    done = subprocess.run(["gh", *arguments], cwd=checkout, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise PullRequestError(f"{failure}: {' '.join((done.stderr or done.stdout).split())}")
    return done.stdout
