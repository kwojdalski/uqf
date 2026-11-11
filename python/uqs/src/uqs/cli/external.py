"""The external connectors: `uqs connector start|stop|restart|status|logs NAME`.

Each connector drives a process this repository does not own, publishing onto
the running stack's tickerplant. None goes through torq.sh or process.csv - the
q half of each is an ordinary pipeline row that `uqs start` brings up, some of
them one a sidecar bundle installs first - so they are one group of their own
rather than lifecycle commands. Called
`connector` rather than `feed` (#1417) because a feed is also a TorQ-managed q
process here; these supervise the publisher OUTSIDE the plant.

One `start` for every connector, rather than a sub-app per connector, because
every connector has the same five verbs. Each one's options are its own: one given to
a connector it does not belong to is refused by name, as `uqs job new` refuses an
option for another kind.

This module names no connector itself (#1776). Every connector declares itself in its
`*_feed.py` module - its options, what it produces, its status title -
`external.feeds.discover()` finds them, and `start`'s options are built from
theirs: its signature is generated, and `restart` copies it.

The whole lifecycle lives in this group rather than as `uqs start
connector:NAME` beside the stack's verbs (#1734): a connector is not a torq.sh
process, so it shares none of `start`'s options (`--profile`, `--follow`, the
skips) while each connector has start options of its own, and #1738's rule 1 is
noun, then verb. Its log is one plain file, not a TorQ out_/err_ pair.

A secret option - the tickerplant credential, an API key - is never taken on
the command line (#1853), where `ps`, shell history and the connector's pid
file would show it: `start` takes the NAME of the variable holding it,
`--<option>-env NAME`. The inline spelling still parses for one release,
hidden, and warns.
"""

from __future__ import annotations

import functools
import inspect
import os
from typing import Annotated, Any

import typer
from rich.table import Table

from uqs.cli import completion, renames
from uqs.cli.choices import alternatives
from uqs.cli.json_out import emit, json_opt, snake
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.renames import renamed
from uqs.cli.safety import ChangesState, DryRunOpt, ReadOnly, dry_run_note
from uqs.cli.shared import _die, _paths, app, console
from uqs.cli.stop_options import KillOpt
from uqs.external.feeds import ExternalFeed, FeedOption, check_options, discover
from uqs.paths import UqsError, UqsUsageError, repo_root

connector_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Connectors: start, stop, restart, status, logs.\n\n"
    "The processes outside the stack "
    "(kafka, crypto, crypto-fills, and those installed bundles carry) that write into its "
    "tickerplant. These supervise external processes, not the TorQ-managed q processes "
    "`uqs start` runs.",
)
app.add_typer(connector_app, name="connector")

#: Installed bundle connectors that did not load, and why: each is reported
#: by name - in `status`, and to whoever names it - and every other one works.
BROKEN: dict[str, str] = {}

#: Every connector, as its module declares it (external/feeds.py, #1776): uqs's
#: own, and those of the sidecar bundles installed in this tree.
FEEDS: dict[str, ExternalFeed] = discover(repo_root(), BROKEN)

FeedArg = Annotated[
    str,
    typer.Argument(
        metavar="CONNECTOR",
        # every connector found, each as it describes itself (#1893)
        help=", ".join(f"`{n}` ({f.about})" if f.about else f"`{n}`" for n, f in FEEDS.items())
        + ", or one a bundle installed later carries - `connector status` lists them",
        autocompletion=completion.choices(*FEEDS),
    ),
]

#: What every `connector` command needs: a runtime without it refuses them.
_PLANT = "publishes this tree's tables into the plant"

#: `start` and `restart`'s class: each option's old spellings, for their
#: one-release window (cli/renames.py, #1729), and their mark (#1780).
_START = renamed(
    {
        alias: option.flag
        for option, _takers in check_options(FEEDS).values()
        for alias in option.aliases
    },
    needs_pipelines(_PLANT, ChangesState),
)


def _feed(name: str) -> ExternalFeed:
    if name in BROKEN:
        _die(UqsError(BROKEN[name]))
    if name not in FEEDS:
        _die(UqsError(f"no connector {name!r} - the connectors are {', '.join(FEEDS)}"))
    return FEEDS[name]


#: The suffix of a secret option's parameter that names its variable (#1853):
#: `tp_credential` is given as `--tp-credential-env NAME`.
ENV_SUFFIX = "_env"


def _described(name: str, option: FeedOption, takers: list[str]) -> str:
    """An option's help, its fallback rendered from each declaration (#1893):
    one, where the feeds that take it agree; each feed's, where they do not."""
    declared = {f: next(o for o in FEEDS[f].options if o.name == name) for f in takers}
    if len({o.fallback for o in declared.values()}) == 1:
        return option.described
    return f"{option.help}; " + "; ".join(
        f"{feed}: {o.fallback or 'no default'}" for feed, o in declared.items()
    )


def _start_signature() -> inspect.Signature:
    """`connector start`'s parameters: NAME, then every option any feed
    declares - one CLI option per name, its help naming the feeds that take
    it - then --dry-run. Typer reads this, so the options are the feeds'.

    A secret option is two parameters (#1853): `--<option>-env NAME`, the
    variable holding it, and the inline `--<option>`, hidden - kept for one
    release (cli/rename_expiry.py), warning when used."""
    kind = inspect.Parameter.POSITIONAL_OR_KEYWORD
    params = [inspect.Parameter("name", kind, annotation=FeedArg)]
    for name, (option, takers) in check_options(FEEDS).items():
        feeds = ", ".join(takers)
        described = _described(name, option, takers)
        # each connector's options in a panel of their own, so `--help` says
        # which apply to the one being started (#1858)
        panel = f"Options of {feeds}"
        if option.secret:
            env_help = (
                f"Environment variable holding the {described}. The value itself is "
                f"never printed ({feeds})"
            )
            named = Annotated[
                str | None,
                typer.Option(
                    metavar="NAME", help=env_help, show_default=False, rich_help_panel=panel
                ),
            ]
            params.append(
                inspect.Parameter(name + ENV_SUFFIX, kind, default=None, annotation=named)
            )
        help_ = f"{described} ({feeds})"
        typed = Annotated[
            option.type | None,
            typer.Option(
                help=help_,
                show_default=False,
                hidden=option.secret,
                rich_help_panel=panel,
                metavar=option.shown_as,
            ),
        ]
        params.append(inspect.Parameter(name, kind, default=None, annotation=typed))
    params.append(inspect.Parameter("dry_run", kind, default=False, annotation=DryRunOpt))
    return inspect.Signature(params)


def start(name: str, dry_run: bool = False, **options: Any) -> None:
    """Start a connector publishing onto the running stack's tickerplant.

    kafka: a topic onto `kafka_message`, decoded and deduplicated by kafka_flow1; needs
    a reachable broker and `confluent-kafka` (the uqs `kafka` extra).
    crypto: a sibling cryptorust checkout's order-book recorder into
    `crypto_book` (see $CRYPTORUST_ROOT in crypto.cryptorust_root).
    crypto-fills: cryptorust's fills recorder - paper fills into
    `crypto_sim_fills`, real executions into `crypto_trades`; needs an
    already-running cryptorust OMS.
    A sidecar bundle can carry a connector of its own: installing the bundle
    adds it here, with its options.
    """
    _start(name, options, dry_run)


start.__signature__ = _start_signature()  # ty: ignore[unresolved-attribute]
start = connector_app.command("start", cls=_START)(start)


def _start(name: str, options: dict[str, Any], dry_run: bool = False, verb: str = "start") -> None:
    """Start feed `name` with the options given (refusing any not its own), or dry-run it."""
    feed = _feed(name)
    try:
        given, origins = _secrets_from_env(options)
    except UqsError as exc:
        _die(exc)
    own = {option.name: option for option in feed.options}
    for option in given:
        if option not in own:
            _die(UqsUsageError(f"--{option.replace('_', '-')} does not apply to connector {name}"))
    try:
        # Bindings first (#1786), dry run or not: a topic or table that binds
        # to nothing is refused here, not left to the detached process's log.
        resolved = feed.check_bindings(_paths(), given, origins)
    except UqsError as exc:
        _die(exc)
    if dry_run:
        _would(name, f"{verb} {name} with {_settings(feed, given, origins)}")
        return
    try:
        pid = feed.start(_paths(), resolved)
    except UqsError as exc:
        _die(exc)
    console.print(feed.started(pid))


def _secrets_from_env(options: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The options given, each secret read from the variable its
    `--<option>-env NAME` names, and {option: "$NAME"} for those (#1853).

    An inline secret still works for its one release, warning that it is in
    `ps` and shell history; given with its `-env` form too, it is refused
    rather than one silently winning."""
    secrets = {name for name, (option, _) in check_options(FEEDS).items() if option.secret}
    named = {name: options.get(name + ENV_SUFFIX) for name in secrets}
    given: dict[str, Any] = {
        k: v
        for k, v in options.items()
        if v is not None and not (k.endswith(ENV_SUFFIX) and k[: -len(ENV_SUFFIX)] in secrets)
    }
    origins: dict[str, str] = {}
    for key, variable in named.items():
        flag = "--" + key.replace("_", "-")
        if variable is None:
            if key in given:
                renames.warn_secret_argv(flag, f"{flag}-env NAME")
            continue
        if key in given:
            raise UqsUsageError(f"give {flag}-env or {flag}, not both")
        value = os.environ.get(variable, "")
        if not value.strip():
            raise UqsError(f"{flag}-env {variable}: ${variable} is not set")
        given[key], origins[key] = value, f"${variable}"
    return given, origins


def _settings(feed: ExternalFeed, given: dict[str, Any], origins: dict[str, str]) -> str:
    """A dry run's settings: each option given, or taken from its variable
    (named), a secret as ***. The defaults are not listed."""
    shown = []
    for option in feed.options:
        origin = origins.get(option.name) or option.origin(given.get(option.name))
        if origin == "default":
            continue
        value = "***" if option.secret else option.value(given.get(option.name))
        if isinstance(value, tuple):
            value = ",".join(value)
        shown.append(f"{option.name}={value}" + ("" if origin == option.flag else f" ({origin})"))
    return ", ".join(shown) or "its defaults"


def _would(name: str, action: str) -> None:
    """A connector dry run's lines: what it would do, and whether it runs now."""
    try:
        running = _feed(name).status(_paths()).get("running") == "True"
    except UqsError as exc:
        _die(exc)
    console.print(f"would {action} - it is {'running' if running else 'not running'}", markup=False)
    console.print(dry_run_note())


@connector_app.command("stop", cls=needs_pipelines(_PLANT, ChangesState))
def stop(name: FeedArg, kill: KillOpt = False, dry_run: DryRunOpt = False) -> None:
    """Stop a connector started by `uqs connector start`.

    It is forgotten only once it has exited: one still running after SIGTERM
    and its grace is named, kept, and still refuses a second start - unless
    `--kill` SIGKILLs it."""
    if dry_run:
        _would(name, f"stop {name}")
        return
    try:
        _feed(name).stop(_paths(), kill=kill)
    except UqsError as exc:
        _die(exc)
    console.print(f"{name} connector stopped")


def _takes_start_options(fn: Any) -> Any:
    """`fn` with `start`'s parameters, so `restart` takes exactly its options
    and cannot drift from them; `fn` keeps its own name and help."""
    doc, name = fn.__doc__, fn.__name__
    functools.update_wrapper(fn, start)
    fn.__doc__, fn.__name__, fn.__qualname__ = doc, name, name
    return fn


@connector_app.command("restart", cls=_START)
@_takes_start_options
def restart(name: str, dry_run: bool = False, **options: Any) -> None:
    """Restart a connector with the options given here.

    Stops a connector if it is running, then starts it again - with exactly
    `start`'s options, which are not remembered from the last start."""
    feed = _feed(name)
    if dry_run:
        return _start(name, options, dry_run=True, verb="restart")
    try:
        if feed.status(_paths()).get("running") == "True":
            feed.stop(_paths())
            console.print(f"{name} connector stopped")
    except UqsError as exc:
        _die(exc)
    _start(name, options)


StatusJson = json_opt(
    '{"connectors": [{name, running: bool, pid: int or null, log, '
    "...each connector's own fields}], "
    '"broken": [{name, error}] only when an installed connector did not load}'
)


def _status_record(name: str, fields: dict[str, str]) -> dict:
    """One feed's status table as a record: `running` a bool, `pid` an int or null."""
    record: dict = {"name": name}
    for key, value in fields.items():
        record[snake(key)] = value
    record["running"] = fields.get("running") == "True"
    record["pid"] = int(fields["pid"]) if fields.get("pid", "").isdigit() else None
    return record


@connector_app.command("status", cls=needs_pipelines(_PLANT, ReadOnly))
def status(
    name: Annotated[
        str | None,
        typer.Argument(
            metavar="CONNECTOR",
            help=f"One connector - {alternatives(FEEDS)}; omit it for every connector",
            autocompletion=completion.choices(*FEEDS),
        ),
    ] = None,
    as_json: StatusJson = False,
) -> None:
    """Show whether a connector is running, its pid, and where its log lives."""
    found = []
    for each in [name] if name is not None else list(FEEDS):
        feed = _feed(each)
        found.append((each, feed.status_title, feed.status(_paths())))
    broken = {} if name is not None else BROKEN
    if as_json:
        doc: dict = {"connectors": [_status_record(each, fields) for each, _, fields in found]}
        if broken:
            doc["broken"] = [{"name": n, "error": why} for n, why in broken.items()]
        emit(doc)
        return
    for _, title, fields in found:
        table = Table(title=title)
        table.add_column("field")
        table.add_column("value")
        for k, v in fields.items():
            table.add_row(k, v)
        console.print(table)
    for why in dict.fromkeys(broken.values()):
        console.print(why, style="red", markup=False)
