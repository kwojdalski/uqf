"""Starting, stopping and inspecting the fleet as processes.

The commands that drive torq.sh; the advisory warnings that run before a
start are in cli/start_warnings.py. Split from cli/entry.py, which had grown to 1270 lines across
twenty-four commands - past the 400 this package holds its modules to.

Commands register onto the shared `app` here rather than onto a sub-Typer,
so `uqs start` stays `uqs start`: the split is about where the
code lives, not about what anyone types.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.attach import live as _registers_live_check  # noqa: F401 - the start gate's (#1651)
from uqs.cli import completion
from uqs.cli.force_stop import ForceOpt, _force_stop
from uqs.cli.renames import BASE_PORT, renamed
from uqs.cli.safety import OverrideOpt, ReasonOpt, dry_run_note
from uqs.cli.shared import (
    PASS_THROUGH,
    BasePortOpt,
    ProcsArg,
    _die,
    _paths,
    _procs,
    _run_streaming,
    app,
    console,
    log,
)
from uqs.cli.start_override_opts import OLD_SPELLINGS as OLD_SKIPS
from uqs.cli.start_override_opts import parsed as _overrides
from uqs.cli.start_warnings import (
    _note_monitor_coverage,
    _running,
    _selected,
    _warn_about_connection_cap,
    _warn_about_unfed_inputs,
)
from uqs.model import profiles
from uqs.paths import UqsError
from uqs.stack import listing, runtime, start_policy
from uqs.stack import logs as stack_logs
from uqs.stack import procs as procs_model
from uqs.stack.start_overrides import NONE, Overrides

ProfileOpt = Annotated[
    str | None,
    typer.Option(
        "--profile",
        help=(
            "Comma-separated named start set(s) - see `uqs list profiles`. "
            "Process names given as well are added to the set. Refused if the "
            "total is past the licence's connection cap."
        ),
        autocompletion=completion.profiles,
    ),
]


def _resolve_profiles(
    names: str, extra: list[str] | None = None, overrides: Overrides = NONE
) -> str:
    """The space-separated process list `names` stands for, plus `extra`
    process names, or exit - refused by stack.start_policy, which every
    front end shares (#887); only the summary line is the CLI's."""
    try:
        members, what = start_policy.resolve_profiles(_paths(), names, extra or [], overrides)
    except UqsError as exc:
        _die(exc)
        raise  # unreachable: _die exits. Keeps the type checker honest.
    console.print(
        f"[dim]{what}: {len(members)} process(es), "
        f"{profiles.plant_slots(members)}/{profiles.allowance() or 'no cap'} plant slots[/]"
    )
    return " ".join(members)


def _names_to_start(
    procs: list[str] | None, profile: str | None, overrides: Overrides = NONE
) -> str:
    """What torq.sh is asked to start: the names given, `all`, a profile, or a
    profile with names added to it."""
    if profile is not None:
        return _resolve_profiles(profile, procs, overrides)
    return _procs(procs)


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


def _start(
    names: str, port: int | None, profile: str | None = None, overrides: Overrides = NONE
) -> None:
    _reject_unknown(names)
    _warn_about_unfed_inputs(names, port)
    _warn_about_connection_cap(names, port)
    _note_monitor_coverage("start", names, profile)
    selected = _selected(names, profile)
    _run_streaming(runtime.start, names, base_port=port, selected=selected, skips=overrides)


def _start_in_foreground(
    names: str, port: int | None, profile: str | None = None, overrides: Overrides = NONE
) -> None:
    """`_start`, minus its ending: _run_streaming always exits the command,
    and `up` has the whole run still to stream after the start returns."""
    _reject_unknown(names)
    _warn_about_unfed_inputs(names, port)
    _warn_about_connection_cap(names, port)
    _note_monitor_coverage("start", names, profile)
    selected = _selected(names, profile)
    result = runtime.start(
        _paths(), names, base_port=port, capture=False, selected=selected, skips=overrides
    )
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)


#: start's old spellings: --print, then --show-command, are --dry-run (#1732).
_START_RENAMES = {"--print": "--dry-run", "--show-command": "--dry-run"}


@app.command(cls=renamed(BASE_PORT | _START_RENAMES | OLD_SKIPS))
def start(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: ProfileOpt = None,
    print_only: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show the exact start line(s); start nothing"),
    ] = False,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
) -> None:
    """Start every startwithall=1 process (or specific process name(s)).

    `--profile fx` starts a named set instead: its leaves and everything they
    read, resolved from the dependency graph rather than listed by hand.
    `--profile essential vectorize1` starts the set plus the names given.
    """
    overrides = _overrides("start", override, reason, profile=profile, print_only=print_only)
    names = _names_to_start(procs, profile, overrides)
    if print_only:
        _print_startlines(names, port)
        return
    _start(names, port, profile, overrides)


LevelOpt = Annotated[
    str | None,
    typer.Option(
        help=stack_logs.LEVEL_HELP,
        autocompletion=completion.choices(*stack_logs.LEVEL_CHOICES),
    ),
]


def _procnames_started_by(names: str, port: int | None) -> list[str]:
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


@app.command(cls=renamed(BASE_PORT | OLD_SKIPS))
def up(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: ProfileOpt = None,
    level: LevelOpt = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show the exact start line(s); start nothing"),
    ] = False,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
) -> None:
    """Start, and stream every started process's log to this console until
    Ctrl-C - which stops what this started. `up`, `up rdb1 fxpositions1`,
    `up --profile fx`, `up --profile essential vectorize1`, `up --level WARNING`.

    The foreground form of `start` + `logs -f`, the way `docker compose up`
    is: the console is the run. Processes that were already running when it
    began are left running; if `summary` cannot say which those were, Ctrl-C
    stops everything this was asked to start.
    """
    overrides = _overrides("up", override, reason, profile=profile, print_only=dry_run)
    names = _names_to_start(procs, profile, overrides)
    if dry_run:
        _print_startlines(names, port)
        return
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
            _paths(),
            wanted,
            lambda: _start_in_foreground(names, port, profile, overrides),
            min_level=level,
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


@app.command(cls=renamed(BASE_PORT | {"--force": "--kill"}))
def stop(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    force: ForceOpt = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Name what would be stopped; stop nothing"),
    ] = False,
) -> None:
    """Stop every startwithall=1 process (or specific process name(s))."""
    names = _procs(procs)
    _reject_unknown(names)
    if dry_run:
        _would("kill" if force else "stop", names, port)
        return
    if force:
        _force_stop(names, port)
    _run_streaming(runtime.stop, names, base_port=port)


@app.command(cls=renamed(BASE_PORT | OLD_SKIPS))
def restart(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Name what would stop, show the start line(s); change nothing"
        ),
    ] = False,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
) -> None:
    """Restart every startwithall=1 process (or specific process name(s))."""
    overrides = _overrides("restart", override, reason, print_only=dry_run)
    names = _procs(procs)
    _reject_unknown(names)
    if dry_run:
        _would("stop", names, port)
        _print_startlines(names, port)
        return
    _warn_about_unfed_inputs(names, port)
    _warn_about_connection_cap(names, port)
    _note_monitor_coverage("restart", names)
    _run_streaming(runtime.restart, names, base_port=port, skips=overrides)


def _would(verb: str, names: str, port: int | None) -> None:
    """A lifecycle dry run's line: the processes `verb` would act on."""
    try:
        wanted = _procnames_started_by(names, port)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"would {verb}: {' '.join(wanted) or 'nothing'}", markup=False)
    console.print(dry_run_note())


def _print_startlines(names: str, port: int | None) -> None:
    """Print the startup command line(s) for `names`, then exit - `start --dry-run`."""
    _reject_unknown(names)
    try:
        result = runtime.print_procs(_paths(), names, base_port=port)
    except UqsError as exc:
        _die(exc)
        return
    console.print(result.stdout)
    raise typer.Exit(code=result.returncode)


@app.command(context_settings=PASS_THROUGH, cls=renamed(BASE_PORT))
def raw(ctx: typer.Context, port: BasePortOpt = None) -> None:
    """Pass any other torq.sh verb straight through, e.g.:
    `raw -- debug rdb1`, `raw -- qcon gateway1 admin:admin`, `raw -- top feed1`.
    """
    try:
        result = runtime.run_torq_sh(_paths(), ctx.args, base_port=port, capture=False)
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)
