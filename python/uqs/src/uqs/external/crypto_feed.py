"""The cryptorust recorders as connectors: `uqs connector start crypto|crypto-fills`.

The process code is crypto.py's; this module only declares the two feeds, so
`external.feeds.discover()` - which reads `*_feed` modules - finds them. Their
functions are looked up when called, so what crypto.py holds at call time is
what runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from uqs.external import connection, crypto
from uqs.external.crypto import (
    CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS,
    CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    CRYPTO_FILLS_RECORDER_TABLE,
    CRYPTO_REAL_FILLS_RECORDER_TABLE,
    CRYPTO_RECORDER_DEFAULT_SYMBOLS,
    CRYPTO_RECORDER_DEFAULT_VENUES,
    CRYPTO_RECORDER_TABLE,
    DEFAULT_OMS_SOCKET_PATH,
)
from uqs.external.feeds import ExternalFeed, FeedOption
from uqs.paths import UqsPaths

#: The stack's base port, which both recorders take to find stp1.
BASE_PORT = FeedOption(
    "base_port",
    "Stack base port",
    type=int,
    aliases=("--port",),  # #1729's rename
)

#: What crypto_book and crypto_trades are fed by, besides the in-stack mock.
RECORDER = "cryptorust's kdb recorder, which cryptomock1 stands in for"


def _start_recorder(paths: UqsPaths, options: Mapping[str, Any]) -> int:
    return crypto.start_crypto_recorder(
        paths,
        base_port=options["base_port"],
        venues=options["venues"],
        symbols=options["symbols"],
        top_n_levels=options["top_n_levels"],
        interval_ms=options["interval_ms"],
        credential=options["tp_credential"],
    )


def _start_fills_recorder(paths: UqsPaths, options: Mapping[str, Any]) -> int:
    return crypto.start_crypto_fills_recorder(
        paths,
        base_port=options["base_port"],
        oms_socket_path=options["oms_socket_path"],
        symbol=options["symbol"],
        poll_interval_ms=options["poll_interval_ms"],
        credential=options["tp_credential"],
    )


FEEDS = (
    ExternalFeed(
        "crypto",
        _start_recorder,
        lambda paths: crypto.stop_crypto_recorder(paths),
        lambda paths: crypto.crypto_recorder_status(paths),
        options=(
            FeedOption(
                "symbols",
                "Comma-separated symbols",
                default=CRYPTO_RECORDER_DEFAULT_SYMBOLS,
                many=True,
            ),
            FeedOption(
                "venues",
                "Comma-separated cryptorust venue names",
                default=CRYPTO_RECORDER_DEFAULT_VENUES,
                many=True,
            ),
            FeedOption(
                "top_n_levels", "Book depth levels per snapshot (default 5)", type=int, default=5
            ),
            FeedOption(
                "interval_ms",
                "Publish interval in milliseconds (default 1000)",
                type=int,
                default=1000,
            ),
            BASE_PORT,
            connection.TP_CREDENTIAL,
        ),  # fmt: skip
        produces={CRYPTO_RECORDER_TABLE: RECORDER},
        title="crypto recorder status",
    ),
    ExternalFeed(
        "crypto-fills",
        _start_fills_recorder,
        lambda paths: crypto.stop_crypto_fills_recorder(paths),
        lambda paths: crypto.crypto_fills_recorder_status(paths),
        options=(
            FeedOption(
                "oms_socket_path",
                "Unix socket of an already-running cryptorust OMS",
                default=DEFAULT_OMS_SOCKET_PATH,
            ),
            FeedOption(
                "symbol",
                "Symbol to tag rows with (the OMS's fills carry none)",
                default=CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
            ),
            FeedOption(
                "poll_interval_ms",
                "Poll interval in milliseconds",
                type=int,
                default=CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS,
            ),
            BASE_PORT,
            connection.TP_CREDENTIAL,
        ),  # fmt: skip
        produces={
            CRYPTO_REAL_FILLS_RECORDER_TABLE: RECORDER,
            CRYPTO_FILLS_RECORDER_TABLE: (
                "cryptorust's kdb fills recorder (`uqs connector start crypto-fills`) - "
                "SIMULATED paper fills"
            ),
        },
        title="crypto fills recorder status "
        "(sim_table = paper fills, real_table = confirmed executions)",
        started_note=f"{CRYPTO_FILLS_RECORDER_TABLE} is SIMULATED, "
        f"{CRYPTO_REAL_FILLS_RECORDER_TABLE} is real",
    ),
)
