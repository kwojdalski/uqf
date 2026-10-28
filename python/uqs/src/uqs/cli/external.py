"""The external connectors: `uqs connector start|stop|restart|status|logs NAME`.

Databento, Kafka, and cryptorust's two recorders each drive a process this
repository does not own, publishing onto the running stack's tickerplant. They
do not go through torq.sh or process.csv - the q half of each (databento1,
kafka_flow1, ...) is an ordinary pipeline row that `uqs start` brings up - so
they are one group of their own rather than lifecycle commands. Called
`connector` rather than `feed` (#1417) because a feed is also a TorQ-managed q
process here; these supervise the publisher OUTSIDE the plant.

One `start` for every feed, rather than a sub-app per feed, because every
feed has the same five verbs. Each feed's options are its own: one given to a
feed it does not belong to is refused by name, as `uqs job new` refuses an
option for another kind.

The whole lifecycle lives in this group rather than as `uqs start
connector:NAME` beside the stack's verbs (#1734): a connector is not a torq.sh
process, so it shares none of `start`'s options (`--profile`, `--follow`, the
skips) while each feed has start options of its own, and #1738's rule 1 is
noun, then verb. Its log is one plain file, not a TorQ out_/err_ pair.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.json_out import emit, json_opt, snake
from uqs.cli.renames import renamed
from uqs.cli.shared import _die, _paths, app, console
from uqs.external import crypto, databento_feed, kafka_feed
from uqs.external.crypto import (
    CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS,
    CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    CRYPTO_FILLS_RECORDER_TABLE,
    CRYPTO_REAL_FILLS_RECORDER_TABLE,
    CRYPTO_RECORDER_DEFAULT_SYMBOLS,
    CRYPTO_RECORDER_DEFAULT_VENUES,
    DEFAULT_OMS_SOCKET_PATH,
)
from uqs.external.feeds import ExternalFeed, discover
from uqs.paths import UqsError

connector_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="External connectors: start, stop, restart, check and read the logs of the "
    "publishers outside the plant "
    "(databento, kafka, crypto, crypto-fills) that write into its tickerplant. These "
    "supervise external processes, not the TorQ-managed q processes `uqs start` runs.",
)
app.add_typer(connector_app, name="connector")


def _split(text: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in text.split(",") if s.strip())


def _or(value: Any, default: Any) -> Any:
    """`value` when the option was given, else `default`.

    `is None`, not truthiness: `--interval-ms 0` or `--symbols ""` was typed,
    and reaches the feed as typed rather than as the default.
    """
    return default if value is None else value


def _list_or(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    return default if value is None else _split(value)


def _start_databento(o: dict[str, Any]) -> str:
    pid = databento_feed.start_databento_feed(
        _paths(),
        dataset=_or(o["databento_dataset"], databento_feed.DEFAULT_DATASET),
        symbols=_list_or(o["symbols"], tuple(databento_feed.DEFAULT_SYMBOLS)),
        api_key=o["api_key"],
    )
    return f"databento feed started (pid {pid})"


def _start_kafka(o: dict[str, Any]) -> str:
    pid = kafka_feed.start_kafka_feed(
        _paths(),
        brokers=_or(o["brokers"], kafka_feed.DEFAULT_BROKERS),
        topic=_or(o["topic"], kafka_feed.DEFAULT_TOPIC),
        group=_or(o["group"], kafka_feed.DEFAULT_GROUP),
    )
    return f"kafka feed started (pid {pid})"


def _start_crypto(o: dict[str, Any]) -> str:
    pid = crypto.start_crypto_recorder(
        _paths(),
        base_port=o["port"],
        venues=_list_or(o["venues"], CRYPTO_RECORDER_DEFAULT_VENUES),
        symbols=_list_or(o["symbols"], CRYPTO_RECORDER_DEFAULT_SYMBOLS),
        top_n_levels=_or(o["top_n_levels"], 5),
        interval_ms=_or(o["interval_ms"], 1000),
    )
    return f"crypto recorder started (pid {pid})"


def _start_crypto_fills(o: dict[str, Any]) -> str:
    pid = crypto.start_crypto_fills_recorder(
        _paths(),
        base_port=o["port"],
        oms_socket_path=_or(o["oms_socket_path"], DEFAULT_OMS_SOCKET_PATH),
        symbol=_or(o["symbol"], CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL),
        poll_interval_ms=_or(o["poll_interval_ms"], CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS),
    )
    return (
        f"crypto fills recorder started (pid {pid}) - "
        f"{CRYPTO_FILLS_RECORDER_TABLE} is SIMULATED, "
        f"{CRYPTO_REAL_FILLS_RECORDER_TABLE} is real"
    )


@dataclass(frozen=True)
class _Feed:
    """One feed: how to start, stop and read it, and which start options are its own.

    The functions are looked up when called, not when this table is built, so
    what the feed modules hold at call time is what runs.
    """

    options: tuple[str, ...]
    start: Callable[[dict[str, Any]], str]
    stop: Callable[[Any], None]
    status: Callable[[Any], dict[str, str]]
    title: str


FEEDS: dict[str, _Feed] = {
    "databento": _Feed(
        ("databento_dataset", "symbols", "api_key"),
        _start_databento,
        lambda paths: databento_feed.stop_databento_feed(paths),
        lambda paths: databento_feed.databento_feed_status(paths),
        "databento feed status",
    ),
    "kafka": _Feed(
        ("brokers", "topic", "group"),
        _start_kafka,
        lambda paths: kafka_feed.stop_kafka_feed(paths),
        lambda paths: kafka_feed.kafka_feed_status(paths),
        "kafka feed status",
    ),
    "crypto": _Feed(
        ("venues", "symbols", "top_n_levels", "interval_ms", "port"),
        _start_crypto,
        lambda paths: crypto.stop_crypto_recorder(paths),
        lambda paths: crypto.crypto_recorder_status(paths),
        "crypto recorder status",
    ),
    "crypto-fills": _Feed(
        ("oms_socket_path", "symbol", "poll_interval_ms", "port"),
        _start_crypto_fills,
        lambda paths: crypto.stop_crypto_fills_recorder(paths),
        lambda paths: crypto.crypto_fills_recorder_status(paths),
        "crypto fills recorder status (sim_table = paper fills, real_table = confirmed executions)",
    ),
}


def _stop_quietly(feed: ExternalFeed, paths: Any) -> None:
    feed.stop(paths)


def _from_discovered(feed: ExternalFeed) -> _Feed:
    """A self-declaring feed (external/NAME_feed.py's FEED) in this table's shape.
    It takes no start options: its settings live in its own module."""
    return _Feed(
        (),
        lambda o: f"{feed.name} feed started (pid {feed.start(_paths())})",
        lambda paths: _stop_quietly(feed, paths),
        lambda paths: feed.status(paths),
        f"{feed.name} feed status",
    )


# Feeds scaffolded with `uqs job new NAME --kind external` declare themselves
# (#715); a name already listed above is a collision, not an override.
for _name, _feed_decl in discover().items():
    if _name in FEEDS:
        raise UqsError(f"external feed {_name!r} is declared twice - rename one")
    FEEDS[_name] = _from_discovered(_feed_decl)

FeedArg = Annotated[
    str,
    typer.Argument(
        help="databento, kafka, crypto (order books) or crypto-fills (the bot's fills)",
        autocompletion=completion.choices(*FEEDS),
    ),
]


def _feed(name: str) -> _Feed:
    if name not in FEEDS:
        _die(UqsError(f"no feed {name!r} - the feeds are {', '.join(FEEDS)}"))
        raise typer.Exit(code=1)
    return FEEDS[name]


def _opt(help_: str, feeds: str):
    return typer.Option(help=f"{help_} ({feeds})", show_default=False)


@connector_app.command(
    "start", cls=renamed({"--port": "--base-port", "--dataset": "--databento-dataset"})
)
def start(
    name: FeedArg,
    databento_dataset: Annotated[
        str | None, _opt("Databento dataset, e.g. XNAS.ITCH", "databento")
    ] = None,
    api_key: Annotated[
        str | None,
        _opt(f"API key; defaults to ${databento_feed.DATABENTO_API_KEY_ENV}", "databento"),
    ] = None,
    brokers: Annotated[str | None, _opt("bootstrap.servers of the cluster", "kafka")] = None,
    topic: Annotated[str | None, _opt("Topic to consume", "kafka")] = None,
    group: Annotated[
        str | None, _opt("Consumer group id - the committed offsets belong to it", "kafka")
    ] = None,
    symbols: Annotated[str | None, _opt("Comma-separated symbols", "databento, crypto")] = None,
    venues: Annotated[str | None, _opt("Comma-separated cryptorust venue names", "crypto")] = None,
    top_n_levels: Annotated[
        int | None, _opt("Book depth levels per snapshot (default 5)", "crypto")
    ] = None,
    interval_ms: Annotated[
        int | None, _opt("Publish interval in milliseconds (default 1000)", "crypto")
    ] = None,
    oms_socket_path: Annotated[
        str | None, _opt("Unix socket of an already-running cryptorust OMS", "crypto-fills")
    ] = None,
    symbol: Annotated[
        str | None, _opt("Symbol to tag rows with (the OMS's fills carry none)", "crypto-fills")
    ] = None,
    poll_interval_ms: Annotated[
        int | None, _opt("Poll interval in milliseconds", "crypto-fills")
    ] = None,
    base_port: Annotated[int | None, _opt("Stack base port", "crypto, crypto-fills")] = None,
) -> None:
    """Start a feed publishing onto the running stack's tickerplant.

    databento: live MBP-10 onto `databento_mbp10`, folded by databento1.
    kafka: a topic onto `kafka_message`, decoded and deduplicated by kafka_flow1; needs
    a reachable broker and `confluent-kafka` (the uqs `kafka` extra).
    crypto: a sibling cryptorust checkout's order-book recorder into
    `crypto_book` (see $CRYPTORUST_ROOT in crypto.cryptorust_root).
    crypto-fills: cryptorust's fills recorder - paper fills into
    `crypto_sim_fills`, real executions into `crypto_trades`; needs an
    already-running cryptorust OMS.
    """
    _start(name, {
        "databento_dataset": databento_dataset,
        "api_key": api_key,
        "brokers": brokers,
        "topic": topic,
        "group": group,
        "symbols": symbols,
        "venues": venues,
        "top_n_levels": top_n_levels,
        "interval_ms": interval_ms,
        "oms_socket_path": oms_socket_path,
        "symbol": symbol,
        "poll_interval_ms": poll_interval_ms,
        "port": base_port,
    })  # fmt: skip


def _start(name: str, options: dict[str, Any]) -> None:
    """Start feed `name` with the options given, refusing any not its own."""
    feed = _feed(name)
    given = {k: v for k, v in options.items() if v is not None}
    for option in given:
        if option not in feed.options:
            flag = "base-port" if option == "port" else option.replace("_", "-")
            _die(UqsError(f"--{flag} does not apply to feed {name}"))
            return
    try:
        message = feed.start({k: given.get(k) for k in feed.options})
    except UqsError as exc:
        _die(exc)
        return
    console.print(message)


@connector_app.command("stop")
def stop(name: FeedArg) -> None:
    """Stop a feed started by `uqs connector start`."""
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


@connector_app.command(
    "restart", cls=renamed({"--port": "--base-port", "--dataset": "--databento-dataset"})
)
@_takes_start_options
def restart(name: str, **options: Any) -> None:
    """Stop a feed if it is running, then start it again - with exactly
    `start`'s options, which are not remembered from the last start."""
    feed = _feed(name)
    try:
        if feed.status(_paths()).get("running") == "True":
            feed.stop(_paths())
            console.print(f"{name} feed stopped")
    except UqsError as exc:
        _die(exc)
        return
    _start(name, {"port" if k == "base_port" else k: v for k, v in options.items()})


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


@connector_app.command("status")
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
        found.append((each, feed.title, feed.status(_paths())))
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
