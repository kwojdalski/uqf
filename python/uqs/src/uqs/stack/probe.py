"""Can each process answer at all, within a deadline measured per process.

`summary`'s Status column comes from a PID lookup, and a hung process still
has a PID. Its Heartbeat column comes from monitor1, which only notices after
a tolerance of missed beats. Neither answers "will this process respond to me
now", which is what this does, for every process that is up.

THE PROBE IS THE KDB+ HANDSHAKE, NOT A QUERY. The client sends
`user:password`, a capability byte and a NUL; q replies with one byte, or
closes the connection if it refuses the login. q answers from its main loop,
so a process busy in a long query, a timer or its own load cannot reply until
it is free - which is exactly the unresponsiveness this is for. And no q code
runs on the process being probed.

WHY A RAW SOCKET AND NOT KOLA. kola's timeout is whole seconds, and the point
here is a sub-second deadline per process. A plain socket takes a float.

WHY NOT q's OWN -T. That is a server-side limit on how long one client query
may run. It says nothing about a process stuck in its own timer, applies to
every client of the process - the gateway's long queries included - and takes
whole seconds.

Each probe opens and closes one connection, so it briefly holds one of the
process's inbound slots, and TorQ logs the connection like any other.
"""

from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

#: How long one process has to complete the handshake, in seconds.
DEFAULT_PROBE_TIMEOUT = 0.5

#: The capability byte this client asks for. 3 is what kdb+ 3.x/4.x clients
#: send; the server answers with the lower of its own and this.
_CAPABILITY = 3

#: How many processes are probed at once. A stack is ~50 processes, so this
#: probes nearly all of them in one round and the whole column costs about
#: one timeout rather than one per process.
_WORKERS = 32


@dataclass(frozen=True)
class ProbeResult:
    """How one process answered, or why it did not."""

    #: ok, timeout, refused, reset, rejected or error - see `probe`.
    outcome: str
    #: Round trip for an `ok`, in milliseconds; None otherwise.
    millis: float | None = None

    def cell(self) -> str:
        """What the Responds column shows."""
        return f"{self.millis:.0f}ms" if self.millis is not None else self.outcome


def probe(
    port: int,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> ProbeResult:
    """Complete the kdb+ handshake with one process inside `timeout` seconds.

    Outcomes, deliberately distinct:
      ok        it replied in time
      timeout   it accepted the connection, or never did, but did not reply in
                time - busy, hung, or at its licence connection cap, which can
                look the same from outside
      refused   nothing is listening on the port
      reset     it dropped the connection - what a process past its licence
                connection cap does to a new handle
      rejected  it closed the connection without replying: it refused these
                credentials
      error     any other socket failure, e.g. the host is unreachable
    """
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            # One deadline for the whole exchange, not one per step.
            sock.settimeout(max(0.001, timeout - (time.monotonic() - start)))
            sock.sendall(f"{user}:{passwd}".encode() + bytes([_CAPABILITY, 0]))
            reply = sock.recv(1)
    except TimeoutError:
        return ProbeResult("timeout")
    except ConnectionRefusedError:
        return ProbeResult("refused")
    except ConnectionResetError, BrokenPipeError:
        return ProbeResult("reset")
    except OSError:
        return ProbeResult("error")
    if not reply:
        return ProbeResult("rejected")
    return ProbeResult("ok", (time.monotonic() - start) * 1000)


def probe_all(
    ports: dict[str, int], timeout: float = DEFAULT_PROBE_TIMEOUT, **creds: str
) -> dict[str, ProbeResult]:
    """`probe` every process in `ports` (procname -> port) at once."""
    if not ports:
        return {}
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(ports))) as pool:
        futures = {
            name: pool.submit(probe, port, timeout=timeout, **creds) for name, port in ports.items()
        }
        return {name: future.result() for name, future in futures.items()}


def attach_probe_column(
    rows: list[dict[str, str]], timeout: float, deadline: float | None
) -> list[str]:
    """Probe every up process with a reported port, in place, and return the
    ones that did not answer.

    Only a REPORTED port is probed: a configured one belongs to a process
    that is down, which the Status column already says. The probe's timeout
    is also capped by what is left of the command's own budget, so it cannot
    push `summary` past `--timeout`.
    """
    targets = {
        row["Process"]: int(row["Port"])
        for row in rows
        if row["Status"] == "up" and row["PortSource"] == "reported" and row["Port"].isdigit()
    }
    if deadline is not None:
        timeout = min(timeout, max(0.0, deadline - time.monotonic()))
    results = probe_all(targets, timeout=timeout) if timeout > 0 else {}
    for row in rows:
        result = results.get(row["Process"])
        row["Responds"] = result.cell() if result else "-"
    return [name for name, result in results.items() if result.outcome != "ok"]
