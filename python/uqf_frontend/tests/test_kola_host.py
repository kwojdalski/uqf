"""kola_host: the address kola is handed, so a timeout does not refuse localhost.

kola 2.5 with a timeout connects only to the first address a name resolves
to; `localhost` resolves to `::1` first on macOS and q listens on IPv4 only.
test_kola_ipc.py is the end-to-end proof against a real q; these pin the rule.
"""

from __future__ import annotations

import socket

from uqf_frontend.gateway import kola_host


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
