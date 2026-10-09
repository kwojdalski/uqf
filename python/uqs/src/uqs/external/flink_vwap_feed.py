"""flink_vwap_feed.py - start, stop and report the flink_vwap publisher.

A process outside TorQ: not a process.csv row, so `uqs feed start|stop|status
flink_vwap` drives it, through FEED below - the CLI finds it without an edit.
"""

from __future__ import annotations

from pathlib import Path

from uqs.external.feeds import ExternalFeed
from uqs.external.lifecycle import DetachedProcess
from uqs.paths import UqsPaths
from uqs.stack.procs import get_process_config

RAW_TABLE = "flink_vwap_raw"

#: The tickerplant credential every external publisher uses; stp1 checks it.
CREDENTIAL = "feed:pass"


def _process(paths: UqsPaths) -> DetachedProcess:
    return DetachedProcess(
        "the flink_vwap feed",
        paths.orchestrator_dir / "flink_vwap_feed.pid",
        paths.log_dir / "flink_vwap_feed.log",
    )


def start(paths: UqsPaths) -> int:
    port = get_process_config(paths, "stp1")["port"]
    runner = Path(__file__).resolve().parent / "flink_vwap_streamer.py"
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
        CREDENTIAL,
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


FEED = ExternalFeed("flink_vwap", start, stop, status)
