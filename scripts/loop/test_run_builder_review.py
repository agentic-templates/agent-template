"""Tests of the reviewers that scripts/loop/run-builder starts for a pull request."""

import contextlib
import io
import json
import os
from unittest import mock

from test_run_builder import INIT, RunBuilderTest, lines, run_builder
from test_run_builder_message import MESSAGE

REVIEWER_FILE = "---\nname: reviewer\nmodel: opus\neffort: high\n---\n\nReview it.\n"
REVIEW = {"type": "result", "subtype": "success", "is_error": False, "result": "No errors."}
PERMISSION = "api repos/{owner}/{repo}/collaborators/alice/permission"


class ReviewTest(RunBuilderTest):
    """A review of pull request #7, which alice opened, and whose head is a commit on top of main."""

    def setUp(self) -> None:
        super().setUp()
        (self.agents / "reviewer.md").write_text(REVIEWER_FILE)
        main = self.git("rev-parse", "HEAD")
        (self.repo / "change.txt").write_text("the pull request's change\n")
        self.git("add", "change.txt")
        self.git("commit", "--quiet", "-m", "feat: change")
        self.head = self.git("rev-parse", "HEAD")
        self.git("push", "--quiet", str(self.origin), "HEAD:refs/pull/7/head")
        self.git("reset", "--quiet", "--hard", main)
        self.pull_request = {
            "number": 7,
            "headRefOid": self.head,
            "isCrossRepository": False,
            "author": {"login": "alice"},
            "labels": [{"name": "bug"}],
        }
        self.answer("gh", "pr view", {"stdout": json.dumps(self.pull_request)})
        self.answer("gh", PERMISSION, {"stdout": json.dumps({"permission": "write", "role_name": "maintain"})})
        # The reviewer leaves a change in its worktree, which the script must neither commit nor push.
        self.answer("claude", "", {"stdout": lines(INIT, REVIEW), "write": {"scratch.txt": "scratch\n"}})
        self.worktree = self.repo / ".claude" / "worktrees" / "review-7"
        self.origin_refs = self.origin_git("for-each-ref")

    def review(self, *arguments: str) -> tuple[int, str, str]:
        (self.standins / "answers.json").write_text(json.dumps(self.answers))
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            status = run_builder.main(list(arguments))
        return status, printed.getvalue(), errors.getvalue()

    def assert_changed_nothing_on_github(self) -> None:
        self.assertEqual(self.origin_git("for-each-ref"), self.origin_refs)
        gh_calls = [call["start"] for call in self.calls("gh")]
        self.assertEqual([start for start in gh_calls if start not in ("repo view", "pr view", PERMISSION)], [])

    def test_reviews_the_pull_request_on_the_named_account_in_a_worktree_detached_at_its_head(self) -> None:
        with mock.patch.dict(os.environ, {"LOOP_HAND_OFF": "1"}):
            status, printed, errors = self.review("--review", "7", "--account", "second")
        self.assertEqual((status, errors), (0, ""))
        run = self.run_folder()
        self.assertTrue(run.name.startswith("review-7-"))
        report = run / "report.md"
        self.assertRegex(printed, rf"^pull request 7 \| claude second \| finished \| [0-9.]+ min \| report {report}\n$")
        self.assertEqual(report.read_text(), "No errors.")
        [call] = self.calls("claude")
        self.assertEqual((call["cwd"], call["head"]), (str(self.worktree), self.head))
        brief = (run / "brief.md").read_text()
        settings = ["--model", "opus", "--effort", "high", "--append-system-prompt", "Review it."]
        self.assertEqual(call["args"], [*run_builder.CLAUDE_COMMAND[1:], *settings, brief])
        self.assertTrue(brief.startswith("7\n\nYou review pull request #7 for the orchestrator"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", call["env"])
        self.assertEqual(call["env"]["LOOP_RUN"], "1")
        self.assertNotIn("LOOP_HAND_OFF", call["env"])
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.git("worktree", "list", "--porcelain").count("worktree "), 1)
        self.assert_changed_nothing_on_github()
        self.assertEqual((self.status()["pull_request"], self.status()["account"]), (7, "second"))
        start, end = self.agent_lines()
        self.assertEqual(
            {name: start[name] for name in ("type", "issue", "label", "kind", "test", "account")},
            {"type": "reviewer", "issue": 7, "label": "bug", "kind": None, "test": False, "account": "second"},
        )
        self.assertEqual(end["pull_request"], None)

    def test_reviewer_gets_the_message_after_the_number_of_the_pull_request(self) -> None:
        message = self.root / "message.md"
        message.write_text(f"{MESSAGE}\n")
        status, _, _ = self.review("--review", "7", "--message", str(message))
        self.assertEqual(status, 0)
        brief = (self.run_folder() / "brief.md").read_text()
        self.assertTrue(brief.startswith(f"7\n\nThe orchestrator adds this message for you:\n\n{MESSAGE}\n\n---\n\n"))
        self.assertIn("You review pull request #7", brief)

    def test_fails_and_removes_the_worktree_when_the_reviewer_fails(self) -> None:
        self.answer("claude", "", {"stdout": lines(INIT), "status": 1, "write": {"half.txt": "half\n"}})
        status, printed, _ = self.review("--review", "7")
        self.assertEqual(status, 1)
        self.assertIn("pull request 7 | claude main | failed: claude ended with code 1 and gave no result.", printed)
        self.assertFalse(self.worktree.exists())
        self.assert_changed_nothing_on_github()

    def test_reviews_the_head_that_gh_gave_when_the_pull_request_got_a_newer_commit(self) -> None:
        self.git("checkout", "--quiet", self.head)
        (self.repo / "later.txt").write_text("a commit pushed after gh read the pull request\n")
        self.git("add", "later.txt")
        self.git("commit", "--quiet", "-m", "feat: later")
        self.git("push", "--quiet", "--force", str(self.origin), "HEAD:refs/pull/7/head")
        self.git("checkout", "--quiet", "-")
        status, _, _ = self.review("--review", "7")
        self.assertEqual(status, 0)
        [call] = self.calls("claude")
        self.assertEqual(call["head"], self.head)

    def test_stops_when_the_head_of_the_pull_request_is_in_another_repository(self) -> None:
        self.answer("gh", "pr view", {"stdout": json.dumps({**self.pull_request, "isCrossRepository": True})})
        status, printed, errors = self.review("--review", "7")
        self.assertEqual((status, printed), (1, ""))
        self.assertEqual(
            errors,
            "error: the head of pull request #7 is a branch in another repository, whose commits anyone may have "
            "written, and the reviewer would run hooks from them. Start the reviewer in your own session, which reads "
            "the diff without running its code.\n",
        )
        self.assertEqual((self.calls("claude"), (self.loop / "agents.jsonl").exists()), ([], False))

    def test_stops_when_the_author_of_the_pull_request_cannot_write_to_the_repository(self) -> None:
        self.answer("gh", PERMISSION, {"stdout": json.dumps({"permission": "read", "role_name": "triage"})})
        status, printed, errors = self.review("--review", "7")
        self.assertEqual((status, printed), (1, ""))
        self.assertEqual(
            errors,
            "error: alice, who opened pull request #7, has the permission read, not write, and the reviewer would run "
            "hooks from the pull request. Start the reviewer in your own session, which reads the diff without "
            "running its code.\n",
        )
        self.assertEqual((self.calls("claude"), (self.loop / "agents.jsonl").exists()), ([], False))

    def test_stops_when_gh_cannot_tell_whether_the_author_may_write(self) -> None:
        self.answer("gh", PERMISSION, {"stderr": "gh: Not Found (HTTP 404)\n", "status": 1})
        status, _, errors = self.review("--review", "7")
        self.assertEqual(status, 1)
        self.assertEqual(
            errors,
            "error: can't tell whether alice, who opened pull request #7, may write to the repository, because gh "
            "failed: gh: Not Found (HTTP 404)\n",
        )
        self.assertEqual(self.calls("claude"), [])

    def test_stops_a_reviewer_that_gets_an_issue_a_tool_a_type_or_a_test(self) -> None:
        message = (
            "error: a reviewer runs on Claude with the type reviewer, for the pull request that --review names, so it "
            "takes no issue, kind, --tool, --type or --test. Leave them out.\n"
        )
        for options in (["12", "other"], ["--tool", "claude"], ["--type", "reviewer"], ["--test", "7"]):
            with self.subTest(options=options):
                self.assertEqual(self.review("--review", "7", *options), (1, "", message))
        self.assertEqual(self.calls("gh"), [])

    def test_stops_a_builder_without_an_issue_its_kind_or_a_tool(self) -> None:
        message = (
            "error: a builder needs the issue and its kind, as scripts/take-next-issue printed them, and --tool, such "
            "as 12 other --tool codex. For a reviewer, give --review and the pull request's number instead.\n"
        )
        for arguments in (["--tool", "codex"], ["12", "--tool", "codex"], ["12", "other"]):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.review(*arguments), (1, "", message))
        self.assertEqual(self.calls("gh"), [])
