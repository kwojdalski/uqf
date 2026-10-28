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

from typing import Annotated, Any

import typer

from uqs.attach import live as _registers_live_check  # noqa: F401 - the start gate's (#1651)
from uqs.cli import completion, start_follow, start_warnings
from uqs.cli.force_stop import ForceOpt, _force_stop
from uqs.cli.renames import BASE_PORT, alias_command, renamed
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
from uqs.cli.start_follow import FollowOpt, LevelOpt
from uqs.cli.start_override_opts import SkipProfileBudgetOpt, SkipQVersionOpt
from uqs.cli.start_override_opts import parsed as _overrides
from uqs.cli.start_warnings import running as _running
from uqs.model import profiles
from uqs.paths import UqsError
from uqs.stack import procs as procs_model
from uqs.stack import runtime, start_policy
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


ShowCommandOpt = Annotated[
    bool,
    typer.Option("--show-command", help="Show the exact start line(s); start nothing"),
]

#: The options `start` and `restart` share, word for word (#1734): the
#: selection `stop` takes too - PROC..., --profile, --base-port - and what a
#: launch adds to it. A test holds the two signatures equal.
_LAUNCH_RENAMES = BASE_PORT | {"--print": "--show-command"}


def _launch(
    verb: str,
    procs: list[str] | None,
    port: int | None,
    profile: str | None,
    follow: bool,
    level: str | None,
    print_only: bool,
    skip_q_version_check: str | None,
    skip_profile_budget_check: str | None,
) -> None:
    """`start` and `restart`, which differ only in the torq.sh verb they run."""
    if level is not None and not follow:
        _die(UqsError("--level needs --follow"))
    if print_only and follow:
        _die(UqsError("--show-command does not apply with --follow"))
    overrides = _overrides(
        skip_q_version_check, skip_profile_budget_check, profile=profile, print_only=print_only
    )
    names = _names_to_start(procs, profile, overrides)
    if print_only:
        _print_startlines(names, port)
        return
    launch = runtime.start if verb == "start" else runtime.restart
    selected = _selected(names, profile)
    kwargs: dict[str, Any] = {"base_port": port, "selected": selected, "skips": overrides}
    if not follow:
        _launch_checks(verb, names, port, profile)
        _run_streaming(launch, names, **kwargs)
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
        start_follow.in_foreground(lambda **kw: launch(_paths(), names, **kwargs, **kw))()

    start_follow.follow(wanted, already, before, port, level)


@app.command(cls=renamed(_LAUNCH_RENAMES))
def start(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: ProfileOpt = None,
    follow: FollowOpt = False,
    level: LevelOpt = None,
    print_only: ShowCommandOpt = False,
    skip_q_version_check: SkipQVersionOpt = None,
    skip_profile_budget_check: SkipProfileBudgetOpt = None,
) -> None:
    """Start every startwithall=1 process (or specific process name(s)).

    `--profile fx` starts a named set instead: its leaves and everything they
    read, resolved from the dependency graph rather than listed by hand.
    `--profile essential vectorize1` starts the set plus the names given.

    `--follow` (`-f`) is the foreground form, the way `docker compose up` is:
    it streams every started process's log until Ctrl-C, then stops what it
    started - `start -f`, `start rdb1 fxpositions1 -f`, `start --profile fx -f
    --level WARNING`. What was already running is left running.
    """
    _launch(
        "start", procs, port, profile, follow, level, print_only,
        skip_q_version_check, skip_profile_budget_check,
    )  # fmt: skip


@app.command(cls=renamed(_LAUNCH_RENAMES))
def restart(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: ProfileOpt = None,
    follow: FollowOpt = False,
    level: LevelOpt = None,
    print_only: ShowCommandOpt = False,
    skip_q_version_check: SkipQVersionOpt = None,
    skip_profile_budget_check: SkipProfileBudgetOpt = None,
) -> None:
    """Restart every startwithall=1 process (or specific process name(s)).

    Takes exactly `start`'s options: `--profile` restarts a named set,
    `--follow` streams the restarted processes' logs until Ctrl-C.
    """
    _launch(
        "restart", procs, port, profile, follow, level, print_only,
        skip_q_version_check, skip_profile_budget_check,
    )  # fmt: skip


def _up(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: ProfileOpt = None,
    level: LevelOpt = None,
    skip_q_version_check: SkipQVersionOpt = None,
    skip_profile_budget_check: SkipProfileBudgetOpt = None,
) -> None:
    """`start --follow`, under the name it had before #1734."""
    _launch(
        "start", procs, port, profile, True, level, False,
        skip_q_version_check, skip_profile_budget_check,
    )  # fmt: skip


alias_command(app, "up", "uqs start --follow", _up, cls=renamed(BASE_PORT))


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


@app.command(cls=renamed(BASE_PORT | {"--force": "--kill"}))
def stop(
    procs: ProcsArg = None,
    port: BasePortOpt = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="Comma-separated named start set(s) to stop - see `uqs list profiles`. "
            "Process names given as well are added to the set",
            autocompletion=completion.profiles,
        ),
    ] = None,
    force: ForceOpt = False,
) -> None:
    """Stop every startwithall=1 process (or specific process name(s), or a profile's)."""
    names = _names_to_stop(procs, profile)
    _reject_unknown(names)
    if force:
        _force_stop(names, port)
    _run_streaming(runtime.stop, names, base_port=port)


def _print_startlines(names: str, port: int | None) -> None:
    """Print the startup command line(s) for `names`, then exit - `start --show-command`."""
    _reject_unknown(names)
    try:
        result = runtime.print_procs(_paths(), names, base_port=port)
    except UqsError as exc:
        _die(exc)
        return
    console.print(result.stdout)
    raise typer.Exit(code=result.returncode)


def _exec(ctx: typer.Context, port: BasePortOpt = None) -> None:
    """Run any other torq.sh verb, passed straight through, e.g.:
    `exec -- debug rdb1`, `exec -- qcon gateway1 admin:admin`, `exec -- top feed1`.
    """
    try:
        result = runtime.run_torq_sh(_paths(), ctx.args, base_port=port, capture=False)
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)


app.command("exec", context_settings=PASS_THROUGH, cls=renamed(BASE_PORT))(_exec)
alias_command(app, "raw", "uqs exec", _exec, context_settings=PASS_THROUGH, cls=renamed(BASE_PORT))
