"""Reads the full model ID behind each of Claude Code's aliases, and tells which of them no retrospective has tested.

Claude Code has no command that lists its models, so the meter sends one short request for each alias and reads the
model IDs from the result's modelUsage. It does this at most once a day, and keeps the answer in models.json in the
loop folder. TESTED_MODELS lists each model ID that a retrospective tested or that the maintainer chose not to test,
with the number of the pull request that added it."""

import datetime as dt
import json
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict

from accounts import Account, claude_environment

ALIASES = ("opus", "sonnet", "haiku", "fable")
READ_EVERY = dt.timedelta(days=1)
NAME = "models.json"
TESTED_MODELS = Path(__file__).resolve().parents[2] / ".claude" / "tested-models.json"
TIMEOUT_SECONDS = 120
PROMPT = "Reply with the word OK."


class Reading(TypedDict):
    at: str
    # The model IDs in the modelUsage of each alias's request
    models: dict[str, list[str]]
    # Why an alias's request gave no model ID
    problems: dict[str, str]


class NoModel(Exception):
    """Why an alias's request gave no model ID, with what to do about it."""


def current(loop_folder: Path, account: Account, reading_time: dt.datetime) -> Reading:
    """The reading in the loop folder, or a new one on the account when that reading is a day old or there is none. A
    reading whose requests failed counts too, so that a failing request runs at most once a day."""
    file = loop_folder / NAME
    if file.exists():
        last: Reading = json.loads(file.read_text())
        if reading_time - dt.datetime.fromisoformat(last["at"]) < READ_EVERY:
            return last
    reading = read(account, reading_time)
    file.write_text(json.dumps(reading) + "\n")
    return reading


def read(account: Account, reading_time: dt.datetime) -> Reading:
    models: dict[str, list[str]] = {}
    problems: dict[str, str] = {}
    for alias in ALIASES:
        try:
            models[alias] = ask(alias, account)
        except NoModel as problem:
            problems[alias] = str(problem)
    return {"at": reading_time.isoformat(timespec="seconds"), "models": models, "problems": problems}


def ask(alias: str, account: Account) -> list[str]:
    command = ["claude", "-p", "--model", alias, "--output-format", "json", "--max-turns", "1", "--tools", ""]
    command += ["--no-session-persistence", PROMPT]
    # An empty folder has no project settings or hooks that would run.
    with tempfile.TemporaryDirectory() as folder:
        try:
            done = subprocess.run(
                command,
                cwd=folder,
                env=claude_environment(account),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as error:
            raise NoModel("claude isn't installed. Install Claude Code.") from error
        except subprocess.TimeoutExpired as error:
            raise NoModel(f"claude didn't answer within {TIMEOUT_SECONDS} seconds.") from error
    return model_ids(done.stdout + done.stderr, alias)


def model_ids(output: str, alias: str) -> list[str]:
    try:
        usage = json.loads(output).get("modelUsage")
    except ValueError, AttributeError:
        usage = None
    if not isinstance(usage, dict) or not usage:
        first_line = next((line.strip() for line in output.splitlines() if line.strip()), "nothing")
        raise NoModel(
            f'claude -p --model {alias} --output-format json printed no modelUsage, and began with "{first_line}". '
            "Run it in an empty folder to see why."
        )
    return sorted(usage)


def tested() -> set[str]:
    return {entry["id"] for entry in json.loads(TESTED_MODELS.read_text())["models"]}


def notes(reading: Reading, tested_ids: set[str]) -> list[str]:
    """The parts of the meter's machine line: one for each model ID of an alias that tested_ids lacks, and one for
    each alias whose request gave no model ID."""
    parts = []
    for alias, ids in reading["models"].items():
        parts += [
            f"retrospective due: {alias} names {model}, which no retrospective tested"
            for model in ids
            if model not in tested_ids
        ]
    parts += [f"model of {alias} unknown: {problem}" for alias, problem in reading["problems"].items()]
    return parts
