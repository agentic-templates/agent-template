import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("run-tests")


class RunTestsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write_tests(self, module: str, body: str) -> None:
        (self.folder / f"{module}.py").write_text(textwrap.dedent(body))

    def write_printing_tests(self, count: int) -> None:
        """Writes tests named test_00 and up, each of which prints its start and its end, a moment apart."""
        methods = "".join(
            f"    def test_{number:02}(self):\n"
            f"        print('start {number:02}', flush=True)\n"
            "        time.sleep(0.05)\n"
            f"        print('end {number:02}', flush=True)\n"
            for number in range(count)
        )
        self.write_tests(
            "test_printing", "import time\nimport unittest\n\nclass PrintingTest(unittest.TestCase):\n" + methods
        )

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments, str(self.folder)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_runs_each_test_once_and_passes_when_every_test_passes(self) -> None:
        self.write_printing_tests(20)
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        started = sorted(line for line in done.stdout.splitlines() if line.startswith("start"))
        self.assertEqual(started, [f"start {number:02}" for number in range(20)])

    def test_prints_the_output_of_each_test_without_another_tests_output_inside_it(self) -> None:
        self.write_printing_tests(20)
        lines = self.run_script().stdout.splitlines()
        self.assertEqual(lines[1::2], [line.replace("start", "end") for line in lines[0::2]])

    def test_fails_and_prints_the_failure_when_a_test_fails(self) -> None:
        self.write_tests(
            "test_failing",
            """\
            import unittest

            class FailingTest(unittest.TestCase):
                def test_passes(self):
                    pass

                def test_fails(self):
                    self.fail("the expected failure")
            """,
        )
        done = self.run_script()
        self.assertEqual(done.returncode, 1)
        self.assertIn("the expected failure", done.stderr)

    def test_names_each_test_that_skips_itself_with_the_reason(self) -> None:
        self.write_tests(
            "test_skipping",
            """\
            import unittest

            class SomeSkipTest(unittest.TestCase):
                def test_runs(self):
                    pass

                @unittest.skip("it needs a GPU")
                def test_needs_a_gpu(self):
                    pass

            @unittest.skip("it opens a TCP port")
            class AllSkipTest(unittest.TestCase):
                def test_listens(self):
                    pass

                def test_connects(self):
                    pass
            """,
        )
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        skipped = sorted(line for line in done.stderr.splitlines() if line.startswith("Skipped"))
        self.assertEqual(
            skipped,
            [
                "Skipped test_skipping.AllSkipTest.test_connects, because it opens a TCP port.",
                "Skipped test_skipping.AllSkipTest.test_listens, because it opens a TCP port.",
                "Skipped test_skipping.SomeSkipTest.test_needs_a_gpu, because it needs a GPU.",
            ],
        )

    def test_fails_when_a_test_module_cannot_be_imported(self) -> None:
        self.write_tests("test_broken", "import module_that_does_not_exist\n")
        done = self.run_script()
        self.assertEqual(done.returncode, 1)
        self.assertIn("module_that_does_not_exist", done.stderr)

    def test_runs_every_nth_test_from_the_part_with_part(self) -> None:
        self.write_printing_tests(7)
        done = self.run_script("--part", "2/3")
        self.assertEqual(done.returncode, 0, done.stderr)
        started = [line for line in done.stdout.splitlines() if line.startswith("start")]
        self.assertEqual(started, ["start 01", "start 04"])

    def test_rejects_a_part_after_the_last(self) -> None:
        done = self.run_script("--part", "4/3")
        self.assertEqual(done.returncode, 2)
        self.assertIn("4/3 names part 4 of 3. Give a part from 1 to 3.", done.stderr)

    def test_rejects_a_part_without_the_number_of_parts(self) -> None:
        done = self.run_script("--part", "2")
        self.assertEqual(done.returncode, 2)
        self.assertIn("takes a part such as 2/3, and got 2", done.stderr)
