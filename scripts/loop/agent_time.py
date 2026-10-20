"""Splits an agent's time, from the first line of its transcript to the last, into the model's time, the time of its
tool calls by kind, and its waits. A tool call's time runs from the line with its tool_use to the line with its
result, and while several calls run at once, the one that started first counts. A wait runs to a line that brings the
agent a task notification, which ends a wait for a background task, or a message, which ends a wait for the
orchestrator. The model's time is the rest, also when the user interrupts the agent."""

import datetime as dt
import itertools
import re
from pathlib import Path
from typing import Any

import transcripts

MODEL = "model"
CHECK = "scripts/check"
TEST_FILE = "single test files"
CI = "waiting for CI"
SLEEP = "sleeps"
COMMAND = "other commands"
TOOL = "other tools"
BACKGROUND_WAIT = "waiting for background tasks"
ORCHESTRATOR_WAIT = "waiting for the orchestrator"
# The parts in the order that scripts/loop/report gives them
PARTS = (MODEL, CHECK, TEST_FILE, CI, SLEEP, COMMAND, TOOL, BACKGROUND_WAIT, ORCHESTRATOR_WAIT)

# The origin that Claude Code gives the line that tells an agent that one of its background tasks ended or printed
TASK_NOTIFICATION = "task-notification"
# A here-document, whose body is text for a command rather than commands. Group 3 holds the rest of its first line.
HERE_DOCUMENT = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n(.*?\n)?[ \t]*\2[ \t]*(?=\n|$)", re.DOTALL)
# A command substitution with no parentheses inside, whose command runs also inside double quotes
COMMAND_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)")
# Quoted text, which is an argument rather than commands
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
# What separates the simple commands of a command line, such as && and the start of a subshell
SEPARATOR = re.compile(r"&&|\|\||\$\(|[;|&\n(){}`]")
# The words that can come before a simple command's program, such as a loop's keyword, or bash before a script
PREFIXES = frozenset(
    ("do", "then", "else", "if", "while", "until", "!", "time", "env", "exec", "command", "bash", "sh")
)
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
# The names of this project's test files, such as scripts/start-issue.test and scripts/loop/test_report.py, and of a
# test module that python -m unittest takes, such as test_report
TEST_FILE_NAME = re.compile(r"[^/\s]+\.test|test_\w+(\.py)?")
# The programs that run the test file that follows them, as in python3 scripts/loop/test_report.py
TEST_RUNNERS = frozenset(("python", "python3", "pytest"))
CHECK_SCRIPT = re.compile(r"(.*/)?scripts/check")
WAIT_FOR_PULL_REQUESTS = re.compile(r"(.*/)?scripts/wait-for-pull-requests")
CI_WATCHES = (["gh", "pr", "checks"], ["gh", "run", "watch"])
CI_LOOKS = (["gh", "pr"], ["gh", "run"])


def read_split(file: Path) -> dict[str, float]:
    return split(transcripts.transcript_entries(file))


def split(entries: list[dict[str, Any]]) -> dict[str, float]:
    """The seconds of each part of the time from the first entry to the last."""
    seconds = dict.fromkeys(PARTS, 0.0)
    # The kind of each tool call that runs, by its tool_use's id, in the order that the calls started
    running: dict[str, str] = {}
    for entry, following in itertools.pairwise(entries):
        running.update((use["id"], tool_kind(use)) for use in transcripts.blocks(entry, "tool_use"))
        for result in transcripts.blocks(entry, "tool_result"):
            running.pop(result.get("tool_use_id", ""), None)
        seconds[part_until(following, running)] += (moment(following) - moment(entry)).total_seconds()
    return seconds


def part_until(following: dict[str, Any], running: dict[str, str]) -> str:
    """The part that the time until the following entry counts in, while the tool calls run."""
    if running:
        return next(iter(running.values()))
    if not transcripts.is_prompt(following) or transcripts.is_interruption(following):
        return MODEL
    origin = following.get("origin") or {}
    return BACKGROUND_WAIT if origin.get("kind") == TASK_NOTIFICATION else ORCHESTRATOR_WAIT


def moment(entry: dict[str, Any]) -> dt.datetime:
    return dt.datetime.fromisoformat(entry["timestamp"])


def tool_kind(use: dict[str, Any]) -> str:
    if use.get("name") != "Bash":
        return TOOL
    return command_kind(str(use.get("input", {}).get("command", "")))


def command_kind(command: str) -> str:
    """The kind of a Bash command, from the programs that it runs. A command that sleeps between looks at a pull
    request or a CI run waits for CI."""
    programs = simple_commands(command)
    sleeps = any(words[0] == "sleep" for words in programs)
    if any(watches_ci(words) for words in programs) or (sleeps and any(words[:2] in CI_LOOKS for words in programs)):
        return CI
    if any(CHECK_SCRIPT.fullmatch(words[0]) for words in programs):
        return CHECK
    if any(runs_test_file(words) for words in programs):
        return TEST_FILE
    return SLEEP if sleeps else COMMAND


def simple_commands(command: str) -> list[list[str]]:
    """The words of each simple command in a command line, from its program on."""
    found = []
    for text in command_texts(command):
        for part in SEPARATOR.split(QUOTED.sub("''", text)):
            words = program_on(part.split())
            if words:
                found.append(words)
    return found


def command_texts(command: str) -> list[str]:
    """The command line without the bodies of its here-documents, and the command of each command substitution in
    it, each apart, so that leaving out the quoted text keeps the commands that run inside double quotes."""
    substitutions: list[str] = []

    def take(substitution: re.Match[str]) -> str:
        substitutions.append(substitution[1])
        return "''"

    text = HERE_DOCUMENT.sub(r"\3", command)
    # Taking out a substitution can leave the one around it without parentheses inside.
    while COMMAND_SUBSTITUTION.search(text):
        text = COMMAND_SUBSTITUTION.sub(take, text)
    return [text, *substitutions]


def program_on(words: list[str]) -> list[str]:
    """The words of a simple command from its program on, after the words before it, such as uv run and its
    options before python."""
    while words and (words[0] in PREFIXES or ASSIGNMENT.fullmatch(words[0])):
        words = words[1:]
    if words[:2] == ["uv", "run"]:
        return program_on(list(itertools.dropwhile(lambda word: word.startswith("-"), words[2:])))
    return words


def watches_ci(words: list[str]) -> bool:
    return words[:3] in CI_WATCHES or bool(WAIT_FOR_PULL_REQUESTS.fullmatch(words[0]))


def runs_test_file(words: list[str]) -> bool:
    program, *arguments = words
    if is_test_file(program):
        return True
    return program in TEST_RUNNERS and any(is_test_file(argument) for argument in arguments)


def is_test_file(word: str) -> bool:
    return bool(TEST_FILE_NAME.fullmatch(word.rsplit("/", 1)[-1]))
