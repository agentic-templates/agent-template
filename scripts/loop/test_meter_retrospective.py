import datetime as dt
import json
from pathlib import Path
from unittest import mock

import github
import paths
from test_meter import NEW_YORK, MeterTest, github_answer, meter, project_folder
from test_transcripts import call_line, prompt_line, write_transcript


def new_york(time: str) -> dt.datetime:
    return dt.datetime.fromisoformat(time).replace(tzinfo=NEW_YORK)


def agent(
    agent_id: str,
    started: str,
    minutes: float | None,
    agent_type: str = "builder",
    is_test: bool = False,
    output_tokens: int = 0,
    requests: dict[str, int] | None = None,
) -> list[str]:
    """The lines of .loop/agents.jsonl for an agent that started at a time in New York, with an end line unless
    minutes is None. The agent worked from its start for the minutes. The end line gives the output tokens, and those
    of each request as the transcript gives them, as for an agent of a session."""
    start = {"at": new_york(started).isoformat(), "event": "start", "id": agent_id, "type": agent_type, "test": is_test}
    lines = [json.dumps(start)]
    if minutes is None:
        return lines
    finished = new_york(started) + dt.timedelta(minutes=minutes)
    end = {
        "at": new_york(started).isoformat(),
        "event": "end",
        "id": agent_id,
        "minutes": minutes,
        "periods": [[new_york(started).isoformat(), finished.isoformat()]],
        "output_tokens": output_tokens,
        "requests": requests,
    }
    lines.append(json.dumps(end))
    return lines


class RetrospectiveTest(MeterTest):
    """The last retrospective merged at 1:00 in New York, unless a test says otherwise, and 10,000 output tokens make
    the next one due."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(meter, "OUTPUT_TOKENS_PER_RETROSPECTIVE", 10_000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def retrospective(self, *agents: list[str]) -> str:
        lines = [line for lines in agents for line in lines]
        (self.loop / "agents.jsonl").write_text("".join(f"{line}\n" for line in lines))
        return self.run_meter()[1][-1].split(" | ")[-1]

    def log_requests(self, *requests: tuple[str, int]) -> None:
        """Writes .loop/requests.jsonl, with the output tokens of each request id."""
        lines = [{"session_id": "s1", "request_id": request, "output_tokens": tokens} for request, tokens in requests]
        (self.loop / "requests.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))

    def test_is_due_once_the_output_tokens_of_the_claude_and_codex_agents_add_up_to_the_number(self) -> None:
        self.log_requests(("req_a", 3000), ("req_b", 1000), ("req_other", 50_000))
        cases = {
            6000: "retrospective due: 10000 output tokens of agents' work since the last one",
            5999: "retrospective not due: 9999 of 10000 output tokens of agents' work since the last one",
        }
        for codex_tokens, note in cases.items():
            with self.subTest(codex_tokens=codex_tokens):
                agents = [
                    agent("toolu_1", "2026-10-18T01:30:00", 60, output_tokens=7, requests={"req_a": 2, "req_b": 5}),
                    agent("issue-12-run", "2026-10-18T02:00:00", 1, "codex", output_tokens=codex_tokens),
                ]
                self.assertEqual(self.retrospective(*agents), note)

    def test_counts_the_transcripts_tokens_of_a_request_without_an_event_and_says_how_many_have_none(self) -> None:
        self.log_requests(("req_a", 3000))
        self.assertEqual(
            self.retrospective(agent("toolu_1", "2026-10-18T01:30:00", 60, requests={"req_a": 2, "req_b": 5})),
            "retrospective not due: 3005 of 10000 output tokens of agents' work since the last one, 1 of 2 requests "
            "with no event",
        )

    def test_says_that_no_request_has_an_event_when_nothing_received_them(self) -> None:
        self.assertEqual(
            self.retrospective(agent("toolu_1", "2026-10-18T01:30:00", 60, requests={"req_a": 2, "req_b": 5})),
            "retrospective not due: 7 of 10000 output tokens of agents' work since the last one, 2 of 2 requests "
            "with no event",
        )

    def write_agent_transcript(self, project: Path) -> None:
        """Writes the transcript of the agent that the Agent call toolu_1 started, in a session in the project folder.
        Its requests req_a and req_b gave 2 and 5 output tokens. Another agent's .meta.json there names no Agent call."""
        agents = project / "session-1" / "subagents"
        write_transcript(
            agents / "agent-a1.jsonl",
            [
                prompt_line(0),
                call_line("a", 1, output_tokens=2, request_id="req_a"),
                call_line("b", 2, output_tokens=5, request_id="req_b"),
            ],
        )
        (agents / "agent-a1.meta.json").write_text(json.dumps({"agentType": "builder", "toolUseId": "toolu_1"}))
        (agents / "agent-a2.meta.json").write_text(json.dumps({"agentType": "builder"}))

    def test_counts_an_agent_of_a_session_without_an_end_line_until_the_last_line_of_its_transcript(self) -> None:
        self.write_agent_transcript(project_folder(self.root / "claude-config"))
        self.log_requests(("req_a", 3000), ("req_b", 1000))
        self.assertEqual(
            self.retrospective(
                agent("toolu_1", "2026-10-18T01:30:00", None), agent("other", "2026-10-18T01:30:00", None)
            ),
            "retrospective not due: 4000 of 10000 output tokens of agents' work since the last one",
        )

    def test_counts_an_agent_whose_transcript_is_in_the_folder_of_another_claude_account(self) -> None:
        self.write_agent_transcript(self.orchestrator_on_another_account())
        self.log_requests(("req_a", 3000), ("req_b", 1000))
        self.assertEqual(
            self.retrospective(agent("toolu_1", "2026-10-18T01:30:00", None)),
            "retrospective not due: 4000 of 10000 output tokens of agents' work since the last one",
        )

    def test_adds_up_the_output_tokens_of_every_type_of_agent(self) -> None:
        agents = [
            agent("a", "2026-10-18T01:00:00", 60, output_tokens=2500),
            agent("b", "2026-10-18T01:30:00", 60, "researcher", output_tokens=2500),
            agent("c", "2026-10-18T02:00:00", 60, "reviewer", output_tokens=2500),
            agent("d", "2026-10-18T02:00:00", 1, "codex", output_tokens=2500),
        ]
        self.assertEqual(
            self.retrospective(*agents), "retrospective due: 10000 output tokens of agents' work since the last one"
        )

    def test_counts_the_last_end_of_an_agent_that_ended_again_after_a_message(self) -> None:
        first_end = agent("a", "2026-10-18T01:30:00", 60, output_tokens=1000)
        again = agent("a", "2026-10-18T01:30:00", 90, output_tokens=1200)[1]
        self.assertEqual(
            self.retrospective([*first_end, again]),
            "retrospective not due: 1200 of 10000 output tokens of agents' work since the last one",
        )

    def test_is_not_due_after_30_hours_of_work(self) -> None:
        self.assertEqual(
            self.retrospective(agent("a", "2026-10-18T01:30:00", 1800, output_tokens=100)),
            "retrospective not due: 100 of 10000 output tokens of agents' work since the last one",
        )

    def test_leaves_out_test_builds_auditors_agents_before_the_last_one_and_agents_without_an_end_or_transcript(
        self,
    ) -> None:
        agents = [
            agent("test", "2026-10-18T01:30:00", 60, is_test=True, output_tokens=20_000),
            agent("auditor", "2026-10-18T01:30:00", 60, "auditor", output_tokens=20_000),
            agent("before", "2026-10-18T00:30:00", 60, output_tokens=20_000),
            agent("no transcript", "2026-10-18T01:30:00", None),
            agent("counted", "2026-10-18T01:00:00", 60, output_tokens=40),
        ]
        self.assertEqual(
            self.retrospective(*agents),
            "retrospective not due: 40 of 10000 output tokens of agents' work since the last one",
        )

    def test_counts_the_whole_log_before_the_first_retrospective(self) -> None:
        self.answer("gh", github_answer(merges=()))
        agents = [
            agent("a", "2026-09-01T12:00:00", 60, output_tokens=9000),
            agent("b", "2026-10-18T02:00:00", 60, output_tokens=1000),
        ]
        self.assertEqual(
            self.retrospective(*agents),
            "retrospective due: 10000 output tokens of agents' work with no retrospective yet",
        )

    def test_starts_at_the_newest_retrospective(self) -> None:
        self.answer(
            "gh", github_answer(merges=("2026-10-11T09:00:00Z", "2026-10-18T05:00:00Z", "2026-10-15T09:00:00Z"))
        )
        agents = [
            agent("a", "2026-10-16T12:00:00", 60, output_tokens=20_000),
            agent("b", "2026-10-18T02:00:00", 60, output_tokens=1500),
        ]
        self.assertEqual(
            self.retrospective(*agents),
            "retrospective not due: 1500 of 10000 output tokens of agents' work since the last one",
        )
        (repository,) = self.calls("gh")
        self.assertEqual(repository["args"][:4], ["api", "graphql", "-f", f"query={github.RETROSPECTIVES_QUERY}"])
        self.assertEqual(
            repository["args"][4:], ["-F", "owner={owner}", "-F", "name={repo}", "-F", "label=retrospective"]
        )
        self.assertEqual(Path(repository["cwd"]), paths.main_checkout())

    def test_is_not_due_after_a_release_alone(self) -> None:
        release = {"tagName": "v1.2.0", "publishedAt": "2026-10-18T06:00:00Z"}
        self.answer("gh", github_answer(latestRelease=release))
        self.assertEqual(
            self.retrospective(agent("a", "2026-10-18T01:30:00", 60, output_tokens=100)),
            "retrospective not due: 100 of 10000 output tokens of agents' work since the last one",
        )

    def test_is_unknown_when_gh_fails(self) -> None:
        self.answer("gh", {"status": 1, "stderr": "HTTP 401: Bad credentials\n(https://api.github.com)\n"})
        self.assertEqual(
            self.retrospective(),
            "retrospective unknown: gh api graphql failed: HTTP 401: Bad credentials (https://api.github.com). Check the "
            "network, and that gh auth status shows a login.",
        )

    def test_is_unknown_when_gh_is_not_installed(self) -> None:
        self.without_standin("gh")
        self.assertEqual(self.retrospective(), "retrospective unknown: gh isn't installed. Install the GitHub CLI.")
