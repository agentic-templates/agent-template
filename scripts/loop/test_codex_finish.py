import unittest

import codex_finish
from test_run_builder import ISSUE


class CommitSubjectTest(unittest.TestCase):
    def test_takes_its_type_from_the_label_of_the_issue(self) -> None:
        for label, subject_type in (
            ("feature", "feat"),
            ("bug", "fix"),
            ("maintenance", "chore"),
            ("research", "chore"),
        ):
            with self.subTest(label=label):
                issue = {**ISSUE, "labels": [{"name": label}]}
                self.assertEqual(codex_finish.commit_subject(issue), f"{subject_type}: Fix the importer")
