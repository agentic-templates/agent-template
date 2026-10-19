"""Keeps the exact tokens of each request that Claude Code makes to the API, from the claude_code.api_request events
that it sends over OpenTelemetry. An agent's transcript gives most replies' output tokens as of the reply's start, so
scripts/loop/report takes them from these events instead.

scripts/loop/receive-events receives the events of the loop's Claude Code sessions, and appends a line for each
request to requests.jsonl in the loop folder, with these fields of its event:

  session_id      the id of the session that made the request
  request_id      the id that the transcript's line of the request gives as requestId
  model           the model, such as claude-opus-5-5
  input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
                  the request's tokens

It keeps nothing else from the events, because each one also carries the account's email address and ids.
"""

import fcntl
import http.client
import http.server
import json
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, override

REQUESTS_FILE = "requests.jsonl"
# The receiver writes its port here, and its errors to the log.
PORT_FILE = "receiver.port"
LOG_FILE = "receiver.log"
# The file that the scripts that start the receiver lock, so that two of them don't start two receivers.
START_LOCK_FILE = "receiver.lock"
RECEIVER = Path(__file__).with_name("receive-events")
HOST = "127.0.0.1"
LOGS_PATH = "/v1/logs"
REQUEST_EVENT = "claude_code.api_request"
# The fields that the receiver keeps, under the names of the event's attributes
KEPT_ATTRIBUTES = {
    "session.id": "session_id",
    "request_id": "request_id",
    "model": "model",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_tokens",
    "cache_creation_tokens": "cache_creation_tokens",
}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
# The variables that would make Claude Code send more than the events: the prompts, the tools' details, the metrics or
# the traces
UNSET_VARIABLES = ("OTEL_LOG_USER_PROMPTS", "OTEL_LOG_TOOL_DETAILS", "OTEL_METRICS_EXPORTER", "OTEL_TRACES_EXPORTER")
ANSWER_TIMEOUT_SECONDS = 2


class ReceiverError(Exception):
    """The receiver didn't start, with where to find out why."""


def kept_requests(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The kept fields of each api_request event in the body of an OTLP request for logs, in JSON."""
    requests = []
    for resource in payload.get("resourceLogs", []):
        for scope in resource.get("scopeLogs", []):
            for record in scope.get("logRecords", []):
                if record.get("body", {}).get("stringValue") == REQUEST_EVENT:
                    requests.append(kept_fields(record.get("attributes", [])))
    return requests


def kept_fields(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    """The kept fields of an event's attributes, with each token count as a number. OTLP's JSON gives a number as a
    string or as a number."""
    values = {attribute["key"]: attribute_value(attribute["value"]) for attribute in attributes}
    kept = {name: values.get(key) for key, name in KEPT_ATTRIBUTES.items()}
    for field in TOKEN_FIELDS:
        kept[field] = int(kept[field] or 0)
    return kept


def attribute_value(value: dict[str, Any]) -> Any:
    """The value of an OTLP attribute, which holds it under a key that names its type, such as stringValue."""
    return next(iter(value.values()), None)


def output_tokens(log: str) -> dict[str, int]:
    """The output tokens of each request in the lines of requests.jsonl, by request id."""
    counts = {}
    for line in log.splitlines():
        request = json.loads(line)
        counts[request["request_id"]] = request["output_tokens"]
    return counts


class Receiver(http.server.ThreadingHTTPServer):
    """Listens on a free port of 127.0.0.1, and appends the kept fields of each api_request event to requests.jsonl
    in the folder. It answers a GET with the folder's path, so that a script can tell that it receives for that
    folder."""

    def __init__(self, folder: Path) -> None:
        super().__init__((HOST, 0), ReceiverHandler)
        self.folder = folder.resolve()
        self.lock = threading.Lock()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def append(self, requests: list[dict[str, Any]]) -> None:
        text = "".join(json.dumps(request) + "\n" for request in requests)
        with self.lock, (self.folder / REQUESTS_FILE).open("a") as handle:
            handle.write(text)


class ReceiverHandler(http.server.BaseHTTPRequestHandler):
    server: Receiver

    def do_GET(self) -> None:
        self.answer(200, str(self.server.folder).encode(), "text/plain")

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path != LOGS_PATH:
            self.answer(404, b"{}")
            return
        try:
            payload = json.loads(body)
        except ValueError:
            self.answer(400, b"{}")
            return
        self.server.append(kept_requests(payload))
        self.answer(200, b"{}")

    def answer(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Logs nothing for each request, so that the log holds only errors."""


def open_receiver(folder: Path) -> Receiver:
    """A receiver for the folder, whose port the folder's port file gives."""
    receiver = Receiver(folder)
    (folder / PORT_FILE).write_text(f"{receiver.port}\n")
    return receiver


def receiver_port(folder: Path) -> int:
    """The port of the folder's receiver, which this starts if it isn't running. The receiver keeps running after
    this process ends."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / START_LOCK_FILE).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return answering_port(folder) or start_receiver(folder)


def answering_port(folder: Path) -> int | None:
    """The port in the folder's port file, if the receiver for that folder answers on it, or None. Another program
    may hold the port by now, and answer with anything."""
    port_file = folder / PORT_FILE
    if not port_file.exists():
        return None
    try:
        port = int(port_file.read_text())
        with urllib.request.urlopen(f"http://{HOST}:{port}/", timeout=ANSWER_TIMEOUT_SECONDS) as answer:
            is_receiver = answer.read() == str(folder.resolve()).encode()
    except OSError, ValueError, http.client.HTTPException:
        return None
    return port if is_receiver else None


def start_receiver(folder: Path) -> int:
    """Starts a receiver for the folder, which keeps running after this process ends, and returns its port."""
    with (folder / LOG_FILE).open("a") as log:
        started = subprocess.run(
            [sys.executable, str(RECEIVER), str(folder)],
            cwd=folder,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            check=False,
        )
    if started.returncode != 0:
        raise ReceiverError(
            f"The receiver of Claude Code's events didn't start, and ended with code {started.returncode}. "
            f"{folder / LOG_FILE} says why."
        )
    return int(started.stdout)


def with_events(environment: Mapping[str, str], port: int) -> dict[str, str]:
    """The environment with the variables that make Claude Code send its events, and only them, to the receiver on
    the port."""
    kept = {name: value for name, value in environment.items() if name not in UNSET_VARIABLES}
    return kept | event_variables(port)


def event_variables(port: int) -> dict[str, str]:
    """The variables that make Claude Code send its events to the receiver on the port."""
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"http://{HOST}:{port}{LOGS_PATH}",
    }
