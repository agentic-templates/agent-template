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

import paths

REPORT = Path(__file__).with_name("report")
# The report's file name has no .py, so the tests load it by its path.
report = types.ModuleType("report")
importlib.machinery.SourceFileLoader("report", str(REPORT)).exec_module(report)

SINCE = dt.datetime(2026, 10, 11, 14, 0, tzinfo=dt.UTC)

# A stand-in for gh. It writes each call's arguments to calls.jsonl in STANDIN_DIR. For a list of runs it prints the
# lines under "runs" in gh.json. For a GraphQL query it gives the jobs under "jobs" for each id that the query names.
# An answer under "fail" makes each call print it to stderr and exit with code 1. -S keeps coverage, which starts in
# each Python process of the tests, out of the stand-in.
STANDIN = f"""#!{sys.executable} -S
import json, os, sys
folder = os.environ["STANDIN_DIR"]
with open(os.path.join(folder, "calls.jsonl"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
answers = json.load(open(os.path.join(folder, "gh.json")))
if "fail" in answers:
    sys.stderr.write(answers["fail"])
    sys.exit(1)
if sys.argv[2] == "graphql":
    ids = [argument.removeprefix("ids[]=") for argument in sys.argv if argument.startswith("ids[]=")]
    nodes = [{{"checkSuite": {{"checkRuns": {{"nodes": answers["jobs"][id]}}}}}} for id in ids]
    print(json.dumps({{"data": {{"nodes": nodes}}}}))
else:
    print("".join(json.dumps(run) + "\\n" for run in answers["runs"]), end="")
"""


def at(minutes: float) -> str:
    """The time that many minutes after SINCE."""
    return (SINCE + dt.timedelta(minutes=minutes)).isoformat()


def job(name: str, minutes: float, conclusion: str = "SUCCESS") -> dict[str, str]:
    return {"name": name, "startedAt": at(0), "completedAt": at(minutes), "conclusion": conclusion}


def call_line(call: str, minute: float, tokens: int = 0, tool_calls: int = 0) -> dict[str, Any]:
    """A transcript's line for a call, with the tokens as its input."""
    content: list[dict[str, str]] = [{"type": "text"}] + [{"type": "tool_use"}] * tool_calls
    usage = {"input_tokens": tokens, "output_tokens": None}
    return {"type": "assistant", "timestamp": at(minute), "message": {"id": call, "usage": usage, "content": content}}


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
        self.runs: list[dict[str, str]] = []
        self.jobs: dict[str, list[dict[str, str]]] = {}
        self.answer()

    def answer(self) -> None:
        (self.standins / "gh.json").write_text(json.dumps({"runs": self.runs, "jobs": self.jobs}))

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
                    "Builders: 0",
                    "Orchestrator's share of the tokens: none used",
                    "Hook blocks: 0",
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

    def test_fails_with_what_gh_printed_when_gh_fails(self) -> None:
        (self.standins / "gh.json").write_text(json.dumps({"fail": "HTTP 401: Bad credentials\n"}))
        error = (
            "error: gh api repos/{owner}/{repo}/actions/workflows/ci.yml/runs failed: HTTP 401: Bad credentials. "
            "Check the network, and that gh auth status shows a login.\n"
        )
        self.assertEqual(self.run_report(), (1, [], error))


class BuilderTest(ReportTest):
    def test_gives_the_number_of_builders_and_the_median_of_their_minutes_and_tool_calls(self) -> None:
        self.transcript("session", [])
        builders = {"builder-small": (10, 4), "builder": (20, 6), "builder-deep": (60, 30)}
        for agent_type, (minutes, tool_calls) in builders.items():
            lines = [call_line(f"{agent_type}-1", 1, tool_calls=tool_calls), call_line(f"{agent_type}-2", 1 + minutes)]
            self.transcript(agent_type, lines, session="session", agent_type=agent_type)
        self.transcript("reviewer", [call_line("r", 1, tool_calls=99)], session="session", agent_type="reviewer")
        self.transcript("unnamed", [call_line("u", 1, tool_calls=99)], session="session")
        self.assertEqual(self.line("Builders"), "Builders: 3, median 20.0 min and 6 tool calls")

    def test_adds_up_the_tool_calls_on_each_line_of_a_response(self) -> None:
        self.transcript("session", [])
        lines = [call_line("a", 1, tool_calls=1), call_line("a", 2, tool_calls=2), call_line("b", 3, tool_calls=1)]
        self.transcript("builder", lines, session="session", agent_type="builder")
        self.assertEqual(self.line("Builders"), "Builders: 1, median 2.0 min and 4 tool calls")

    def test_counts_only_the_calls_that_started_after_the_time(self) -> None:
        self.transcript("session", [])
        lines = [call_line("before", -30, tool_calls=5), call_line("after", 1, tool_calls=1), call_line("last", 11)]
        self.transcript("builder", lines, session="session", agent_type="builder")
        old = self.transcript("old", [call_line("old", 1, tool_calls=7)], session="session", agent_type="builder")
        modified = (SINCE - dt.timedelta(minutes=1)).timestamp()
        os.utime(old, (modified, modified))
        self.assertEqual(self.line("Builders"), "Builders: 1, median 10.0 min and 1 tool calls")


class OrchestratorTest(ReportTest):
    def test_gives_the_share_of_the_tokens_that_the_sessions_used(self) -> None:
        self.transcript("session", [call_line("s", 1, tokens=1000)])
        self.transcript("builder", [call_line("b", 1, tokens=2000)], session="session", agent_type="builder")
        self.transcript("reviewer", [call_line("r", 1, tokens=1000)], session="session", agent_type="reviewer")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the tokens: 25%")

    def test_counts_every_kind_of_token(self) -> None:
        usage = {"input_tokens": 1, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 100}
        session = {"type": "assistant", "timestamp": at(1), "message": {"id": "s", "usage": usage, "content": "text"}}
        self.transcript("session", [session])
        self.transcript("builder", [call_line("b", 1, tokens=889)], session="session", agent_type="builder")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the tokens: 11%")

    def test_counts_the_tokens_of_a_response_on_several_lines_once(self) -> None:
        self.transcript("session", [call_line("s", 1, tokens=1000), call_line("s", 2, tokens=1000)])
        self.transcript("builder", [call_line("b", 1, tokens=1000)], session="session", agent_type="builder")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the tokens: 50%")

    def test_counts_a_call_that_a_resumed_session_copied_once(self) -> None:
        earlier = self.transcript("earlier", [call_line("s", 1, tokens=1000)])
        modified = (SINCE + dt.timedelta(minutes=5)).timestamp()
        os.utime(earlier, (modified, modified))
        self.transcript("resumed", [call_line("s", 1, tokens=1000), call_line("t", 6, tokens=1000)])
        self.transcript("builder", [call_line("b", 1, tokens=2000)], session="resumed", agent_type="builder")
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the tokens: 50%")

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
        self.assertEqual(self.line("Orchestrator"), "Orchestrator's share of the tokens: 50%")


class BlockTest(ReportTest):
    def test_counts_the_blocks_after_the_time_by_rule(self) -> None:
        log = [
            f"{at(-1)}\tforbidden-commands\tgit push --force",
            f"{at(0)}\tsecret-files\tcat .env",
            f"{at(1)}\tforbidden-commands\tgit reset --hard",
            f"{at(2)}\tforbidden-commands\trm -rf /",
            f"{at(3)}\tposted-text\tgh pr create",
        ]
        (self.loop / "blocks.log").write_text("\n".join(log) + "\n")
        self.assertEqual(
            self.line("Hook blocks"), "Hook blocks: 4 | forbidden-commands 2 | posted-text 1 | secret-files 1"
        )


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
