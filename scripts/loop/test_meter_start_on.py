import datetime as dt
import json

from test_meter import INTERRUPTION, NOW, MeterTest, meters


class StartOnTest(MeterTest):
    """Account a has room for two runs of 30 minutes, and account b for four. agents.jsonl has no earlier runs, so each
    type's run takes 30 minutes."""

    def setUp(self) -> None:
        super().setUp()
        self.accounts({"name": "a", "tool": "claude"}, {"name": "b", "tool": "claude"})
        self.reading("a", 20.0, 1.0)
        self.reading("b", 40.0, 0.5)

    def reading(self, account: str, session: float, per_agent_minute: float, at: dt.datetime = NOW) -> None:
        entry = {
            "at": at.isoformat(),
            "account": account,
            "meters": meters(session),
            "per_agent_minute": {"session": per_agent_minute, "week": 0.01},
        }
        with (self.loop / "meter.jsonl").open("a") as log:
            log.write(json.dumps(entry) + "\n")

    def started(self, account: str, at: dt.datetime) -> None:
        line = {"at": at.isoformat(), "event": "start", "id": f"run-{at:%H%M%S}", "type": "builder", "account": account}
        with (self.loop / "agents.jsonl").open("a") as log:
            log.write(json.dumps(line) + "\n")

    def ended(self, account: str, at: dt.datetime) -> None:
        """An agent that ran 30 minutes until the time, which leaves each type's run at 30 minutes."""
        self.started(account, at - dt.timedelta(minutes=30))
        line = {
            "at": at.isoformat(),
            "event": "end",
            "id": f"run-{at - dt.timedelta(minutes=30):%H%M%S}",
            "minutes": 30,
        }
        with (self.loop / "agents.jsonl").open("a") as log:
            log.write(json.dumps(line) + "\n")

    def start_on(self, agent_type: str = "builder") -> str:
        status, lines, errors = self.run_meter("--start-on", agent_type)
        self.assertEqual((status, errors), (0, ""))
        return " ".join(lines)

    def test_starts_only_as_many_agents_on_each_account_as_fit_from_one_reading(self) -> None:
        named = [self.start_on("reviewer") for _ in range(7)]
        self.assertEqual(named, ["a", "a", "b", "b", "b", "b", "no account"])
        self.assertFalse((self.standins / "calls.jsonl").exists())

    def test_counts_the_agents_that_started_on_an_account_after_its_reading(self) -> None:
        self.started("a", NOW + dt.timedelta(minutes=1))
        self.clock.time = NOW + dt.timedelta(minutes=2)
        self.assertEqual([self.start_on(), self.start_on()], ["a", "b"])

    def test_counts_once_an_agent_that_started_after_the_meter_named_its_account(self) -> None:
        self.assertEqual(self.start_on(), "a")
        self.started("a", NOW + dt.timedelta(minutes=1))
        self.assertEqual([self.start_on(), self.start_on()], ["a", "b"])

    def test_counts_the_agents_that_ran_at_the_reading_at_their_minutes_then(self) -> None:
        # At the reading, the builder had run 5 minutes, so it still needed 25, which leaves room for one more run.
        self.started("a", NOW - dt.timedelta(minutes=5))
        self.clock.time = NOW + dt.timedelta(minutes=25)
        self.assertEqual([self.start_on(), self.start_on()], ["a", "b"])

    def test_counts_an_agent_that_ended_after_the_reading_but_not_one_that_ended_before_it(self) -> None:
        # The agent that ended after the reading had run 5 minutes then, so it still needed 25.
        self.ended("a", NOW - dt.timedelta(minutes=10))
        self.assertEqual([self.start_on(), self.start_on()], ["a", "a"])
        self.ended("b", NOW + dt.timedelta(minutes=25))
        self.reading("b", 20.0, 1.0)
        self.clock.time = NOW + dt.timedelta(minutes=26)
        self.assertEqual([self.start_on(), self.start_on()], ["b", "no account"])

    def test_starts_from_a_new_reading_without_the_agents_named_before_it(self) -> None:
        self.assertEqual([self.start_on(), self.start_on()], ["a", "a"])
        self.clock.time = NOW + dt.timedelta(minutes=5)
        self.reading("a", 20.0, 1.0, at=self.clock.time)
        self.assertEqual(self.start_on(), "a")

    def test_names_an_account_for_each_builder_type_while_12_agents_build(self) -> None:
        for minutes in range(1, 13):
            self.started("codex", NOW - dt.timedelta(minutes=minutes))
        named = [self.start_on(agent_type) for agent_type in ("builder", "builder-small", "builder-deep")]
        self.assertEqual(named, ["a", "a", "b"])

    def test_leaves_out_an_agent_that_the_user_interrupted_whose_transcript_is_in_the_folder_of_another_account(
        self,
    ) -> None:
        # Counted as running, the builder would still need 29 minutes, which leaves room for one more run.
        subagents = self.orchestrator_on_another_account() / "abc123" / "subagents"
        subagents.mkdir(parents=True)
        transcript = subagents / "agent-c1.jsonl"
        transcript.write_text(INTERRUPTION + "\n")
        start = NOW - dt.timedelta(minutes=1)
        meta = {"agentType": "builder", "toolUseId": f"run-{start:%H%M%S}"}
        transcript.with_suffix(".meta.json").write_text(json.dumps(meta))
        self.started("claude", start)
        self.reading("claude", 20.0, 1.0)
        self.assertEqual([self.start_on(), self.start_on()], ["claude", "claude"])

    def test_leaves_out_an_account_whose_newest_reading_is_older_than_30_minutes(self) -> None:
        self.clock.time = NOW + dt.timedelta(minutes=30)
        self.reading("b", 40.0, 0.5, at=self.clock.time)
        self.assertEqual(self.start_on(), "a")
        self.clock.time = NOW + dt.timedelta(minutes=30, seconds=1)
        self.assertEqual(self.start_on(), "b")

    def test_leaves_out_a_codex_account_and_an_account_without_a_reading(self) -> None:
        self.accounts(
            {"name": "codex", "tool": "codex"}, {"name": "c", "tool": "claude"}, {"name": "b", "tool": "claude"}
        )
        self.reading("codex", 0.0, 0.01)
        self.assertEqual(self.start_on(), "b")

    def test_stops_when_no_account_has_a_reading_from_the_last_30_minutes(self) -> None:
        (self.loop / "meter.jsonl").unlink()
        error = (
            "error: no Claude account has a reading from the last 30 minutes. Run scripts/loop/meter, and then "
            "scripts/loop/meter --start-on again.\n"
        )
        self.assertEqual(self.run_meter("--start-on", "builder"), (1, [], error))
        self.reading("a", 20.0, 1.0)
        self.clock.time = NOW + dt.timedelta(minutes=31)
        error = (
            "error: no Claude account has a reading from the last 30 minutes. Run scripts/loop/meter, and then "
            "scripts/loop/meter --start-on again.\n"
        )
        self.assertEqual(self.run_meter("--start-on", "builder"), (1, [], error))
