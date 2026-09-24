"""Tests for the per-process responsiveness probe (stack/probe.py).

Real sockets on localhost, each standing in for one way a q process can
answer - or not - the kdb+ handshake. No q is needed: the handshake is the
whole protocol under test, and these servers speak exactly that much of it.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from uqs.stack import probe
from uqs.stack.probe import ProbeResult


@contextmanager
def _server(handle: Callable[[socket.socket], None]) -> Iterator[int]:
    """A one-connection-at-a-time TCP server on a free port; yields the port."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.05)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            with conn:
                handle(conn)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join()
        listener.close()


def _answers(conn: socket.socket) -> None:
    """q accepting the login: read the credentials, reply one capability byte."""
    conn.recv(64)
    conn.sendall(bytes([3]))


def _hangs(conn: socket.socket) -> None:
    """q busy in its main loop: the connection is accepted, nothing replies."""
    conn.recv(64)
    time.sleep(0.5)


def _rejects(conn: socket.socket) -> None:
    """q refusing the credentials: it closes without a reply."""
    conn.recv(64)


def _resets(conn: socket.socket) -> None:
    """A process past its connection cap: the handle is reset."""
    conn.recv(64)
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_process_that_answers_is_ok_with_its_round_trip():
    with _server(_answers) as port:
        got = probe.probe(port, host="127.0.0.1", timeout=0.5)
    assert got.outcome == "ok"
    assert got.millis is not None and got.millis < 500
    assert got.cell().endswith("ms")


def test_a_process_that_does_not_answer_in_time_is_a_timeout_not_a_hang():
    """The reason this exists: a busy q accepts the TCP connection and then
    says nothing. The probe must give up at its deadline."""
    with _server(_hangs) as port:
        start = time.monotonic()
        got = probe.probe(port, host="127.0.0.1", timeout=0.1)
        elapsed = time.monotonic() - start
    assert got == ProbeResult("timeout")
    assert elapsed < 0.4, "one deadline for connect, send and reply together"


def test_a_refused_login_is_rejected_not_ok():
    with _server(_rejects) as port:
        assert probe.probe(port, host="127.0.0.1").outcome == "rejected"


def test_a_reset_connection_is_reported_as_such():
    with _server(_resets) as port:
        assert probe.probe(port, host="127.0.0.1").outcome == "reset"


def test_nothing_listening_is_refused():
    assert probe.probe(_free_port(), host="127.0.0.1").outcome == "refused"


def test_every_process_is_probed_at_once_not_one_after_another():
    """Ten hung processes at 0.2s each must cost about 0.2s, not 2s."""
    with _server(_hangs) as port:
        start = time.monotonic()
        got = probe.probe_all({f"p{i}": port for i in range(10)}, timeout=0.2, host="127.0.0.1")
        elapsed = time.monotonic() - start
    assert {r.outcome for r in got.values()} == {"timeout"}
    assert elapsed < 1.0


def _row(name: str, status: str, port: str, source: str) -> dict[str, str]:
    return {"Process": name, "Status": status, "Port": port, "PortSource": source}


def test_only_up_processes_on_a_reported_port_are_probed(monkeypatch):
    asked: list[dict[str, int]] = []

    def fake(targets, timeout, **_kw):
        asked.append(targets)
        return {name: ProbeResult("ok", 3.0) for name in targets}

    monkeypatch.setattr(probe, "probe_all", fake)
    rows = [
        _row("rdb1", "up", "6052", "reported"),
        _row("hdb1", "down", "6053", "configured"),
        _row("odd1", "up", "", "unknown"),
    ]
    assert probe.attach_probe_column(rows, 0.5, None) == []
    assert asked == [{"rdb1": 6052}]
    assert [r["Responds"] for r in rows] == ["3ms", "-", "-"]


def test_the_silent_processes_are_returned_for_the_warning_line(monkeypatch):
    monkeypatch.setattr(
        probe,
        "probe_all",
        lambda targets, timeout, **_kw: {"a": ProbeResult("ok", 1.0), "b": ProbeResult("timeout")},
    )
    rows = [_row("a", "up", "1", "reported"), _row("b", "up", "2", "reported")]
    assert probe.attach_probe_column(rows, 0.5, None) == ["b"]
    assert rows[1]["Responds"] == "timeout"


@pytest.mark.parametrize("deadline_in", [-1.0, 0.0])
def test_a_spent_summary_budget_skips_the_probe_rather_than_overrunning_it(
    monkeypatch, deadline_in
):
    called = []
    monkeypatch.setattr(probe, "probe_all", lambda *a, **k: called.append(1) or {})
    rows = [_row("a", "up", "1", "reported")]
    probe.attach_probe_column(rows, 0.5, time.monotonic() + deadline_in)
    assert called == []
    assert rows[0]["Responds"] == "-"
