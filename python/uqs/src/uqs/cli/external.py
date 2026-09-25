"""The external publishers: Databento, Kafka, and cryptorust.

Each is a sub-app (`uqs databento ...`, `uqs kafka ...`, `uqs crypto ...`)
driving a process this repository does not own. See cli/lifecycle.py for why
the split is shaped this way.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import (
    PortOpt,
    _die,
    _paths,
    app,
    console,
)
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
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError

# Proof of concept: cryptorust (Rust, no TorQ/q involved) publishing live
# venue order books onto stp1 over kdb+ IPC - a separate sub-app (`uqs
# crypto start/stop/status`) rather than flat crypto-* commands, since these
# don't drive torq.sh/process.csv at all (see external/crypto.py's
# start_crypto_recorder docstring) - a distinct enough concern to read as its own namespace.
# Live Databento. Its own group for the same reason crypto has one: this
# does not drive torq.sh or process.csv either - the handler is an external
# publisher, and the q half of it (databento1) is an ordinary pipeline row
# that `uqs start` brings up like any other.
databento_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Live Databento MBP-10 into the tickerplant, folded by databento1.",
)
app.add_typer(databento_app, name="databento")


@databento_app.command("start")
def databento_start(
    dataset: Annotated[str, typer.Option(help="Databento dataset, e.g. XNAS.ITCH")] = (
        databento_feed.DEFAULT_DATASET
    ),
    symbols: Annotated[str, typer.Option(help="Comma-separated symbols")] = ",".join(
        databento_feed.DEFAULT_SYMBOLS
    ),
    api_key: Annotated[
        str | None,
        typer.Option(
            help=f"Databento API key; defaults to ${databento_feed.DATABENTO_API_KEY_ENV}"
        ),
    ] = None,
) -> None:
    """Subscribe to Databento and publish MBP-10 onto this stack's stp1.

    The rows land on `databento_mbp10` raw; `databento1` folds them into
    `databento_book` with the same transform the ODBC backfill uses.
    """
    try:
        pid = databento_feed.start_databento_feed(
            _paths(),
            dataset=dataset,
            symbols=tuple(s.strip() for s in symbols.split(",") if s.strip()),
            api_key=api_key,
        )
    except UqsError as exc:
        _die(exc)
    console.print(f"databento feed started (pid {pid})")


@databento_app.command("stop")
def databento_stop() -> None:
    """Stop the Databento feed handler started by `databento start`."""
    databento_feed.stop_databento_feed(_paths())
    console.print("databento feed stopped")


@databento_app.command("status")
def databento_status() -> None:
    """Whether the handler is running, its pid, and where its log lives."""
    status = databento_feed.databento_feed_status(_paths())
    table = Table(title="databento feed status")
    table.add_column("field")
    table.add_column("value")
    for k, v in status.items():
        table.add_row(k, v)
    console.print(table)


# Live Kafka. Its own group for the reason databento has one: it does not
# drive torq.sh or process.csv either - the consumer is an external
# publisher, and the q half of it (kafka_flow1) is an ordinary pipeline row.
kafka_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A Kafka topic into the tickerplant, deduplicated by kafka_flow1.",
)
app.add_typer(kafka_app, name="kafka")


@kafka_app.command("start")
def kafka_start(
    brokers: Annotated[
        str, typer.Option(help="bootstrap.servers for the Kafka cluster")
    ] = kafka_feed.DEFAULT_BROKERS,
    topic: Annotated[str, typer.Option(help="Topic to consume")] = kafka_feed.DEFAULT_TOPIC,
    group: Annotated[
        str, typer.Option(help="Consumer group id - the committed offsets belong to it")
    ] = kafka_feed.DEFAULT_GROUP,
) -> None:
    """Start the Kafka consumer against the running stack's tickerplant.

    Rows land on `kafka_client_flow` raw; `kafka_flow1` drops any the broker
    has already delivered and republishes the rest onto `client_flow`.

    Needs a reachable broker and `confluent-kafka` installed - neither ships
    with this repository, and nothing in the default test lane needs either.
    """
    try:
        pid = kafka_feed.start_kafka_feed(_paths(), brokers=brokers, topic=topic, group=group)
    except UqsError as exc:
        _die(exc)
    console.print(f"kafka feed started (pid {pid})")


@kafka_app.command("stop")
def kafka_stop() -> None:
    """Stop the Kafka consumer started by `kafka start`."""
    kafka_feed.stop_kafka_feed(_paths())
    console.print("kafka feed stopped")


@kafka_app.command("status")
def kafka_status() -> None:
    """Whether the consumer is running, its pid, and where its log lives."""
    status = kafka_feed.kafka_feed_status(_paths())
    table = Table(title="kafka feed status")
    table.add_column("field")
    table.add_column("value")
    for k, v in status.items():
        table.add_row(k, v)
    console.print(table)


crypto_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Proof of concept: cryptorust (Rust) publishing live order books over kdb+ IPC.",
)
app.add_typer(crypto_app, name="crypto")


@crypto_app.command("start")
def crypto_start(
    venues: Annotated[
        str, typer.Option(help="Comma-separated cryptorust venue names to connect")
    ] = ",".join(CRYPTO_RECORDER_DEFAULT_VENUES),
    symbols: Annotated[
        str, typer.Option(help="Comma-separated symbols, cryptorust's own venue-agnostic format")
    ] = ",".join(CRYPTO_RECORDER_DEFAULT_SYMBOLS),
    top_n_levels: Annotated[
        int, typer.Option(help="Book depth levels to publish per snapshot")
    ] = 5,
    interval_ms: Annotated[int, typer.Option(help="Publish interval in milliseconds")] = 1000,
    port: PortOpt = DEFAULT_BASE_PORT,
) -> None:
    """Build and launch a sibling cryptorust checkout's own
    kdb-market-data-recorder and point it at this demo's stp1 - publishing
    live venue order books onto the same kdb+ infra everything else here
    already runs on, into `crypto_book` (defined in
    scripts/processes/uqs_tables.q). Requires a cryptorust checkout - see
    $CRYPTORUST_ROOT in crypto.cryptorust_root's docstring.
    """
    try:
        pid = crypto.start_crypto_recorder(
            _paths(),
            base_port=port,
            venues=tuple(v.strip() for v in venues.split(",") if v.strip()),
            symbols=tuple(s.strip() for s in symbols.split(",") if s.strip()),
            top_n_levels=top_n_levels,
            interval_ms=interval_ms,
        )
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"crypto recorder started (pid {pid})")


@crypto_app.command("stop")
def crypto_stop() -> None:
    """Stop the cryptorust recorder started by `crypto start`."""
    try:
        crypto.stop_crypto_recorder(_paths())
    except UqsError as exc:
        _die(exc)
        return
    console.print("crypto recorder stopped")


@crypto_app.command("status")
def crypto_status() -> None:
    """Show whether the cryptorust recorder is running, its pid, and where
    its config/log live."""
    status = crypto.crypto_recorder_status(_paths())
    table = Table(title="crypto recorder status")
    table.add_column("field")
    table.add_column("value")
    for k, v in status.items():
        table.add_row(k, v)
    console.print(table)


@crypto_app.command("fills-start")
def crypto_fills_start(
    oms_socket_path: Annotated[
        str, typer.Option(help="Unix socket of an already-running cryptorust OMS to poll")
    ] = DEFAULT_OMS_SOCKET_PATH,
    symbol: Annotated[
        str, typer.Option(help="Symbol to tag published rows with (the OMS's fills carry none)")
    ] = CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    poll_interval_ms: Annotated[int, typer.Option(help="Poll interval in milliseconds")] = (
        CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS
    ),
    port: PortOpt = DEFAULT_BASE_PORT,
) -> None:
    """Build and launch a sibling cryptorust checkout's own
    kdb-fills-recorder, publishing BOTH the market-making bot's SIMULATED
    (paper) fills into `crypto_sim_fills` AND real confirmed exchange
    executions into `crypto_trades` (both defined in
    scripts/processes/uqs_tables.q) - see that binary's
    own doc header for how each source differs. Requires an already-running
    cryptorust service (its OMS IPC socket, default /tmp/beacon.sock) -
    this doesn't start one itself, unlike `crypto start` which owns its
    own exchange connectors.
    """
    try:
        pid = crypto.start_crypto_fills_recorder(
            _paths(),
            base_port=port,
            oms_socket_path=oms_socket_path,
            symbol=symbol,
            poll_interval_ms=poll_interval_ms,
        )
    except UqsError as exc:
        _die(exc)
        return
    console.print(
        f"crypto fills recorder started (pid {pid}) - "
        f"{CRYPTO_FILLS_RECORDER_TABLE} is SIMULATED, "
        f"{CRYPTO_REAL_FILLS_RECORDER_TABLE} is real"
    )


@crypto_app.command("fills-stop")
def crypto_fills_stop() -> None:
    """Stop the cryptorust fills recorder started by `crypto fills-start`."""
    try:
        crypto.stop_crypto_fills_recorder(_paths())
    except UqsError as exc:
        _die(exc)
        return
    console.print("crypto fills recorder stopped")


@crypto_app.command("fills-status")
def crypto_fills_status() -> None:
    """Show whether the cryptorust fills recorder is running, its pid, and
    where its log lives."""
    status = crypto.crypto_fills_recorder_status(_paths())
    table = Table(
        title="crypto fills recorder status "
        "(sim_table = paper fills, real_table = confirmed executions)"
    )
    table.add_column("field")
    table.add_column("value")
    for k, v in status.items():
        table.add_row(k, v)
    console.print(table)
