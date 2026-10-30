"""flink_vwap_feed.py - start, stop and report the flink_vwap publisher.

A process outside TorQ: not a process.csv row, so `uqs connector start|stop|restart|status|logs
flink_vwap` drives it, through FEED below - the CLI finds it without an edit.

The flink bundle's connector (#1776): installed with its q half into
src/etl/connectors/flink/, beside flink_vwap_streamer.py, which `start` runs
from there. Without the bundle there is no flink_vwap connector at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uqs.external import connection
from uqs.external.feeds import ExternalFeed
from uqs.external.lifecycle import DetachedProcess
from uqs.paths import UqsPaths

RAW_TABLE = "flink_vwap_raw"


def _process(paths: UqsPaths) -> DetachedProcess:
    return DetachedProcess(
        "the flink_vwap feed",
        paths.orchestrator_dir / "flink_vwap_feed.pid",
        paths.log_dir / "flink_vwap_feed.log",
    )


def start(paths: UqsPaths, options: Mapping[str, Any]) -> int:
    """stp1's host, port and credential are every connector's
    (uqs.external.connection): `options` holds --tp-credential, resolved."""
    port = connection.tickerplant_port(paths)
    credential = connection.tp_credential(options["tp_credential"])
    runner = Path(__file__).resolve().parent / "flink_vwap_streamer.py"
    cmd = [
        "uv",
        "run",
        "--project",
        str(paths.repo_root / "python" / "uqs"),
        "python",
        str(runner),
        "--host",
        connection.TP_HOST,
        "--port",
        str(port),
        "--credential",
        credential,
        "--table",
        RAW_TABLE,
    ]
    return _process(paths).start(cmd, cwd=paths.repo_root)


def stop(paths: UqsPaths) -> int | None:
    return _process(paths).stop()


def status(paths: UqsPaths) -> dict[str, str]:
    return _process(paths).status(
        publishes=RAW_TABLE, **{"deduplicated by": "flink_vwap1 -> flink_vwap"}
    )


FEED = ExternalFeed(
    "flink_vwap",
    start,
    stop,
    status,
    options=(connection.TP_CREDENTIAL,),
    produces={RAW_TABLE: "the external flink_vwap feed (`uqs connector start flink_vwap`)"},
)
