"""kola_host: the address kola is handed, so a timeout does not refuse localhost.

kola 2.5 with a timeout connects only to the first address a name resolves
to; `localhost` resolves to `::1` first on macOS and q listens on IPv4 only.
The query test below is the end-to-end proof against a real q.
"""

from __future__ import annotations

import socket

from uqs.stack.runtime import kola_host


def test_with_a_timeout_localhost_becomes_its_ipv4_address():
    assert kola_host("localhost", 30) == "127.0.0.1"


def test_without_a_timeout_the_host_is_untouched():
    assert kola_host("localhost", 0) == "localhost"


def test_an_ipv4_literal_passes_through():
    assert kola_host("127.0.0.1", 30) == "127.0.0.1"


def test_a_name_that_does_not_resolve_is_left_for_kola_to_report(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert kola_host("no-such-host.invalid", 30) == "no-such-host.invalid"


def test_a_query_with_a_timeout_reaches_a_q_on_localhost(start_q):
    """The case that was refused: runtime.query with a timeout, as `uqs
    summary`'s heartbeat check makes, against a q listening on IPv4."""
    from uqs.stack import runtime

    with start_q() as port:
        assert runtime.query("1+1", port, host="localhost", timeout=5) == 2
