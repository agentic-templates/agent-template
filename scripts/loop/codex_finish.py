"""Finishes the run of a Codex builder for scripts/loop/run-builder, outside Codex's sandbox: commits the builder's
changes and pushes them with a pull request, or posts its report as a comment on the issue when it changed no file."""

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from builder_run import (
    TRUSTED_ROOT,
    Folders,
    Outcome,
    Stop,
    git,
    hold_for_review,
    label_names,
    one_line,
    own_pull_request,
    run_ok,
    shell_function,
)

COMMIT_TYPES = {"feature": "feat", "bug": "fix", "maintenance": "chore"}


def finish_codex(issue: dict[str, Any], branch: str, folders: Folders) -> Outcome:
    """Posts what codex did: the pull request of its changes, or its report as a comment when it changed no file.
    Returns the outcome."""
    report_file = folders.run / "report.md"
    report = report_file.read_text().strip() if report_file.exists() else ""
    if not report:
        raise Stop("codex wrote no report, so the script can't describe its change or post its answer.")
    if git(["status", "--porcelain"], folders.worktree, folders.hooks).strip():
        return push_changes(issue, branch, folders, report)
    comment = ["gh", "issue", "comment", str(issue["number"]), "--body", report]
    reason = hook_block(comment, folders.checkout)
    if reason:
        raise Stop(f"the hooks would block codex's report as a comment on the issue: {reason}")
    run_ok(comment, folders.checkout)
    return Outcome("comment")


def push_changes(issue: dict[str, Any], branch: str, folders: Folders, report: str) -> Outcome:
    """Commits the changes and checks the commit, pushes it, and opens a pull request if the branch has none. Returns
    the outcome."""
    title = commit_subject(issue)
    head = commit_changes(title, folders)
    scan_for_leaks(f"refs/remotes/origin/{branch}..{head}", folders.checkout)
    needs_review = is_consequential(head, folders.checkout)
    pull_request = own_pull_request(branch, folders.checkout)
    body = pull_request_body(issue["number"], report)
    if pull_request is None:
        problems = pull_request_problems(title, body, head, folders.checkout)
        if problems:
            raise Stop(f"the pull request's text has problems, so the script pushed nothing: {problems}")
    elif needs_review:
        hold_for_review(pull_request, folders.checkout)
    git(["push", "--quiet", "origin", f"{head}:refs/heads/{branch}"], folders.worktree, folders.hooks)
    if pull_request is not None:
        return Outcome(pull_request_outcome(str(pull_request["number"]), needs_review or pull_request["isDraft"]))
    return open_pull_request(title, body, branch, head, needs_review, folders.checkout)


def commit_changes(title: str, folders: Folders) -> str:
    """Commits the changes in the worktree, unless a file's name suggests a secret, and returns the commit."""
    # The commit stays off the local branch, so that a commit that a check refuses can't go out with a later push.
    git(["switch", "--quiet", "--detach"], folders.worktree, folders.hooks)
    git(["add", "--all"], folders.worktree, folders.hooks)
    staged = git(["diff", "--cached", "--name-only", "--diff-filter=d", "-z"], folders.worktree, folders.hooks)
    secrets = secret_names([path for path in staged.split("\0") if path])
    if secrets:
        raise Stop(f"codex left files whose names suggest a secret, so the script pushed nothing: {', '.join(secrets)}")
    git(["commit", "--quiet", "--message", title], folders.worktree, folders.hooks)
    return git(["rev-parse", "HEAD"], folders.worktree, folders.hooks).strip()


def commit_subject(issue: dict[str, Any]) -> str:
    types = [COMMIT_TYPES[name] for name in label_names(issue) if name in COMMIT_TYPES]
    return f"{types[0] if types else 'chore'}: {issue['title']}"


def pull_request_body(number: int, report: str) -> str:
    return f"Closes #{number}\n\n## What changed and why\n\n{report}\n"


def secret_names(files: list[str]) -> list[str]:
    """The files whose names suggest a secret, as has_secret_name in scripts/secret-file-names tells."""
    each = 'for file in "$@"; do if has_secret_name "$file"; then printf "%s\\n" "$file"; fi; done'
    return shell_function("secret-file-names", each, *files).splitlines()


def scan_for_leaks(commits: str, checkout: Path) -> None:
    done = subprocess.run(
        [TRUSTED_ROOT / "scripts" / "leak-scan", "--range", commits],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        problems = one_line(done.stdout + done.stderr)
        raise Stop(f"scripts/leak-scan refused codex's commit, so the script pushed nothing: {problems}")


def is_consequential(head: str, checkout: Path) -> bool:
    """Whether scripts/loop/consequential lists a file that the branch changes against main. The script exits with
    code 1 when it crashes too, so only its own line says that a change isn't consequential."""
    done = subprocess.run(
        [TRUSTED_ROOT / "scripts" / "loop" / "consequential", f"refs/remotes/origin/main...{head}"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode == 0:
        return True
    if done.returncode == 1 and done.stdout.strip() == "Not consequential.":
        return False
    raise Stop(
        "scripts/loop/consequential can't tell whether a reviewer must read the change, so the script pushed "
        f"nothing: {one_line(done.stderr or done.stdout)}"
    )


def open_pull_request(title: str, body: str, branch: str, head: str, is_draft: bool, checkout: Path) -> Outcome:
    """Opens the pull request, as a draft or with auto-merge, and returns the outcome."""
    command = ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch, "--base", "main"]
    created = run_ok([*command, "--draft"] if is_draft else command, checkout)
    # gh prints the new pull request's URL, which ends in its number.
    number = created.strip().rsplit("/", 1)[-1]
    if is_draft:
        return Outcome(pull_request_outcome(number, True), pull_request=int(number))
    text = f"{pull_request_outcome(number, False)}{turn_on_auto_merge(number, head, checkout)}"
    return Outcome(text, pull_request=int(number))


def pull_request_outcome(number: str, is_draft: bool) -> str:
    return f"draft pull request #{number}" if is_draft else f"pull request #{number}"


def hook_block(command: list[str], checkout: Path) -> str:
    """Why scripts/hooks/before-tool would block an agent's command, or an empty string if it wouldn't."""
    hook_input = {"tool_name": "Bash", "tool_input": {"command": shlex.join(command)}, "cwd": str(checkout)}
    done = subprocess.run(
        [TRUSTED_ROOT / "scripts" / "hooks" / "before-tool"],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        check=False,
    )
    # The hook blocks with code 2, and allows the command with any other code, as when it fails on its own error.
    return one_line(done.stderr) if done.returncode == 2 else ""


def pull_request_problems(title: str, body: str, head: str, checkout: Path) -> str:
    """The problems that scripts/check-pull-request finds in a pull request into main, or an empty string."""
    done = subprocess.run(
        [TRUSTED_ROOT / "scripts" / "check-pull-request", "refs/remotes/origin/main", head],
        cwd=checkout,
        env=os.environ | {"TITLE": title, "BODY": body},
        capture_output=True,
        text=True,
        check=False,
    )
    return "" if done.returncode == 0 else one_line((done.stdout + done.stderr).replace("::error::", ""))


def turn_on_auto_merge(number: str, head: str, checkout: Path) -> str:
    """Turns on auto-merge for the pull request at the commit that the script pushed, as the after-tool hook does for
    an agent's pull request. Returns what the outcome adds."""
    rules = subprocess.run([TRUSTED_ROOT / "scripts" / "check-main-rules"], capture_output=True, text=True, check=False)
    # The script exits with code 1 when main lacks a required check, and with 2 when it can't tell.
    if rules.returncode == 1:
        return f", auto-merge off: {one_line(rules.stderr).removeprefix('error: ')}"
    run_ok(["gh", "pr", "merge", number, "--match-head-commit", head], checkout)
    return ""
