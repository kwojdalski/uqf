"""The advisory warnings a start prints before it runs - never a refusal.

Each looks at the fleet the start will leave and names a problem the plant
would not: a subscriber with no producer, more connections than the licence
lets one process hold, proctypes monitor1 cannot watch. None can fail the
command. Split from cli/lifecycle.py, which holds its modules to 400 lines.
"""

from __future__ import annotations

from uqs.cli.shared import _paths, console, log
from uqs.model import dependencies, profiles
from uqs.stack import alive, deployment, listing
from uqs.stack import procs as procs_model


def running(port: int | None) -> set[str]:
    """The processes `status` reports up. Raises when it cannot say; each
    caller decides what that means for it."""
    return alive.running(_paths(), base_port=port)


def unfed_inputs(procs: str, port: int | None) -> None:
    """Say so when what is being started subscribes to a table nothing
    running publishes.

    A subscriber started without its producer subscribes SUCCESSFULLY - the
    table is defined on the plant either way - then heartbeats and reports
    `up` while receiving nothing, with no error and no symptom but an
    output table that stays empty (#290).

    Advisory only, and deliberately so: starting a subscriber before its
    feed is how you avoid missing the first batch, and some tables come
    from outside the process list entirely. Processes named in this same
    invocation count as present, so starting a whole chain is silent.

    Never fails the command. A warning that cannot be produced - the fleet
    is unreachable, the registry cannot be read - must not stop a start.
    """
    try:
        names = [p for p in procs.split() if p != "all"]
        if not names:
            return  # `start all` brings up every startwithall=1 producer too
        up = running(port)
        warnings = [
            line for name in names for line in dependencies.unfed_inputs(name, up | set(names))
        ]
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        # loguru formats with str.format, not %-interpolation: the `%s` this
        # used to carry printed literally and the exception was dropped, so
        # the one line explaining why the warning was skipped explained
        # nothing.
        log.debug("dependency warning skipped: {}", exc)
        return
    for line in warnings:
        console.print(f"[yellow]warning[/] {line}")


def monitor_coverage(verb: str, names: str, profile: str | None = None) -> None:
    """Name the proctypes monitor1 gives up under the connection budget (#620).

    Their processes get no heartbeat subscription, and heartbeat.q cannot
    report what it never subscribed to - so without this the gap shows only
    as a `-` nobody can read. Advisory, and never blocks a start.

    Planned for the fleet THIS start brings up - the set bootstrap is about
    to be handed (stack/deployment.planned) - not the `startwithall=1` rows:
    under `--profile crypto` those named a gap the profile's fleet does not
    have, while the one it had went unnamed (#1318).
    """
    try:
        chosen = names.split() if profile is not None else None
        selected = deployment.planned(_paths(), [verb, *names.split()], chosen)
        dropped = procs_model.monitor_dropped_proctypes(_paths(), selected)
        limit = profiles.licence_limit()
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        log.debug("monitor coverage note skipped: {}", exc)
        return
    if dropped:
        console.print(
            f"[yellow]note[/] monitor1 cannot watch {', '.join(dropped)}: the "
            f"{limit}-connection licence budget leaves no slot for them, so their "
            "processes run with no heartbeat in `uqs status`"
        )


def connection_cap(procs: str, port: int | None) -> None:
    """Say so when the fleet this start produces is bigger than the licence
    lets one process hold handles for.

    The licence caps a q process at `profiles.licence_limit()` concurrent
    connections - 16 on the community licence, none on PeachQ, unless
    UQS_LICENCE_CONNECTIONS says. Every streaming job opens a handle to stp1 and monitor1
    opens one per process it watches, so past that count the cap - not the
    configuration - decides what works. The plant does not complain: it
    resets the extra connection, the process wedges in its retry loop, and
    `uqs status` still reports it `up` because that is a PID check.

    Advisory only, and never fails the command: the limit is a property of
    the licence, not a mistake, and a bigger fleet is a legitimate thing to
    want on a full kdb+/KDB-X licence.
    """
    try:
        up = running(port)
        if procs.strip() == "all":
            starting = {
                row["procname"]
                for row in listing.list_items(_paths(), "processes", base_port=port)
                if row.get("startwithall") == "1"
            }
        else:
            starting = {p for p in procs.split() if p != "all"}
        total = len(up | starting)
        limit = profiles.licence_limit()
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        log.debug("connection-cap warning skipped: {}", exc)
        return
    if limit is None or total <= limit:
        return
    console.print(
        f"[yellow]warning[/] this start leaves {total} processes running, past the "
        f"{limit} concurrent connections this licence allows "
        "one q process. Handles past the cap are reset, not refused: the process "
        "wedges in its retry loop and still reports `up`, and monitor1 may become "
        "unreachable so the Heartbeat column empties. Start a subset, or stop what "
        "you are not using."
    )
