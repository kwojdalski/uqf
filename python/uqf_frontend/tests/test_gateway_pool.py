"""KolaGateway's connection pool, with a fake connection and no q (#635).

The gateway used to open a connection per request. It now keeps idle handles
and reuses them; these pin the rules that make that safe. test_kola_ipc.py
proves the same against a live q, including a connection the server drops.
"""

from __future__ import annotations

import threading
from typing import Any

import kola
import pytest

from uqf_frontend.config import Settings
from uqf_frontend.errors import GatewayUnavailable, QueryRejected
from uqf_frontend.gateway import KolaGateway, _classify


class FakeConnection:
    """Answers every query with its own id; `fail_next` makes one query raise."""

    def __init__(self, ident: int) -> None:
        self.ident = ident
        self.fail_next: Exception | None = None
        self.closed = False

    def sync(self, program: str, *args: Any) -> Any:
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        return self.ident

    def disconnect(self) -> None:
        self.closed = True


class Factory:
    def __init__(self) -> None:
        self.made: list[FakeConnection] = []

    def __call__(self) -> FakeConnection:
        c = FakeConnection(len(self.made))
        self.made.append(c)
        return c


def gateway() -> tuple[KolaGateway, Factory]:
    factory = Factory()
    return KolaGateway(Settings(), connect=factory), factory


def test_sequential_requests_reuse_one_connection():
    gw, made = gateway()
    assert [gw.call("{x}", i) for i in range(5)] == [0] * 5
    assert len(made.made) == 1


def test_a_dropped_connection_is_replaced_on_the_next_request():
    """The acceptance case: a dead handle fails the request it was used for,
    and only that one - it is discarded, so the next request reconnects
    rather than every later request failing on it."""
    gw, made = gateway()
    gw.call("1")
    made.made[0].fail_next = kola.KolaIOError("Broken pipe (os error 32)")
    with pytest.raises(GatewayUnavailable):
        gw.call("1")
    assert made.made[0].closed
    assert gw.call("1") == 1
    assert len(made.made) == 2


def test_a_q_error_also_discards_the_handle():
    """Any failure: a timed-out query may still have its answer in flight,
    and the next request on that handle would read it as its own."""
    gw, made = gateway()
    gw.call("1")
    made.made[0].fail_next = kola.KolaError("type")
    with pytest.raises(QueryRejected):
        gw.call("1")
    assert gw.call("1") == 1


def test_concurrent_requests_each_get_their_own_connection():
    """Not one handle behind a lock: a slow query must not hold up a poll."""
    factory = Factory()
    inside = threading.Barrier(2, timeout=5)

    class Slow(FakeConnection):
        def sync(self, program: str, *args: Any) -> Any:
            inside.wait()
            return self.ident

    def connect() -> FakeConnection:
        c = Slow(len(factory.made))
        factory.made.append(c)
        return c

    gw = KolaGateway(Settings(), connect=connect)
    results: list[Any] = []
    threads = [threading.Thread(target=lambda: results.append(gw.call("1"))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [0, 1]
    assert len(factory.made) == 2


def test_idle_connections_beyond_the_cap_are_closed():
    gw, made = gateway()
    held = [gw._checkout() for _ in range(KolaGateway.MAX_IDLE + 2)]
    for q in held:
        gw._checkin(q)
    assert sum(not c.closed for c in made.made) == KolaGateway.MAX_IDLE


def test_close_disconnects_every_idle_connection():
    gw, made = gateway()
    gw.call("1")
    gw.close()
    assert made.made[0].closed
    gw.call("1")
    assert len(made.made) == 2


def test_an_io_failure_is_transient_unavailable_not_a_rejection():
    """A rejection is a 400 and the browser stops polling on it; a dropped
    connection is reopened by the next request, so it must read as transient."""
    assert isinstance(_classify(kola.KolaIOError("Broken pipe (os error 32)")), GatewayUnavailable)


def test_an_io_timeout_is_still_a_timeout():
    from uqf_frontend.errors import QueryTimedOut

    assert isinstance(_classify(kola.KolaIOError("operation timed out")), QueryTimedOut)
