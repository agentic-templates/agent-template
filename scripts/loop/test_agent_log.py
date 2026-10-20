import datetime as dt
import hashlib
import importlib.machinery
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import agent_log
from test_transcripts import call_line, command_line, prompt_line, result_line, write_transcript

AGENT_LOG = Path(__file__).with_name("agent-log")
CHECKOUT = AGENT_LOG.parents[2]
# The script's file name has no .py, so the tests load it by its path.
script = types.ModuleType("agent_log_script")
importlib.machinery.SourceFileLoader("agent_log_script", str(AGENT_LOG)).exec_module(script)

BUILDER_FILE = "---\nname: builder\ndescription: Builds one issue.\nmodel: opus\neffort: high\n---\n\nBuild it.\n"
# A stand-in for gh, which prints the labels in labels.txt in STANDIN_DIR, or fails without the file. -S keeps
# coverage, which starts in each Python process of the tests, out of the stand-in.
GH_STANDIN = f"""#!{sys.executable} -S
import os, sys
labels = os.path.join(os.environ["STANDIN_DIR"], "labels.txt")
with open(os.path.join(os.environ["STANDIN_DIR"], "gh-calls.txt"), "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
if not os.path.exists(labels):
    sys.stderr.write("HTTP 502: Bad Gateway\\n")
    sys.exit(1)
sys.stdout.write(open(labels).read())
"""


def git_blob_hash(text: str) -> str:
    """The hash that git gives a file with the text, which is the SHA-1 of a header and the text."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def agent_call(prompt: str, **fields: str) -> dict[str, Any]:
    """What Claude Code sends the before-tool hook for an Agent call."""
    tool_input = {"description": "Build the issue", "prompt": prompt, **fields}
    return {
        "session_id": "abc123",
        "cwd": str(CHECKOUT),
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": tool_input,
        "tool_use_id": "toolu_01",
    }


class AgentLogTest(unittest.TestCase):
    """A test with a loop folder, a folder of agent files with a builder, and a stand-in for gh first on PATH."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.loop, self.agents, self.standins = self.root / "loop", self.root / "agents", self.root / "standins"
        bin_folder = self.root / "bin"
        for created in (self.loop, self.agents, self.standins, bin_folder):
            created.mkdir()
        (self.agents / "builder.md").write_text(BUILDER_FILE)
        (bin_folder / "gh").write_text(GH_STANDIN)
        (bin_folder / "gh").chmod(0o755)
        environment = {
            "PATH": f"{bin_folder}{os.pathsep}{os.environ['PATH']}",
            "STANDIN_DIR": str(self.standins),
            "LOOP_DIR": str(self.loop),
            "CLAUDE_PID": "4321",
        }
        for patched in (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(agent_log, "AGENTS_FOLDER", self.agents),
        ):
            patched.start()
            self.addCleanup(patched.stop)
        # scripts/loop/run-builder sets the variable for a builder that runs these tests, and with it the script
        # writes nothing. The patch of the environment above puts it back after the test.
        os.environ.pop(agent_log.RUN_VARIABLE, None)
        self.labels("bug\nready\n")

    def labels(self, text: str | None) -> None:
        """Has gh give the labels, one to a line, or fail without them."""
        file = self.standins / "labels.txt"
        if text is None:
            file.unlink()
        else:
            file.write_text(text)

    def run_script(self, event: str, hook_input: dict[str, Any]) -> int:
        with mock.patch("sys.stdin", io.StringIO(json.dumps(hook_input))):
            return int(script.main([event]))

    def lines(self) -> list[dict[str, Any]]:
        log = self.loop / "agents.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def assert_time(self, text: str) -> None:
        moment = dt.datetime.fromisoformat(text)
        self.assertIsNotNone(moment.tzinfo)
        self.assertLess(abs(dt.datetime.now(dt.UTC) - moment), dt.timedelta(minutes=1))

    def gh_calls(self) -> list[str]:
        calls = self.standins / "gh-calls.txt"
        return calls.read_text().splitlines() if calls.exists() else []


class StartTest(AgentLogTest):
    def test_takes_the_model_and_effort_from_the_agent_file_when_the_call_gives_none(self) -> None:
        self.assertEqual(
            self.run_script("start", agent_call("12 other\nStart from the importer.", subagent_type="builder")), 0
        )
        [line] = self.lines()
        self.assert_time(line.pop("at"))
        self.assertEqual(
            line,
            {
                "event": "start",
                "id": "toolu_01",
                "type": "builder",
                "model": "opus",
                "effort": "high",
                "agent_file": git_blob_hash(BUILDER_FILE),
                "issue": 12,
                "label": "bug",
                "kind": "other",
                "test": False,
                "pid": 4321,
                "account": "claude",
            },
        )
        self.assertEqual(self.gh_calls(), ["issue view 12 --json labels --jq .labels[].name"])

    def test_gives_no_process_id_without_a_positive_number_from_claude_code(self) -> None:
        for pid in ("", "0", "-1", "12a"):
            with self.subTest(pid=pid), mock.patch.dict(os.environ, {"CLAUDE_PID": pid}):
                self.run_script("start", agent_call("12 other", subagent_type="builder"))
                self.assertIsNone(self.lines()[-1]["pid"])
        with mock.patch.dict(os.environ):
            del os.environ["CLAUDE_PID"]
            self.run_script("start", agent_call("12 other", subagent_type="builder"))
            self.assertIsNone(self.lines()[-1]["pid"])

    def test_names_the_sessions_own_account_by_its_variables(self) -> None:
        accounts = [
            {"name": "signed-in", "tool": "claude"},
            {"name": "codex", "tool": "codex", "env": {"CODEX_HOME": "/codex"}},
            {"name": "second", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "/second"}},
            {"name": "third", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "/third", "ANTHROPIC_MODEL": "opus"}},
        ]
        (self.loop / "accounts.json").write_text(json.dumps({"accounts": accounts}))
        cases = [
            ({"CLAUDE_CONFIG_DIR": "/second"}, "second"),
            ({"CLAUDE_CONFIG_DIR": "/third", "ANTHROPIC_MODEL": "opus"}, "third"),
            ({"CLAUDE_CONFIG_DIR": "/third"}, "signed-in"),
            ({"CLAUDE_CONFIG_DIR": "/other", "CODEX_HOME": "/codex"}, "signed-in"),
        ]
        for variables, account in cases:
            with self.subTest(variables=variables), mock.patch.dict(os.environ, variables):
                self.run_script("start", agent_call("12 other", subagent_type="builder"))
                self.assertEqual(self.lines()[-1]["account"], account)

    def test_names_no_account_when_none_matches_the_session_or_the_accounts_file_cant_be_read(self) -> None:
        unmatched = {"accounts": [{"name": "second", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": "/second"}}]}
        for text in (json.dumps(unmatched), "{not json", '{"accounts": []}'):
            with self.subTest(text=text), mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/other"}):
                (self.loop / "accounts.json").write_text(text)
                self.assertEqual(self.run_script("start", agent_call("12 other", subagent_type="builder")), 0)
                self.assertIsNone(self.lines()[-1]["account"])

    def test_takes_the_model_and_effort_that_the_call_gives(self) -> None:
        self.run_script("start", agent_call("12 left behind 31", subagent_type="builder", model="sonnet", effort="max"))
        [line] = self.lines()
        self.assertEqual((line["model"], line["effort"], line["kind"]), ("sonnet", "max", "left behind 31"))

    def test_takes_a_field_that_the_agent_file_lacks_from_the_call(self) -> None:
        (self.agents / "builder.md").write_text("---\nname: builder\nmodel: sonnet\n---\n")
        self.run_script("start", agent_call("12 other", subagent_type="builder", effort="low"))
        [line] = self.lines()
        self.assertEqual((line["model"], line["effort"]), ("sonnet", "low"))

    def test_reads_the_settings_of_an_agent_file_without_front_matter_or_its_closing_line(self) -> None:
        cases = {"Build it.\n": (None, None), "---\nmodel: haiku\neffort: low": ("haiku", "low"), "": (None, None)}
        for text, settings in cases.items():
            with self.subTest(text=text):
                (self.agents / "builder.md").write_text(text)
                self.run_script("start", agent_call("12 other", subagent_type="builder"))
                line = self.lines()[-1]
                self.assertEqual((line["model"], line["effort"], line["agent_file"]), (*settings, git_blob_hash(text)))

    def test_gives_the_number_alone_of_a_pull_request(self) -> None:
        self.labels("")
        self.run_script("start", agent_call("57", subagent_type="reviewer"))
        [line] = self.lines()
        self.assertEqual(
            {name: line[name] for name in ("type", "model", "effort", "agent_file", "issue", "label", "kind")},
            {
                "type": "reviewer",
                "model": None,
                "effort": None,
                "agent_file": None,
                "issue": 57,
                "label": None,
                "kind": None,
            },
        )

    def test_gives_no_issue_for_a_prompt_whose_first_line_names_none_and_asks_github_nothing(self) -> None:
        for prompt in ("Find the files that read the config.", "12 files to read", "", "0 other"):
            with self.subTest(prompt=prompt):
                self.run_script("start", agent_call(prompt, subagent_type="Explore"))
                self.assertEqual((self.lines()[-1]["issue"], self.lines()[-1]["kind"]), (None, None))
        self.assertEqual(self.gh_calls(), [])

    def test_gives_a_call_without_a_type_claude_codes_default_type(self) -> None:
        self.run_script("start", agent_call("Find the files."))
        self.assertEqual(self.lines()[0]["type"], "general-purpose")

    def test_writes_the_line_without_a_label_when_github_gives_none(self) -> None:
        self.labels(None)
        self.run_script("start", agent_call("12 other", subagent_type="builder"))
        self.assertEqual((self.lines()[0]["issue"], self.lines()[0]["label"]), (12, None))

    def test_writes_the_line_without_a_label_when_gh_is_missing_or_takes_too_long(self) -> None:
        for problem in (FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 10)):
            with self.subTest(problem=problem), mock.patch.object(script.subprocess, "run", side_effect=problem):
                self.assertEqual(script.issue_labels(12), [])

    def test_appends_to_the_log(self) -> None:
        self.run_script("start", agent_call("12 other", subagent_type="builder"))
        self.run_script("start", {**agent_call("13 other", subagent_type="builder"), "tool_use_id": "toolu_02"})
        self.assertEqual([line["id"] for line in self.lines()], ["toolu_01", "toolu_02"])


class AgentSettingsTest(AgentLogTest):
    def test_reads_the_tools_that_an_agent_file_lists_on_the_lines_after_the_field(self) -> None:
        (self.agents / "builder.md").write_text(
            "---\nname: builder\ntools:\n  - Read\n  - Edit\ndisallowedTools:\n  - WebFetch\nmaxTurns: 9\n---\n"
        )
        settings = agent_log.agent_settings("builder")
        self.assertEqual(
            (settings.tools, settings.disallowed_tools, settings.max_turns), ("Read, Edit", "WebFetch", "9")
        )


class EndTest(AgentLogTest):
    def setUp(self) -> None:
        super().setUp()
        self.transcript = self.root / "projects" / "p" / "abc123" / "subagents" / "agent-def456.jsonl"
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.worktree / "data.bin").write_bytes(b"x" * 300_000)
        self.meta({"agentType": "builder", "toolUseId": "toolu_01", "worktreePath": str(self.worktree)})

    def meta(self, fields: dict[str, Any]) -> None:
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self.transcript.with_suffix(".meta.json").write_text(json.dumps(fields))

    def stop(self, agent_type: str = "builder") -> dict[str, Any]:
        """What Claude Code sends the SubagentStop command when the agent ends."""
        return {
            "session_id": "abc123",
            "cwd": str(self.worktree),
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "agent_id": "def456",
            "agent_type": agent_type,
            "agent_transcript_path": str(self.transcript),
            "last_assistant_message": "Opened pull request #57.",
        }

    def task_stop(self, task_id: str, task_type: str = "local_agent") -> dict[str, Any]:
        """What Claude Code sends the PostToolUse command after a TaskStop call stopped a task."""
        message = f"Successfully stopped task: {task_id} (Build the issue)"
        return {
            "session_id": "abc123",
            "transcript_path": str(self.root / "projects" / "p" / "abc123.jsonl"),
            "cwd": str(self.worktree),
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskStop",
            "tool_input": {"task_id": task_id},
            "tool_response": {"message": message, "task_id": task_id, "task_type": task_type, "command": "Build"},
            "tool_use_id": "toolu_77",
        }

    def write_build(self) -> None:
        write_transcript(
            self.transcript,
            [
                prompt_line(0),
                call_line("a", 1, tokens=1000, tool_calls=2, output_tokens=30, request_id="req_a"),
                command_line("b", 5, "toolu_9", "gh pr create --fill"),
                result_line(6, "toolu_9", "https://github.com/owner/repo/pull/57\n"),
                call_line("c", 12, tokens=500, output_tokens=4, request_id="req_c"),
            ],
        )

    def test_gives_the_numbers_from_the_transcript_and_the_size_of_the_worktree(self) -> None:
        self.write_build()
        self.assertEqual(self.run_script("end", self.stop()), 0)
        [line] = self.lines()
        self.assert_time(line.pop("at"))
        worktree_mb = line.pop("worktree_mb")
        self.assertEqual(
            line,
            {
                "event": "end",
                "id": "toolu_01",
                "model": None,
                "effort": None,
                "minutes": 12.0,
                "periods": [["2026-10-11T14:00:00+00:00", "2026-10-11T14:12:00+00:00"]],
                "tool_calls": 3,
                "input_tokens": 1500,
                "output_tokens": 34,
                "requests": {"req_a": 30, "req_c": 4},
                "pull_request": 57,
            },
        )
        # The disk can give a file more space than its size, in blocks of a few kilobytes.
        self.assertGreaterEqual(worktree_mb, 0.3)
        self.assertLess(worktree_mb, 0.4)

    def test_takes_the_model_and_effort_that_the_file_next_to_the_transcript_holds(self) -> None:
        self.write_build()
        self.meta({"agentType": "general-purpose", "toolUseId": "toolu_01", "model": "opus", "effort": "xhigh"})
        self.run_script("end", self.stop(agent_type="general-purpose"))
        self.meta({"agentType": "general-purpose", "toolUseId": "toolu_02", "model": "haiku"})
        self.run_script("end", self.stop(agent_type="general-purpose"))
        self.assertEqual(
            [(line["id"], line["model"], line["effort"]) for line in self.lines()],
            [("toolu_01", "opus", "xhigh"), ("toolu_02", "haiku", None)],
        )

    def test_writes_no_line_for_an_agent_that_a_run_of_run_builder_started(self) -> None:
        self.write_build()
        with mock.patch.dict(os.environ, {"LOOP_RUN": "1"}):
            self.assertEqual(self.run_script("start", agent_call("12 other", subagent_type="builder")), 0)
            self.assertEqual(self.run_script("end", self.stop()), 0)
        self.assertEqual(self.lines(), [])

    def test_gives_no_size_for_an_agent_without_a_worktree_or_whose_worktree_is_gone(self) -> None:
        self.write_build()
        for fields in ({}, {"worktreePath": str(self.root / "gone")}):
            with self.subTest(fields=fields):
                self.meta({"agentType": "builder", "toolUseId": "toolu_01", **fields})
                self.run_script("end", self.stop())
                self.assertIsNone(self.lines()[-1]["worktree_mb"])

    def test_writes_no_line_for_an_agent_that_no_agent_call_started(self) -> None:
        self.write_build()
        self.meta({"agentType": "general-purpose", "name": "code-review"})
        self.run_script("end", self.stop())
        self.run_script("end", self.stop(agent_type=""))
        self.assertEqual(self.lines(), [])

    def test_gives_the_numbers_of_an_agent_that_task_stop_stopped(self) -> None:
        self.write_build()
        self.assertEqual(self.run_script("stop", self.task_stop("def456")), 0)
        [line] = self.lines()
        self.assertEqual(
            {name: line[name] for name in ("event", "id", "minutes", "tool_calls", "output_tokens", "pull_request")},
            {
                "event": "end",
                "id": "toolu_01",
                "minutes": 12.0,
                "tool_calls": 3,
                "output_tokens": 34,
                "pull_request": 57,
            },
        )
        self.assertGreaterEqual(line["worktree_mb"], 0.3)

    def test_writes_no_line_for_a_task_that_task_stop_stopped_and_that_is_not_an_agent_of_the_session(self) -> None:
        self.write_build()
        self.run_script("stop", self.task_stop("b7c1d2", task_type="local_bash"))
        self.run_script("stop", self.task_stop(""))
        # An id that leads to the agent's transcript through another folder
        (self.transcript.parent / "agent-x").mkdir()
        self.run_script("stop", self.task_stop("x/../agent-def456"))
        self.run_script("stop", self.task_stop("def456") | {"tool_response": "Stopped."})
        self.assertEqual(self.lines(), [])

    def test_fails_when_the_agent_has_no_file_next_to_its_transcript(self) -> None:
        self.transcript.with_suffix(".meta.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_script("end", self.stop())


class HookTest(AgentLogTest):
    """The hooks in .claude/settings.json, which run the script."""

    def run_command(self, command: str, hook_input: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", "-c", command],
            cwd=CHECKOUT,
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def settings_commands(self, event: str, matcher: str | None = None) -> list[str]:
        """The commands for an event in .claude/settings.json, or for the event's entries with the matcher."""
        settings = json.loads((CHECKOUT / ".claude" / "settings.json").read_text())
        entries = [entry for entry in settings["hooks"][event] if matcher is None or entry.get("matcher") == matcher]
        return [hook["command"] for entry in entries for hook in entry["hooks"]]

    def test_the_before_tool_hook_adds_the_start_line_of_an_agent_call_and_lets_it_go_ahead(self) -> None:
        [command] = self.settings_commands("PreToolUse")
        hook_input = agent_call("12 other", subagent_type="Explore", model="haiku", effort="low")
        done = self.run_command(command, hook_input)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        [line] = self.lines()
        self.assertEqual(
            {name: line[name] for name in ("event", "id", "type", "model", "effort", "agent_file", "issue", "label")},
            {
                "event": "start",
                "id": "toolu_01",
                "type": "Explore",
                "model": "haiku",
                "effort": "low",
                "agent_file": None,
                "issue": 12,
                "label": "bug",
            },
        )

    def test_the_before_tool_hook_lets_the_agent_call_go_ahead_when_the_log_fails(self) -> None:
        [command] = self.settings_commands("PreToolUse")
        (self.loop / "agents.jsonl").mkdir()
        done = self.run_command(command, agent_call("12 other", subagent_type="Explore"))
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_the_subagent_stop_command_adds_the_end_line(self) -> None:
        [command] = self.settings_commands("SubagentStop")
        transcript = write_transcript(self.root / "agent-def456.jsonl", [prompt_line(0), call_line("a", 4, tokens=10)])
        transcript.with_suffix(".meta.json").write_text(json.dumps({"agentType": "Explore", "toolUseId": "toolu_01"}))
        hook_input = {"cwd": str(CHECKOUT), "agent_type": "Explore", "agent_transcript_path": str(transcript)}
        done = self.run_command(command, hook_input)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        [line] = self.lines()
        self.assertEqual((line["id"], line["minutes"], line["input_tokens"]), ("toolu_01", 4.0, 10))

    def test_the_task_stop_command_adds_the_end_line(self) -> None:
        [command] = self.settings_commands("PostToolUse", "TaskStop")
        session = self.root / "abc123.jsonl"
        transcript = write_transcript(
            session.with_suffix("") / "subagents" / "agent-def456.jsonl", [prompt_line(0), call_line("a", 4, tokens=10)]
        )
        transcript.with_suffix(".meta.json").write_text(json.dumps({"agentType": "builder", "toolUseId": "toolu_01"}))
        hook_input = {
            "cwd": str(CHECKOUT),
            "transcript_path": str(session),
            "tool_name": "TaskStop",
            "tool_response": {"task_id": "def456", "task_type": "local_agent"},
        }
        done = self.run_command(command, hook_input)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        [line] = self.lines()
        self.assertEqual((line["event"], line["id"], line["minutes"]), ("end", "toolu_01", 4.0))

    def test_the_subagent_stop_command_lets_the_agent_end_when_the_script_fails(self) -> None:
        [command] = self.settings_commands("SubagentStop")
        done = self.run_command(command, {"cwd": str(CHECKOUT), "agent_type": "Explore"})
        self.assertEqual(done.returncode, 1)
        self.assertIn("KeyError", done.stderr)


class ScriptTest(AgentLogTest):
    def test_runs_as_a_script(self) -> None:
        done = subprocess.run(
            [sys.executable, str(AGENT_LOG), "start"],
            input=json.dumps(agent_call("Find the files.", subagent_type="Explore")),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(self.lines()[0]["type"], "Explore")
