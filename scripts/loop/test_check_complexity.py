"""Tests scripts/check-complexity. The Python cases follow the expected counts in sonar-python's own tests. The bash
cases need shfmt, which CI installs."""

import ast
import contextlib
import importlib.machinery
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "check-complexity"
# The script's file name has no .py, so the tests load it by its path.
loader = importlib.machinery.SourceFileLoader("check_complexity", str(SCRIPT))
check_complexity = types.ModuleType(loader.name)
check_complexity.__file__ = loader.path
loader.exec_module(check_complexity)

needs_shfmt = unittest.skipUnless(shutil.which("shfmt"), "shfmt is not installed")
# The tests write the marker from two parts, so that this file holds no skip comment without a reason.
SKIP = "# check-complexity" + ": skip"


def python_counts(source: str) -> dict[str, int]:
    source = textwrap.dedent(source)
    return {
        function.name: function.complexity for function in check_complexity.python_functions(ast.parse(source), source)
    }


class Folder(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name: str, source: str) -> str:
        path = self.folder / name
        path.write_text(textwrap.dedent(source))
        return str(path)

    def run_script(self, *arguments: str) -> tuple[int, str, str]:
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            status = check_complexity.main(list(arguments))
        return status, printed.getvalue(), errors.getvalue()

    def bash_counts(self, source: str) -> dict[str, int]:
        functions = check_complexity.read_bash(self.write("script", source)).functions
        return {function.name: function.complexity for function in functions}


class PythonCountTest(unittest.TestCase):
    def test_counts_nothing_for_a_function_without_branches(self) -> None:
        self.assertEqual(python_counts("def f():\n    return g()\n"), {"f": 0})

    def test_adds_1_for_each_if_elif_and_else(self) -> None:
        source = """
            def f():
                if a:
                    pass
                elif b:
                    pass
                else:
                    pass
        """
        self.assertEqual(python_counts(source), {"f": 3})

    def test_counts_an_if_inside_an_else_one_level_deeper_than_an_elif(self) -> None:
        source = """
            def f():
                if a:
                    pass
                else:
                    if b:
                        pass
        """
        self.assertEqual(python_counts(source), {"f": 4})

    def test_adds_1_for_each_level_of_nesting(self) -> None:
        source = """
            def f():
                if a:
                    if b:
                        if c:
                            pass
                    else:
                        pass
        """
        self.assertEqual(python_counts(source), {"f": 7})

    def test_counts_an_elif_body_one_level_deeper(self) -> None:
        source = """
            def f():
                if a:
                    pass
                elif b:
                    if c:
                        pass
        """
        self.assertEqual(python_counts(source), {"f": 4})

    def test_counts_loops_and_their_else(self) -> None:
        source = """
            def f():
                for x in y:
                    if a:
                        break
                else:
                    if b:
                        pass
            async def g():
                while a:
                    pass
                async for x in y:
                    pass
        """
        self.assertEqual(python_counts(source), {"f": 6, "g": 2})

    def test_counts_each_except_and_else_but_not_try_or_finally(self) -> None:
        source = """
            def f():
                try:
                    if a:
                        pass
                except KeyError:
                    if b:
                        pass
                except (OSError, ValueError):
                    pass
                else:
                    if c:
                        pass
                finally:
                    if d:
                        pass
            def g():
                try:
                    pass
                except* OSError:
                    if a:
                        pass
        """
        self.assertEqual(python_counts(source), {"f": 9, "g": 3})

    def test_counts_nothing_for_with_or_match_but_nests_the_cases(self) -> None:
        source = """
            def f():
                with open(a) as file:
                    if b:
                        pass
                match c:
                    case [x] if x:
                        if d:
                            pass
                    case _:
                        pass
        """
        self.assertEqual(python_counts(source), {"f": 3})

    def test_adds_1_for_each_run_of_the_same_boolean_operator(self) -> None:
        cases = {
            "a and b and c": 1,
            "a or b or c": 1,
            "a and b or c": 2,
            "a and b or c and d": 3,
            "a or b and c or d": 3,
        }
        for expression, count in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(python_counts(f"def f():\n    return {expression}\n"), {"f": count})

    def test_ends_a_run_at_parentheses(self) -> None:
        cases = {
            "a or (b and c) or d": 2,
            "(a or b) or c": 2,
            "a and (b and c)": 2,
            "(a) or b and c or d": 3,
            "f(a and b or c)": 2,
            "(a or  # (\n b and c or d)": 3,
        }
        for expression, count in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(python_counts(f"def f():\n    return {expression}\n"), {"f": count})

    def test_counts_the_operands_of_a_conditional_expression_one_level_deeper(self) -> None:
        cases = {
            "x if a else y": 1,
            "x if a else (y if b else z)": 3,
            "(x if a else y) if b else z": 3,
            "x if (a if b else c) else y": 3,
        }
        for expression, count in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(python_counts(f"def f():\n    return {expression}\n"), {"f": count})

    def test_leaves_out_the_branches_of_a_comprehension(self) -> None:
        self.assertEqual(python_counts("def f():\n    return [x for x in y if x]\n"), {"f": 0})

    def test_adds_a_function_inside_another_one_level_deeper(self) -> None:
        source = """
            def f():
                if a:
                    pass
                def g():
                    if b:
                        pass
                    def h():
                        return x if c else y
                return g
        """
        self.assertEqual(python_counts(source), {"f": 6})

    def test_adds_a_function_that_a_decorator_returns_at_the_same_level(self) -> None:
        source = """
            def f(function):
                def wrapper():
                    if a:
                        pass
                    return function()
                return wrapper
        """
        self.assertEqual(python_counts(source), {"f": 1})

    def test_adds_a_function_one_level_deeper_when_the_other_does_more_than_return_it(self) -> None:
        source = """
            def f(function):
                def wrapper():
                    if a:
                        pass
                return wrapper if b else None
        """
        self.assertEqual(python_counts(source), {"f": 3})

    def test_counts_a_method_from_level_0(self) -> None:
        source = """
            def f():
                if a:
                    class A:
                        def m(self):
                            return x if b else y
            class B:
                if a:
                    def m(self):
                        if b:
                            pass
        """
        self.assertEqual(python_counts(source), {"f": 2, "m": 1})

    def test_starts_a_function_at_its_first_decorator(self) -> None:
        source = "@cache\n@wraps(g)\ndef f():\n    pass\n"
        self.assertEqual(check_complexity.python_functions(ast.parse(source), source)[0].line, 1)


@needs_shfmt
class BashCountTest(Folder):
    def test_counts_nothing_for_a_function_without_branches(self) -> None:
        self.assertEqual(self.bash_counts("f() {\n  echo a | grep b\n}\n"), {"f": 0})

    def test_adds_1_for_each_if_elif_and_else(self) -> None:
        source = """
            f() {
              if a; then
                :
              elif b; then
                :
              else
                :
              fi
            }
        """
        self.assertEqual(self.bash_counts(source), {"f": 3})

    def test_counts_the_commands_of_then_elif_and_else_one_level_deeper(self) -> None:
        source = """
            f() {
              if a; then
                if b; then :; fi
              elif c; then
                if d; then :; fi
              else
                if e; then :; fi
              fi
            }
        """
        self.assertEqual(self.bash_counts(source), {"f": 9})

    def test_counts_a_condition_at_the_level_of_its_if(self) -> None:
        source = """
            f() {
              if if a; then :; fi; then
                :
              elif while b; do :; done; then
                :
              fi
            }
        """
        self.assertEqual(self.bash_counts(source), {"f": 4})

    def test_adds_1_for_each_loop_and_counts_its_body_one_level_deeper(self) -> None:
        loops = ["for x in a b; do", "for ((i = 0; i < 3; i++)); do", "while a; do", "until a; do", "select x in a; do"]
        for loop in loops:
            with self.subTest(loop=loop):
                source = f"f() {{\n  {loop}\n    if b; then :; fi\n  done\n}}\n"
                self.assertEqual(self.bash_counts(source), {"f": 3})

    def test_adds_1_for_a_case_and_counts_its_items_one_level_deeper(self) -> None:
        source = """
            f() {
              case $1 in
                a) if b; then :; fi ;;
                *) : ;;
              esac
            }
        """
        self.assertEqual(self.bash_counts(source), {"f": 3})

    def test_adds_1_for_a_conditional_in_arithmetic_and_counts_its_branches_one_level_deeper(self) -> None:
        cases = {
            "(( a ? b : c ))": 1,
            "x=$(( a ? (b ? c : d) : e ))": 3,
            "(( a ? b : c ? d : e ))": 3,
            "if a; then (( b ? c : d )); fi": 3,
        }
        for command, count in cases.items():
            with self.subTest(command=command):
                self.assertEqual(self.bash_counts(f"f() {{\n  {command}\n}}\n"), {"f": count})

    def test_adds_1_for_each_run_of_the_same_operator_between_commands(self) -> None:
        cases = {
            "a && b && c": 1,
            "a || b": 1,
            "a && b || c": 2,
            "a && b || c && d": 3,
            "a | b && c | d": 1,
            "! a && b || c": 2,
            "a && (b || c)": 2,
            "a && { b && c; }": 2,
        }
        for command, count in cases.items():
            with self.subTest(command=command):
                self.assertEqual(self.bash_counts(f"f() {{\n  {command}\n}}\n"), {"f": count})

    def test_adds_1_for_each_run_of_the_same_operator_inside_tests_and_arithmetic(self) -> None:
        cases = {
            "[[ -n $a && -n $b && -n $c ]]": 1,
            "[[ -n $a && -n $b || -n $c ]]": 2,
            "[[ -n $a && (-n $b || -n $c) ]]": 2,
            "[[ -n $a && (-n $b && -n $c) ]]": 2,
            "(( a && b || c ))": 2,
            "(( a && (b && c) ))": 2,
            "x=$(( a || b ))": 1,
            "[[ -n $a ]] && (( b || c ))": 2,
        }
        for command, count in cases.items():
            with self.subTest(command=command):
                self.assertEqual(self.bash_counts(f"f() {{\n  {command}\n}}\n"), {"f": count})

    def test_counts_nothing_for_subshells_and_command_substitutions(self) -> None:
        source = """
            f() {
              (
                x=$(if a; then echo b; fi)
                if c; then :; fi
              )
            }
        """
        self.assertEqual(self.bash_counts(source), {"f": 2})

    def test_counts_a_function_inside_another_on_its_own(self) -> None:
        source = """
            f() {
              if a; then
                function g {
                  if b; then :; fi
                }
              fi
            }
        """
        self.assertEqual(self.bash_counts(source), {"f": 1, "g": 1})

    def test_leaves_out_the_code_outside_functions(self) -> None:
        self.assertEqual(self.bash_counts("if a; then :; fi\nf() { :; }\n"), {"f": 0})


# A bash function whose count is 15, and the same function with an else, which adds 1.
AT_LIMIT = """\
f() {
  if a; then
    if b; then
      if c; then
        if d; then
          for x in y; do :; done
        fi
      fi
    fi
  fi
}
"""
OVER_LIMIT = AT_LIMIT.replace("  fi\n}", "  else\n    :\n  fi\n}")
# A Python function whose count is 16.
PYTHON_OVER_LIMIT = """\
@decorator
def f():
    if a:
        if b:
            if c:
                if d:
                    for x in y:
                        pass
    else:
        pass
"""


def over_limit_error(path: str, line: int) -> str:
    return (
        f"error: {path}:{line}: f has a cognitive complexity of 16, and the limit is 15. Split it into functions that "
        "each have one purpose.\n"
    )


def misplaced_error(path: str, line: int) -> str:
    return (
        f"error: {path}:{line}: the skip comment isn't in the comment lines right above a function that the check "
        "counts, so it skips nothing. Move it there, or remove it.\n"
    )


class ScriptTest(Folder):
    @needs_shfmt
    def test_passes_a_bash_function_at_the_limit(self) -> None:
        self.assertEqual(self.run_script("--bash", self.write("script", AT_LIMIT)), (0, "", ""))

    @needs_shfmt
    def test_fails_on_a_bash_function_over_the_limit(self) -> None:
        path = self.write("script", "# Does f.\n" + OVER_LIMIT)
        self.assertEqual(self.run_script("--bash", path), (1, "", over_limit_error(path, 2)))

    def test_passes_a_python_function_at_the_limit(self) -> None:
        path = self.write("script.py", PYTHON_OVER_LIMIT.replace("    else:\n        pass\n", ""))
        self.assertEqual(self.run_script("--python", path), (0, "", ""))

    def test_fails_on_a_python_function_over_the_limit(self) -> None:
        path = self.write("script.py", PYTHON_OVER_LIMIT)
        self.assertEqual(self.run_script("--python", path), (1, "", over_limit_error(path, 1)))

    @needs_shfmt
    def test_skips_a_bash_function_with_a_skip_comment_that_gives_a_reason(self) -> None:
        source = f"{SKIP}, because each branch reads one field\n# Does f.\n{OVER_LIMIT}"
        self.assertEqual(self.run_script("--bash", self.write("script", source)), (0, "", ""))

    def test_skips_a_python_function_with_a_skip_comment_above_its_decorators(self) -> None:
        source = f"{SKIP}, because each branch reads one field\n{PYTHON_OVER_LIMIT}"
        self.assertEqual(self.run_script("--python", self.write("script.py", source)), (0, "", ""))

    def test_fails_on_a_skip_comment_without_a_reason(self) -> None:
        for after_marker in ["", ".", ", "]:
            with self.subTest(after_marker=after_marker):
                path = self.write("script.py", f"{SKIP}{after_marker}\n{PYTHON_OVER_LIMIT}")
                error = (
                    f"error: {path}:1: the skip comment gives no reason. Write it after the marker, as in "
                    f'"{SKIP}, because each branch reads one field of the same record".\n'
                )
                self.assertEqual(self.run_script("--python", path), (1, "", error))

    def test_fails_on_a_skip_comment_above_a_function_within_the_limit(self) -> None:
        path = self.write("script.py", f"# Does f.\n{SKIP}, because it reads one record\ndef f():\n    pass\n")
        error = (
            f"error: {path}:2: the skip comment skips nothing, because f has a cognitive complexity of 0, and the "
            "limit is 15. Remove the comment.\n"
        )
        self.assertEqual(self.run_script("--python", path), (1, "", error))

    @needs_shfmt
    def test_fails_on_a_skip_comment_inside_a_bash_function(self) -> None:
        path = self.write("script", f"f() {{\n  {SKIP}, because it reads one record\n  :\n}}\n")
        self.assertEqual(self.run_script("--bash", path), (1, "", misplaced_error(path, 2)))

    def test_fails_on_a_skip_comment_above_a_python_function_inside_another(self) -> None:
        source = f"def f():\n    {SKIP}, because it reads one record\n    def g():\n        pass\n    return g()\n"
        path = self.write("script.py", source)
        self.assertEqual(self.run_script("--python", path), (1, "", misplaced_error(path, 2)))

    @needs_shfmt
    def test_fails_on_a_skip_comment_on_the_line_of_a_function(self) -> None:
        path = self.write("script", OVER_LIMIT.replace("f() {", f"f() {{ {SKIP}, because it reads one record", 1))
        self.assertEqual(self.run_script("--bash", path), (1, "", over_limit_error(path, 1) + misplaced_error(path, 1)))

    def test_fails_on_a_skip_comment_above_a_blank_line(self) -> None:
        path = self.write("script.py", f"{SKIP}, because each branch reads one field\n\n{PYTHON_OVER_LIMIT}")
        self.assertEqual(
            self.run_script("--python", path), (1, "", misplaced_error(path, 1) + over_limit_error(path, 3))
        )

    def test_reads_the_marker_only_as_a_whole_word(self) -> None:
        path = self.write("script.py", f"{SKIP}ping this one\n{PYTHON_OVER_LIMIT}")
        self.assertEqual(self.run_script("--python", path), (1, "", over_limit_error(path, 2)))

    def test_reads_the_marker_only_in_python_comments(self) -> None:
        path = self.write("script.py", f'MARKER = """\n{SKIP}, because it reads one record\n"""\n{PYTHON_OVER_LIMIT}')
        self.assertEqual(self.run_script("--python", path), (1, "", over_limit_error(path, 4)))

    @needs_shfmt
    def test_reads_the_marker_only_in_bash_comments(self) -> None:
        path = self.write("script", f"cat <<'EOF'\n{SKIP}\nEOF\nf() {{\n  :\n}}\n")
        self.assertEqual(self.run_script("--bash", path), (0, "", ""))

    @needs_shfmt
    def test_reports_each_function_over_the_limit_in_each_file(self) -> None:
        bash = self.write("script", OVER_LIMIT + OVER_LIMIT.replace("f()", "g()"))
        python = self.write("script.py", PYTHON_OVER_LIMIT)
        status, _, errors = self.run_script("--bash", bash, "--python", python)
        self.assertEqual(status, 1)
        self.assertEqual([line.split(": ")[2].split()[0] for line in errors.splitlines()], ["f", "g", "f"])

    def test_exits_2_for_a_file_it_cant_read(self) -> None:
        missing = str(self.folder / "missing.py")
        error = f"error: can't read {missing}: No such file or directory. Check the path.\n"
        self.assertEqual(self.run_script("--python", missing), (2, "", error))

    def test_exits_2_for_python_it_cant_parse(self) -> None:
        path = self.write("script.py", "def f(:\n")
        status, printed, errors = self.run_script("--python", path)
        self.assertEqual((status, printed), (2, ""))
        self.assertRegex(errors, rf"^error: Python can't parse {re.escape(path)}: .+ on line 1\. Fix that line\.\n$")

    @needs_shfmt
    def test_exits_2_for_bash_it_cant_parse(self) -> None:
        path = self.write("script", "f() {\n  if a; then\n}\n")
        status, printed, errors = self.run_script("--bash", path)
        self.assertEqual((status, printed), (2, ""))
        escaped = re.escape(path)
        pattern = rf"^error: shfmt can't parse {escaped}: {escaped}:\d+:\d+: .+\. Fix the line that it names\.\n$"
        self.assertRegex(errors, pattern)

    def test_exits_2_when_shfmt_is_not_installed(self) -> None:
        path = self.write("script", "f() { :; }\n")
        self.enterContext(mock.patch.dict(os.environ, {"PATH": str(self.folder)}))
        error = (
            "error: shfmt is not installed, and the check reads bash with it. Install it: "
            "https://github.com/mvdan/sh#shfmt\n"
        )
        self.assertEqual(self.run_script("--bash", path), (2, "", error))

    def test_runs_as_a_script(self) -> None:
        path = self.write("script.py", PYTHON_OVER_LIMIT)
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--python", path], capture_output=True, text=True, check=False, timeout=60
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("f has a cognitive complexity of 16", done.stderr)
