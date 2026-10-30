"""The external connectors: `uqs connector start|stop|restart|status|logs NAME`.

Each connector drives a process this repository does not own, publishing onto
the running stack's tickerplant. None goes through torq.sh or process.csv - the
q half of each is an ordinary pipeline row that `uqs start` brings up, some of
them one a sidecar bundle installs first - so they are one group of their own
rather than lifecycle commands. Called
`connector` rather than `feed` (#1417) because a feed is also a TorQ-managed q
process here; these supervise the publisher OUTSIDE the plant.

One `start` for every feed, rather than a sub-app per feed, because every
feed has the same five verbs. Each feed's options are its own: one given to a
feed it does not belong to is refused by name, as `uqs job new` refuses an
option for another kind.

This module names no feed (#1776). Every connector declares itself in its
`*_feed.py` module - its options, what it produces, its status title -
`external.feeds.discover()` finds them, and `start`'s options are built from
theirs: its signature is generated, and `restart` copies it.

The whole lifecycle lives in this group rather than as `uqs start
connector:NAME` beside the stack's verbs (#1734): a connector is not a torq.sh
process, so it shares none of `start`'s options (`--profile`, `--follow`, the
skips) while each feed has start options of its own, and #1738's rule 1 is
noun, then verb. Its log is one plain file, not a TorQ out_/err_ pair.
"""

from __future__ import annotations

import functools
import inspect
from typing import Annotated, Any

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.json_out import emit, json_opt, snake
from uqs.cli.renames import renamed
from uqs.cli.safety import ChangesState, DryRunOpt, ReadOnly, dry_run_note
from uqs.cli.shared import _die, _paths, app, console
from uqs.external.feeds import ExternalFeed, check_options, discover
from uqs.paths import UqsError

connector_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="External connectors: start, stop, restart, status and logs of the "
    "publishers outside the plant "
    "(databento, kafka, crypto, crypto-fills) that write into its tickerplant. These "
    "supervise external processes, not the TorQ-managed q processes `uqs start` runs.",
)
app.add_typer(connector_app, name="connector")

#: Every connector, as its module declares it (external/feeds.py, #1776).
FEEDS: dict[str, ExternalFeed] = discover()

FeedArg = Annotated[
    str,
    typer.Argument(
        help="databento, kafka, crypto (order books) or crypto-fills (the bot's fills)",
        autocompletion=completion.choices(*FEEDS),
    ),
]

#: `start` and `restart`'s class: each option's old spellings, for their
#: one-release window (cli/renames.py, #1729), and their mark (#1780).
_START = renamed(
    {
        alias: option.flag
        for option, _takers in check_options(FEEDS).values()
        for alias in option.aliases
    },
    ChangesState,
)


def _feed(name: str) -> ExternalFeed:
    if name not in FEEDS:
        _die(UqsError(f"no feed {name!r} - the feeds are {', '.join(FEEDS)}"))
        raise typer.Exit(code=1)
    return FEEDS[name]


def _start_signature() -> inspect.Signature:
    """`connector start`'s parameters: NAME, then every option any feed
    declares - one CLI option per name, its help naming the feeds that take
    it - then --dry-run. Typer reads this, so the options are the feeds'."""
    kind = inspect.Parameter.POSITIONAL_OR_KEYWORD
    params = [inspect.Parameter("name", kind, annotation=FeedArg)]
    for name, (option, takers) in check_options(FEEDS).items():
        help_ = f"{option.help} ({', '.join(takers)})"
        typed = Annotated[option.type | None, typer.Option(help=help_, show_default=False)]
        params.append(inspect.Parameter(name, kind, default=None, annotation=typed))
    params.append(inspect.Parameter("dry_run", kind, default=False, annotation=DryRunOpt))
    return inspect.Signature(params)


def start(name: str, dry_run: bool = False, **options: Any) -> None:
    """Start a feed publishing onto the running stack's tickerplant.

    databento: live MBP-10 onto `databento_mbp10`, folded by databento1; needs the
    databento bundle (`uqs job install sidecars/databento`).
    kafka: a topic onto `kafka_message`, decoded and deduplicated by kafka_flow1; needs
    a reachable broker and `confluent-kafka` (the uqs `kafka` extra).
    crypto: a sibling cryptorust checkout's order-book recorder into
    `crypto_book` (see $CRYPTORUST_ROOT in crypto.cryptorust_root).
    crypto-fills: cryptorust's fills recorder - paper fills into
    `crypto_sim_fills`, real executions into `crypto_trades`; needs an
    already-running cryptorust OMS.
    """
    _start(name, options, dry_run)


start.__signature__ = _start_signature()  # ty: ignore[unresolved-attribute]
start = connector_app.command("start", cls=_START)(start)


def _start(name: str, options: dict[str, Any], dry_run: bool = False, verb: str = "start") -> None:
    """Start feed `name` with the options given (refusing any not its own), or dry-run it."""
    feed = _feed(name)
    given = {k: v for k, v in options.items() if v is not None}
    own = {option.name: option for option in feed.options}
    for option in given:
        if option not in own:
            _die(UqsError(f"--{option.replace('_', '-')} does not apply to feed {name}"))
            return
    if dry_run:
        shown = {k: ("***" if o.secret else given[k]) for k, o in own.items() if k in given}
        settings = ", ".join(f"{k}={v}" for k, v in shown.items()) or "its defaults"
        _would(name, f"{verb} {name} with {settings}")
        return
    try:
        pid = feed.start(_paths(), feed.start_options(given))
    except UqsError as exc:
        _die(exc)
        return
    console.print(feed.started(pid))


def _would(name: str, action: str) -> None:
    """A connector dry run's lines: what it would do, and whether it runs now."""
    try:
        running = _feed(name).status(_paths()).get("running") == "True"
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"would {action} - it is {'running' if running else 'not running'}", markup=False)
    console.print(dry_run_note())


@connector_app.command("stop", cls=ChangesState)
def stop(name: FeedArg, dry_run: DryRunOpt = False) -> None:
    """Stop a feed started by `uqs connector start`."""
    if dry_run:
        _would(name, f"stop {name}")
        return
    try:
        _feed(name).stop(_paths())
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"{name} feed stopped")


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
    """Stop a feed if it is running, then start it again - with exactly
    `start`'s options, which are not remembered from the last start."""
    feed = _feed(name)
    if dry_run:
        return _start(name, options, dry_run=True, verb="restart")
    try:
        if feed.status(_paths()).get("running") == "True":
            feed.stop(_paths())
            console.print(f"{name} feed stopped")
    except UqsError as exc:
        _die(exc)
        return
    _start(name, options)


StatusJson = json_opt(
    '{"connectors": [{name, running: bool, pid: int or null, log, ...each feed\'s own fields}]}'
)


def _status_record(name: str, fields: dict[str, str]) -> dict:
    """One feed's status table as a record: `running` a bool, `pid` an int or null."""
    record: dict = {"name": name}
    for key, value in fields.items():
        record[snake(key)] = value
    record["running"] = fields.get("running") == "True"
    record["pid"] = int(fields["pid"]) if fields.get("pid", "").isdigit() else None
    return record


@connector_app.command("status", cls=ReadOnly)
def status(
    name: Annotated[
        str | None,
        typer.Argument(
            help="One feed; omit it for every feed",
            autocompletion=completion.choices(*FEEDS),
        ),
    ] = None,
    as_json: StatusJson = False,
) -> None:
    """Whether a feed is running, its pid, and where its log lives."""
    found = []
    for each in [name] if name is not None else list(FEEDS):
        feed = _feed(each)
        found.append((each, feed.status_title, feed.status(_paths())))
    if as_json:
        emit({"connectors": [_status_record(each, fields) for each, _, fields in found]})
        return
    for _, title, fields in found:
        table = Table(title=title)
        table.add_column("field")
        table.add_column("value")
        for k, v in fields.items():
            table.add_row(k, v)
        console.print(table)
