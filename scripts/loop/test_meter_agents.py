import json
import os
import subprocess
import sys
import unittest
from typing import Any
from unittest import mock

import paths
from test_meter import INTERRUPTION, METER, MeterTest, meter
from test_transcripts import call_line, prompt_line, write_transcript


def call(tokens: int, **fields: Any) -> str:
    """A line of a transcript or of a builder's events: a call that read the given number of tokens. Its output tokens
    are far off, as in Claude Code's transcripts."""
    usage = {"input_tokens": 5, "cache_read_input_tokens": tokens - 1005, "cache_creation_input_tokens": 1000}
    return json.dumps({"type": "assistant", "message": {"usage": usage | {"output_tokens": 999_999}}} | fields)


class AgentsLineTest(MeterTest):
    def setUp(self) -> None:
        super().setUp()
        project = "".join(character if character.isalnum() else "-" for character in str(paths.main_checkout()))
        self.subagents = self.root / "claude-config" / "projects" / project / "abc123" / "subagents"
        self.subagents.mkdir(parents=True)

    def agents(self) -> str:
        return self.run_meter()[1][-2]

    def log(
        self,
        event: str,
        agent_id: str,
        agent_type: str = "builder",
        issue: int | None = 12,
        pid: int | None = None,
        is_test: bool = False,
    ) -> None:
        line: dict[str, Any] = {"at": "2026-10-18T02:00:00-04:00", "event": event, "id": agent_id}
        if event == "start":
            line |= {"type": agent_type, "issue": issue, "test": is_test, "pid": pid}
        else:
            line |= {"minutes": 1.0}
        with (self.loop / "agents.jsonl").open("a") as handle:
            handle.write(json.dumps(line) + "\n")

    def subagent(self, agent_id: str, tool_use_id: str, lines: list[str]) -> None:
        transcript = self.subagents / f"agent-{agent_id}.jsonl"
        transcript.write_text("\n".join(['{"type": "user", "message": {"content": "12 other"}}', *lines]) + "\n")
        meta = {"agentType": "builder", "toolUseId": tool_use_id, "spawnDepth": 1}
        transcript.with_suffix(".meta.json").write_text(json.dumps(meta))

    def builder_run(self, name: str, lines: list[str]) -> None:
        folder = self.loop / "runs" / name
        folder.mkdir(parents=True)
        (folder / "events.jsonl").write_text("\n".join(lines) + "\n")

    def hand_offs(self, *notes: str) -> None:
        (self.loop / "hand-offs").mkdir()
        for note in notes:
            (self.loop / "hand-offs" / note).touch()

    def test_gives_the_context_of_each_running_agent_and_the_threshold_of_its_hand_off_note(self) -> None:
        self.log("start", "toolu_01", issue=64)
        self.subagent("a1", "toolu_01", [call(300_000), call(412_000)])
        self.log("start", "toolu_02", "Explore", None)
        self.subagent("a2", "toolu_02", ['{"type": "user", "message": {"content": "next"}}'])
        self.log("start", "64-20261018-020000", issue=64)
        init = '{"type": "system", "subtype": "init", "session_id": "s64"}'
        of_subagent = call(30_000, parent_tool_use_id="toolu_09")
        result = '{"type": "result", "usage": {"input_tokens": 999}}'
        self.builder_run("64-20261018-020000", [init, call(520_000, parent_tool_use_id=None), of_subagent, result])
        self.log("start", "70-20261018-021000", "codex", 70)
        self.builder_run("70-20261018-021000", ['{"type": "thread.started", "thread_id": "t1"}'])
        self.log("start", "71-20261018-021000", issue=71)
        self.builder_run("71-20261018-021000", ["not an event", call(200_000, session_id="s71")])
        self.log("start", "72-20261018-021000", issue=72)
        self.hand_offs(
            *("a1.400000", "s64.400000", "s64.500000", "a9.400000", "t1.400000", "s71.400000", "notes.txt"),
            *("a1.room-now", "s64.room-soon", "a9.room-soon", "s71.room-soon"),
        )
        self.assertEqual(
            self.agents(),
            "agents | builder #64 412k, told at 400k to hand off, told to hand off for its account's room | "
            "Explore context unknown | "
            "builder #64 520k, told at 500k to hand off, told to hand off for its account's room | "
            "codex #70 context unknown | builder #71 200k | builder #72 context unknown",
        )

    def test_leaves_out_the_agents_with_an_end_line(self) -> None:
        self.log("start", "toolu_01")
        self.subagent("a1", "toolu_01", [call(300_000)])
        self.log("end", "toolu_01")
        self.log("start", "64-20261018-020000")
        self.builder_run("64-20261018-020000", [call(300_000)])
        self.log("end", "64-20261018-020000")
        self.assertEqual(self.agents(), "agents | none running")

    def test_leaves_out_an_agent_that_task_stop_stopped(self) -> None:
        self.log("start", "toolu_01", issue=64, pid=os.getpid())
        stopped = write_transcript(self.subagents / "agent-a1.jsonl", [prompt_line(0), call_line("a", 4, tokens=10)])
        stopped.with_suffix(".meta.json").write_text(json.dumps({"agentType": "builder", "toolUseId": "toolu_01"}))
        self.log("start", "toolu_02", issue=65, pid=os.getpid())
        self.subagent("a2", "toolu_02", [call(300_000)])
        settings = json.loads((METER.parents[2] / ".claude" / "settings.json").read_text())
        [command] = [
            hook["command"]
            for entry in settings["hooks"]["PostToolUse"]
            if entry.get("matcher") == "TaskStop"
            for hook in entry["hooks"]
        ]
        # What Claude Code sends the command after a TaskStop call stopped the agent a1 of the session abc123
        hook_input = {
            "cwd": str(METER.parents[2]),
            "transcript_path": str(self.subagents.parent.with_suffix(".jsonl")),
            "tool_name": "TaskStop",
            "tool_input": {"task_id": "a1"},
            "tool_response": {"task_id": "a1", "task_type": "local_agent"},
        }
        # scripts/loop/run-builder sets LOOP_RUN for a builder that runs this test, and with it the command writes
        # nothing.
        environment = {name: value for name, value in os.environ.items() if name != "LOOP_RUN"}
        stop = subprocess.run(
            ["sh", "-c", command],
            cwd=METER.parents[2],
            env=environment,
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual((stop.returncode, stop.stderr), (0, ""))
        self.assertEqual(self.agents(), "agents | builder #65 300k")

    def test_says_when_no_agent_runs_without_a_log(self) -> None:
        self.assertEqual(self.agents(), "agents | none running")

    def test_leaves_out_an_agent_whose_process_ended_and_lists_one_whose_process_runs_or_is_unknown(self) -> None:
        self.log("start", "toolu_01", issue=64, pid=101)
        self.log("start", "toolu_02", issue=65, pid=102)
        self.log("start", "64-20261018-020000", issue=64, pid=103)
        self.log("start", "65-20261018-020000", issue=65, pid=104)
        self.log("start", "toolu_03", issue=66)
        with mock.patch.object(meter, "process_runs", lambda pid: pid in (102, 104)):
            self.assertEqual(
                self.agents(),
                "agents | builder #65 context unknown | builder #65 context unknown | builder #66 context unknown",
            )

    def test_leaves_out_an_agent_that_the_user_interrupted_and_lists_it_again_once_it_gets_a_message(self) -> None:
        self.log("start", "toolu_01", issue=64, pid=os.getpid())
        self.subagent("a1", "toolu_01", [call(300_000), INTERRUPTION])
        self.log("start", "toolu_02", issue=65, pid=os.getpid())
        message = '{"type": "user", "message": {"content": "Go on."}}'
        self.subagent("a2", "toolu_02", [call(300_000), INTERRUPTION, message, call(310_000)])
        self.assertEqual(self.agents(), "agents | builder #65 310k")

    def limit_parts(self) -> list[str]:
        """The parts of the machine line that name the limit on the agents that build, at a limit of 4, while a
        reviewer, a researcher and an auditor run too."""
        for agent_id, agent_type in (("toolu_11", "reviewer"), ("toolu_12", "researcher"), ("toolu_13", "auditor")):
            self.log("start", agent_id, agent_type)
        with mock.patch.object(meter, "MOST_BUILDING_AGENTS", 4):
            machine = self.run_meter()[1][-1]
        self.assertTrue(machine.startswith("machine | "))
        return [part for part in machine.split(" | ") if "limit" in part]

    def test_names_the_limit_once_the_agents_that_build_test_builds_included_reach_it(self) -> None:
        self.log("start", "toolu_01", "builder-small")
        self.log("start", "70-20261018-021000", "codex", 70)
        self.log("start", "test-12-builder-deep-max-20261018-021000", "builder-deep-max", is_test=True)
        self.log("start", "test-12-fixer-20261018-021000", "fixer", is_test=True)
        self.assertEqual(self.limit_parts(), ["4 agents build, and the limit is 4: start no builder or test build"])

    def test_names_no_limit_while_fewer_agents_build(self) -> None:
        self.log("start", "toolu_01", "builder-deep")
        self.log("start", "test-12-builder-cheaper-20261018-021000", "builder-cheaper", is_test=True)
        self.log("start", "test-13-builder-cheaper-20261018-021000", "builder-cheaper", 13, is_test=True)
        self.log("start", "toolu_02", "builder")
        self.log("end", "toolu_02")
        self.assertEqual(self.limit_parts(), [])


class ProcessTest(unittest.TestCase):
    def test_says_that_a_process_runs(self) -> None:
        self.assertTrue(meter.process_runs(os.getpid()))

    def test_says_that_a_process_that_ended_does_not_run(self) -> None:
        ended = subprocess.Popen([sys.executable, "-S", "-c", ""])
        ended.wait()
        self.assertFalse(meter.process_runs(ended.pid))

    def test_says_that_a_process_of_another_user_runs(self) -> None:
        # The system's first process belongs to root, so a signal to it fails for any other user.
        self.assertTrue(meter.process_runs(1))
