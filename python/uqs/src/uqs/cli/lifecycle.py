"""Starting, stopping and restarting the fleet as processes.

The commands that drive torq.sh: `start`, `restart` and `stop`, which share
one option set (#1734), and `exec`, any other torq.sh verb. The warnings a
start prints are cli/start_warnings.py; `--follow` is cli/start_follow.py.
Split from cli/entry.py, which had grown to 1270 lines across twenty-four
commands - past the 400 this package holds its modules to.

Commands register onto the shared `app` here rather than onto a sub-Typer,
so `uqs start` stays `uqs start`: the split is about where the
code lives, not about what anyone types.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

import typer

from uqs.attach import live as _registers_live_check  # noqa: F401 - the start gate's (#1651)
from uqs.cli import completion, start_follow, start_warnings, vocabulary
from uqs.cli.force_stop import ForceOpt, _force_stop
from uqs.cli.safety import ChangesState, Effect, OverrideOpt, ReasonOpt, dry_run_note, marked
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
from uqs.cli.start_follow import FollowOpt, LevelOpt, StopOnExitOpt
from uqs.cli.start_override_opts import parsed as _overrides
from uqs.cli.start_warnings import TraceOpt
from uqs.cli.start_warnings import running as _running
from uqs.model import profiles
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import procs as procs_model
from uqs.stack import runtime, start_check, start_policy
from uqs.stack.start_overrides import NONE, Overrides

ProfileOpt = Annotated[
    str | None,
    typer.Option(
        "--profile",
        metavar="PROFILE,...",
        help=(
            "Comma-separated named start set(s) - see `uqs profile list`. "
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


def _selected(names: str, profile: str | None) -> list[str] | None:
    """A profile start's resolved set - what TorQ is let discover
    (stack/deployment.py) - or None for a start that only adds to it."""
    return names.split() if profile is not None else None


def _launch_checks(verb: str, names: str, port: int | None, profile: str | None) -> None:
    """What runs before torq.sh is asked to start anything: the name check,
    then the advisory warnings, which count the requested set."""
    _reject_unknown(names)
    start_warnings.unfed_inputs(names, port)
    start_warnings.connection_cap(names, port)
    start_warnings.monitor_coverage(verb, names, profile)


DryRunOpt = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        "-n",
        help="Show the exact start line(s), and what a restart stops; change nothing",
    ),
]

GraceOpt = Annotated[
    float,
    vocabulary.duration(
        "--grace",
        help=(
            "Wait DURATION after launching, then fail if a process this started "
            f"has died or another process holds its port (default {start_check.DEFAULT_GRACE:g}s)"
        ),
        show_default=False,
    ),
]

#: The options `start` and `restart` share, word for word (#1734): the
#: selection `stop` takes too - PROC..., --profile, --base-port - and what a
#: launch adds to it. A test holds the two signatures equal.
#: `--skip-q-version-check` and `--skip-profile-budget-check` are generated
#: from the guardrails (#1918).


def _launch(
    verb: str,
    procs: list[str] | None,
    port: int | None,
    profile: str | None,
    follow: bool,
    level: str | None,
    print_only: bool,
    override: list[str] | None,
    reason: str | None,
    stop_on_exit: bool = False,
    grace: float = start_check.DEFAULT_GRACE,
    trace: bool = False,
) -> None:
    """`start` and `restart`, which differ only in the torq.sh verb they run."""
    if level is not None and not follow:
        _die(UqsUsageError("--level needs --follow"))
    if stop_on_exit and not follow:
        _die(UqsUsageError("--stop-on-exit needs --follow: it stops what the stream started"))
    if print_only and follow:
        _die(UqsUsageError("--dry-run does not apply with --follow"))
    overrides = _overrides(verb, override, reason, profile=profile, print_only=print_only)
    names = _names_to_start(procs, profile, overrides)
    if print_only:
        _print_startlines(verb, names, port, profile, trace)
        return
    launch = runtime.start if verb == "start" else runtime.restart
    selected = _selected(names, profile)
    kwargs: dict[str, Any] = {"base_port": port, "selected": selected, "skips": overrides}
    kwargs["trace"] = trace  # -extras -trace on each start line (runtime.TRACE_EXTRAS)
    untraced = start_warnings.untraced(verb, names, port, _running) if trace else []
    if not follow:
        _launch_checks(verb, names, port, profile)
        _launch_and_confirm(verb, launch, names, port, grace, kwargs)
        start_warnings.trace_unreached(untraced, port)
        return
    try:
        wanted = start_follow.procnames_started_by(names, port)
    except UqsError as exc:
        _die(exc)
        return
    try:
        already: set[str] | None = _running(port)
    except (UqsError, OSError) as exc:  # not knowing only widens the stop
        log.debug("could not tell what was already running: {}", exc)
        already = None

    def before() -> None:
        _launch_checks(verb, names, port, profile)
        start_warnings.trace_unreached(untraced, port)
        start_follow.in_foreground(lambda **kw: launch(_paths(), names, **kwargs, **kw))()

    start_follow.follow(wanted, already, before, port, level, stop=stop_on_exit)


def _launch_and_confirm(
    verb: str, launch: Any, names: str, port: int | None, grace: float, kwargs: dict[str, Any]
) -> None:
    """Launch, then wait `grace` and fail, naming each one, if a process this
    started died or is not the one on its port (#2180): torq.sh returns once
    each is forked, not once it is up."""
    try:
        before = start_check.snapshot(_paths(), port)
        since = time.time()
        result = launch(_paths(), names, capture=False, **kwargs)
    except UqsError as exc:
        _die(exc)
        return
    if result.returncode:
        raise typer.Exit(code=result.returncode)
    failed = start_check.confirm(
        _paths(),
        lambda: start_follow.procnames_started_by(names, port),
        before,
        port,
        grace,
        restarted=verb == "restart",
        since=since,
    )
    if failed:
        _die(UqsError(start_check.describe(failed, grace)))


@app.command(cls=ChangesState)
def start(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: ProfileOpt = None,
    follow: FollowOpt = False,
    stop_on_exit: StopOnExitOpt = False,
    level: LevelOpt = None,
    print_only: DryRunOpt = False,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
    grace: GraceOpt = start_check.DEFAULT_GRACE,
    trace: TraceOpt = False,
) -> None:
    """Start every startwithall=1 process (or specific process name(s)).

    `--profile fx` starts a named set instead: its leaves and everything they
    read, resolved from the dependency graph rather than listed by hand.
    `--profile essential vectorize1` starts the set plus the names given.

    `--follow` (`-f`) stays in the foreground, streaming every started
    process's log until Ctrl-C, which ends the stream and leaves the processes
    running - `start -f`, `start rdb1 fxpositions1 -f`, `start --profile fx -f
    --level WARNING`. `--stop-on-exit` makes it the foreground form `docker
    compose up` is: Ctrl-C then stops what it started, and leaves what was
    already running. `--trace` turns source-query tracing on in each process
    it starts, before its first source request, and names any already running.
    """
    _launch(
        "start", procs, port, profile, follow, level, print_only,
        override, reason, stop_on_exit, grace, trace,
    )  # fmt: skip


@app.command(cls=ChangesState)
def restart(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: ProfileOpt = None,
    follow: FollowOpt = False,
    stop_on_exit: StopOnExitOpt = False,
    level: LevelOpt = None,
    print_only: DryRunOpt = False,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
    grace: GraceOpt = start_check.DEFAULT_GRACE,
    trace: TraceOpt = False,
) -> None:
    """Restart every startwithall=1 process (or specific process name(s)).

    Takes exactly `start`'s options: `--profile` restarts a named set,
    `--follow` streams the restarted processes' logs until Ctrl-C, which ends
    the stream and leaves them running; `--stop-on-exit` stops them then.
    """
    _launch(
        "restart", procs, port, profile, follow, level, print_only,
        override, reason, stop_on_exit, grace, trace,
    )  # fmt: skip


def _names_to_stop(procs: list[str] | None, profile: str | None) -> str:
    """What torq.sh is asked to stop: as `_names_to_start`, but with no
    budget - a profile too big to start must still be stoppable."""
    if profile is None:
        return _procs(procs)
    try:
        members, _ = start_policy.profile_members(_paths(), profile, procs or [])
    except UqsError as exc:
        _die(exc)
        raise  # unreachable: _die exits
    return " ".join(members)


@app.command(cls=ChangesState)
def stop(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            metavar="PROFILE,...",
            help="Comma-separated named start set(s) to stop - see `uqs profile list`. "
            "Process names given as well are added to the set",
            autocompletion=completion.profiles,
        ),
    ] = None,
    force: ForceOpt = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Name what would be stopped; stop nothing"),
    ] = False,
) -> None:
    """Stop every startwithall=1 process (or specific process name(s), or a profile's)."""
    names = _names_to_stop(procs, profile)
    _reject_unknown(names)
    if dry_run:
        try:
            wanted = start_follow.procnames_started_by(names, port)
        except UqsError as exc:
            _die(exc)
            return
        console.print(f"would {'kill' if force else 'stop'}: {' '.join(wanted)}", markup=False)
        console.print(dry_run_note())
        return
    if force:
        _force_stop(names, port)
    _run_streaming(runtime.stop, names, base_port=port)


def _print_startlines(
    verb: str, names: str, port: int | None, profile: str | None, trace: bool = False
) -> None:
    """`start --dry-run` and `restart --dry-run`: what a restart would stop, then
    each start line - composed, never bootstrapped, so nothing is written here
    or on a server `uqs --target` sent it to (stack/start_lines.py, #1880)."""
    _reject_unknown(names)
    try:
        stopping = start_follow.procnames_started_by(names, port) if verb == "restart" else []
        result = runtime.print_procs(
            _paths(), names, base_port=port, selected=_selected(names, profile), trace=trace
        )
    except UqsError as exc:
        _die(exc)
        return
    if stopping:
        console.print(f"would stop: {' '.join(stopping)}", markup=False)
    console.print(result.stdout.rstrip("\n"), markup=False, highlight=False)
    if result.returncode:
        raise typer.Exit(code=result.returncode)
    console.print(dry_run_note())


def _exec(ctx: typer.Context, port: BasePortOpt = None) -> None:
    """Run any other torq.sh verb, passed straight through.

    For example:
    `exec -- debug rdb1`, `exec -- qcon gateway1 admin:admin`, `exec -- top feed1`.
    """
    try:
        result = runtime.run_torq_sh(_paths(), ctx.args, base_port=port, capture=False)
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)


_EXEC = marked(Effect.CHANGES_STATE, own_output="passes any verb through to torq.sh")
app.command("exec", context_settings=PASS_THROUGH, cls=_EXEC)(_exec)
