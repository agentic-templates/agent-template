"""Asks GitHub's API through gh, in a checkout of the repository, where gh fills in {owner} and {repo}."""

import subprocess
from pathlib import Path


class GitHubError(Exception):
    """Why gh gave no answer, with what to do about it."""


def gh_api(endpoint: str, options: list[str], checkout: Path) -> str:
    """What gh api prints for an endpoint, such as graphql."""
    try:
        done = subprocess.run(
            ["gh", "api", endpoint, *options], cwd=checkout, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise GitHubError("gh isn't installed. Install the GitHub CLI.") from error
    if done.returncode != 0:
        problem = " ".join(done.stderr.split())
        raise GitHubError(
            f"gh api {endpoint} failed: {problem}. Check the network, and that gh auth status shows a login."
        )
    return done.stdout
