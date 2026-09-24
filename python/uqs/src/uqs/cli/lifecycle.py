"""Starting, stopping and inspecting the fleet as processes.

The commands that drive torq.sh, plus the two advisory warnings that run
before a start. Split from cli/entry.py, which had grown to 1270 lines across
twenty-four commands - past the 400 this package holds its modules to, and
past the point where the file could be read in one sitting.

Commands register onto the shared `app` here rather than onto a sub-Typer,
so `uqs start` stays `uqs start`: the split is about where the
code lives, not about what anyone types.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs import paths as stack_paths
from uqs.cli import completion
from uqs.cli.shared import (
    PortOpt,
    ProcsArg,
    _die,
    _paths,
    _procs,
    _run_streaming,
    app,
    console,
    log,
)
from uqs.model import dependencies, profiles
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError
from uqs.stack import listing, runtime
from uqs.stack import logs as stack_logs
from uqs.stack import procs as procs_model


def _running(port: int) -> set[str]:
    """The processes `summary` reports up. Raises when it cannot say; each
    caller decides what that means for it."""
    return {
        row["Process"]
        for row in listing.summary_rows(runtime.summary(_paths(), base_port=port).stdout, {}, None)
        if row["Status"] == "up"
    }


def _warn_about_unfed_inputs(procs: str, port: int) -> None:
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
        running = _running(port)
        warnings = [
            line for name in names for line in dependencies.unfed_inputs(name, running | set(names))
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


def _warn_about_connection_cap(procs: str, port: int) -> None:
    """Say so when the fleet this start produces is bigger than the licence
    lets one process hold handles for.

    The licence caps a q process at `profiles.licence_limit()` concurrent
    connections - the community licence's 16 unless UQS_LICENCE_CONNECTIONS
    says otherwise. Every streaming job opens a handle to stp1 and monitor1
    opens one per process it watches, so past that count the cap - not the
    configuration - decides what works. The plant does not complain: it
    resets the extra connection, the process wedges in its retry loop, and
    `summary` still reports it `up` because that is a PID check.

    Advisory only, and never fails the command: the limit is a property of
    the licence, not a mistake, and a bigger fleet is a legitimate thing to
    want on a full kdb+/KDB-X licence.
    """
    try:
        running = _running(port)
        if procs.strip() == "all":
            starting = {
                row["procname"]
                for row in listing.list_items(_paths(), "processes", base_port=port)
                if row.get("startwithall") == "1"
            }
        else:
            starting = {p for p in procs.split() if p != "all"}
        total = len(running | starting)
        limit = profiles.licence_limit()
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        log.debug("connection-cap warning skipped: {}", exc)
        return
    if total <= limit:
        return
    console.print(
        f"[yellow]warning[/] this start leaves {total} processes running, past the "
        f"{limit} concurrent connections this licence allows "
        "one q process. Handles past the cap are reset, not refused: the process "
        "wedges in its retry loop and still reports `up`, and monitor1 may become "
        "unreachable so the Heartbeat column empties. Start a subset, or stop what "
        "you are not using."
    )


ProfileOpt = Annotated[
    str | None,
    typer.Option(
        "--profile",
        help=(
            "Comma-separated named start set(s) instead of process names - "
            "see `uqs list profiles`. Refused if the total is past the "
            "licence's connection cap."
        ),
        autocompletion=completion.profiles,
    ),
]


def _resolve_profiles(names: str) -> str:
    """The space-separated process list `names` stands for, or exit.

    A REFUSAL where a positional start only warns, and the asymmetry is
    deliberate. A positional start is an operator naming processes they chose;
    over the cap is their call, and several orderings that exceed it briefly
    are legitimate. A profile is a set THIS TREE defined and named, so one
    that cannot run is this tree's mistake to report - not theirs to discover
    when the plant resets a handle and the process wedges while reporting
    `up`.
    """
    wanted = [name.strip() for name in names.split(",") if name.strip()]
    if not wanted:
        _die(UqsError("--profile needs at least one name"))
    try:
        resolved = profiles.resolve(wanted)
        problem = profiles.over_budget(wanted)
    except UqsError as exc:
        _die(exc)
        raise  # unreachable: _die exits. Keeps the type checker honest.
    if problem:
        _die(UqsError(problem))
    console.print(
        f"[dim]profile {', '.join(wanted)}: {len(resolved)} process(es), "
        f"{profiles.plant_slots(resolved)}/{profiles.allowance()} plant slots[/]"
    )
    return " ".join(resolved)


def _names_to_start(procs: list[str] | None, profile: str | None) -> str:
    """What torq.sh is asked to start: the names given, `all`, or a profile."""
    names = _procs(procs)
    if profile is not None:
        if procs:
            _die(UqsError("--profile and explicit process names are mutually exclusive"))
            return names
        names = _resolve_profiles(profile)
    return names


def _reject_unknown(names: str) -> None:
    """Stop on a name that is not a process, before anything else runs.

    Must come before the warnings: they aggregate over the requested set, so
    a typo made `_warn_about_connection_cap` count a process that does not
    exist and print a licence warning about a fleet nobody asked for.
    """
    try:
        procs_model.assert_known_procnames(_paths(), names)
    except UqsError as exc:
        _die(exc)


def _start(names: str, port: int) -> None:
    _reject_unknown(names)
    _warn_about_unfed_inputs(names, port)
    _warn_about_connection_cap(names, port)
    _run_streaming(runtime.start, names, base_port=port)


def _start_in_foreground(names: str, port: int) -> None:
    """`_start`, minus its ending: _run_streaming always exits the command,
    and `up` has the whole run still to stream after the start returns."""
    _reject_unknown(names)
    _warn_about_unfed_inputs(names, port)
    _warn_about_connection_cap(names, port)
    result = runtime.start(_paths(), names, base_port=port, capture=False)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


@app.command()
def start(
    procs: ProcsArg = None, port: PortOpt = DEFAULT_BASE_PORT, profile: ProfileOpt = None
) -> None:
    """Start every startwithall=1 process (or specific process name(s)).

    `--profile fx` starts a named set instead: its leaves and everything they
    read, resolved from the dependency graph rather than listed by hand.
    """
    _start(_names_to_start(procs, profile), port)


LevelOpt = Annotated[
    str | None,
    typer.Option(
        help="Only show this level and above: DEBUG/INFO/WARNING/ERROR",
        autocompletion=completion.choices("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
]


def _procnames_started_by(names: str, port: int) -> list[str]:
    """The processes a start of `names` covers: `all` is every startwithall=1
    row, anything else is the names themselves - checked, so a typo fails
    before anything starts rather than as a log nobody ever writes."""
    if names.strip() == "all":
        return [
            row["procname"]
            for row in listing.list_items(_paths(), "processes", base_port=port)
            if row.get("startwithall") == "1"
        ]
    return stack_logs.resolve_procnames(_paths(), names)


@app.command()
def up(
    procs: ProcsArg = None,
    port: PortOpt = DEFAULT_BASE_PORT,
    profile: ProfileOpt = None,
    level: LevelOpt = None,
) -> None:
    """Start, and stream every started process's log to this console until
    Ctrl-C - which stops what this started. `up`, `up rdb1 fxpositions1`,
    `up --profile fx`, `up --level WARNING`.

    The foreground form of `start` + `logs -f`, the way `docker compose up`
    is: the console is the run. Processes that were already running when it
    began are left running; if `summary` cannot say which those were, Ctrl-C
    stops everything this was asked to start.
    """
    names = _names_to_start(procs, profile)
    try:
        wanted = _procnames_started_by(names, port)
    except UqsError as exc:
        _die(exc)
        return
    try:
        already: set[str] | None = _running(port)
    except Exception as exc:  # noqa: BLE001 - not knowing only widens the stop
        log.debug("could not tell what was already running: {}", exc)
        already = None
    # Ctrl-C ends the stream normally. A failed start ends it early - and
    # still stops what did start, because the console is the run.
    exit_code = 0
    try:
        stack_logs.follow_during(
            _paths(), wanted, lambda: _start_in_foreground(names, port), min_level=level
        )
    except UqsError as exc:
        log.error("{}", exc)
        exit_code = 1
    except typer.Exit as exc:
        exit_code = exc.exit_code
    started = [p for p in wanted if already is None or p not in already]
    if not started:
        console.print("[dim]every process was already running; leaving them up[/]")
    else:
        console.print(f"[dim]stopping what this started: {' '.join(started)}[/]")
        try:
            result = runtime.stop(_paths(), " ".join(started), base_port=port, capture=False)
            exit_code = exit_code or result.returncode
        except UqsError as exc:
            log.error("{}", exc)
            exit_code = exit_code or 1
    raise typer.Exit(code=exit_code)


@app.command()
def stop(procs: ProcsArg = None, port: PortOpt = DEFAULT_BASE_PORT) -> None:
    """Stop every running process (or specific process name(s))."""
    names = _procs(procs)
    _reject_unknown(names)
    _run_streaming(runtime.stop, names, base_port=port)


@app.command()
def restart(procs: ProcsArg = None, port: PortOpt = DEFAULT_BASE_PORT) -> None:
    """Restart every startwithall=1 process (or specific process name(s))."""
    names = _procs(procs)
    _reject_unknown(names)
    _warn_about_unfed_inputs(names, port)
    _warn_about_connection_cap(names, port)
    _run_streaming(runtime.restart, names, base_port=port)


@app.command("print")
def print_startlines(procs: ProcsArg = None, port: PortOpt = DEFAULT_BASE_PORT) -> None:
    """Show the exact startup command line(s) without starting anything."""
    names = _procs(procs)
    _reject_unknown(names)
    try:
        result = runtime.print_procs(_paths(), names, base_port=port)
    except UqsError as exc:
        _die(exc)
        return
    console.print(result.stdout)
    raise typer.Exit(code=result.returncode)


@app.command()
def clean() -> None:
    """Wipe output/uqs/ (logs, tplogs, wdb, the copied sample data)."""
    stack_paths.clean(_paths())


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def raw(ctx: typer.Context, port: PortOpt = DEFAULT_BASE_PORT) -> None:
    """Pass any other torq.sh verb straight through, e.g.:
    `raw -- debug rdb1`, `raw -- qcon gateway1 admin:admin`, `raw -- top feed1`.
    """
    try:
        result = runtime.run_torq_sh(_paths(), ctx.args, base_port=port, capture=False)
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)
