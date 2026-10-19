import contextlib
import datetime as dt
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

import heavy_log
import paths
from test_agent_time import message_line
from test_transcripts import START, at, call_line, command_line, prompt_line, result_line, typed_line

REPORT = Path(__file__).with_name("report")
# The report's file name has no .py, so the tests load it by its path.
report = types.ModuleType("report")
importlib.machinery.SourceFileLoader("report", str(REPORT)).exec_module(report)

SINCE = START
# A line of requests.jsonl
REQUEST = {
    "session_id": "s1",
    "request_id": "req_a",
    "model": "claude-opus-5-5",
    "input_tokens": 2,
    "output_tokens": 1,
    "cache_read_tokens": 100,
    "cache_creation_tokens": 10,
}

# A stand-in for gh. It writes each call's arguments to calls.jsonl in STANDIN_DIR. For a list of runs it prints the
# lines under "runs" in gh.json. For a GraphQL query of runs it gives the jobs under "jobs" for each id that the query
# names, and no node for an id that "jobs" doesn't hold. For a query of pull requests it gives the facts under
# "pull_requests" for each number that the query names.
# An answer under "fail" makes each call print it to stderr and exit with code 1. -S keeps coverage, which starts in
# each Python process of the tests, out of the stand-in.
STANDIN = f"""#!{sys.executable} -S
import json, os, re, sys
folder = os.environ["STANDIN_DIR"]
with open(os.path.join(folder, "calls.jsonl"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
answers = json.load(open(os.path.join(folder, "gh.json")))
if "fail" in answers:
    sys.stderr.write(answers["fail"])
    sys.exit(1)
query = next((argument for argument in sys.argv if argument.startswith("query=")), "")
if "pullRequest(" in query:
    numbers = re.findall(r"pr([0-9]+): pullRequest", query)
    facts = {{"pr" + number: answers["pull_requests"][number] for number in numbers}}
    print(json.dumps({{"data": {{"repository": facts}}}}))
elif sys.argv[2] == "graphql":
    ids = [argument.removeprefix("ids[]=") for argument in sys.argv if argument.startswith("ids[]=")]
    jobs = answers["jobs"]
    nodes = [{{"checkSuite": {{"checkRuns": {{"nodes": jobs[id]}}}}}} if id in jobs else None for id in ids]
    print(json.dumps({{"data": {{"nodes": nodes}}}}))
else:
    print("".join(json.dumps(run) + "\\n" for run in answers["runs"]), end="")
"""


def job(name: str, minutes: float, conclusion: str = "SUCCESS") -> dict[str, str]:
    return {"name": name, "startedAt": at(0), "completedAt": at(minutes), "conclusion": conclusion}


class ReportTest(unittest.TestCase):
    """A test with a loop folder, a folder for Claude Code's transcripts, and a stand-in for gh first on PATH."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.loop = self.root / "loop"
        self.standins = self.root / "standins"
        bin_folder = self.root / "bin"
        for created in (self.loop, self.standins, bin_folder):
            created.mkdir()
        (bin_folder / "gh").write_text(STANDIN)
        (bin_folder / "gh").chmod(0o755)
        environment = {
            "PATH": f"{bin_folder}{os.pathsep}{os.environ['PATH']}",
            "STANDIN_DIR": str(self.standins),
            "LOOP_DIR": str(self.loop),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude-config"),
        }
        patched = mock.patch.dict(os.environ, environment)
        patched.start()
        self.addCleanup(patched.stop)
        self.project = paths.claude_project_folder(paths.main_checkout())
        self.project.mkdir(parents=True)
        self.runs: list[dict[str, str]] = []
        self.jobs: dict[str, list[dict[str, str]]] = {}
        self.pull_requests: dict[str, dict[str, Any]] = {}
        self.answer()

    def answer(self) -> None:
        answers = {"runs": self.runs, "jobs": self.jobs, "pull_requests": self.pull_requests}
        (self.standins / "gh.json").write_text(json.dumps(answers))

    def add_pull_request(
        self,
        number: int,
        commit_minutes: tuple[float, ...] = (1,),
        conclusions: tuple[str | None, ...] = (),
        removals: tuple[str, ...] = (),
    ) -> None:
        """Gives GitHub's facts about a pull request: its commits, each with check suites of the conclusions, and the
        reason of each time that the merge queue removed it."""
        suites = {"nodes": [{"conclusion": conclusion} for conclusion in conclusions]}
        commits = [{"commit": {"committedDate": at(minute), "checkSuites": suites}} for minute in commit_minutes]
        self.pull_requests[str(number)] = {
            "commits": {"nodes": commits},
            "timelineItems": {"nodes": [{"reason": reason} for reason in removals]},
        }
        self.answer()

    def log_agents(self, *lines: dict[str, Any]) -> None:
        (self.loop / "agents.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))

    def log_requests(self, *requests: tuple[str, int]) -> None:
        """Writes requests.jsonl, with the output tokens of each request id."""
        lines = [{**REQUEST, "request_id": request_id, "output_tokens": tokens} for request_id, tokens in requests]
        (self.loop / "requests.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))

    def add_run(self, minutes: float, *jobs: dict[str, str]) -> None:
        node = f"run-{len(self.runs)}"
        self.runs.append({"node_id": node, "run_started_at": at(0), "updated_at": at(minutes)})
        self.jobs[node] = list(jobs)
        self.answer()

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in (self.standins / "calls.jsonl").read_text().splitlines()]

    def transcript(
        self, name: str, lines: list[Any], session: str | None = None, agent_type: str | None = None
    ) -> Path:
        """Writes a session's transcript, or with a session, the transcript of one of its agents."""
        folder = self.project if session is None else self.project / session / "subagents"
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / f"{name}.jsonl"
        file.write_text("".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines))
        if agent_type is not None:
            file.with_suffix(".meta.json").write_text(json.dumps({"agentType": agent_type}))
        return file

    def agent_transcript(self, agent_id: str, lines: list[Any]) -> Path:
        """Writes the transcript of the agent that the Agent call with the id started."""
        transcript = self.transcript(f"agent-{agent_id}", lines, "session")
        transcript.with_suffix(".meta.json").write_text(json.dumps({"toolUseId": agent_id}))
        return transcript

    def run_report(self, *arguments: str) -> tuple[int, list[str], str]:
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            try:
                status = report.main(list(arguments or ["--since", SINCE.isoformat()]))
            except SystemExit as stopped:
                status = int(stopped.code or 0)
        return status, printed.getvalue().splitlines(), errors.getvalue()

    def line(self, start: str) -> str:
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        return next(line for line in lines if line.startswith(start))


class EmptyPeriodTest(ReportTest):
    def test_counts_nothing_in_a_period_with_no_merged_pull_requests(self) -> None:
        self.transcript("old", [call_line("a", -5, tokens=100, tool_calls=2)])
        self.assertEqual(
            self.run_report(),
            (
                0,
                [
                    "Since 2026-10-11T14:00:00+00:00",
                    "CI runs that passed: 0",
                    "Types: no agent started",
                    "Requests of the agents of a session that have no event: 0 of 0",
                    "Orchestrator's share of the input tokens: none used",
                    "Hook blocks: 0",
                    "Agents' time: 0.0 min, from the transcripts of 0 agents, and 0 agents have none",
                    "Context of the orchestrator: no calls",
                    "Heavy commands: none ran",
                    "Machine: no readings",
                ],
                "",
            ),
        )


class CiTest(ReportTest):
    def test_gives_the_number_of_runs_that_passed_and_their_median_time(self) -> None:
        for minutes in (2, 3, 10):
            self.add_run(minutes)
        self.assertEqual(self.line("CI runs"), "CI runs that passed: 3, median 3.0 min")

    def test_gives_each_jobs_median_time_without_the_runs_that_skipped_it(self) -> None:
        self.add_run(5, job("tests", 1.5), job("lint", 0.5))
        self.add_run(5, job("tests", 2.5), job("lint", 0.25))
        self.add_run(5, job("tests", 0, "SKIPPED"), job("lint", 0.75))
        self.assertEqual(self.line("CI jobs"), "CI jobs, median minutes: lint 0.5 | tests 2.0")

    def test_asks_for_the_runs_of_the_ci_workflow_that_passed_since_the_time(self) -> None:
        self.run_report("--since", "2026-10-11T16:00:00+02:00")
        listing = self.calls()[0]
        self.assertEqual(listing[:2], ["api", "repos/{owner}/{repo}/actions/workflows/ci.yml/runs"])
        self.assertIn("status=success", listing)
        self.assertIn("created=>=2026-10-11T14:00:00Z", listing)

    def test_asks_for_the_jobs_of_100_runs_at_a_time(self) -> None:
        for _ in range(101):
            self.add_run(1, job("tests", 1))
        self.assertEqual(self.line("CI runs"), "CI runs that passed: 101, median 1.0 min")
        queries = [call for call in self.calls() if call[1] == "graphql"]
        self.assertEqual([sum(argument.startswith("ids[]=") for argument in query) for query in queries], [100, 1])

    def test_skips_a_run_that_was_deleted_after_the_listing(self) -> None:
        self.add_run(2, job("tests", 1))
        self.add_run(3, job("tests", 1))
        del self.jobs["run-1"]
        self.answer()
        self.assertEqual(self.line("CI runs"), "CI runs that passed: 1, median 2.0 min")

    def test_fails_with_what_gh_printed_when_gh_fails(self) -> None:
        (self.standins / "gh.json").write_text(json.dumps({"fail": "HTTP 401: Bad credentials\n"}))
        error = (
            "error: gh api repos/{owner}/{repo}/actions/workflows/ci.yml/runs failed: HTTP 401: Bad credentials. "
            "Check the network, and that gh auth status shows a login.\n"
        )
        self.assertEqual(self.run_report(), (1, [], error))


def start(
    agent_id: str,
    minute: float,
    agent_type: str = "builder",
    issue: int | None = None,
    kind: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """A start line of agents.jsonl."""
    return {
        "at": at(minute),
        "event": "start",
        "id": agent_id,
        "type": agent_type,
        "model": "opus",
        "effort": "high",
        "agent_file": None,
        "issue": issue,
        "label": label,
        "kind": kind,
        "test": False,
    }


def end(
    agent_id: str,
    minute: float,
    minutes: float = 1,
    tool_calls: int = 1,
    tokens: int = 1,
    output_tokens: int = 1,
    requests: dict[str, int] | None = None,
    pull_request: int | None = None,
) -> dict[str, Any]:
    """An end line of agents.jsonl, with the tokens as its input tokens. Without requests, it's the line of a run of
    scripts/loop/run-builder. With them, it's the line of an agent of a session, whose output tokens are the sum of
    the requests' counts in its transcript."""
    numbers = {
        "minutes": minutes,
        "tool_calls": tool_calls,
        "input_tokens": tokens,
        "output_tokens": output_tokens if requests is None else sum(requests.values()),
        "requests": requests,
        "pull_request": pull_request,
    }
    return {
        "at": at(minute),
        "event": "end",
        "id": agent_id,
        "model": None,
        "effort": None,
        **numbers,
        "worktree_mb": 1.5,
    }


class TypeTest(ReportTest):
    def test_gives_how_many_of_each_type_started_and_ended_and_the_medians_of_those_that_ended(self) -> None:
        self.log_agents(
            start("a", 1),
            start("b", 2),
            start("c", 3),
            start("d", 4),
            start("r", 5, agent_type="reviewer"),
            end("a", 11, minutes=10, tool_calls=4, tokens=1000),
            end("b", 22, minutes=20, tool_calls=6, tokens=2000),
            end("c", 63, minutes=60, tool_calls=30, tokens=3000),
            end("r", 6, minutes=1, tool_calls=99, tokens=2000),
        )
        self.add_pull_request(57)
        status, lines, errors = self.run_report()
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            [line for line in lines if line.startswith("Type")],
            [
                (
                    "Type builder: 4 started, 3 ended, median 20.0 min and 6 tool calls, 75% of the agents' input "
                    "tokens | output tokens: 3 | pull requests: 0"
                ),
                (
                    "Type reviewer: 1 started, 1 ended, median 1.0 min and 99 tool calls, 25% of the agents' input "
                    "tokens | output tokens: 1 | pull requests: 0"
                ),
            ],
        )

    def test_takes_the_output_tokens_of_each_request_of_an_agent_of_a_session_from_its_event(self) -> None:
        self.log_agents(
            start("toolu_1", 1),
            end("toolu_1", 10, requests={"req_a": 4, "req_b": 9}),
            start("toolu_2", 2),
            end("toolu_2", 10, requests={"req_c": 0}),
        )
        self.log_requests(("req_a", 1200), ("req_b", 300), ("req_c", 45), ("req_other", 7000))
        self.assertEqual(self.line("Type").split(" | ")[1], "output tokens: 1545")
        self.assertEqual(self.line("Requests"), "Requests of the agents of a session that have no event: 0 of 3")

    def test_takes_the_transcripts_count_for_a_request_with_no_event_and_counts_those_requests(self) -> None:
        self.log_agents(
            start("toolu_1", 1),
            end("toolu_1", 10, requests={"req_a": 4, "req_b": 9}),
            start("toolu_2", 2),
            end("toolu_2", 10, requests={"req_c": 6}),
        )
        self.log_requests(("req_a", 1200))
        self.assertEqual(self.line("Type").split(" | ")[1], "output tokens: 1215")
        self.assertEqual(self.line("Requests"), "Requests of the agents of a session that have no event: 2 of 3")

    def test_takes_the_output_tokens_of_a_run_of_run_builder_from_its_end_line(self) -> None:
        self.log_agents(
            start("run-1", 1),
            end("run-1", 10, output_tokens=9000),
            start("toolu_1", 2),
            end("toolu_1", 10, requests={"req_a": 4}),
        )
        self.log_requests(("req_a", 1200))
        self.assertEqual(self.line("Type").split(" | ")[1], "output tokens: 10200")
        self.assertEqual(self.line("Requests"), "Requests of the agents of a session that have no event: 0 of 1")

    def test_takes_the_output_tokens_of_an_end_line_written_before_end_lines_had_requests_from_the_line(self) -> None:
        old_end = {key: value for key, value in end("toolu_1", 10, output_tokens=3).items() if key != "requests"}
        self.log_agents(start("toolu_1", 1), {**old_end, "output_tokens_exact": False})
        self.log_requests(("req_a", 1200))
        self.assertEqual(self.line("Type").split(" | ")[1], "output tokens: 3")
        self.assertEqual(self.line("Requests"), "Requests of the agents of a session that have no event: 0 of 0")

    def test_counts_only_the_agents_that_started_after_the_time(self) -> None:
        self.log_agents(start("old", -10), end("old", 5, tokens=5000), start("new", 1), end("new", 6, tokens=1000))
        self.assertEqual(
            self.line("Type"),
            "Type builder: 1 started, 1 ended, median 1.0 min and 1 tool calls, 100% of the agents' input tokens "
            "| output tokens: 1 | pull requests: 0",
        )

    def test_leaves_out_the_test_builds_of_a_retrospective(self) -> None:
        test_build = {**start("t", 1, issue=12, kind="other"), "test": True}
        self.log_agents(test_build, end("t", 9, pull_request=57), start("ra", 20, agent_type="reviewer", issue=57))
        status, lines, errors = self.run_report("--since", SINCE.isoformat(), "--issues")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            [line for line in lines if line.startswith(("Type", "Issue"))],
            ["Type reviewer: 1 started, 0 ended | pull requests: 0"],
        )

    def test_counts_the_last_end_of_an_agent_that_ended_again_after_a_message(self) -> None:
        self.log_agents(start("a", 1), end("a", 10, minutes=9, tool_calls=5), end("a", 30, minutes=15, tool_calls=8))
        self.assertIn("1 started, 1 ended, median 15.0 min and 8 tool calls", self.line("Type"))

    def test_gives_a_type_whose_agents_have_not_ended_and_one_whose_agents_used_no_tokens(self) -> None:
        self.log_agents(start("a", 1), start("x", 2, agent_type="Explore"), end("x", 3, tokens=0))
        status, lines, errors = self.run_report()
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            [line for line in lines if line.startswith("Type")],
            [
                (
                    "Type Explore: 1 started, 1 ended, median 1.0 min and 1 tool calls, 0% of the agents' input "
                    "tokens | output tokens: 1 | pull requests: 0"
                ),
                "Type builder: 1 started, 0 ended | pull requests: 0",
            ],
        )

    def test_counts_what_happened_to_the_pull_requests_that_the_agents_of_a_type_opened(self) -> None:
        self.log_agents(
            # #57 got a commit after a reviewer started on it.
            start("a", 0, issue=12, kind="other"),
            end("a", 9, pull_request=57),
            start("ra", 20, agent_type="reviewer", issue=57),
            # #58 got its commits before its reviewer started, failed a check, and left the merge queue unmerged.
            start("b", 0, issue=13, kind="other"),
            end("b", 9, pull_request=58),
            start("rb", 20, agent_type="reviewer", issue=58),
            # #59 got a second builder on its issue, and #60 one on the pull request.
            start("c", 0, issue=14, kind="other"),
            end("c", 9, pull_request=59),
            start("c2", 40, issue=14, kind="other"),
            start("d", 0, issue=15, kind="other"),
            end("d", 9, pull_request=60),
            start("rd", 20, agent_type="reviewer", issue=60),
            start("d2", 40, agent_type="builder-deep", issue=60),
            # A builder of the issue that started before the one that opened the pull request isn't a second one.
            start("e1", 0, issue=16, kind="other"),
            start("e", 5, issue=16, kind="other"),
            end("e", 9, pull_request=61),
        )
        self.add_pull_request(57, commit_minutes=(8, 30))
        self.add_pull_request(
            58, commit_minutes=(8, 10), conclusions=("CANCELLED", "FAILURE", None), removals=("merge_conflict",)
        )
        self.add_pull_request(59, commit_minutes=(8, 50), conclusions=("CANCELLED", "SUCCESS"))
        self.add_pull_request(60, commit_minutes=(8, 50), conclusions=("TIMED_OUT",))
        self.add_pull_request(61, conclusions=("STARTUP_FAILURE",), removals=("merge_conflict", "merged"))
        self.assertEqual(
            self.line("Type builder:").split(" | ")[-1],
            "pull requests: 5, 2 needed a fix after review, 3 failed a check, 2 left the merge queue unmerged, "
            "2 got a second builder",
        )

    def test_does_not_count_the_removal_from_the_merge_queue_that_github_records_when_the_queue_merges(self) -> None:
        self.log_agents(start("a", 0, issue=12, kind="other"), end("a", 9, pull_request=57))
        self.add_pull_request(57, removals=("merged",))
        self.assertEqual(
            self.line("Type builder:").split(" | ")[-1],
            "pull requests: 1, 0 needed a fix after review, 0 failed a check, 0 left the merge queue unmerged, "
            "0 got a second builder",
        )

    def test_asks_for_the_facts_of_25_pull_requests_at_a_time(self) -> None:
        lines = []
        for number in range(1, 27):
            lines += [start(f"a{number}", 1), end(f"a{number}", 2, pull_request=number)]
            self.add_pull_request(number)
        self.log_agents(*lines)
        self.assertIn("| pull requests: 26, 0 needed a fix after review", self.line("Type"))
        queries = [call for call in self.calls() if any("pullRequest(" in argument for argument in call)]
        self.assertEqual([sum(argument.count("pullRequest(") for argument in query) for query in queries], [25, 1])
        self.assertIn("owner={owner}", queries[0])

    def test_lists_each_agent_that_built_an_issue_with_the_issues_option(self) -> None:
        self.log_agents(
            start("a", 0, issue=12, kind="other", label="feature"),
            end("a", 9, minutes=8.5, tool_calls=40, tokens=1200, requests={"req_a": 5, "req_b": 3}, pull_request=57),
            start("ra", 20, agent_type="reviewer", issue=57),
            start("b", 1, agent_type="builder-small", issue=13, kind="left behind 31", label="bug"),
            end("b", 9, minutes=3, tool_calls=10, tokens=300, output_tokens=200, pull_request=58),
            start("c", 2, issue=14, kind="other", label="maintenance"),
            end("c", 9, minutes=4, tool_calls=11, tokens=400, output_tokens=50),
            start("d", 3, issue=15, kind="other", label="feature"),
            start("research", 4, agent_type="researcher", issue=16, kind="research", label="research"),
            end("research", 9),
        )
        self.add_pull_request(57, commit_minutes=(8, 30), conclusions=("FAILURE",))
        self.add_pull_request(58)
        self.log_requests(("req_a", 800))
        status, lines, errors = self.run_report("--since", SINCE.isoformat(), "--issues")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            [line for line in lines if line.startswith("Issue")],
            [
                (
                    "Issue #12 feature other: builder, 8.5 min, 40 tool calls, 1200 input tokens, 803 output tokens, "
                    "1 of its requests with no event | pull request #57: needed a fix after review, failed a check"
                ),
                (
                    "Issue #13 bug left behind 31: builder-small, 3.0 min, 10 tool calls, 300 input tokens, 200 output "
                    "tokens | pull request #58: no fix after review, failed check, unmerged exit from the merge queue "
                    "or second builder"
                ),
                (
                    "Issue #14 maintenance other: builder, 4.0 min, 11 tool calls, 400 input tokens, 50 output tokens "
                    "| no pull request"
                ),
                "Issue #15 feature other: builder, not ended",
            ],
        )

    def test_fails_with_what_gh_printed_when_gh_cannot_give_a_pull_request(self) -> None:
        self.log_agents(start("a", 1), end("a", 2, pull_request=45))
        (self.standins / "gh.json").write_text(json.dumps({"fail": "Could not resolve to a PullRequest\n"}))
        status, lines, errors = self.run_report()
        self.assertEqual((status, lines), (1, []))
        self.assertIn("Could not resolve to a PullRequest", errors)


class AgentTimeTest(ReportTest):
    def test_totals_each_part_of_the_time_of_the_agents_with_a_transcript_and_counts_those_without(self) -> None:
        self.log_agents(
            start("a", 0, issue=12, kind="other", label="feature"),
            end("a", 30, minutes=12, pull_request=57),
            start("b", 1),
            start("c", 2),
            start("gone", 3),
            {**start("test", 4), "test": True},
            start("old", -10),
        )
        self.add_pull_request(57)
        self.agent_transcript(
            "a",
            [
                prompt_line(0),
                command_line("x", 2, "toolu_1", "scripts/check"),
                result_line(8, "toolu_1", "passed"),
                call_line("y", 10),
                message_line(28),
                call_line("z", 30),
            ],
        )
        self.agent_transcript(
            "b", [prompt_line(1), command_line("x", 3, "toolu_2", "sleep 120"), result_line(5, "toolu_2", "")]
        )
        self.agent_transcript("gone", [prompt_line(3)]).unlink()
        self.agent_transcript("test", [prompt_line(4), call_line("x", 50)])
        self.agent_transcript("old", [prompt_line(-10), call_line("x", 50)])
        status, lines, errors = self.run_report("--since", SINCE.isoformat(), "--issues")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            next(line for line in lines if line.startswith("Agents' time")),
            (
                "Agents' time: 34.0 min, from the transcripts of 2 agents, and 2 agents have none | model 8.0 min "
                "(24%) | scripts/check 6.0 min (18%) | single test files 0.0 min (0%) | waiting for CI 0.0 min (0%) "
                "| sleeps 2.0 min (6%) | other commands 0.0 min (0%) | other tools 0.0 min (0%) | waiting for "
                "background tasks 0.0 min (0%) | waiting for the orchestrator 18.0 min (53%)"
            ),
        )
        self.assertEqual(
            next(line for line in lines if line.startswith("Issue")),
            (
                "Issue #12 feature other: builder, 12.0 min, 1 tool calls, 1 input tokens, 1 output tokens | pull "
                "request #57: no fix after review, failed check, unmerged exit from the merge queue or second builder "
                "| context: peak 0.0k, no tool call measured | time: model 6.0 min, scripts/check 6.0 min, waiting for "
                "the orchestrator 18.0 min"
            ),
        )

    def test_gives_the_split_of_an_agent_that_has_not_ended_or_opened_no_pull_request(self) -> None:
        self.log_agents(
            start("a", 0, issue=12, kind="other", label="feature"),
            start("b", 0, issue=13, kind="other", label="bug"),
            end("b", 2, minutes=2),
        )
        self.agent_transcript("a", [prompt_line(0), call_line("x", 1)])
        self.agent_transcript("b", [prompt_line(0), call_line("x", 2)])
        status, lines, errors = self.run_report("--since", SINCE.isoformat(), "--issues")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(
            [line for line in lines if line.startswith("Issue")],
            [
                "Issue #12 feature other: builder, not ended | context: peak 0.0k, no tool call measured | time: model 1.0 min",
                (
                    "Issue #13 bug other: builder, 2.0 min, 1 tool calls, 1 input tokens, 1 output tokens | no pull "
                    "request | context: peak 0.0k, no tool call measured | time: model 2.0 min"
                ),
            ],
        )


class OrchestratorTest(ReportTest):
    def test_gives_the_share_of_the_tokens_that_the_sessions_used(self) -> None:
        self.transcript("session", [call_line("s", 1, tokens=1000)])
        self.transcript("builder", [call_line("b", 1, tokens=2000)], session="session", agent_type="builder")
        self.transcript("reviewer", [call_line("r", 1, tokens=1000)], session="session", agent_type="reviewer")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the input tokens: 25%")

    def test_counts_every_kind_of_input_token_and_no_output_tokens(self) -> None:
        usage = {"input_tokens": 1, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 100}
        session = {
            "type": "assistant",
            "timestamp": at(1),
            "message": {"id": "s", "usage": {**usage, "output_tokens": 5000}, "content": "text"},
        }
        self.transcript("session", [session])
        self.transcript("builder", [call_line("b", 1, tokens=889)], session="session", agent_type="builder")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the input tokens: 11%")

    def test_counts_the_tokens_of_a_response_on_several_lines_once(self) -> None:
        self.transcript("session", [call_line("s", 1, tokens=1000), call_line("s", 2, tokens=1000)])
        self.transcript("builder", [call_line("b", 1, tokens=1000)], session="session", agent_type="builder")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the input tokens: 50%")

    def test_counts_a_call_that_a_resumed_session_copied_once(self) -> None:
        earlier = self.transcript("earlier", [call_line("s", 1, tokens=1000)])
        modified = (SINCE + dt.timedelta(minutes=5)).timestamp()
        os.utime(earlier, (modified, modified))
        self.transcript("resumed", [call_line("s", 1, tokens=1000), call_line("t", 6, tokens=1000)])
        self.transcript("builder", [call_line("b", 1, tokens=2000)], session="resumed", agent_type="builder")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the input tokens: 50%")

    def test_counts_only_the_calls_that_started_after_the_time(self) -> None:
        self.transcript("session", [call_line("before", -30, tokens=5000), call_line("after", 1, tokens=1000)])
        self.transcript("builder", [call_line("b", 1, tokens=1000)], session="session", agent_type="builder")
        old = self.transcript("old", [call_line("old", 1, tokens=7000)], session="session", agent_type="builder")
        modified = (SINCE - dt.timedelta(minutes=1)).timestamp()
        os.utime(old, (modified, modified))
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the input tokens: 50%")

    def test_skips_the_lines_that_hold_no_call(self) -> None:
        lines = [
            {"type": "user", "timestamp": at(1), "message": {"id": "u", "usage": {"input_tokens": 500}}},
            {"type": "assistant", "timestamp": at(1), "message": "text"},
            {"type": "assistant", "timestamp": at(1), "message": {"usage": {"input_tokens": 500}}},
            '{"type": "assistant", "message": {"id": "half written',
            call_line("s", 1, tokens=100),
        ]
        self.transcript("session", lines)
        self.transcript("builder", [call_line("b", 1, tokens=100)], session="session", agent_type="builder")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the input tokens: 50%")


class MessageTest(ReportTest):
    def messages(self) -> list[str]:
        status, lines, errors = self.run_report("--since", SINCE.isoformat(), "--messages")
        self.assertEqual(status, 0, errors)
        return lines[lines.index(next(line for line in lines if line.startswith("Messages"))) :]

    def test_gives_the_messages_that_a_person_typed_in_each_session_in_order_of_time(self) -> None:
        self.transcript("first", [typed_line(5, "Stop guessing the times.\nRead the clock."), prompt_line(6, "no")])
        self.transcript("second", [typed_line(2, "Start the loop.")])
        self.assertEqual(
            self.messages(),
            [
                "Messages that a person typed: 2",
                "Message at 2026-10-11T14:02:00+00:00:",
                "  Start the loop.",
                "Message at 2026-10-11T14:05:00+00:00:",
                "  Stop guessing the times.",
                "  Read the clock.",
            ],
        )

    def test_gives_a_message_that_a_resumed_session_copied_once(self) -> None:
        earlier = self.transcript("earlier", [typed_line(1, "Start the loop.")])
        modified = (SINCE + dt.timedelta(minutes=5)).timestamp()
        os.utime(earlier, (modified, modified))
        self.transcript("resumed", [typed_line(1, "Start the loop."), typed_line(6, "Continue.")])
        self.assertEqual(self.messages()[0], "Messages that a person typed: 2")

    def test_gives_only_the_messages_of_the_sessions_after_the_time(self) -> None:
        self.transcript("session", [typed_line(-5, "Before."), typed_line(1, "After.")])
        old = self.transcript("old", [typed_line(1, "In an old transcript.")])
        modified = (SINCE - dt.timedelta(minutes=1)).timestamp()
        os.utime(old, (modified, modified))
        self.transcript("builder", [typed_line(2, "To an agent.")], session="session", agent_type="builder")
        self.assertEqual(
            self.messages(), ["Messages that a person typed: 1", "Message at 2026-10-11T14:01:00+00:00:", "  After."]
        )

    def test_gives_no_messages_without_the_messages_option(self) -> None:
        self.transcript("session", [typed_line(1, "Start the loop.")])
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        self.assertFalse([line for line in lines if "Start the loop." in line or line.startswith("Message")])


class TranscriptFolderTest(ReportTest):
    def test_stops_when_the_folder_of_the_transcripts_is_missing(self) -> None:
        self.project.rmdir()
        status, lines, errors = self.run_report()
        self.assertEqual((status, lines), (1, []))
        self.assertEqual(
            errors,
            f"error: Can't find {self.project}, where Claude Code keeps the transcripts of the sessions in "
            f"{paths.main_checkout()}. If Claude Code keeps its files elsewhere, set CLAUDE_CONFIG_DIR to that "
            "folder.\n",
        )


class BlockTest(ReportTest):
    def test_counts_the_blocks_after_the_time_by_rule(self) -> None:
        log = [
            f"{at(-1)} forbidden-commands.check git push --force",
            f"{at(0)} secret-files.check cat .env",
            f"{at(1)} forbidden-commands.check git reset --hard",
            f"{at(2)} forbidden-commands.check rm -rf /",
            f"{at(3)} posted-text.check",
        ]
        (self.loop / "blocks.log").write_text("\n".join(log) + "\n")
        self.assertEqual(
            self.line("Hook blocks"),
            "Hook blocks: 4 | forbidden-commands.check 2 | posted-text.check 1 | secret-files.check 1",
        )


def heavy_entry(minute: float, event: str, command: str = "scripts/check", **fields: Any) -> dict[str, Any]:
    return {"at": at(minute), "event": event, "id": f"{command}-{minute}", "command": command, **fields}


def heavy_end(minute: float, seconds: float, cores: float, memory_gb: float, swap: float | None) -> dict[str, Any]:
    used = {"cores": cores, "memory_gb": memory_gb, "gpu_percent": None}
    return heavy_entry(minute, "end", exit_code=0, seconds=seconds, used=used, highest_swap_used_gb=swap)


class HeavyTest(ReportTest):
    def log_heavy(self, *entries: dict[str, Any]) -> None:
        (self.loop / "heavy.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries))

    def test_gives_the_runs_of_each_command_their_waits_and_the_swap_while_they_ran(self) -> None:
        self.log_heavy(
            heavy_entry(-5, "start", waited_seconds=600, waited_with_room_seconds=600),
            heavy_end(-4, 90, 3.0, 1.0, 9.0),
            heavy_entry(1, "start", waited_seconds=0, waited_with_room_seconds=0),
            heavy_entry(2, "wait"),
            heavy_entry(4, "start", waited_seconds=120, waited_with_room_seconds=30),
            heavy_entry(5, "wait"),
            heavy_entry(8, "start", waited_seconds=240, waited_with_room_seconds=60),
            heavy_end(3, 120, 3.4, 1.2, 0.5),
            heavy_end(6, 60, 2.0, 0.8, None),
            heavy_end(10, 90, 3.0, 1.0, 2.5),
            heavy_entry(9, "start", "make test", waited_seconds=0, waited_with_room_seconds=0),
        )
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        heavy = [line for line in lines if "heavy" in line.lower()]
        self.assertEqual(
            heavy,
            [
                (
                    "Heavy command make test: 1 started, 0 ended | 0 waited, 0.0 min in all, 0.0 min of it while the "
                    "readings showed room"
                ),
                (
                    "Heavy command scripts/check: 3 started, 3 ended, median 1.5 min, 3.0 cores and 1.0 GB | 2 waited, "
                    "6.0 min in all, 1.5 min of it while the readings showed room"
                ),
                "Swap while heavy commands ran, the highest in each run, GB: median 1.5, highest 2.5",
            ],
        )

    def test_says_when_no_run_gave_the_swap(self) -> None:
        self.log_heavy(
            heavy_entry(1, "start", waited_seconds=0, waited_with_room_seconds=0), heavy_end(2, 30, 1.0, 1.0, None)
        )
        self.assertEqual(self.line("Swap while heavy"), "Swap while heavy commands ran: no readings")

    def test_counts_the_wait_of_a_command_that_a_signal_stopped_while_it_waited(self) -> None:
        self.log_heavy(heavy_entry(1, "wait"), heavy_entry(3, "stop", waited_seconds=120, waited_with_room_seconds=90))
        self.assertEqual(
            self.line("Heavy command scripts/check"),
            (
                "Heavy command scripts/check: 0 started, 0 ended | 1 waited, 2.0 min in all, 1.5 min of it while the "
                "readings showed room"
            ),
        )

    def test_leaves_out_a_line_that_is_not_json_with_a_warning(self) -> None:
        start = heavy_entry(1, "start", waited_seconds=60, waited_with_room_seconds=0)
        log = self.loop / "heavy.jsonl"
        log.write_text(json.dumps(start) + "\n" + '{"at": "2026-10-11T14:02:00+00:00", "event": "en\n')
        # The warning comes once in a process, and the tests share one.
        heavy_log.warn.cache_clear()
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        self.assertIn(
            "Heavy command scripts/check: 1 started, 0 ended | 1 waited, 1.0 min in all, 0.0 min of it while the "
            "readings showed room",
            lines,
        )
        self.assertEqual(
            errors, f"warning: line 2 of {log} isn't JSON, so it doesn't count. Delete the line to end this warning.\n"
        )


def meter_reading(minute: float, **numbers: float | None) -> dict[str, Any]:
    """A line of the meter's log, with a machine that gives the numbers, and gives no others."""
    names = ("disk_free_gb", "memory_free_gb", "swap_used_gb", "gpu_percent", "gpu_memory_used_gb", "load")
    machine = {name: numbers.get(name) for name in names} | {"cores": 10, "gpu_shares_memory": True}
    return {"at": at(minute), "account": "claude", "meters": {}, "meters_at_stop": [], "machine": machine}


def worked(agent_id: str, *periods: tuple[float, float]) -> dict[str, Any]:
    """An end line of agents.jsonl for an agent that worked in the periods, each from one minute to another."""
    return {
        **end(agent_id, periods[-1][1]),
        "periods": [[at(first), at(last)] for first, last in periods],
    }


class MachineTest(ReportTest):
    def setUp(self) -> None:
        super().setUp()
        # From minute 0 to 40 these agents work, one, two, or four at once. The test build c has no end line, and its
        # transcript's last call is at minute 41.
        self.log_agents(
            start("d", -5),
            start("a", 0),
            start("b", 10),
            {**start("c", 15), "test": True},
            start("e", 16),
            worked("d", (-5, 5)),
            worked("e", (16, 18)),
            worked("b", (10, 20)),
            worked("a", (0, 30)),
        )
        transcript = self.transcript("agent-c", [prompt_line(15), call_line("x", 16), call_line("y", 41)], "session")
        transcript.with_suffix(".meta.json").write_text(json.dumps({"toolUseId": "c"}))

    def log_readings(self, *readings: dict[str, Any]) -> None:
        (self.loop / "meter.jsonl").write_text("".join(json.dumps(reading) + "\n" for reading in readings))

    def test_gives_the_median_and_the_worst_of_each_number_and_its_median_by_how_many_agents_ran(self) -> None:
        disk = {0: 100, 8: 98, 12: 97, 17: 95, 20: 96, 40: 90}
        swap = {0: 1, 8: 2, 12: 3, 17: 6, 20: 4, 40: None}
        before_the_time = meter_reading(-10, disk_free_gb=500, swap_used_gb=50)
        self.log_readings(
            before_the_time,
            *[meter_reading(minute, disk_free_gb=disk[minute], swap_used_gb=swap[minute]) for minute in disk],
        )
        status, lines, errors = self.run_report()
        self.assertEqual(status, 0, errors)
        self.assertEqual(
            lines[-6:],
            [
                (
                    "Free disk, GB: median 96.5, lowest 90.0, net change -10.0 | median with 1 agent 94.0, 2 agents "
                    "97.0, 3 or more 95.0"
                ),
                "Free memory, GB: no readings",
                "Swap in use, GB: median 3.0, highest 6.0 | median with 1 agent 2.0, 2 agents 3.0, 3 or more 6.0",
                "GPU use, %: no readings",
                "GPU memory in use, GB: no readings",
                "Load: no readings",
            ],
        )

    def test_says_when_no_reading_ran_with_that_many_agents(self) -> None:
        self.log_readings(meter_reading(8, load=2.5, gpu_percent=40), meter_reading(12, load=4.5, gpu_percent=None))
        self.assertEqual(
            self.line("Load"), "Load: median 3.5, highest 4.5 | median with 1 agent 2.5, 2 agents 4.5, 3 or more none"
        )
        self.assertEqual(
            self.line("GPU use"),
            "GPU use, %: median 40.0, highest 40.0 | median with 1 agent 40.0, 2 agents none, 3 or more none",
        )

    def test_counts_an_agent_without_an_end_line_until_the_last_line_of_its_transcript(self) -> None:
        self.log_readings(meter_reading(40.5, memory_free_gb=12), meter_reading(41.5, memory_free_gb=20))
        self.assertEqual(
            self.line("Free memory"),
            "Free memory, GB: median 16.0, lowest 12.0 | median with 1 agent 12.0, 2 agents none, 3 or more none",
        )

    def test_leaves_out_the_time_that_an_agent_waited_for_a_message(self) -> None:
        self.log_agents(start("f", 0), worked("f", (0, 5), (20, 25)))
        self.log_readings(*[meter_reading(minute, load=load) for minute, load in ((3, 1.0), (10, 9.0), (22, 3.0))])
        self.assertEqual(
            self.line("Load"), "Load: median 3.0, highest 9.0 | median with 1 agent 2.0, 2 agents none, 3 or more none"
        )

    def test_leaves_out_the_readings_from_before_the_meter_read_the_machine(self) -> None:
        reading = meter_reading(8, disk_free_gb=50)
        del reading["machine"]
        self.log_readings(reading)
        self.assertEqual(self.line("Machine"), "Machine: no readings")


class SinceTest(ReportTest):
    def test_stops_on_a_time_without_a_zone_or_one_that_is_not_a_time(self) -> None:
        cases = {
            "2026-10-11T14:00": "'2026-10-11T14:00' has no zone. Add one, as in 2026-10-11T14:00:00Z.",
            "7d": "'7d' isn't an ISO time, such as 2026-10-11T14:00:00Z",
        }
        for since, message in cases.items():
            with self.subTest(since=since):
                status, lines, errors = self.run_report("--since", since)
                self.assertEqual((status, lines), (2, []))
                self.assertIn(f"argument --since: {message}\n", errors)


class ScriptTest(ReportTest):
    def test_runs_as_a_script(self) -> None:
        done = subprocess.run(
            [sys.executable, str(REPORT), "--since", SINCE.isoformat()],
            capture_output=True,
            text=True,
            env=dict(os.environ),
            check=False,
            timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[0], "Since 2026-10-11T14:00:00+00:00")
