"""Asks GitHub's API through gh, in a checkout of the repository, where gh fills in {owner} and {repo}."""

import datetime as dt
import json
import subprocess
from pathlib import Path

RETROSPECTIVE_LABEL = "retrospective"
# GitHub can't order pull requests by when they merged, so the query takes the newest ones by when they were opened.
RETROSPECTIVES_QUERY = """
query($owner: String!, $name: String!, $label: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(labels: [$label], states: MERGED, first: 100, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes { mergedAt }
    }
  }
}"""


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


def retrospective_merges(checkout: Path) -> list[dt.datetime]:
    """When each merged pull request with the label retrospective merged."""
    options = ["-f", f"query={RETROSPECTIVES_QUERY}"]
    for name, value in (("owner", "{owner}"), ("name", "{repo}"), ("label", RETROSPECTIVE_LABEL)):
        options += ["-F", f"{name}={value}"]
    nodes = json.loads(gh_api("graphql", options, checkout))["data"]["repository"]["pullRequests"]["nodes"]
    return [dt.datetime.fromisoformat(node["mergedAt"]) for node in nodes]
