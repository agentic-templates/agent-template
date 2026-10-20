"""Tests of the agent files in .claude/agents, which tools other than Claude Code read too, such as GitHub's view of a
file."""

import unittest

import yaml

from agent_log import AGENTS_FOLDER


class AgentFileTest(unittest.TestCase):
    def test_front_matter_of_each_agent_file_is_a_yaml_mapping(self) -> None:
        files = sorted(AGENTS_FOLDER.glob("*.md"))
        self.assertNotEqual(files, [])
        for file in files:
            with self.subTest(file=file.name):
                first_line, front_matter, _ = file.read_text().split("---\n", 2)
                self.assertEqual(first_line, "")
                self.assertIsInstance(yaml.safe_load(front_matter), dict)

    def test_each_agent_file_names_the_full_id_of_its_model(self) -> None:
        for file in sorted(AGENTS_FOLDER.glob("*.md")):
            with self.subTest(file=file.name):
                _, front_matter, _ = file.read_text().split("---\n", 2)
                self.assertRegex(
                    str(yaml.safe_load(front_matter).get("model")),
                    r"^claude-[a-z]+-[0-9]",
                    "Name the model's full ID, such as claude-opus-5-5, and not an alias such as opus, so that the type "
                    "keeps its model until a retrospective tests a newer one.",
                )
