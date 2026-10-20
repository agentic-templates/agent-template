"""Tests of the agent type that a run of scripts/loop/run-builder takes its settings from, the lines that a run adds
to agents.jsonl, and the test builds of a retrospective."""

import datetime as dt
import hashlib
import json
import os
import re
from typing import Any
from unittest import mock

import paths
from test_run_builder import (
    BRANCH,
    BUILDER_FILE,
    INIT,
    OWN_PULL_REQUEST,
    REPORT,
    RESULT,
    RunBuilderTest,
    lines,
    run_builder,
)
from test_run_builder_message import MESSAGE, MESSAGE_START
from test_transcripts import call_line, command_line, prompt_line, result_line


class TypeTest(RunBuilderTest):
    def test_stops_a_claude_builder_without_a_type(self) -> None:
        status, printed, errors = self.run_builder("--tool", "claude", agent_type=None)
        self.assertEqual((status, printed), (1, ""))
        self.assertEqual(
            errors,
            "error: a Claude builder needs the agent type whose model and effort it runs with. Add --type, such as "
            "--type builder.\n",
        )
        self.assertEqual((self.calls("claude"), (self.loop / "agents.jsonl").exists()), ([], False))

    def test_stops_a_claude_builder_whose_type_has_no_agent_file(self) -> None:
        status, _, errors = self.run_builder("--tool", "claude", agent_type="builder-huge")
        self.assertEqual(status, 1)
        self.assertEqual(
            errors,
            "error: there's no agent file .claude/agents/builder-huge.md for the type builder-huge. Give a type that "
            ".claude/agents has.\n",
        )

    def test_stops_a_codex_builder_that_gets_a_type_or_a_test(self) -> None:
        message = (
            "error: a Codex builder runs with Codex's own model and effort, so it takes neither --type nor --test. "
            "Leave them out.\n"
        )
        for options in (["--type", "builder"], ["--test", "7"]):
            with self.subTest(options=options):
                self.assertEqual(self.run_builder("--tool", "codex", *options), (1, "", message))

    def test_runs_claude_with_the_tools_disallowed_tools_and_limit_on_turns_of_the_agent_file(self) -> None:
        fields = "tools: Read, Grep, Bash\ndisallowedTools: Bash(git push *), WebFetch\nmaxTurns: 40\n"
        (self.agents / "builder-cheaper.md").write_text(f"---\nname: builder-cheaper\n{fields}---\n")
        status, _, errors = self.run_builder("--tool", "claude", agent_type="builder-cheaper")
        self.assertEqual((status, errors), (0, ""))
        [call] = self.calls("claude")
        self.assertEqual(
            call["args"][len(run_builder.CLAUDE_COMMAND) - 1 :],
            [
                "--tools",
                "Read, Grep, Bash",
                "--disallowedTools",
                "Bash(git push *), WebFetch",
                "--max-turns",
                "40",
                "--",
                (self.run_folder() / "brief.md").read_text(),
            ],
        )

    def test_puts_the_brief_after_a_double_dash_when_the_agent_file_disallows_tools(self) -> None:
        (self.agents / "builder-cheaper.md").write_text("---\nname: builder-cheaper\ndisallowedTools: Bash\n---\n")
        status, _, errors = self.run_builder("--tool", "claude", agent_type="builder-cheaper")
        self.assertEqual((status, errors), (0, ""))
        [call] = self.calls("claude")
        self.assertEqual(call["args"][-3:], ["Bash", "--", (self.run_folder() / "brief.md").read_text()])

    def test_runs_claude_without_the_settings_that_the_agent_file_lacks(self) -> None:
        (self.agents / "builder-small.md").write_text("---\nname: builder-small\nmodel: sonnet\n---\n")
        status, _, _ = self.run_builder("--tool", "claude", agent_type="builder-small")
        self.assertEqual(status, 0)
        [call] = self.calls("claude")
        self.assertEqual(call["args"][len(run_builder.CLAUDE_COMMAND) - 1 : -1], ["--model", "sonnet", "--"])


class AgentLinesTest(RunBuilderTest):
    """The lines that a run adds to agents.jsonl."""

    def start_and_end(self) -> tuple[dict[str, Any], dict[str, Any]]:
        start, end = self.agent_lines()
        for line in (start, end):
            moment = dt.datetime.fromisoformat(line.pop("at"))
            self.assertLess(abs(dt.datetime.now(dt.UTC) - moment), dt.timedelta(minutes=1))
        self.assertEqual((start.pop("event"), end.pop("event")), ("start", "end"))
        self.assertEqual(start.pop("id"), self.run_folder().name)
        self.assertEqual(end.pop("id"), self.run_folder().name)
        return start, end

    def assert_run_usage(self, end: dict[str, Any]) -> None:
        """Checks the CPU time and peak memory, which differ from one run to the next."""
        self.assertGreaterEqual(end.pop("cpu_seconds"), 0)
        self.assertGreater(end.pop("peak_memory_mb"), 0)

    def test_gives_a_claude_builders_numbers_from_its_transcript(self) -> None:
        config = self.root / "claude-config"
        (self.loop / "accounts.json").write_text(
            json.dumps({"accounts": [{"name": "main", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": str(config)}}]})
        )
        # Claude Code names a project's folder after its path, with a dash for each character other than a letter or a
        # digit.
        transcript = config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.worktree)) / "s1.jsonl"
        entries = [
            prompt_line(0),
            call_line("a", 1, tokens=1000, tool_calls=3),
            command_line("b", 5, "toolu_9", "gh pr create --fill"),
            result_line(6, "toolu_9", "https://github.com/owner/repo/pull/5\n"),
            call_line("c", 12, tokens=500),
        ]
        written = {str(transcript): lines(*entries), "big.bin": "x" * 300_000}
        # A run with a background agent gives a result for each prompt. The usage of a result counts only the builder's
        # own replies to its prompt, and its modelUsage the whole run until then, with the agents that it started.
        first = {
            **RESULT,
            "usage": {"input_tokens": 4, "cache_read_input_tokens": 1000, "output_tokens": 300},
            "modelUsage": {"claude-opus": {"inputTokens": 6, "outputTokens": 363, "cacheReadInputTokens": 20000}},
        }
        builder = {
            "inputTokens": 10,
            "outputTokens": 366,
            "cacheReadInputTokens": 30000,
            "cacheCreationInputTokens": 900,
        }
        agent = {"inputTokens": 3, "outputTokens": 1000, "cacheReadInputTokens": 2000, "costUSD": 0.5}
        second = {
            **RESULT,
            "usage": {"input_tokens": 4, "cache_read_input_tokens": 1000, "output_tokens": 3},
            "modelUsage": {"claude-opus": builder, "claude-haiku": agent},
        }
        self.answer("claude", "", {"stdout": lines({**INIT, "session_id": "s1"}, first, second), "write": written})
        status, _, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 0)
        start, end = self.start_and_end()
        self.assertEqual(
            start,
            {
                "type": "builder",
                "model": "opus",
                "effort": "high",
                "agent_file": hashlib.sha1(b"blob %d\0" % len(BUILDER_FILE) + BUILDER_FILE.encode()).hexdigest(),
                "issue": 12,
                "label": "bug",
                "kind": "other",
                "test": False,
                "pid": os.getpid(),
                "account": "main",
            },
        )
        self.assert_run_usage(end)
        self.assertGreaterEqual(end.pop("worktree_mb"), 0.3)
        self.assertEqual(
            end,
            {
                "model": None,
                "effort": None,
                "minutes": 12.0,
                "periods": [["2026-10-11T14:00:00+00:00", "2026-10-11T14:12:00+00:00"]],
                "tool_calls": 4,
                "input_tokens": 32913,
                "output_tokens": 1366,
                "requests": None,
                "pull_request": 5,
            },
        )

    def test_names_the_account_that_the_builder_ran_on(self) -> None:
        status, _, _ = self.run_builder("--tool", "claude", "--account", "second")
        self.assertEqual(status, 0)
        start, _ = self.start_and_end()
        self.assertEqual(start["account"], "second")

    def test_gives_a_claude_builder_without_a_transcript_no_calls(self) -> None:
        status, _, _ = self.run_builder("--tool", "claude", kind="left behind 31")
        self.assertEqual(status, 0)
        start, end = self.start_and_end()
        self.assertEqual(start["kind"], "left behind 31")
        self.assertGreaterEqual(end.pop("minutes"), 0)
        self.assertEqual(
            {name: end[name] for name in ("tool_calls", "input_tokens", "output_tokens", "pull_request")},
            {"tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "pull_request": None},
        )

    def test_gives_a_codex_builders_numbers_from_its_events(self) -> None:
        events = [
            {"type": "thread.started"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "reasoning"}},
            {"type": "item.started", "item": {"type": "command_execution"}},
            {"type": "item.completed", "item": {"type": "command_execution"}},
            {"type": "item.completed", "item": {"type": "command_execution"}},
            {"type": "item.completed", "item": {"type": "file_change"}},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {
                "type": "turn.completed",
                # Codex counts the reasoning tokens among the output tokens, and repeats them on their own.
                "usage": {
                    "input_tokens": 24763,
                    "cached_input_tokens": 24448,
                    "output_tokens": 122,
                    "reasoning_output_tokens": 80,
                },
            },
        ]
        self.answer("codex", "", {"stdout": lines(*events), "write": {"built.txt": "built\n"}, "report": REPORT})
        status, _, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 0)
        start, end = self.start_and_end()
        self.assertEqual(
            start,
            {
                "type": "codex",
                "model": None,
                "effort": None,
                "agent_file": None,
                "issue": 12,
                "label": "bug",
                "kind": "other",
                "test": False,
                "pid": os.getpid(),
                "account": "work",
            },
        )
        self.assert_run_usage(end)
        [[began, ended]] = end.pop("periods")
        minutes = (dt.datetime.fromisoformat(ended) - dt.datetime.fromisoformat(began)).total_seconds() / 60
        # The minutes are exact to a tenth, and the periods are to the second, so the two can round apart.
        self.assertAlmostEqual(end.pop("minutes"), minutes, delta=0.05 + 1 / 60)
        self.assertGreaterEqual(end.pop("worktree_mb"), 0)
        self.assertEqual(
            end,
            {
                "model": None,
                "effort": None,
                "tool_calls": 3,
                "input_tokens": 24763,
                "output_tokens": 122,
                "requests": None,
                "pull_request": 7,
            },
        )

    def test_gives_no_pull_request_to_a_codex_builder_that_pushed_to_the_branchs_own(self) -> None:
        self.answer("gh", "pr list", {"stdout": json.dumps([OWN_PULL_REQUEST])})
        status, printed, _ = self.run_builder("--tool", "codex")
        self.assertEqual(status, 0)
        self.assertIn("| pull request #6 |", printed)
        self.assertIsNone(self.agent_lines()[1]["pull_request"])

    def test_ends_a_run_that_failed_before_it_had_a_worktree(self) -> None:
        self.answer("gh", "api graphql", {"stdout": ""})
        self.answer("gh", "issue develop", {})
        status, _, _ = self.run_builder("--tool", "claude")
        self.assertEqual(status, 1)
        _, end = self.start_and_end()
        self.assertEqual((end["worktree_mb"], end["pull_request"]), (None, None))


class TestBuildTest(RunBuilderTest):
    """A test build of issue 12, from the commit that its pull request #7 started from."""

    def setUp(self) -> None:
        super().setUp()
        self.base = self.git("rev-parse", "HEAD")
        (self.repo / "first.txt").write_text("the pull request's first change\n")
        self.git("add", "first.txt")
        self.git("commit", "--quiet", "-m", "feat: start the importer")
        first = self.git("rev-parse", "HEAD")
        self.git("push", "--quiet", str(self.origin), "HEAD:refs/pull/7/head")
        self.git("reset", "--quiet", "--hard", self.base)
        self.answer("gh", "pr view", {"stdout": f"{first}\n"})
        self.answer("claude", "", {"stdout": lines(INIT, RESULT), "write": {"built.txt": "built\n"}})
        self.worktree = self.repo / ".claude" / "worktrees" / "test-12-builder"

    def test_builds_the_issue_from_the_commit_that_its_pull_request_started_from_and_keeps_the_diff(self) -> None:
        with mock.patch.dict(os.environ, {"LOOP_HAND_OFF": "1"}):
            status, printed, errors = self.run_builder("--tool", "claude", "--test", "7")
        self.assertEqual((status, errors), (0, ""))
        run = self.run_folder()
        self.assertTrue(run.name.startswith("test-12-builder-"))
        changes = run / "changes.diff"
        self.assertRegex(printed, rf"^issue 12 \| claude main \| test finished, changes in {changes} \| [0-9.]+ min \|")
        [call] = self.calls("claude")
        self.assertEqual((call["cwd"], call["head"]), (str(self.worktree), self.base))
        self.assertIn(paths.RETROSPECTIVE_MARK, call["git_files"])
        self.assertEqual(call["env"]["LOOP_RUN"], run.name)
        self.assertNotIn("LOOP_HAND_OFF", call["env"])
        self.assertIn("This build is a test", call["args"][-1])
        self.assertIn("+++ b/built.txt\n@@ -0,0 +1 @@\n+built\n", changes.read_text())
        self.assertNotIn("first.txt", changes.read_text())
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.git("worktree", "list", "--porcelain").count("worktree "), 1)
        self.assertEqual(self.origin_git("rev-parse", BRANCH), self.origin_git("rev-parse", "main"))
        self.assertEqual([call["start"] for call in self.calls("gh")], ["repo view", "issue view", "pr view"])
        start, end = self.agent_lines()
        self.assertEqual((start["id"], start["test"], end["id"]), (run.name, True, run.name))

    def test_test_builder_gets_the_message_after_the_text_of_its_issue(self) -> None:
        message = self.root / "message.md"
        message.write_text(f"{MESSAGE}\n")
        status, _, errors = self.run_builder("--tool", "claude", "--test", "7", "--message", str(message))
        self.assertEqual((status, errors), (0, ""))
        brief = (self.run_folder() / "brief.md").read_text()
        self.assertTrue(brief.startswith(MESSAGE_START))
        self.assertIn("This build is a test", brief)
        self.assertEqual(self.calls("claude")[0]["args"][-1], brief)

    def test_keeps_the_diff_of_a_test_build_that_failed(self) -> None:
        self.answer("claude", "", {"stdout": lines(INIT), "status": 1, "write": {"half.txt": "half\n"}})
        status, printed, _ = self.run_builder("--tool", "claude", "--test", "7")
        self.assertEqual(status, 1)
        self.assertIn("| failed: claude ended with code 1 and gave no result.", printed)
        self.assertIn("+half", (self.run_folder() / "changes.diff").read_text())
        self.assertFalse(self.worktree.exists())
