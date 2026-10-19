import contextlib
import importlib.machinery
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

import request_events

LAUNCHER = Path(__file__).with_name("claude")
# The scripts' file names have no .py, so the tests load them by their paths.
launcher = types.ModuleType("launcher")
importlib.machinery.SourceFileLoader("launcher", str(LAUNCHER)).exec_module(launcher)
receive_events = types.ModuleType("receive_events")
importlib.machinery.SourceFileLoader("receive_events", str(request_events.RECEIVER)).exec_module(receive_events)
# The attributes of an api_request event as Claude Code sends them, with the account's ids and email address.
REQUEST_ATTRIBUTES = {
    "user.email": {"stringValue": "someone@example.com"},
    "user.account_uuid": {"stringValue": "account-1"},
    "organization.id": {"stringValue": "organization-1"},
    "session.id": {"stringValue": "session-1"},
    "terminal.type": {"stringValue": "kitty"},
    "event.name": {"stringValue": "api_request"},
    "prompt.id": {"stringValue": "prompt-1"},
    "model": {"stringValue": "claude-haiku-5-5"},
    "input_tokens": {"intValue": 2},
    "output_tokens": {"intValue": 895},
    "cache_read_tokens": {"intValue": 13111},
    "cache_creation_tokens": {"intValue": "6947"},
    "cost_usd": {"doubleValue": 0.0015},
    "request_id": {"stringValue": "req_1"},
    "query_source": {"stringValue": "agent:custom"},
}
KEPT_REQUEST = {
    "session_id": "session-1",
    "request_id": "req_1",
    "model": "claude-haiku-5-5",
    "input_tokens": 2,
    "output_tokens": 895,
    "cache_read_tokens": 13111,
    "cache_creation_tokens": 6947,
}
PROMPT_ATTRIBUTES = {
    "user.email": {"stringValue": "someone@example.com"},
    "session.id": {"stringValue": "session-1"},
    "event.name": {"stringValue": "user_prompt"},
    "prompt": {"stringValue": "<REDACTED>"},
}
# A port where nothing listens, as the port file of a receiver that stopped gives
CLOSED_PORT = 1


def record(event: str, attributes: dict[str, Any]) -> dict[str, Any]:
    """An OTLP log record of a Claude Code event."""
    listed = [{"key": key, "value": value} for key, value in attributes.items()]
    return {"timeUnixNano": "1", "body": {"stringValue": f"claude_code.{event}"}, "attributes": listed}


def payload(*records: dict[str, Any]) -> dict[str, Any]:
    """The body of an OTLP request for logs, in JSON, as Claude Code sends it."""
    scope = {"scope": {"name": "com.anthropic.claude_code.events"}, "logRecords": list(records)}
    return {"resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": [scope]}]}


def post(port: int, body: bytes, path: str = request_events.LOGS_PATH) -> int:
    """Sends the body to the port as Claude Code does, and returns the status of the answer."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as error:
        with error:
            return error.code


def stop_receivers(folder: Path) -> None:
    """Stops the receivers that a test started for the folder, which keep running after the test otherwise."""
    subprocess.run(["pkill", "-TERM", "-f", f"receive-events {folder}$"], check=False)


class KeptRequestsTest(unittest.TestCase):
    def test_keeps_only_the_session_request_model_and_tokens_of_an_api_request_event(self) -> None:
        self.assertEqual(
            request_events.kept_requests(payload(record("api_request", REQUEST_ATTRIBUTES))), [KEPT_REQUEST]
        )

    def test_ignores_the_other_events(self) -> None:
        records = [
            record("user_prompt", PROMPT_ATTRIBUTES),
            record("assistant_response", {**REQUEST_ATTRIBUTES, "event.name": {"stringValue": "assistant_response"}}),
            record("api_request", REQUEST_ATTRIBUTES),
        ]
        self.assertEqual(request_events.kept_requests(payload(*records)), [KEPT_REQUEST])

    def test_counts_a_missing_token_count_as_0(self) -> None:
        attributes = {key: value for key, value in REQUEST_ATTRIBUTES.items() if key != "cache_creation_tokens"}
        [kept] = request_events.kept_requests(payload(record("api_request", attributes)))
        self.assertEqual(kept["cache_creation_tokens"], 0)

    def test_keeps_nothing_from_a_body_without_records(self) -> None:
        self.assertEqual(request_events.kept_requests({}), [])


class OutputTokensTest(unittest.TestCase):
    def test_gives_the_output_tokens_of_each_request_by_its_id(self) -> None:
        lines = [KEPT_REQUEST, {**KEPT_REQUEST, "request_id": "req_2", "output_tokens": 40}]
        log = "".join(json.dumps(line) + "\n" for line in lines)
        self.assertEqual(request_events.output_tokens(log), {"req_1": 895, "req_2": 40})

    def test_gives_nothing_for_an_empty_log(self) -> None:
        self.assertEqual(request_events.output_tokens(""), {})


class FolderTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve() / "loop"
        self.folder.mkdir()

    def serve(self, folder: Path) -> request_events.Receiver:
        """A receiver for the folder that runs in this process until the test ends."""
        receiver = request_events.open_receiver(folder)
        threading.Thread(target=receiver.serve_forever, daemon=True).start()
        self.addCleanup(receiver.server_close)
        self.addCleanup(receiver.shutdown)
        return receiver

    def requests(self) -> list[dict[str, Any]]:
        log = self.folder / request_events.REQUESTS_FILE
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def stop_receivers_at_the_end(self) -> None:
        self.addCleanup(stop_receivers, self.folder)

    def block_the_port_file(self) -> None:
        """Makes the port file name a port where nothing listens, and keeps a new receiver from writing it."""
        port_file = self.folder / request_events.PORT_FILE
        port_file.write_text(f"{CLOSED_PORT}\n")
        port_file.chmod(0o444)


class ReceiverTest(FolderTest):
    def test_appends_the_kept_fields_of_each_api_request_event(self) -> None:
        receiver = self.serve(self.folder)
        first = payload(record("user_prompt", PROMPT_ATTRIBUTES), record("api_request", REQUEST_ATTRIBUTES))
        second_attributes = {**REQUEST_ATTRIBUTES, "request_id": {"stringValue": "req_2"}}
        second = payload(record("api_request", second_attributes))
        self.assertEqual(post(receiver.port, json.dumps(first).encode()), 200)
        self.assertEqual(post(receiver.port, json.dumps(second).encode()), 200)
        self.assertEqual(self.requests(), [KEPT_REQUEST, {**KEPT_REQUEST, "request_id": "req_2"}])

    def test_writes_its_port_to_the_port_file(self) -> None:
        receiver = self.serve(self.folder)
        self.assertEqual((self.folder / request_events.PORT_FILE).read_text(), f"{receiver.port}\n")

    def test_answers_a_get_with_its_folder(self) -> None:
        receiver = self.serve(self.folder)
        with urllib.request.urlopen(f"http://127.0.0.1:{receiver.port}/", timeout=5) as answer:
            self.assertEqual(answer.read().decode(), str(self.folder))

    def test_refuses_a_body_that_is_not_json_and_a_signal_other_than_logs(self) -> None:
        receiver = self.serve(self.folder)
        events = json.dumps(payload(record("api_request", REQUEST_ATTRIBUTES))).encode()
        self.assertEqual(post(receiver.port, b"not json"), 400)
        self.assertEqual(post(receiver.port, events, path="/v1/metrics"), 404)
        self.assertEqual(self.requests(), [])


class ReceiverPortTest(FolderTest):
    def test_gives_the_port_of_the_receiver_that_runs_for_the_folder(self) -> None:
        receiver = self.serve(self.folder)
        self.assertEqual(request_events.receiver_port(self.folder), receiver.port)
        self.assertFalse((self.folder / request_events.LOG_FILE).exists())

    def test_starts_a_receiver_that_keeps_running_when_none_runs(self) -> None:
        self.stop_receivers_at_the_end()
        port = request_events.receiver_port(self.folder)
        self.assertEqual(request_events.receiver_port(self.folder), port)
        self.assertEqual(post(port, json.dumps(payload(record("api_request", REQUEST_ATTRIBUTES))).encode()), 200)
        self.assertEqual(self.requests(), [KEPT_REQUEST])

    def test_starts_a_receiver_when_the_port_is_another_folders_receiver(self) -> None:
        self.stop_receivers_at_the_end()
        other = self.folder.with_name("other")
        other.mkdir()
        others_port = self.serve(other).port
        (self.folder / request_events.PORT_FILE).write_text(f"{others_port}\n")
        port = request_events.receiver_port(self.folder)
        self.assertNotEqual(port, others_port)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as answer:
            self.assertEqual(answer.read().decode(), str(self.folder))

    def test_creates_the_folder(self) -> None:
        self.folder.rmdir()
        self.stop_receivers_at_the_end()
        port = request_events.receiver_port(self.folder)
        self.assertEqual((self.folder / request_events.PORT_FILE).read_text(), f"{port}\n")

    def test_fails_and_names_the_log_when_the_receiver_does_not_start(self) -> None:
        self.block_the_port_file()
        with self.assertRaises(request_events.ReceiverError) as raised:
            request_events.receiver_port(self.folder)
        log = self.folder / request_events.LOG_FILE
        self.assertEqual(
            str(raised.exception),
            f"The receiver of Claude Code's events didn't start, and ended with code 1. {log} says why.",
        )
        self.assertIn("PermissionError", log.read_text())


class TelemetryEnvironmentTest(unittest.TestCase):
    def test_turns_on_only_the_events_and_sends_them_to_the_port_over_http_in_json(self) -> None:
        self.assertEqual(
            request_events.telemetry_environment(4318),
            {
                "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
                "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://127.0.0.1:4318/v1/logs",
            },
        )


class LauncherTest(FolderTest):
    """Runs scripts/loop/claude with a stand-in for claude first on PATH, which writes its arguments and the
    variables that send events to claude.json in the loop folder."""

    def setUp(self) -> None:
        super().setUp()
        bin_folder = self.folder.with_name("bin")
        bin_folder.mkdir()
        standin = bin_folder / "claude"
        standin.write_text(
            f"#!{sys.executable} -S\n"
            "import json, os, sys\n"
            "names = [name for name in os.environ if name.startswith(('OTEL_', 'CLAUDE_CODE_ENABLE'))]\n"
            "seen = {'args': sys.argv[1:], 'env': {name: os.environ[name] for name in names}}\n"
            "open(os.path.join(os.environ['LOOP_DIR'], 'claude.json'), 'w').write(json.dumps(seen))\n"
        )
        standin.chmod(0o755)
        environment = {"PATH": f"{bin_folder}{os.pathsep}{os.environ['PATH']}", "LOOP_DIR": str(self.folder)}
        self.environment = {
            name: value for name, value in os.environ.items() if not name.startswith("OTEL_")
        } | environment

    def launch(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(LAUNCHER), *arguments],
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def test_runs_claude_with_its_arguments_and_sends_the_events_to_the_loops_receiver(self) -> None:
        self.stop_receivers_at_the_end()
        done = self.launch("--resume", "a b")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        seen = json.loads((self.folder / "claude.json").read_text())
        port = int((self.folder / request_events.PORT_FILE).read_text())
        self.assertEqual(seen, {"args": ["--resume", "a b"], "env": request_events.telemetry_environment(port)})
        self.assertEqual(request_events.receiver_port(self.folder), port)

    def test_fails_without_running_claude_when_the_receiver_does_not_start(self) -> None:
        self.block_the_port_file()
        errors = io.StringIO()
        with mock.patch.dict(os.environ, self.environment), contextlib.redirect_stderr(errors):
            status = launcher.main([])
        log = self.folder / request_events.LOG_FILE
        self.assertEqual(
            (status, errors.getvalue()),
            (1, f"error: The receiver of Claude Code's events didn't start, and ended with code 1. {log} says why.\n"),
        )
        self.assertFalse((self.folder / "claude.json").exists())

    def test_fails_when_claude_is_not_installed(self) -> None:
        self.stop_receivers_at_the_end()
        empty = self.folder.with_name("empty")
        empty.mkdir()
        errors = io.StringIO()
        with mock.patch.dict(os.environ, {**self.environment, "PATH": str(empty)}), contextlib.redirect_stderr(errors):
            status = launcher.main([])
        self.assertEqual(
            (status, errors.getvalue()),
            (1, "error: claude isn't installed. Install Claude Code, and run the script again.\n"),
        )


class ReceiverScriptTest(FolderTest):
    def test_prints_the_port_of_the_receiver_that_goes_on_in_the_background(self) -> None:
        printed = io.StringIO()
        # fork gives the parent the pid of its child, so with a pid the script does only what the parent does.
        with mock.patch.object(os, "fork", return_value=1), contextlib.redirect_stdout(printed):
            receive_events.main([str(self.folder)])
        self.assertEqual(printed.getvalue(), (self.folder / request_events.PORT_FILE).read_text())
