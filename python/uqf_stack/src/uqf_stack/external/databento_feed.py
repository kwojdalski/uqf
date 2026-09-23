"""The live Databento feed handler - an external, non-TorQ publisher.

Like the cryptorust recorders next door, this is not a process.csv row: it
opens its own kdb+ IPC handle straight to the tickerplant and calls
``.u.upd``, so it is started and stopped here rather than by ``torq.sh``.
The reason is the same one that keeps cryptorust out of the process table -
a q process cannot hold a Databento subscription, and TorQ only drives q.

**It carries bytes and decides nothing.** Databento's MBP-10 records go onto
the tickerplant in the source contract's own field order, unfolded and
unrenamed, and ``databento1`` (``src/etl/streaming/databento_book.q``)
applies ``.qxf.apply[`databento_book;...]`` - the same transform the ODBC
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
import signal
import subprocess
from pathlib import Path

from uqf_stack.logger import get_logger
from uqf_stack.model.pipelines import DEFAULT_BASE_PORT
from uqf_stack.paths import UqfStackError, UqfStackPaths
from uqf_stack.stack.procs import get_process_config

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
#: MBP-10 is what the source contract describes; a different schema would
#: not fold with the transform databento1 applies.
DATABENTO_SCHEMA = "mbp-10"


def _read_pid(paths: UqfStackPaths) -> int | None:
    path = paths.databento_feed_pid_path
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def is_databento_feed_running(paths: UqfStackPaths) -> bool:
    """Whether the handler this repository started is still alive.

    Signal 0 asks the kernel about the process without touching it, which
    is how the crypto recorders answer the same question.
    """
    pid = _read_pid(paths)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError, PermissionError:
        return False
    return True


def start_databento_feed(
    paths: UqfStackPaths,
    *,
    dataset: str = DEFAULT_DATASET,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    api_key: str | None = None,
) -> int:
    """Start the handler against the running stack's tickerplant.

    Refuses rather than starting a second one, and refuses without an API
    key rather than starting a process that will fail its first call - the
    same "resolve what can fail before any work happens" order
    ``.qbw.init`` follows.
    """
    if is_databento_feed_running(paths):
        raise UqfStackError("the databento feed is already running - stop it first")

    key = api_key or os.environ.get(DATABENTO_API_KEY_ENV)
    if not key:
        raise UqfStackError(
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
        str(paths.repo_root / "python" / "uqf_stack"),
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

    log_path = paths.torqdata / "logs" / "databento_feed.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, **{DATABENTO_API_KEY_ENV: key})
    with log_path.open("w") as log_file:
        process = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=paths.repo_root,
            env=env,
            start_new_session=True,
        )
    paths.databento_feed_pid_path.parent.mkdir(parents=True, exist_ok=True)
    paths.databento_feed_pid_path.write_text(str(process.pid))
    log.info(
        f"started the databento feed (pid {process.pid}), {dataset} "
        f"{','.join(symbols)} -> stp1:{port} {DATABENTO_RAW_TABLE} - "
        f"logging to {log_path}"
    )
    return process.pid


def stop_databento_feed(paths: UqfStackPaths) -> None:
    """Stop it, and say so when there was nothing to stop."""
    pid = _read_pid(paths)
    if pid is None or not is_databento_feed_running(paths):
        paths.databento_feed_pid_path.unlink(missing_ok=True)
        log.info("no databento feed running")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    paths.databento_feed_pid_path.unlink(missing_ok=True)
    log.info(f"stopped the databento feed (pid {pid})")


def databento_feed_status(paths: UqfStackPaths) -> dict[str, str]:
    """What the CLI renders. Strings, because it is a display table."""
    pid = _read_pid(paths)
    return {
        "running": str(is_databento_feed_running(paths)),
        "pid": str(pid) if pid is not None else "",
        "publishes": DATABENTO_RAW_TABLE,
        "folded by": "databento1 -> databento_book",
        "log": str(paths.torqdata / "logs" / "databento_feed.log"),
    }
