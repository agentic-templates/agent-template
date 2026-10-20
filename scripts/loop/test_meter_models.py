import datetime as dt
import json
import re
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import models
from test_meter import NOW, MeterTest, usage_output

TESTED = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
    "fable": "claude-fable-5-1",
}
AGENT_TYPES = Path(__file__).resolve().parents[2] / ".claude" / "agents"


def model_answer(*model_ids: str) -> dict[str, str]:
    """An answer of claude -p --output-format json, whose request ran on the models."""
    usage = {model_id: {"inputTokens": 12, "outputTokens": 4} for model_id in model_ids}
    return {"stdout": json.dumps({"type": "result", "result": "OK", "modelUsage": usage})}


class ModelsTest(MeterTest):
    """The aliases name the models in TESTED, which .claude/tested-models.json lists, unless a test says otherwise."""

    def setUp(self) -> None:
        super().setUp()
        (self.loop / "models.json").unlink()

    def model_parts(self, **models_by_alias: str) -> list[str]:
        """The parts of the machine line about the models, when each alias names its model in TESTED or the one
        given."""
        return self.parts_with_answers(
            [model_answer(models_by_alias.get(alias, model)) for alias, model in TESTED.items()]
        )

    def parts_with_answers(self, model_answers: list[dict[str, Any]]) -> list[str]:
        """The parts of the machine line about the models, when the meter's requests for them, if it sends any, get
        the answers. The stand-in counts the earlier runs' calls too, so each of them gets an answer of /usage."""
        earlier = len(self.calls("claude")) if (self.standins / "calls.jsonl").exists() else 0
        self.answer("claude", [usage_output(8)] * (earlier + 1) + model_answers)
        parts = self.run_meter()[1][-1].split(" | ")
        return [part for part in parts if part.startswith(("retrospective due: ", "model of "))]

    def answers_after_opus(self, opus: dict[str, Any]) -> list[dict[str, Any]]:
        """The answer for opus, followed by the models in TESTED for the other aliases."""
        return [opus, *(model_answer(model) for model in list(TESTED.values())[1:])]

    def model_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls("claude") if "--model" in call["args"]]

    def write_reading(self, at: dt.datetime) -> None:
        reading = {"at": at.isoformat(), "models": {"fable": ["claude-fable-9-9"]}, "problems": {}}
        (self.loop / "models.json").write_text(json.dumps(reading))

    def test_says_a_retrospective_is_due_when_an_alias_names_a_model_that_the_list_lacks(self) -> None:
        self.assertEqual(
            self.model_parts(fable="claude-fable-9-9"),
            ["retrospective due: fable names claude-fable-9-9, which no retrospective tested"],
        )

    def test_says_nothing_about_the_models_when_the_list_has_each_of_them(self) -> None:
        self.assertEqual(self.model_parts(), [])

    def test_names_each_model_that_a_request_ran_on_and_the_list_lacks(self) -> None:
        self.assertEqual(
            self.parts_with_answers(self.answers_after_opus(model_answer("claude-opus-9-9", "claude-haiku-5-5"))),
            ["retrospective due: opus names claude-opus-9-9, which no retrospective tested"],
        )

    def test_asks_each_alias_once_in_an_empty_folder_on_the_first_claude_account(self) -> None:
        self.accounts(
            {"name": "codex", "tool": "codex", "env": {"CODEX_HOME": str(self.root / "codex")}},
            {"name": "work", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": str(self.root / "work")}},
            {"name": "other", "tool": "claude", "env": {"CLAUDE_CONFIG_DIR": str(self.root / "other")}},
        )
        self.model_parts()
        calls = self.model_calls()
        self.assertEqual([call["args"][call["args"].index("--model") + 1] for call in calls], list(TESTED))
        for call in calls:
            self.assertEqual(call["files"], [])
            self.assertEqual(call["env"]["CLAUDE_CONFIG_DIR"], str(self.root / "work"))
            self.assertEqual(call["args"][call["args"].index("--output-format") + 1], "json")

    def test_reads_the_models_again_only_once_its_last_reading_is_a_day_old(self) -> None:
        self.write_reading(NOW - models.READ_EVERY + dt.timedelta(minutes=1))
        self.assertEqual(
            self.model_parts(), ["retrospective due: fable names claude-fable-9-9, which no retrospective tested"]
        )
        self.assertEqual(self.model_calls(), [])
        self.write_reading(NOW - models.READ_EVERY)
        self.assertEqual(self.model_parts(), [])
        self.assertEqual(len(self.model_calls()), len(TESTED))
        reading = json.loads((self.loop / "models.json").read_text())
        self.assertEqual(reading["at"], NOW.isoformat(timespec="seconds"))
        self.assertEqual(reading["models"], {alias: [model] for alias, model in TESTED.items()})

    def test_names_each_alias_whose_request_failed_and_asks_again_only_the_next_day(self) -> None:
        answers = self.answers_after_opus({"stdout": "", "stderr": "Not logged in\n", "status": 1})
        failed = (
            "model of opus unknown: claude -p --model opus --output-format json printed no modelUsage, and began with "
            '"Not logged in". Run it in an empty folder to see why.'
        )
        self.assertEqual(self.parts_with_answers(answers), [failed])
        self.assertEqual(self.parts_with_answers(answers), [failed])
        self.assertEqual(len(self.model_calls()), len(TESTED))

    def test_names_an_alias_whose_request_did_not_end(self) -> None:
        with mock.patch.object(models, "TIMEOUT_SECONDS", 0.1):
            parts = self.parts_with_answers([{"sleep": 2}])
        self.assertIn("model of fable unknown: claude didn't answer within 0.1 seconds.", parts)

    def test_names_each_alias_when_claude_is_not_installed(self) -> None:
        self.without_standin("claude")
        self.assertIn(
            "model of opus unknown: claude isn't installed. Install Claude Code.", self.parts_with_answers([])
        )


class TestedModelsTest(unittest.TestCase):
    def test_lists_the_model_of_each_agent_type(self) -> None:
        named = {
            match.group(1)
            for agent_type in AGENT_TYPES.glob("*.md")
            if (match := re.search(r"^model: (\S+)$", agent_type.read_text(), re.MULTILINE))
        }
        self.assertTrue(named)
        self.assertLessEqual(named, models.tested())
