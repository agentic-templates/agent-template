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
