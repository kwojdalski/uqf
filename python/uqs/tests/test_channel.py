"""Log channels (#1067): turning a process's logmsg publications into the
records `uqs logs` prints, and the subscriber loop, against a fake
connection - the live path was checked against a running stack."""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from uqs.paths import UqsError, UqsPaths
from uqs.stack import channel
from uqs.stack import logs as stack_logs

#: The functions under test only hand paths on to what each test replaces.
_PATHS = cast(UqsPaths, Path("."))


def _logmsg(*rows: tuple[str, str, str, str]) -> list[Any]:
    """A `.ps` publication as kola delivers it: (`upd; `logmsg; table)."""
    return [
        "upd",
        "logmsg",
        pl.DataFrame(
            {
                "time": [datetime(2026, 10, 10, 12, 0, 0, 123456)] * len(rows),
                "sym": [r[0] for r in rows],
                "proctype": ["rdb"] * len(rows),
                "host": ["h"] * len(rows),
                "loglevel": [r[1] for r in rows],
                "id": [r[2] for r in rows],
                "message": [r[3] for r in rows],
            }
        ),
    ]


def test_a_publication_reads_as_the_records_uqs_logs_prints():
    recs = channel.records(_logmsg(("rdb1", "WARN", "fx_positions", "book is stale")))
    assert recs == [
        {
            "time": "2026.10.10D12:00:00.123456",
            "host": "h",
            "proctype": "rdb",
            "procname": "rdb1",
            "loglevel": "WARNING",
            "id": "fx_positions",
            "message": "book is stale",
        }
    ]


def test_torqs_short_level_names_read_as_the_five(monkeypatch):
    """A TorQ process publishes WARN and ERR; the channel prints the names the
    files print, through the one map both read."""
    levels = [
        r["loglevel"]
        for r in channel.records(
            _logmsg(("p", "WARN", "x", "a"), ("p", "ERR", "x", "b"), ("p", "ERROR", "x", "c"))
        )
    ]
    assert levels == ["WARNING", "ERROR", "ERROR"]


@pytest.mark.parametrize(
    "message",
    [["upd", "trade", pl.DataFrame({"x": [1]})], ["upd", "logmsg"], "not a message", None],
)
def test_anything_but_a_logmsg_publication_gives_no_records(message):
    assert channel.records(message) == []


def test_ids_filter_and_none_means_every_id():
    rec = {"id": "fx_positions"}
    assert channel.wanted(rec, [])
    assert channel.wanted(rec, ["fx_positions", "posbook"])
    assert not channel.wanted(rec, ["posbook"])


class _FakeConn:
    """One process's connection: answers the subscribe, then hands out the
    queued messages, then blocks as a quiet subscription does."""

    def __init__(self, messages: list[Any], fail: bool = False) -> None:
        self.messages = list(messages)
        self.fail = fail
        self.sent: list[str] = []

    def connect(self) -> None:
        if self.fail:
            raise OSError("Connection refused")

    def sync(self, expr: str) -> Any:
        self.sent.append(expr)
        return None

    def receive(self) -> Any:
        if self.messages:
            return self.messages.pop(0)
        time.sleep(3600)


def _run(monkeypatch, conns: dict[int, _FakeConn], **kw: Any) -> str:
    """Run follow_channel over fake processes rdb1 (port 1) and stp1 (port 2)
    for a moment, and return what it printed."""
    monkeypatch.setattr(channel.stack_logs, "resolve_procnames", lambda paths, procs: procs.split())
    monkeypatch.setattr(channel, "configured_ports", lambda paths, base: {"rdb1": "1", "stp1": "2"})
    monkeypatch.setattr(channel, "RETRY_SECONDS", 0.05)
    printed: list[str] = []
    monkeypatch.setattr(
        channel.stack_logs,
        "emit",
        lambda sink, rec, lvl: (
            printed.append(f"{rec['procname']}:{rec['loglevel']}:{rec['id']}:{rec['message']}")
            if stack_logs._passes_level(rec["loglevel"], lvl)
            else None
        ),
    )
    monkeypatch.setattr(channel.stack_logs, "log_sink", lambda: None)
    stop = threading.Event()
    worker = threading.Thread(
        target=channel.follow_channel,
        args=(_PATHS, kw.pop("procs", "rdb1 stp1")),
        kwargs={"connect": lambda port: conns[port], "stop": stop, **kw},
        daemon=True,
    )
    worker.start()
    time.sleep(0.4)
    stop.set()
    worker.join(2)
    return "\n".join(printed)


def test_lines_from_every_process_print_through_the_channel_filter(monkeypatch):
    conns = {
        1: _FakeConn(
            [
                _logmsg(
                    ("rdb1", "WARN", "fx_positions", "stale book"),
                    ("rdb1", "WARN", "posbook", "other worker"),
                )
            ]
        ),
        2: _FakeConn([_logmsg(("stp1", "ERR", "fx_positions", "log write failed"))]),
    }
    out = _run(monkeypatch, conns, ids=["fx_positions"])
    assert "rdb1:WARNING:fx_positions:stale book" in out
    assert "stp1:ERROR:fx_positions:log write failed" in out
    assert "other worker" not in out
    assert conns[1].sent == [channel.SUBSCRIBE] and conns[2].sent == [channel.SUBSCRIBE]


def test_level_filters_what_the_channel_prints(monkeypatch):
    conns = {
        1: _FakeConn(
            [_logmsg(("rdb1", "WARN", "x", "a warning"), ("rdb1", "ERR", "x", "an error"))]
        )
    }
    out = _run(monkeypatch, conns, procs="rdb1", min_level="ERROR")
    assert "an error" in out and "a warning" not in out


def test_a_process_that_is_down_is_reported_once_and_retried(monkeypatch):
    """A stopped process must not stop the channel, nor print a line on every retry."""
    down = _FakeConn([], fail=True)
    warnings: list[str] = []

    class _Log:
        def warning(self, message: str) -> None:
            warnings.append(message)

        def info(self, message: str) -> None:
            pass

    monkeypatch.setattr(channel, "log", _Log())
    _run(monkeypatch, {1: down, 2: _FakeConn([])})
    assert len(warnings) == 1 and "rdb1" in warnings[0]


def test_a_process_with_no_port_is_refused_before_connecting(monkeypatch):
    monkeypatch.setattr(channel.stack_logs, "resolve_procnames", lambda paths, procs: ["ghost1"])
    monkeypatch.setattr(channel, "configured_ports", lambda paths, base: {})
    with pytest.raises(UqsError, match="ghost1"):
        channel.follow_channel(_PATHS, "ghost1", connect=lambda port: None)


def test_a_bad_level_is_refused_before_connecting(monkeypatch):
    monkeypatch.setattr(channel.stack_logs, "resolve_procnames", lambda paths, procs: ["rdb1"])
    with pytest.raises(UqsError, match="not a level"):
        channel.follow_channel(_PATHS, "rdb1", min_level="LOUD", connect=lambda port: None)


def test_the_subscriber_puts_each_publication_on_the_queue():
    out: queue.Queue = queue.Queue()
    stop = threading.Event()
    msg = _logmsg(("rdb1", "WARN", "x", "m"))
    threading.Thread(
        target=channel._subscribe,
        args=("rdb1", 1, out, stop, lambda p: _FakeConn([msg])),
        daemon=True,
    ).start()
    assert out.get(timeout=2)[:2] == ("up", "rdb1")
    assert out.get(timeout=2) == ("msg", "rdb1", msg)
    stop.set()
