import contextlib
import functools
import importlib.machinery
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from test_paths import GIT_ENVIRONMENT

RUN_BUILDER = Path(__file__).with_name("run-builder")
SCRIPTS = RUN_BUILDER.parents[1]
# The script's file name has no .py, so the tests load it by its path.
run_builder = types.ModuleType("run_builder")
run_builder.__file__ = str(RUN_BUILDER)
importlib.machinery.SourceFileLoader("run_builder", str(RUN_BUILDER)).exec_module(run_builder)

# A stand-in for gh, claude and codex. It writes each call to calls.jsonl in STANDIN_DIR, with its arguments, the folder
# it runs in, the HEAD of that folder's worktree and its environment. It gives the answers in answers.json there under
# the longest start of its arguments, such as "pr create", one answer for each call, and the last one again when they
# run out. An answer can print to stdout and stderr, exit with a status, write files in the folder it runs in, write
# codex's report, and push a branch from origin's main, as gh issue develop creates one. -S keeps coverage, which
# starts in each Python process of the tests, out of the stand-ins.
STANDIN = """#!PYTHON -S
import json, os, sys
folder, tool, args = os.environ["STANDIN_DIR"], os.path.basename(sys.argv[0]), sys.argv[1:]
answers = json.load(open(os.path.join(folder, "answers.json")))[tool]
start = max((key for key in answers if args[: len(key.split())] == key.split()), key=len)
head = None
if os.path.isfile(".git"):
    head = open(os.path.join(open(".git").read().split(":", 1)[1].strip(), "HEAD")).read().strip()
log = os.path.join(folder, "calls.jsonl")
with open(log, "a") as handle:
    call = {"tool": tool, "start": start, "args": args, "cwd": os.getcwd(), "head": head, "env": dict(os.environ)}
    handle.write(json.dumps(call) + "\\n")
calls = [json.loads(line) for line in open(log)]
count = sum(call["tool"] == tool and call["start"] == start for call in calls)
answer = answers[start][min(count, len(answers[start])) - 1]
for path, content in answer.get("write", {}).items():
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w").write(content)
if "report" in answer:
    open(args[args.index("--output-last-message") + 1], "w").write(answer["report"])
if "push" in answer:
    import subprocess
    target = "refs/remotes/origin/main:refs/heads/" + answer["push"]
    subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "push", "--quiet", "origin", target], check=True)
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("status", 0))
""".replace("PYTHON", sys.executable)

GITHUB_URL = "https://github.com/owner/repo.git"
BRANCH = "12-fix-the-importer"
ISSUE = {"number": 12, "title": "Fix the importer", "body": "It drops rows.", "labels": [{"name": "bug"}]}
ACCOUNTS = [
    {"name": "work", "tool": "codex", "env": {"CODEX_HOME": "/codex-work"}},
    {"name": "main", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "/claude-main"}},
    {"name": "second", "tool": "claude"},
    {"name": "spare", "tool": "codex"},
]
INIT = {"type": "system", "subtype": "init", "permissionMode": "auto"}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "Opened pull request #5."}
CODEX_TURN = [
    {"type": "thread.started"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "agent_message"}},
    {"type": "turn.completed"},
]
REPORT = "The importer keeps every row now."
# The git hooks of the main checkout, which record that they ran.
GIT_HOOKS = ("post-checkout", "pre-commit", "pre-push")
SAVE_SUBJECT = "chore: save the unfinished work of a stopped session"
# A pull request that the branch has already, as gh pr list gives it.
OWN_PULL_REQUEST = {"number": 6, "isCrossRepository": False, "isDraft": False, "autoMergeRequest": None}


def lines(*events: object) -> str:
    return "".join((event if isinstance(event, str) else json.dumps(event)) + "\n" for event in events)


def linked(*branches: str, repository: str = "owner/repo") -> str:
    """What gh issue develop --list prints for branches of the repository."""
    return "".join(f"{branch}\thttps://github.com/{repository}/tree/{branch}\n" for branch in branches)


def git(*arguments: str, folder: Path) -> str:
    """The output of git, which runs no hooks."""
    done = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *arguments],
        cwd=folder,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@functools.cache
def starting_repositories() -> tempfile.TemporaryDirectory[str]:
    """A folder that holds origin.git, a bare repository with one commit on main and on the issue's branch, and repo,
    a clone of it whose origin has a GitHub URL, whose git hooks record that they ran, and which has copies of
    scripts/clear-worktrees and the script it reads. Each test copies them, because making them took 0.1 seconds of
    the 0.13 seconds of a test that stops before the run starts."""
    folder = tempfile.TemporaryDirectory()
    root = Path(folder.name)
    origin, repo = root / "origin.git", root / "repo"
    git("init", "--quiet", "--bare", "--initial-branch=main", str(origin), folder=root)
    git("init", "--quiet", "--initial-branch=main", str(repo), folder=root)
    (repo / "README").write_text("start\n")
    for hook in GIT_HOOKS:
        path = repo / "git-hooks" / hook
        path.parent.mkdir(exist_ok=True)
        path.write_text(f'#!/bin/sh\ntouch "$STANDIN_DIR/{hook}-ran"\n')
        path.chmod(0o755)
    (repo / "scripts").mkdir()
    for script in ("clear-worktrees", "secret-file-names"):
        shutil.copy2(SCRIPTS / script, repo / "scripts" / script)
    git("add", ".", folder=repo)
    git("commit", "--quiet", "-m", "chore: start", folder=repo)
    git("push", "--quiet", str(origin), "main", f"main:refs/heads/{BRANCH}", folder=repo)
    git("remote", "add", "origin", GITHUB_URL, folder=repo)
    git("config", "core.hooksPath", "git-hooks", folder=repo)
    return folder


class RunBuilderTest(unittest.TestCase):
    """A test in a clone whose origin is a bare repository with a GitHub URL, with stand-ins for gh, claude and codex
    first on PATH, a loop folder with four accounts, and git hooks in the clone that record that they ran."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.repo, self.origin = self.root / "repo", self.root / "origin.git"
        self.loop, self.standins, self.bin = self.root / "loop", self.root / "standins", self.root / "bin"
        for created in (self.loop, self.standins, self.bin):
            created.mkdir()
        for tool in ("gh", "claude", "codex"):
            (self.bin / tool).write_text(STANDIN)
            (self.bin / tool).chmod(0o755)
        environment = mock.patch.dict(
            os.environ,
            {
                **GIT_ENVIRONMENT,
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "STANDIN_DIR": str(self.standins),
                "LOOP_DIR": str(self.loop),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        starting = Path(starting_repositories().name)
        shutil.copytree(starting / "origin.git", self.origin, symlinks=True)
        shutil.copytree(starting / "repo", self.repo, symlinks=True)
        # git sends what goes to the GitHub URL to this test's bare repository instead.
        self.git("config", f"url.{self.origin}.insteadOf", GITHUB_URL)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.repo)
        self.worktree = self.repo / ".claude" / "worktrees" / "run-12"
        (self.loop / "accounts.json").write_text(json.dumps({"accounts": ACCOUNTS}))
        self.answers: dict[str, dict[str, list[dict[str, Any]]]] = {
            "gh": {
                "repo view": [{"stdout": "owner/repo\n"}],
                "issue view": [{"stdout": json.dumps(ISSUE)}],
                "issue develop --list": [{"stdout": linked(BRANCH)}],
                "issue comment": [{}],
                "pr list": [{"stdout": "[]"}],
                # scripts/clear-worktrees asks how many open pull requests the branch has.
                f"pr list --head {BRANCH} --state open --json number --jq length": [{"stdout": "0\n"}],
                "pr create": [{"stdout": "https://github.com/owner/repo/pull/7\n"}],
                "pr merge": [{}],
                "pr ready": [{}],
                "api repos/{owner}/{repo}/rules/branches/main": [{"stdout": "check\ntitle\n"}],
                "api repos/{owner}/{repo}/issues/12": [{"stdout": "open\tfalse\tfalse\t0\n"}],
            },
            "claude": {"": [{"stdout": lines(INIT, "not an event", RESULT)}]},
            "codex": {"": [self.codex_answer({"built.txt": "built\n"})]},
        }

    def codex_answer(self, files: dict[str, str], report: str = REPORT) -> dict[str, Any]:
        """Codex's answer for a turn that writes the files and the report."""
        return {"stdout": lines(*CODEX_TURN), "write": files, "report": report}

    def git(self, *arguments: str, folder: Path | None = None) -> str:
        return git(*arguments, folder=folder or self.repo)

    def origin_git(self, *arguments: str) -> str:
        return self.git("--git-dir", str(self.origin), *arguments)

    def answer(self, tool: str, start: str, *answers: dict[str, Any]) -> None:
        self.answers[tool][start] = list(answers)

    def run_builder(self, *arguments: str, kind: str = "other") -> tuple[int, str, str]:
        (self.standins / "answers.json").write_text(json.dumps(self.answers))
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            status = run_builder.main(["12", *kind.split(), *arguments])
        return status, printed.getvalue(), errors.getvalue()

    def calls(self, tool: str, start: str | None = None) -> list[dict[str, Any]]:
        log = self.standins / "calls.jsonl"
        if not log.exists():
            return []
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return [call for call in calls if call["tool"] == tool and start in (None, call["start"])]

    def arguments(self, tool: str, start: str) -> list[list[str]]:
        return [call["args"] for call in self.calls(tool, start)]

    def run_folder(self) -> Path:
        [folder] = (self.loop / "runs").iterdir()
        return folder

    def status(self) -> dict[str, Any]:
        status: dict[str, Any] = json.loads((self.run_folder() / "status.json").read_text())
        return status

    def hooks_that_ran(self) -> list[str]:
        return sorted(name.removesuffix("-ran") for name in os.listdir(self.standins) if name.endswith("-ran"))

    def assert_pushed_nothing(self) -> None:
        self.assertEqual(self.origin_git("rev-parse", BRANCH), self.origin_git("rev-parse", "main"))
        self.assertEqual(self.git("rev-parse", BRANCH), self.origin_git("rev-parse", "main"))
        self.assertEqual(self.calls("gh", "pr create"), [])


class StartTest(RunBuilderTest):
    def test_stops_on_an_account_that_the_accounts_file_does_not_list(self) -> None:
        status, printed, errors = self.run_builder("--tool", "claude", "--account", "nobody")
        self.assertEqual((status, printed), (1, ""))
        self.assertEqual(
            errors,
            "error: .loop/accounts.json lists no claude account called nobody. Add it, as scripts/loop/meter --help "
            "shows, or name another one.\n",
        )
        self.assertEqual(self.calls("claude"), [])
        self.assertFalse((self.loop / "runs").exists())

    def test_stops_on_a_tool_that_has_no_account(self) -> None:
        (self.loop / "accounts.json").unlink()
        status, _, errors = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("lists no codex account.", errors)

    def test_stops_on_a_kind_that_take_next_issue_does_not_print(self) -> None:
        status, _, errors = self.run_builder("--tool", "claude", kind="left behind")
        self.assertEqual(status, 1)
        self.assertIn('error: "left behind" isn\'t a kind that scripts/take-next-issue prints:', errors)

    def test_stops_a_codex_builder_on_a_kind_that_needs_gh(self) -> None:
        for kind in ("parent", "left behind 31"):
            with self.subTest(kind=kind):
                status, _, errors = self.run_builder("--tool", "codex", kind=kind)
                self.assertEqual(status, 1)
                self.assertIn(f"a Codex builder can't build an issue of the kind {kind}, because it needs gh.", errors)
        self.assertEqual(self.calls("gh"), [])

    def test_stops_when_the_tool_is_not_installed(self) -> None:
        (self.bin / "codex").unlink()
        only_git = self.root / "only-git"
        only_git.mkdir()
        git = shutil.which("git")
        assert git
        (only_git / "git").symlink_to(git)
        with mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{only_git}"}):
            status, _, errors = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertEqual(errors, "error: codex isn't installed. Install it, and run the script again.\n")

    def test_stops_when_the_worktree_exists_already(self) -> None:
        self.worktree.mkdir(parents=True)
        status, _, errors = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn(f"error: {self.worktree} exists already, so another run may still use it.", errors)
        self.assertEqual(self.calls("gh"), [])

    def test_stops_unless_gh_acts_on_the_repository_of_origin(self) -> None:
        cases: list[tuple[dict[str, Any], str, str]] = [
            ({"stdout": "other/project\n"}, GITHUB_URL, "gh acts on other/project, and origin is owner/repo."),
            ({"stderr": "HTTP 401: Bad credentials\n", "status": 1}, GITHUB_URL, "because gh failed: HTTP 401"),
            ({"stdout": "owner/repo\n"}, str(self.origin), f'in the URL of origin, "{self.origin}".'),
        ]
        for answer, url, message in cases:
            with self.subTest(message=message):
                self.answer("gh", "repo view", answer)
                self.git("remote", "set-url", "origin", url)
                status, _, errors = self.run_builder("--tool", "claude")
                self.assertEqual(status, 1)
                self.assertIn(message, errors)
                self.assertFalse((self.loop / "runs").exists())

    def test_stops_on_an_issue_that_needs_triage(self) -> None:
        self.answer("gh", "issue view", {"stdout": json.dumps({**ISSUE, "labels": [{"name": "needs-triage"}]})})
        status, _, errors = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertTrue(errors.startswith("error: issue 12 has needs-triage, so no maintainer has accepted it yet. "))
        self.assertEqual(self.calls("claude"), [])

    def test_stops_when_gh_cannot_read_the_issue(self) -> None:
        self.answer("gh", "issue view", {"stderr": "GraphQL: Could not resolve to an issue\n", "status": 1})
        status, _, errors = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertEqual(errors, "error: gh failed: GraphQL: Could not resolve to an issue\n")


class WorktreeTest(RunBuilderTest):
    def test_creates_the_branch_of_an_issue_that_has_none(self) -> None:
        self.answer("gh", "issue develop --list", {"stdout": ""}, {"stdout": linked("12-new")})
        self.answer("gh", "issue develop", {"push": "12-new"})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 0, printed)
        self.assertEqual(self.arguments("gh", "issue develop"), [["issue", "develop", "12"]])
        self.assertEqual(self.calls("claude")[0]["head"], "ref: refs/heads/12-new")

    def test_builds_on_the_newest_branch_of_the_issue_in_this_repository(self) -> None:
        for branch, date in (("12-older", "2026-10-01"), ("12-newer", "2026-10-02"), ("12-elsewhere", "2026-10-03")):
            with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": f"{date}T12:00:00Z"}):
                commit = self.git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", branch)
            self.git("push", "--quiet", "origin", f"{commit}:refs/heads/{branch}")
        listing = linked("12-gone", "12-older", "12-newer") + linked("12-elsewhere", repository="other/project")
        self.answer("gh", "issue develop --list", {"stdout": listing})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 0, printed)
        self.assertEqual(self.calls("claude")[0]["head"], "ref: refs/heads/12-newer")
        self.assertEqual(self.calls("gh", "issue develop"), [])

    def test_fails_when_gh_creates_no_branch(self) -> None:
        self.answer("gh", "issue develop --list", {"stdout": ""})
        self.answer("gh", "issue develop", {})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn("| failed: gh issue develop created no branch for issue #12 that origin has. |", printed)
        self.assertTrue(printed.endswith(f"| no report, see {self.run_folder()}\n"))
        self.assertEqual(self.calls("claude"), [])


class ClaudeTest(RunBuilderTest):
    def test_claude_builder_builds_the_issue_in_its_worktree_and_says_where_its_report_is(self) -> None:
        caller = {
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "ANTHROPIC_API_KEY": "a",
            "CLAUDE_CONFIG_DIR": "/c",
        }
        with mock.patch.dict(os.environ, caller):
            status, printed, errors = self.run_builder("--tool", "claude", "--model", "opus", "--effort", "high")
        self.assertEqual((status, errors), (0, ""))
        report = self.run_folder() / "report.md"
        self.assertRegex(printed, rf"^issue 12 \| claude main \| finished \| [0-9.]+ min \| report {report}\n$")
        self.assertEqual(report.read_text(), "Opened pull request #5.")
        [call] = self.calls("claude")
        brief = (self.run_folder() / "brief.md").read_text()
        self.assertEqual(call["args"], [*run_builder.CLAUDE_COMMAND[1:], "--model", "opus", "--effort", "high", brief])
        self.assertTrue(brief.startswith("Issue #12: Fix the importer\n\nIt drops rows.\n"))
        self.assertIn('of the kind `other`, as a helper in a build run. Read only "Build one issue"', brief)
        self.assertEqual((call["cwd"], call["head"]), (str(self.worktree), f"ref: refs/heads/{BRANCH}"))
        environment = call["env"]
        self.assertEqual(environment["CLAUDE_CONFIG_DIR"], "/claude-main")
        self.assertEqual(environment["CLAUDE_CODE_RETRY_WATCHDOG"], "1")
        self.assertEqual(environment["GH_REPO"], "owner/repo")
        self.assertEqual([name for name in caller if name in environment], ["CLAUDE_CONFIG_DIR"])
        self.assertEqual(
            (self.status()["account"], self.status()["outcome"], self.status()["tool"]), ("main", "finished", "claude")
        )
        self.assertEqual((self.run_folder() / "events.jsonl").read_text(), lines(INIT, "not an event", RESULT))
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.git("worktree", "list", "--porcelain").count("worktree "), 1)

    def test_claude_builder_runs_on_the_account_that_account_names_and_only_on_it(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/caller"}):
            status, printed, _ = self.run_builder("--tool", "claude", "--account", "second")
        self.assertEqual(status, 0)
        self.assertIn("| claude second | finished |", printed)
        self.assertNotIn("CLAUDE_CONFIG_DIR", self.calls("claude")[0]["env"])

    def test_claude_builder_gets_the_kind_that_take_next_issue_printed(self) -> None:
        status, _, _ = self.run_builder("--tool", "claude", kind="left behind 31")
        self.assertEqual(status, 0)
        self.assertIn("of the kind `left behind 31`, as a helper", (self.run_folder() / "brief.md").read_text())

    def test_fails_when_claude_starts_in_another_permission_mode(self) -> None:
        self.answer("claude", "", {"stdout": lines({**INIT, "permissionMode": "default"}, RESULT)})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn("| failed: claude started in permission mode default, not auto,", printed)
        self.assertIn("| no report, see", printed)
        self.assertFalse(self.worktree.exists())

    def test_fails_when_claude_gives_no_result(self) -> None:
        self.answer("claude", "", {"stdout": lines(INIT), "status": 1})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn("| failed: claude ended with code 1 and gave no result.", printed)

    def test_fails_with_the_errors_of_the_result(self) -> None:
        error = {"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": ["Disk full"]}
        self.answer("claude", "", {"stdout": lines(INIT, error)})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn("| failed: claude's result is error_during_execution: Disk full |", printed)
        self.assertFalse((self.run_folder() / "report.md").exists())


class UnsavedWorkTest(RunBuilderTest):
    """A run that ends with changes in the worktree that nobody committed."""

    def assert_branch_holds_the_unsaved_work(self) -> None:
        self.assertEqual(self.origin_git("log", "-1", "--format=%s", BRANCH), SAVE_SUBJECT)
        self.assertEqual(self.origin_git("show", f"{BRANCH}:half.txt"), "half")
        self.assertFalse(self.worktree.exists())

    def test_saves_the_changes_of_a_claude_builder_that_stopped_on_the_branch(self) -> None:
        stopped = {"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": ["Usage limit"]}
        self.answer("claude", "", {"stdout": lines(INIT, stopped), "write": {"half.txt": "half\n"}})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn("| failed: claude's result is error_during_execution: Usage limit |", printed)
        self.assert_branch_holds_the_unsaved_work()

    def test_saves_the_changes_of_a_codex_builder_that_stopped_with_the_hooks_of_the_main_checkout(self) -> None:
        failed = {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}
        # Codex changes the worktree's copy of a git hook, which git would run outside its sandbox.
        changed_hook = '#!/bin/sh\ntouch "$STANDIN_DIR/branch-pre-push-ran"\n'
        files = {"half.txt": "half\n", "git-hooks/pre-push": changed_hook}
        self.answer("codex", "", {"stdout": lines(*CODEX_TURN[:2], failed), "write": files})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("| failed: codex's turn failed: You've hit your usage limit. |", printed)
        self.assert_branch_holds_the_unsaved_work()
        self.assertEqual(self.hooks_that_ran(), list(GIT_HOOKS))

    def test_commits_but_does_not_push_the_changes_on_a_branch_that_has_an_open_pull_request(self) -> None:
        self.answer("gh", f"pr list --head {BRANCH} --state open --json number --jq length", {"stdout": "1\n"})
        self.answer("claude", "", {"stdout": lines(INIT, RESULT), "write": {"half.txt": "half\n"}})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 0, printed)
        self.assertEqual(self.git("log", "-1", "--format=%s", BRANCH), SAVE_SUBJECT)
        self.assertEqual(self.git("show", f"{BRANCH}:half.txt"), "half")
        self.assertEqual(self.origin_git("rev-parse", BRANCH), self.origin_git("rev-parse", "main"))
        self.assertFalse(self.worktree.exists())

    def test_fails_and_keeps_the_worktree_when_clear_worktrees_leaves_it(self) -> None:
        stopped = {"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": ["Usage limit"]}
        self.answer("claude", "", {"stdout": lines(INIT, stopped), "write": {"half.txt": "half\n", ".env": "A=1\n"}})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn(
            "| failed: claude's result is error_during_execution: Usage limit, and scripts/clear-worktrees failed: "
            f"Left {self.worktree}, which holds the branch of issue #12, because it has untracked files whose names "
            "suggest a secret: .env |",
            printed,
        )
        self.assertEqual((self.worktree / "half.txt").read_text(), "half\n")
        self.assert_pushed_nothing()

    def test_fails_when_clear_worktrees_leaves_the_worktree_of_a_run_that_finished(self) -> None:
        self.answer("claude", "", {"stdout": lines(INIT, RESULT), "write": {".env": "A=1\n"}})
        status, printed, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        self.assertIn(
            f"| failed: scripts/clear-worktrees failed: Left {self.worktree}, which holds the branch", printed
        )
        self.assertTrue(self.worktree.exists())


class ClaudeProblemTest(unittest.TestCase):
    def test_is_none_for_a_success_and_exit_code_0(self) -> None:
        self.assertIsNone(run_builder.claude_problem(RESULT, 0))

    def test_gives_each_error_of_a_result_that_is_not_a_success(self) -> None:
        result = {"subtype": "error_max_turns", "is_error": False, "errors": ["Too many turns", "Stopped"]}
        self.assertEqual(
            run_builder.claude_problem(result, 0), "claude's result is error_max_turns: Too many turns, Stopped"
        )

    def test_gives_the_text_of_a_success_that_is_an_error(self) -> None:
        result = {**RESULT, "is_error": True, "result": "API Error: 529 overloaded"}
        self.assertEqual(run_builder.claude_problem(result, 0), "claude's result is success: API Error: 529 overloaded")

    def test_counts_an_exit_code_other_than_0_after_a_success(self) -> None:
        self.assertEqual(
            run_builder.claude_problem(RESULT, 143),
            "claude ended with code 143 after its result. stderr.log in the run folder says why.",
        )


class CodexTest(RunBuilderTest):
    def test_codex_builder_pushes_its_changes_and_opens_a_pull_request_with_auto_merge(self) -> None:
        # Codex changes the worktree's copy of a git hook, which git runs outside its sandbox.
        changed_hook = '#!/bin/sh\ntouch "$STANDIN_DIR/branch-pre-push-ran"\n'
        self.answer("codex", "", self.codex_answer({"built.txt": "built\n", "git-hooks/pre-push": changed_hook}))
        caller = {"GH_TOKEN": "a", "OPENAI_API_KEY": "b", "SSH_AUTH_SOCK": "c", "MY_PASSWORD": "d", "CODEX_HOME": "/c"}
        with mock.patch.dict(os.environ, caller):
            status, printed, errors = self.run_builder("--tool", "codex", "--model", "gpt-6", "--effort", "high")
        self.assertEqual((status, errors), (0, ""))
        report = self.run_folder() / "report.md"
        self.assertRegex(printed, rf"^issue 12 \| codex work \| pull request #7 \| [0-9.]+ min \| report {report}\n$")
        [call] = self.calls("codex")
        brief = (self.run_folder() / "brief.md").read_text()
        command = ["exec", "--sandbox", "workspace-write", "--cd", str(self.worktree), "--json"]
        command += ["--output-last-message", str(report), "--model", "gpt-6"]
        self.assertEqual(call["args"], [*command, "--config", 'model_reasoning_effort="high"', brief])
        self.assertIn("so don't run git or gh.", brief)
        self.assertEqual([name for name in caller if call["env"].get(name, "/codex-work") != "/codex-work"], [])
        self.assertEqual(self.origin_git("log", "-1", "--format=%s", BRANCH), "fix: Fix the importer")
        self.assertEqual(self.origin_git("show", f"{BRANCH}:built.txt"), "built")
        body = f"Closes #12\n\n## What changed and why\n\n{REPORT}\n"
        create = ["--title", "fix: Fix the importer", "--body", body, "--head", BRANCH, "--base", "main"]
        self.assertEqual(self.arguments("gh", "pr create"), [["pr", "create", *create]])
        pushed = self.origin_git("rev-parse", BRANCH)
        self.assertEqual(self.arguments("gh", "pr merge"), [["pr", "merge", "7", "--match-head-commit", pushed]])
        self.assertEqual(self.status()["outcome"], "pull request #7")
        self.assertEqual(self.hooks_that_ran(), list(GIT_HOOKS))
        self.assertFalse(self.worktree.exists())

    def test_codex_builder_that_changes_no_file_comments_its_report_on_the_issue(self) -> None:
        self.answer("codex", "", self.codex_answer({}, report="Use the library.\n"))
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/caller"}):
            status, printed, _ = self.run_builder("--tool", "codex", "--account", "spare", kind="research")
        self.assertEqual(status, 0)
        self.assertIn("| codex spare | comment |", printed)
        self.assertEqual(
            self.arguments("gh", "issue comment"), [["issue", "comment", "12", "--body", "Use the library."]]
        )
        self.assertIn("of the kind `research`", (self.run_folder() / "brief.md").read_text())
        self.assertNotIn("CODEX_HOME", self.calls("codex")[0]["env"])
        self.assertEqual(self.calls("gh", "pr create"), [])

    def test_fails_when_the_hooks_would_block_the_comment(self) -> None:
        # The path is put together here, because scripts/check blocks a home path in the files.
        self.answer("codex", "", self.codex_answer({}, report="Read the notes in " + "/Users" + "/someone/notes.md."))
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("| failed: the hooks would block codex's report as a comment on the issue: Blocked:", printed)
        self.assertIn("holds a path into a home folder", printed)
        self.assertEqual(self.calls("gh", "issue comment"), [])

    def test_fails_when_codex_writes_no_report(self) -> None:
        self.answer("codex", "", {"stdout": lines(*CODEX_TURN), "write": {"built.txt": "built\n"}})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("| failed: codex wrote no report, so the script can't describe its change", printed)

    def test_fails_with_the_message_of_a_turn_that_failed(self) -> None:
        failed = {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}
        self.answer("codex", "", {"stdout": lines(*CODEX_TURN[:2], failed), "write": {"built.txt": "x"}})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("| failed: codex's turn failed: You've hit your usage limit. |", printed)
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.calls("gh", "pr create"), [])

    def test_pushes_to_the_pull_request_that_the_branch_has_and_leaves_its_auto_merge_alone(self) -> None:
        listed = [{**OWN_PULL_REQUEST, "number": 5, "isCrossRepository": True}, OWN_PULL_REQUEST]
        self.answer("gh", "pr list", {"stdout": json.dumps(listed)})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 0)
        self.assertIn("| codex work | pull request #6 |", printed)
        self.assertEqual(self.origin_git("show", f"{BRANCH}:built.txt"), "built")
        self.assertEqual(
            self.calls("gh", "pr create") + self.calls("gh", "pr merge") + self.calls("gh", "pr ready"), []
        )

    def test_leaves_auto_merge_off_while_main_lacks_a_required_check(self) -> None:
        self.answer("gh", "api repos/{owner}/{repo}/rules/branches/main", {"stdout": "check\n"})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 0)
        self.assertIn("| pull request #7, auto-merge off: main no longer requires the title job,", printed)
        self.assertEqual(self.calls("gh", "pr merge"), [])


class CodexPushTest(RunBuilderTest):
    """What keeps a Codex builder's commit from going out."""

    def test_pushes_nothing_when_the_pull_request_would_break_the_writing_rules(self) -> None:
        self.answer("codex", "", self.codex_answer({"built.txt": "x"}, report="One; two."))
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("| failed: the pull request's text has problems, so the script pushed nothing:", printed)
        self.assertIn("has a semicolon in prose", printed)
        self.assert_pushed_nothing()

    def test_pushes_nothing_when_a_git_hook_of_the_main_checkout_refuses_the_push(self) -> None:
        (self.repo / "git-hooks" / "pre-push").write_text('#!/bin/sh\necho "the hook refuses the push" >&2\nexit 1\n')
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("| failed: git failed: the hook refuses the push", printed)
        self.assert_pushed_nothing()

    def test_pushes_nothing_when_a_file_has_the_name_of_a_secret(self) -> None:
        self.answer("codex", "", self.codex_answer({"built.txt": "x", "config/.netrc": "machine github.com"}))
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn(
            "| failed: codex left files whose names suggest a secret, so the script pushed nothing: ", printed
        )
        self.assertIn("config/.netrc |", printed)
        self.assert_pushed_nothing()

    def test_pushes_nothing_when_the_leak_scan_finds_a_token(self) -> None:
        # The token is put together here, because the leak scan blocks a token in the files.
        token = "ghp_" + "A1b2C3d4" * 5
        self.answer("codex", "", self.codex_answer({"built.txt": "x", "notes.txt": f"Use {token} to push.\n"}))
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn("| failed: scripts/leak-scan refused codex's commit, so the script pushed nothing:", printed)
        self.assertNotIn(token, printed)
        self.assert_pushed_nothing()


class CodexReviewTest(RunBuilderTest):
    """A Codex builder's change that scripts/loop/consequential lists."""

    def setUp(self) -> None:
        super().setUp()
        self.answer("codex", "", self.codex_answer({"scripts/hooks/new.check": "#!/usr/bin/env bash\n"}))

    def test_opens_the_pull_request_as_a_draft_without_auto_merge(self) -> None:
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 0)
        self.assertIn("| codex work | draft pull request #7 |", printed)
        self.assertEqual(self.arguments("gh", "pr create")[0][-1], "--draft")
        self.assertEqual(self.calls("gh", "pr merge"), [])
        self.assertIn(
            "the pull request\nis a draft, because a reviewer reads it", (self.run_folder() / "brief.md").read_text()
        )

    def test_makes_the_pull_request_of_the_branch_a_draft_without_auto_merge_before_the_push(self) -> None:
        listed = {**OWN_PULL_REQUEST, "autoMergeRequest": {"enabledAt": "2026-10-18T00:00:00Z"}}
        self.answer("gh", "pr list", {"stdout": json.dumps([listed])})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 0)
        self.assertIn("| codex work | draft pull request #6 |", printed)
        self.assertEqual(self.arguments("gh", "pr merge"), [["pr", "merge", "6", "--disable-auto"]])
        self.assertEqual(self.arguments("gh", "pr ready"), [["pr", "ready", "6", "--undo"]])
        self.assertEqual(self.calls("gh", "pr create"), [])

    def test_leaves_a_draft_pull_request_of_the_branch_as_it_is(self) -> None:
        self.answer("gh", "pr list", {"stdout": json.dumps([{**OWN_PULL_REQUEST, "isDraft": True}])})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 0)
        self.assertIn("| codex work | draft pull request #6 |", printed)
        self.assertEqual(self.calls("gh", "pr merge") + self.calls("gh", "pr ready"), [])

    def test_pushes_nothing_when_consequential_cannot_tell(self) -> None:
        # Without main on origin, git can't compare the branch with it.
        self.origin_git("symbolic-ref", "HEAD", f"refs/heads/{BRANCH}")
        self.origin_git("update-ref", "-d", "refs/heads/main")
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 1)
        self.assertIn(
            "| failed: scripts/loop/consequential can't tell whether a reviewer must read the change,", printed
        )
        self.assertNotIn("scripts/hooks/new.check", self.origin_git("ls-tree", "-r", "--name-only", BRANCH))
        self.assertEqual(self.calls("gh", "pr create"), [])


class CodexProblemTest(unittest.TestCase):
    def test_is_none_for_a_completed_turn_and_exit_code_0(self) -> None:
        self.assertIsNone(run_builder.codex_problem(CODEX_TURN, 0))

    def test_says_when_no_turn_started(self) -> None:
        self.assertEqual(
            run_builder.codex_problem([{"type": "thread.started"}], 1),
            "codex ended with code 1 before its turn started. stderr.log in the run folder says why.",
        )

    def test_says_when_the_turn_did_not_complete(self) -> None:
        self.assertEqual(
            run_builder.codex_problem(CODEX_TURN[:3], 0),
            "codex ended with code 0 before its turn completed. stderr.log in the run folder says why.",
        )

    def test_counts_an_exit_code_other_than_0_after_a_completed_turn(self) -> None:
        self.assertEqual(
            run_builder.codex_problem(CODEX_TURN, 2),
            "codex ended with code 2 after its turn. stderr.log in the run folder says why.",
        )


class CommitSubjectTest(unittest.TestCase):
    def test_takes_its_type_from_the_label_of_the_issue(self) -> None:
        for label, subject_type in (
            ("feature", "feat"),
            ("bug", "fix"),
            ("maintenance", "chore"),
            ("research", "chore"),
        ):
            with self.subTest(label=label):
                issue = {**ISSUE, "labels": [{"name": label}]}
                self.assertEqual(run_builder.commit_subject(issue), f"{subject_type}: Fix the importer")


class ScriptTest(RunBuilderTest):
    def test_runs_as_a_script(self) -> None:
        done = subprocess.run(
            [sys.executable, str(RUN_BUILDER), "12", "other", "--tool", "claude", "--account", "nobody"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("lists no claude account called nobody", done.stderr)
