"""The Kafka consumer - an external, non-TorQ publisher.

Like the Databento handler and the cryptorust recorders next door, this is
not a ``process.csv`` row: it opens its own kdb+ IPC handle straight to the
tickerplant and calls ``.u.upd``, so it is started and stopped here rather
than by ``torq.sh``. A q process cannot hold a Kafka subscription, and TorQ
only drives q.

**It carries bytes and decides nothing.** Records go onto the tickerplant in
the plant table's own field order, and ``kafka_flow1``
(``src/etl/streaming/kafka_flow.q``) decides which of them are new. The
offset-commit ordering that makes that split correct is documented in
``kafka_streamer``, which is where it is implemented.

## Why no broker ships with this

``confluent-kafka`` is deliberately NOT a dependency of ``uqs``, and no test
in the default lane needs a broker. That follows the rule
``etl/core/singlestore_odbc.q`` states for the ODBC driver - a public
single-host demo cannot require infrastructure nobody has, or the path
becomes undemonstrable - and it binds harder here, because a broker is a
great deal heavier than a driver.

So the q half stands alone: ``kafka_flow`` is driven by fixtures in
``tests/q/test_kafka_flow.q`` and proves its dedupe with no broker, no
network and no Python. This file is what you add when you have a real topic.
"""

from __future__ import annotations

from pathlib import Path

from uqs.external.kafka_streamer import KAFKA_RAW_TABLE
from uqs.external.lifecycle import DetachedProcess
from uqs.logger import get_logger
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsPaths
from uqs.stack.procs import get_process_config

log = get_logger(__name__)

#: Same credential the q feeds, the cryptorust recorder and the Databento
#: handler use: stp1 enforces access control on every connection, and
#: appconfig/passwords/feed.txt already resolves to one accesslist.txt
#: accepts. Reused rather than adding another password file.
KAFKA_FEED_CREDENTIAL = "feed:pass"

DEFAULT_BROKERS = "localhost:9092"
DEFAULT_TOPIC = "uqf.client.flow"

#: The consumer group. Fixed rather than random BECAUSE the committed offsets
#: belong to the group: a fresh group id on every start would re-read the
#: whole topic each time, which the q dedupe would then have to absorb in
#: full. A stable group is what makes a restart resume.
DEFAULT_GROUP = "uqf-kafka-flow"


def _process(paths: UqsPaths) -> DetachedProcess:
    return DetachedProcess(
        "the kafka feed", paths.kafka_feed_pid_path, paths.torqdata / "logs" / "kafka_feed.log"
    )


def is_kafka_feed_running(paths: UqsPaths) -> bool:
    """Whether the consumer this repository started is still alive."""
    return _process(paths).running()


def start_kafka_feed(
    paths: UqsPaths,
    *,
    brokers: str = DEFAULT_BROKERS,
    topic: str = DEFAULT_TOPIC,
    group: str = DEFAULT_GROUP,
) -> int:
    """Start the consumer against the running stack's tickerplant.

    Refuses rather than starting a second one: two consumers in the same
    group would split the partitions between them, so the second would look
    like it worked while each received half the topic.
    """
    stp1 = get_process_config(paths, "stp1")
    port = stp1.get("port") or DEFAULT_BASE_PORT

    runner = Path(__file__).resolve().parent / "kafka_streamer.py"
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
        KAFKA_FEED_CREDENTIAL,
        "--brokers",
        brokers,
        "--topic",
        topic,
        "--group",
        group,
        "--table",
        KAFKA_RAW_TABLE,
    ]

    proc = _process(paths)
    pid = proc.start(cmd, cwd=paths.repo_root)
    log.info(
        f"started the kafka feed (pid {pid}), {topic} on {brokers} "
        f"-> stp1:{port} {KAFKA_RAW_TABLE} - logging to {proc.log_path}"
    )
    return pid


def stop_kafka_feed(paths: UqsPaths) -> None:
    """Stop it, and say so when there was nothing to stop.

    SIGTERM rather than SIGKILL so the streamer's `finally` reaches
    `consumer.close()`, which commits nothing but does leave the group
    cleanly - a killed member holds its partitions until the broker's session
    timeout, and a restart inside that window gets none of them.
    """
    pid = _process(paths).stop()
    log.info("no kafka feed running" if pid is None else f"stopped the kafka feed (pid {pid})")


def kafka_feed_status(paths: UqsPaths) -> dict[str, str]:
    """What the CLI renders. Strings, because it is a display table."""
    return _process(paths).status(
        publishes=KAFKA_RAW_TABLE, **{"deduplicated by": "kafka_flow1 -> client_flow"}
    )
