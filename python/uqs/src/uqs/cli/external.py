"""The external publishers: `uqs feed start|stop|status NAME`.

Databento, Kafka, and cryptorust's two recorders each drive a process this
repository does not own, publishing onto the running stack's tickerplant. They
do not go through torq.sh or process.csv - the q half of each (databento1,
kafka_flow1, ...) is an ordinary pipeline row that `uqs start` brings up - so
they are one group of their own rather than lifecycle commands.

One `start` for every feed, rather than a sub-app per feed, because all four
have the same three verbs. Each feed's options are its own: one given to a
feed it does not belong to is refused by name, as `uqs job new` refuses an
option for another kind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import typer
from rich.table import Table

from uqs.cli import completion
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
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError

feed_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="External publishers into the tickerplant: databento, kafka, crypto, crypto-fills.",
)
app.add_typer(feed_app, name="feed")


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
        dataset=_or(o["dataset"], databento_feed.DEFAULT_DATASET),
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
        base_port=_or(o["port"], DEFAULT_BASE_PORT),
        venues=_list_or(o["venues"], CRYPTO_RECORDER_DEFAULT_VENUES),
        symbols=_list_or(o["symbols"], CRYPTO_RECORDER_DEFAULT_SYMBOLS),
        top_n_levels=_or(o["top_n_levels"], 5),
        interval_ms=_or(o["interval_ms"], 1000),
    )
    return f"crypto recorder started (pid {pid})"


def _start_crypto_fills(o: dict[str, Any]) -> str:
    pid = crypto.start_crypto_fills_recorder(
        _paths(),
        base_port=_or(o["port"], DEFAULT_BASE_PORT),
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
        ("dataset", "symbols", "api_key"),
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


@feed_app.command("start")
def start(
    name: FeedArg,
    dataset: Annotated[str | None, _opt("Databento dataset, e.g. XNAS.ITCH", "databento")] = None,
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
    port: Annotated[int | None, _opt("Stack base port", "crypto, crypto-fills")] = None,
) -> None:
    """Start a feed publishing onto the running stack's tickerplant.

    databento: live MBP-10 onto `databento_mbp10`, folded by databento1.
    kafka: a topic onto `kafka_client_flow`, deduplicated by kafka_flow1; needs
    a reachable broker and `confluent-kafka` installed.
    crypto: a sibling cryptorust checkout's order-book recorder into
    `crypto_book` (see $CRYPTORUST_ROOT in crypto.cryptorust_root).
    crypto-fills: cryptorust's fills recorder - paper fills into
    `crypto_sim_fills`, real executions into `crypto_trades`; needs an
    already-running cryptorust OMS.
    """
    feed = _feed(name)
    options = {
        "dataset": dataset,
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
        "port": port,
    }
    given = {k: v for k, v in options.items() if v is not None}
    for option in given:
        if option not in feed.options:
            _die(UqsError(f"--{option.replace('_', '-')} does not apply to feed {name}"))
            return
    try:
        message = feed.start({k: given.get(k) for k in feed.options})
    except UqsError as exc:
        _die(exc)
        return
    console.print(message)


@feed_app.command("stop")
def stop(name: FeedArg) -> None:
    """Stop a feed started by `uqs feed start`."""
    try:
        _feed(name).stop(_paths())
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"{name} feed stopped")


@feed_app.command("status")
def status(
    name: Annotated[
        str | None,
        typer.Argument(
            help="One feed; omit it for every feed",
            autocompletion=completion.choices(*FEEDS),
        ),
    ] = None,
) -> None:
    """Whether a feed is running, its pid, and where its log lives."""
    for each in [name] if name is not None else list(FEEDS):
        feed = _feed(each)
        table = Table(title=feed.title)
        table.add_column("field")
        table.add_column("value")
        for k, v in feed.status(_paths()).items():
            table.add_row(k, v)
        console.print(table)
