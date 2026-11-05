"""A deployed target as a workspace: every process its release declares,
forwarded to loopback over one ssh and held open, so a reader on this machine
can ask that target's processes what it would ask a local stack's (#2029).

The frontend's target-scoped reads (Desk, Fleet, Connections) go through one
of these; nothing in the frontend opens ssh or reads a port itself.

  - The processes and their ports are the target's release's own, read the
    way `query --tunnel` and the tunnel plan read them
    (tunnel.remote_processes): never this checkout's process.csv.
  - Every declared process is forwarded, not only the proctypes a tunnel plan
    offers an operator (tunnel_plan.FORWARDED_PROCTYPES): fleet health probes each.
  - The local ports are free loopback ports the OS hands out, so two
    workspaces, a manual tunnel plan and a local stack never collide.
  - One ssh holds every forward (tunnel.forwarded); `close` ends it. A
    connection whose ssh has gone is not `alive`, and its owner opens a new
    one rather than reading through a dead forward.

The q credentials are not part of a workspace, as they are not part of a
tunnel: the caller logs in with its own.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from uqs.deploy import control, tunnel
from uqs.paths import UqsError

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Endpoint:
    """One of the target's processes, as this machine reaches it."""

    process: str
    proctype: str
    remote_port: int
    local_port: int

    @property
    def local_host(self) -> str:
        return tunnel.LOOPBACK


@dataclass
class Connection:
    """An open workspace on target `target`: its endpoints, by process name,
    until `close`."""

    target: str
    host: str
    endpoints: dict[str, Endpoint]
    ready: Callable[[int], bool] = field(repr=False)
    _stack: ExitStack = field(repr=False)
    closed: bool = False

    def gateway(self) -> Endpoint:
        """The target's gateway; refused when its release declares none."""
        for e in self.endpoints.values():
            if e.proctype == "gateway":
                return e
        raise UqsError(
            f"target {self.target}: its release declares no gateway process - "
            f"it declares: {', '.join(sorted(self.endpoints)) or 'nothing'}"
        )

    def alive(self) -> bool:
        """Whether the ssh still holds the forwards: its first local port
        still accepts. A dead workspace is reopened, never read through."""
        if self.closed or not self.endpoints:
            return False
        first = next(iter(self.endpoints.values()))
        return self.ready(first.local_port)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._stack.close()

    def describe(self) -> dict[str, Any]:
        """The connection as JSON: no credential, nothing secret."""
        return {
            "target": self.target,
            "ssh": self.host,
            "endpoints": [
                {
                    "process": e.process,
                    "proctype": e.proctype,
                    "remote_port": e.remote_port,
                    "local_endpoint": f"{e.local_host}:{e.local_port}",
                }
                for e in self.endpoints.values()
            ],
        }


def open_connection(
    r: control.Remote,
    *,
    runtime: str | None = None,
    runner: Runner = subprocess.run,
    popen: tunnel.Popen = subprocess.Popen,
    ready: Callable[[int], bool] = tunnel._accepts,
    free_port: Callable[[], int] = tunnel.free_port,
) -> Connection:
    """Discover what target `r`'s release declares and forward all of it.
    Raises UqsError naming the layer that failed: discovery or the ssh."""
    declared = tunnel.remote_processes(r, runtime, runner=runner)
    endpoints: dict[str, Endpoint] = {}
    used: set[int] = set()
    for row in declared:
        port = free_port()
        while port in used:
            port = free_port()
        used.add(port)
        endpoints[row["process"]] = Endpoint(row["process"], row["proctype"], row["port"], port)
    stack = ExitStack()
    forwards = [(e.local_port, e.remote_port) for e in endpoints.values()]
    try:
        stack.enter_context(tunnel.forwarded(r, forwards, popen=popen, ready=ready))
    except BaseException:
        stack.close()
        raise
    return Connection(r.name, r.host, endpoints, ready, stack)
