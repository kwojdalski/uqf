"""Can the stack reach the publisher back, once it runs? (#1651)

Discovery connects back to every process that registers with it, at the host
and port it lists, so a publisher on this machine attached to a stack on
another needs the network path in BOTH directions. Preflight can only check
the name (ADVERTISED_LOOPBACK, ADVERTISED_UNRESOLVABLE): the port cannot be
tried before the publisher listens. This tries it, once it does: discovery is
asked, with the read-only `reach` operation, to open a handle to the publisher
at `advertised_host:port` - with the stack's own credentials, as TorQ's
`.servers.opencon` would - and close it at once.

It runs where the publisher is known to be up: in verify when the publisher
already listens here, and in the workflow right after starting it. A stack on
this machine needs none of it - any name it lists reaches this machine.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from uqs.attach.config import Attachment
from uqs.attach.facts import Host
from uqs.attach.findings import Finding
from uqs.attach.preflight import PUBLISHER_HOST
from uqs.attach.publisher import Publisher
from uqs.attach.remote import Endpoint, Ipc, IpcError

CODE = "PUBLISHER_UNREACHABLE_FROM_STACK"
#: How long discovery waits for the handle: what TorQ's own .servers.HOPENTIMEOUT defaults to.
TIMEOUT_MS = 2000
#: How long a publisher just started has to start listening and answer the stack.
START_WAIT_S = 30.0


def applies(att: Attachment, host: Host) -> bool:
    """A stack on another machine, and a name the publisher registers under."""
    return bool(att.publisher.advertised_host) and not host.is_local(att.stack.host)


def address(att: Attachment, port: int) -> str:
    return f"{att.publisher.advertised_host}:{port}"


def check(att: Attachment, ipc: Ipc, port: int) -> Finding | None:
    """Ask discovery to open and close a handle to the publisher; None when it could."""
    st, where = att.stack, address(att, port)
    request = {"host": att.publisher.advertised_host, "port": port, "timeout": TIMEOUT_MS}
    try:
        answer = ipc.call(
            Endpoint("discovery", st.discovery_host, st.discovery_port), "reach", request
        )
    except IpcError as exc:
        reason = f"discovery could not be asked: {exc.reason}"
    else:
        if answer.get("reached") is True:
            return None
        reason = str(answer.get("error") or "no reason given")
    return unreachable(att, where, reason)


def unreachable(att: Attachment, where: str, reason: str) -> Finding:
    pub = att.publisher.procname
    return Finding(
        "blocker",
        CODE,
        pub,
        f"the stack on {att.stack.host} cannot connect back to {pub} at {where}: {reason}",
        f"open {where} to the stack's machine (firewall, routing, the name's address there), "
        f"then run `uqs job attach {att.name}` again",
        {"address": where},
    )


def after_start(
    att: Attachment,
    ipc: Ipc,
    port: int,
    publisher: Publisher,
    *,
    wait_s: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Finding | None:
    """The check for a publisher just started: it may still be loading, so it is
    retried until it passes or `wait_s` runs out, and the last failure is the answer."""
    deadline = clock() + (START_WAIT_S if wait_s is None else wait_s)
    while True:
        if publisher.running(att):
            found = check(att, ipc, port)
        else:
            found = unreachable(
                att, address(att, port), f"{att.publisher.procname} is not listening on {port} here"
            )
        if found is None or clock() >= deadline:
            return found
        sleep(1.0)


def checks(att: Attachment, ipc: Ipc, port: int | None, host: Host) -> list[tuple[str, bool, str]]:
    """For verify, as (name, ok, detail): the check, when the publisher already
    listens here; nothing to try otherwise."""
    if port is None or not applies(att, host) or not host.listening(PUBLISHER_HOST, port):
        return []
    found = check(att, ipc, port)
    name = f"the stack reaches {att.publisher.procname} at {address(att, port)}"
    return [(name, found is None, f"{found.code}: {found.message}" if found else "")]
