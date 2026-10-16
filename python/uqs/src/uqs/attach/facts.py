"""What a preflight needs to know about the machine it runs on, besides the stack."""

from __future__ import annotations

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
        return name.lower() in self.local_names or name.lower().split(".")[0] in {
            n.split(".")[0] for n in self.local_names
        }


@dataclass(frozen=True)
class PublisherPort:
    """Where the publisher will listen: the stack id plus its locked offset."""

    port: int | None
    provisional: bool = False


@dataclass
class Facts:
    """Everything preflight reads besides the observation."""

    publisher: PublisherPort = field(default_factory=lambda: PublisherPort(None))
    host: Host = field(default_factory=Host)
