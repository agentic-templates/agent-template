"""Reads the accounts that the loop builds with from .loop/accounts.json, which scripts/loop/meter --help describes."""

import json
import os
from pathlib import Path
from typing import TypedDict

TOOLS = ("claude", "codex")
CLAUDE_VARIABLE_PREFIXES = ("ANTHROPIC_", "CLAUDE")


class Account(TypedDict):
    name: str
    tool: str
    env: dict[str, str]


class AccountsError(Exception):
    """A problem with the accounts file, with what to do about it."""


def load_accounts(file: Path) -> list[Account]:
    """The accounts that the file lists. Without the file, the list holds the Claude account that this machine is
    signed in to."""
    if not file.exists():
        return [{"name": "claude", "tool": "claude", "env": {}}]
    example = '{"accounts": [{"name": "claude", "tool": "claude"}]}'
    try:
        content = json.loads(file.read_text())
    except ValueError as error:
        raise AccountsError(f"{file} isn't valid JSON: {error}. Fix it, as in {example}.") from error
    entries = content.get("accounts") if isinstance(content, dict) else None
    if not isinstance(entries, list) or not entries:
        raise AccountsError(f'{file} has no list of accounts under "accounts". Add one, as in {example}.')
    return [account_from(entry, file) for entry in entries]


def account_from(entry: object, file: Path) -> Account:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or entry.get("tool") not in TOOLS:
        raise AccountsError(
            f'{file} lists the account {json.dumps(entry)}, which needs a "name" and a "tool" that is "claude" or '
            '"codex". Fix the account.'
        )
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(value, str) for value in env.values()):
        raise AccountsError(
            f'{file} gives the account {entry["name"]} an "env" that isn\'t an object of strings, such as '
            '{"CODEX_HOME": "~/.codex"}. Fix the account.'
        )
    return {"name": entry["name"], "tool": entry["tool"], "env": {k: os.path.expanduser(v) for k, v in env.items()}}


def claude_environment(account: Account) -> dict[str, str]:
    """The environment that runs claude on the account: this process's variables without those whose names start with
    one of CLAUDE_VARIABLE_PREFIXES, and the account's variables. The caller's CLAUDE_CONFIG_DIR or ANTHROPIC_API_KEY
    would run claude on another account or bill an API key, and the variables of a Claude Code session would make
    claude think that it runs inside that session."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith(CLAUDE_VARIABLE_PREFIXES)}
    return environment | account["env"]
