"""Can the stack reach the publisher back, once it runs? (#1651)

Discovery connects back to every process that registers with it, at the host
and port it lists, so a publisher on this machine attached to a stack on
another needs the network path in BOTH directions. Preflight can only check
the name (ADVERTISED_LOOPBACK, ADVERTISED_UNRESOLVABLE): the port cannot be
tried before the publisher listens. This tries it, once it does: discovery is
asked, with the read-only `reach` operation, to open a handle to the publisher
at `advertised_host:port` - with the stack's own credentials, as TorQ's
`.servers.opencon` would - and close it at once.

The other direction is asked of the publisher itself, with the same `reach`
operation: can IT open a handle to discovery and to the tickerplant, with its
own TorQ credential (`.servers.USERPASS`, from its passwords files) - the one
it publishes with, which is not the attachment's `--user`. uqs asks the
publisher with its own local credential (remote.LOCAL_USER).

A refused credential is q's `access`, and is said as that, in each direction
(STACK_REFUSES_PUBLISHER_CREDENTIALS, PUBLISHER_REFUSES_STACK_CREDENTIALS),
not as an unreachable address.

Every call is bounded (ANSWER_S, #1684): a publisher whose port is open but
which never answers - stuck in its TorQ startup - is said as that
(PUBLISHER_UNRESPONSIVE), not waited on forever and not said as a network path.

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
from uqs.attach.remote import Bounded, Endpoint, Ipc, IpcError

CODE = "PUBLISHER_UNREACHABLE_FROM_STACK"
#: The stack's credential refused by the publisher.
REFUSED = "PUBLISHER_REFUSES_STACK_CREDENTIALS"
#: The publisher cannot open the stack's discovery or tickerplant.
OUTBOUND = "STACK_UNREACHABLE_FROM_PUBLISHER"
#: The publisher's own credential refused by the stack.
OUTBOUND_REFUSED = "STACK_REFUSES_PUBLISHER_CREDENTIALS"
#: The publisher listens but does not answer: its startup has not finished (#1684).
UNRESPONSIVE = "PUBLISHER_UNRESPONSIVE"
#: What q's hopen signals for a credential the other side's -U or .z.pw refuses.
ACCESS = "access"
#: How long discovery waits for the handle: what TorQ's own .servers.HOPENTIMEOUT defaults to.
TIMEOUT_MS = 2000
#: How long uqs waits for an answer to `reach`: the asked process's own hopen
#: (TIMEOUT_MS) and a few seconds for the round trip, then it stops waiting (#1684).
ANSWER_S = TIMEOUT_MS / 1000 + 3
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
        answer = Bounded(ipc, ANSWER_S).call(
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
    if reason == ACCESS:
        return Finding(
            "blocker",
            REFUSED,
            pub,
            f"{pub} at {where} refuses the credential the stack on {att.stack.host} connects "
            "with (q's `access`)",
            f"add the stack's TorQ user to {pub}'s access list (passwords/accesslist.txt), "
            f"then run `uqs job attachment add {att.name}` again",
            {"address": where},
        )
    return Finding(
        "blocker",
        CODE,
        pub,
        f"the stack on {att.stack.host} cannot connect back to {pub} at {where}: {reason}",
        f"open {where} to the stack's machine (firewall, routing, the name's address there), "
        f"then run `uqs job attachment add {att.name}` again",
        {"address": where},
    )


def outbound(att: Attachment, ipc: Ipc, port: int, tickerplant: Endpoint | None) -> Finding | None:
    """Ask the running publisher to open and close a handle to discovery and to the
    tickerplant, with its own credential; None when it could open both."""
    pub, st = att.publisher.procname, att.stack
    me = Endpoint(pub, PUBLISHER_HOST, port, local=True)
    targets = [Endpoint("discovery", st.discovery_host, st.discovery_port)]
    targets += [tickerplant] if tickerplant is not None else []
    for t in targets:
        request = {"host": t.host, "port": t.port, "timeout": TIMEOUT_MS}
        try:
            answer = Bounded(ipc, ANSWER_S).call(me, "reach", request)
        except IpcError as exc:
            if exc.unresponsive:
                return unresponsive(att, port, exc.reason)
            return _outbound(att, t, f"{pub} could not be asked: {exc.reason}")
        if answer.get("reached") is not True:
            return _outbound(att, t, str(answer.get("error") or "no reason given"))
    return None


def unresponsive(att: Attachment, port: int, reason: str) -> Finding:
    """The publisher listens on `port` and does not answer: its own startup is stuck,
    so nothing about the network between it and the stack is known yet."""
    pub = att.publisher.procname
    return Finding(
        "blocker",
        UNRESPONSIVE,
        pub,
        f"{pub} listens on {PUBLISHER_HOST}:{port} but {reason}: it has not finished "
        "starting, so whether it and the stack reach each other is not known",
        f"find where {pub}'s startup stops (its TorQ log), fix that, then run "
        f"`uqs job attachment add {att.name}` again",
        {"address": f"{PUBLISHER_HOST}:{port}"},
    )


def _outbound(att: Attachment, target: Endpoint, reason: str) -> Finding:
    pub, where = att.publisher.procname, f"{target.host}:{target.port}"
    facts = {"address": where, "process": target.procname}
    if reason == ACCESS:
        return Finding(
            "blocker",
            OUTBOUND_REFUSED,
            pub,
            f"{target.procname} at {where} refuses the credential {pub} connects with "
            "(its .servers.USERPASS; q's `access`), so its rows would never be published",
            f"give {pub} a credential {target.procname}'s access list accepts (passwords/"
            f"<name>.txt in its TorQ config), then run `uqs job attachment add {att.name}` again",
            facts,
        )
    return Finding(
        "blocker",
        OUTBOUND,
        pub,
        f"{pub} cannot connect to {target.procname} at {where}: {reason}",
        f"open {where} to this machine, then run `uqs job attachment add {att.name}` again",
        facts,
    )


def both(att: Attachment, ipc: Ipc, port: int, tickerplant: Endpoint | None) -> Finding | None:
    """The publisher reaches the stack, then the stack reaches the publisher."""
    return outbound(att, ipc, port, tickerplant) or check(att, ipc, port)


def after_start(
    att: Attachment,
    ipc: Ipc,
    port: int,
    publisher: Publisher,
    *,
    tickerplant: Endpoint | None = None,
    wait_s: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Finding | None:
    """The check for a publisher just started: it may still be loading, so it is
    retried until it passes or `wait_s` runs out, and the last failure is the answer."""
    deadline = clock() + (START_WAIT_S if wait_s is None else wait_s)
    while True:
        if publisher.running(att):
            found = both(att, ipc, port, tickerplant)
        else:
            found = unreachable(
                att, address(att, port), f"{att.publisher.procname} is not listening on {port} here"
            )
        if found is None or clock() >= deadline:
            return found
        sleep(1.0)


def checks(
    att: Attachment, ipc: Ipc, port: int | None, host: Host, tickerplant: Endpoint | None
) -> list[tuple[str, bool, str]]:
    """For verify, as (name, ok, detail): both directions, when the publisher
    already listens here; nothing to try otherwise."""
    if port is None or not applies(att, host) or not host.listening(PUBLISHER_HOST, port):
        return []
    pub = att.publisher.procname
    out = outbound(att, ipc, port, tickerplant)
    back = check(att, ipc, port)
    return [
        (f"{pub} reaches the stack with its own credential", out is None,
         f"{out.code}: {out.message}" if out else ""),
        (f"the stack reaches {pub} at {address(att, port)}", back is None,
         f"{back.code}: {back.message}" if back else ""),
    ]  # fmt: skip
