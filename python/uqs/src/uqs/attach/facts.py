"""What a preflight needs to know about the machine it runs on, besides the stack."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1" if host == "localhost" else host, port), 0.5):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class Host:
    """The machine this runs on, as the checks need to ask about it."""

    #: how to ask whether a port answers; None = a real TCP connect
    port_open: Callable[[str, int], bool] | None = None
    local_names: tuple[str, ...] = LOCAL_HOSTS + (socket.gethostname().lower(),)

    def listening(self, host: str, port: int) -> bool:
        return (self.port_open or _tcp_open)(host, port)

    def is_local(self, name: str) -> bool:
        """Whether `name` is this machine - by name, by loopback, or by ANY address it
        owns. A stack host given as the machine's own IP (`--host 10.1.2.3`, or any
        127.x) is as local as `localhost`; deciding by name alone called it remote,
        so a schema file on this very disk was reported unreachable (#1435)."""
        lowered = name.lower()
        if lowered in self.local_names or lowered.split(".")[0] in {
            n.split(".")[0] for n in self.local_names if not _is_ip(n)
        }:
            return True
        return any(_owned(address) for address in _addresses(lowered))

    def is_elsewhere(self, name: str) -> bool:
        """Whether `name` is KNOWN to be another machine: it resolves, and is not
        this one by `is_local`'s rule. A name that does not resolve here is not
        elsewhere - a check that must not miss a clash on this machine treats
        an unknown host as possibly this one (#1686)."""
        return not self.is_local(name) and bool(_addresses(name.lower()))


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _addresses(name: str) -> list[str]:
    if _is_ip(name):
        return [name]
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(name, None)})
    except OSError:
        return []


def _owned(address: str) -> bool:
    """Loopback, or an address a socket can be bound to - which only an address
    assigned to this machine allows."""
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.is_loopback:
        return True
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((str(ip), 0))
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class PublisherPort:
    """Where the publisher will listen: the stack id plus its locked offset."""

    port: int | None
    provisional: bool = False


@dataclass(frozen=True)
class SchemaRead:
    """The tickerplant's own answer about its schema file, for a block it is to
    append itself (schema_files.inspect, #1651): the file is on its machine."""

    path: str
    #: the file's text; None when it does not exist
    text: str | None
    #: can the tickerplant's user append to it
    writable: bool
    #: why it could not be read, if it could not
    error: str = ""


@dataclass
class Facts:
    """Everything preflight reads besides the observation."""

    publisher: PublisherPort = field(default_factory=lambda: PublisherPort(None))
    host: Host = field(default_factory=Host)
    #: the manifest's token, when the attachment has one: what tells a table
    #: this attachment already registered from one it must only adopt or share
    token: str = ""
    #: with `remote-schema-file`, what the tickerplant said about its schema file
    schema: SchemaRead | None = None
