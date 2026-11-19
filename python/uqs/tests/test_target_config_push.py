"""`uqs target config push` (#2786): the queue the browser's checks make, the
bounds copied from it, urllib's reading of an answer, and --json's refusal.

The push itself, against the frontend's real app, is tested where fastapi is
installed: python/uqf_frontend/tests/test_config_push_cli.py.
"""

from __future__ import annotations

import http.server
import json
import re
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqf_frontend import remote_helper
from uqs.cli import app
from uqs.cli.target import target_config_push
from uqs.deploy import portal_push
from uqs.paths import UqsError

SERVICES_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "services.ts"
CLIENT_TS = SERVICES_TS.parent / "client.ts"


@pytest.fixture
def logged():
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING", format="{message}")
    yield lines
    logger.remove(sink)


def test_json_without_yes_is_refused_and_sends_nothing(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(target_config_push, "SEND", lambda method, url, *a: sent.append(url))
    monkeypatch.setenv(portal_push.TOKEN_ENV, "t")
    (tmp_path / "a.csv").write_bytes(b"a\n")
    argv = ["target", "config", "push", "box", str(tmp_path / "a.csv"), "--json"]
    monkeypatch.setattr("sys.argv", ["uqs", *argv])  # json_requested reads it
    result = CliRunner().invoke(app, argv, input="y\n")
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is False and "--yes" in doc["error"]
    assert sent == []


def test_without_the_token_nothing_is_sent(tmp_path, monkeypatch, logged):
    sent: list[str] = []
    monkeypatch.setattr(target_config_push, "SEND", lambda method, url, *a: sent.append(url))
    monkeypatch.delenv(portal_push.TOKEN_ENV, raising=False)
    (tmp_path / "a.csv").write_bytes(b"a\n")
    argv = ["target", "config", "push", "box", str(tmp_path / "a.csv"), "--yes"]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 1
    assert any(f"set {portal_push.TOKEN_ENV}" in line for line in logged)
    assert sent == []


# ------------------------------------------------------------ the queue, as the browser checks it


def test_a_queue_past_the_browsers_bounds_is_refused_whole(tmp_path):
    files = []
    for n in range(portal_push.MAX_FILES + 1):
        (tmp_path / f"f{n}").write_bytes(b"")
        files.append((tmp_path / f"f{n}", f"f{n}"))
    with pytest.raises(UqsError, match="at most 500 files; this would make 501"):
        portal_push.queue(files, "shared/config")


def test_a_path_queued_twice_is_invalid_the_second_time(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x").write_bytes(b"1")
    (tmp_path / "x").write_bytes(b"2")
    picked = portal_push.expand([tmp_path / "x", tmp_path / "a" / "x"])
    items = portal_push.queue(picked, "shared/config")
    assert [(i.outcome, i.message) for i in items] == [
        ("queued", ""),
        ("invalid", "already queued"),
    ]


def test_into_may_not_leave_shared_config(tmp_path):
    (tmp_path / "x").write_bytes(b"1")
    picked = portal_push.expand([tmp_path / "x"])
    (item,) = portal_push.queue(picked, portal_push.config_dir("../../etc"))
    assert item.outcome == "invalid" and "'..'" in item.message


def _ts_const(path: Path, name: str) -> str:
    match = re.search(rf"export const {name} = (.+?);", path.read_text())
    assert match, f"{name} is not in {path}"
    return match.group(1)


def test_the_bounds_are_the_browsers():
    """The browser's own queue bounds and timeouts, copied: held equal here."""

    def number(path: Path, name: str) -> int:
        expr = _ts_const(path, name)
        assert re.fullmatch(r"[\d\s*]+", expr), expr
        return eval(expr)  # noqa: S307 - digits and * only

    assert number(SERVICES_TS, "UPLOAD_MAX_FILES") == portal_push.MAX_FILES
    assert portal_push.MAX_FILES == remote_helper.MAX_PUSH_FILES
    assert number(SERVICES_TS, "UPLOAD_MAX_TOTAL_BYTES") == portal_push.MAX_TOTAL_BYTES
    assert number(SERVICES_TS, "UPLOAD_TIMEOUT_MS") == portal_push.UPLOAD_TIMEOUT * 1000
    assert number(CLIENT_TS, "DEFAULT_REQUEST_TIMEOUT_MS") == portal_push.REQUEST_TIMEOUT * 1000


# ------------------------------------------------------------ urllib, against a real socket


class _Answer(http.server.BaseHTTPRequestHandler):
    def do_PUT(self):  # noqa: N802 - http.server's name
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"error": "AlreadyExists", "detail": "exists", "transient": False})
        self.send_response(409)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format, *args):  # noqa: A002 - the stdlib's name
        pass


def test_urllib_reads_an_error_answer_and_a_refused_connection_is_lost():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Answer)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/x"
    reply = portal_push.urllib_send("PUT", url, {}, b"bytes", 5.0)
    thread.join(5)
    server.server_close()
    assert reply.status == 409 and reply.payload["detail"] == "exists"
    with pytest.raises(portal_push.Lost):
        portal_push.urllib_send("PUT", url, {}, b"bytes", 5.0)
