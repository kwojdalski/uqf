"""The live Databento feed handler - an external, non-TorQ publisher.

Like the cryptorust recorders next door, this is not a process.csv row: it
opens its own kdb+ IPC handle straight to the tickerplant and calls
``.u.upd``, so it is started and stopped here rather than by ``torq.sh``.
The reason is the same one that keeps cryptorust out of the process table -
a q process cannot hold a Databento subscription, and TorQ only drives q.

**It carries bytes and decides nothing.** Databento's MBP-10 records go onto
the tickerplant in the source contract's own field order, unfolded and
unrenamed, and ``databento1`` (``src/etl/streaming/eq_orderbook.q``)
applies ``.qetl.transform.apply[`eq_orderbook;...]`` - the same transform the ODBC
backfill uses - to fold forty per-level columns into four vectors.

That split is the whole point. The fold is forty columns of index
arithmetic with a level-ordering trap in it (bids0, bids1, bids10, bids2 is
what a lexical sort gives you), it already exists in q with worked
examples, and a Python reimplementation here would be a second place for it
to be wrong - drifting the first time Databento adds a level. So this file
has no opinion about what a book is.

## What it does have an opinion about

The three tickerplant rules, because they are transport concerns and this
is the transport:

1. ``.u.upd`` stamps its own ``time`` on receipt, so a publisher must not
   send one (``torq_pipeline.q``, invariant 1). Databento's own clock goes
   over as ``ts_event`` and survives as a column of its own.
2. ``.u.upd`` derives the row count from column length, so every column is
   a list, never a bare atom - even for a single record.
3. Column ORDER must match the table, because ``.u.upd`` positions by
   index, not by name. The order comes from the source declaration rather
   than a literal here, so a field added there cannot silently transpose
   two columns of the same type here.
"""

from __future__ import annotations

import os
from pathlib import Path

from uqs.external.lifecycle import DetachedProcess
from uqs.logger import get_logger
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError, UqsPaths
from uqs.stack.procs import get_process_config

log = get_logger(__name__)

#: The raw table the handler publishes onto. `databento1` subscribes to it.
DATABENTO_RAW_TABLE = "databento_mbp10"

#: Same credential the q feeds and the cryptorust recorder use: stp1
#: enforces access control on every connection, and appconfig/passwords/
#: feed.txt already resolves to one accesslist.txt accepts. Reused rather
#: than adding another password file to the vendored tree.
DATABENTO_FEED_CREDENTIAL = "feed:pass"

#: Databento's own env var name, so an existing export just works.
DATABENTO_API_KEY_ENV = "DATABENTO_API_KEY"

DEFAULT_DATASET = "XNAS.ITCH"
DEFAULT_SYMBOLS = ("AAPL", "MSFT")


def _process(paths: UqsPaths) -> DetachedProcess:
    return DetachedProcess(
        "the databento feed",
        paths.databento_feed_pid_path,
        paths.torqdata / "logs" / "databento_feed.log",
    )


def is_databento_feed_running(paths: UqsPaths) -> bool:
    """Whether the handler this repository started is still alive."""
    return _process(paths).running()


def start_databento_feed(
    paths: UqsPaths,
    *,
    dataset: str = DEFAULT_DATASET,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    api_key: str | None = None,
) -> int:
    """Start the handler against the running stack's tickerplant.

    Refuses rather than starting a second one, and refuses without an API
    key rather than starting a process that will fail its first call - the
    same "resolve what can fail before any work happens" order
    ``.qetl.job.bounded.init`` follows.
    """
    if is_databento_feed_running(paths):
        # Before the API-key check, so a second start says why it is refused.
        raise UqsError("the databento feed is already running - stop it first")

    key = api_key or os.environ.get(DATABENTO_API_KEY_ENV)
    if not key:
        raise UqsError(
            f"no Databento API key: pass --api-key or set ${DATABENTO_API_KEY_ENV}. "
            "Databento gives new accounts free credit; this feed does not run "
            "against a fixture."
        )

    stp1 = get_process_config(paths, "stp1")
    port = stp1.get("port") or (DEFAULT_BASE_PORT)

    runner = Path(__file__).resolve().parent / "databento_streamer.py"
    cmd = [
        "uv",
        "run",
        "--project",
        str(paths.repo_root / "python" / "uqs"),
        "python",
        str(runner),
        "--host",
        "localhost",
        "--port",
        str(port),
        "--credential",
        DATABENTO_FEED_CREDENTIAL,
        "--dataset",
        dataset,
        "--symbols",
        ",".join(symbols),
        "--table",
        DATABENTO_RAW_TABLE,
    ]

    proc = _process(paths)
    env = dict(os.environ, **{DATABENTO_API_KEY_ENV: key})
    pid = proc.start(cmd, cwd=paths.repo_root, env=env)
    log.info(
        f"started the databento feed (pid {pid}), {dataset} "
        f"{','.join(symbols)} -> stp1:{port} {DATABENTO_RAW_TABLE} - "
        f"logging to {proc.log_path}"
    )
    return pid


def stop_databento_feed(paths: UqsPaths) -> None:
    """Stop it, and say so when there was nothing to stop."""
    pid = _process(paths).stop()
    log.info(
        "no databento feed running" if pid is None else f"stopped the databento feed (pid {pid})"
    )


def databento_feed_status(paths: UqsPaths) -> dict[str, str]:
    """What the CLI renders. Strings, because it is a display table."""
    return _process(paths).status(
        publishes=DATABENTO_RAW_TABLE, **{"folded by": "databento1 -> databento_book"}
    )
