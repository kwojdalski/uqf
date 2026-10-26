"""`uqs query --target NAME`: what the query command needs to reach a process
on a deployed target through an ssh tunnel (#1561). The tunnel and the
remote port lookup are deploy/tunnel.py's; this is the CLI's side - the
target named, its port resolved, the connection's errors worded without the
password.

Not the global `uqs --target NAME query`, which runs `uqs query` on the
server (deploy/control.py). The two are refused together - cli/target.py -
because each means a different machine runs the query.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from uqs import runtimes
from uqs.deploy import control, tunnel
from uqs.logger import get_logger
from uqs.paths import UqsError, repo_root
from uqs.stack import gateway as stack_gateway
from uqs.stack import runtime

log = get_logger()

LOOPBACK = tunnel.LOOPBACK


@dataclass(frozen=True)
class Via:
    """A process on a target, as this call reaches it."""

    target: str
    host: str
    what: str
    remote_port: int
    port: int
    passwd: str

    @property
    def where(self) -> str:
        return f"{self.what} on target {self.target} (port {self.remote_port}, via ssh)"

    def scrub(self, exc: object) -> str:
        return tunnel.scrub(str(exc), [self.passwd])

    def failed(self, exc: object) -> UqsError:
        return UqsError(
            f"query on {self.where} failed: {self.scrub(exc)} - is it running there? "
            f"`uqs --target {self.target} summary` shows"
        )


@contextmanager
def connect(
    name: str,
    proc: str | None,
    port: int | None,
    base_port: int | None,
    user: str,
    passwd: str,
) -> Iterator[Via]:
    """The tunnel to `proc` (or `port`) on target `name`, for the block."""
    r = control.remote_for(name, repo_root())
    if port is None:
        procname = proc or stack_gateway.DEFAULT_PROC
        chosen = os.environ.get(runtimes.RUNTIME_ENV) or None
        ports = tunnel.remote_ports(r, chosen, base_port)
        port = tunnel.remote_port(ports, procname, r, chosen)
        what = procname
    else:
        what = f"port {port}"
    log.debug("query {} on target {} ({}) -> remote port {}", what, r.name, r.host, port)
    with tunnel.tunnel(r, port) as local:
        yield Via(r.name, r.host, what, port, local, passwd)


def run_qcon(host: str, port: int, user: str, passwd: str) -> None:
    """qcon on host:port as a CHILD, not exec'd: this process owns the tunnel
    and must outlive the session to close it. Ctrl-C is qcon's while it runs."""
    if shutil.which("qcon") is None:
        raise UqsError(
            "qcon is not on PATH. It ships with kdb+ rather than with this repository "
            "(macOS: it is beside q in your KDB-X install). `uqs query --target NAME --proc "
            '<name> "<expr>"` still works over IPC.'
        )
    argv = runtime.qcon_command(host, port, user, passwd, rlwrap=shutil.which("rlwrap") is not None)
    before = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        code = subprocess.run(argv, check=False).returncode
    except OSError as exc:
        raise UqsError(f"could not start {argv[0]}: {exc}") from None
    finally:
        signal.signal(signal.SIGINT, before)
    if code:
        raise UqsError(f"qcon exited {code}")
