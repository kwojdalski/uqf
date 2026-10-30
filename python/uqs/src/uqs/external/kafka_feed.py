"""The Python Kafka consumer - an external, non-TorQ publisher.

A q process *can* hold a Kafka subscription - KX's ``kfk`` interface wraps
``librdkafka`` - and #1481 adds a native q feed. This consumer publishes the
same envelope and remains for hosts where the native library is not built.
Pick one per consumer group.

Like the cryptorust recorders next door, this is
not a ``process.csv`` row: it opens its own kdb+ IPC handle straight to the
tickerplant and calls ``.u.upd``, so it is started and stopped here rather
than by ``torq.sh``.

**It carries bytes and decides nothing.** Records go onto the tickerplant in
the plant table's own field order, undecoded, and ``kafka_flow1``
(``src/etl/streaming/kafka_flow.q``) decides what they mean and which are new. The
offset-commit ordering that makes that split correct is documented in
``kafka_streamer``, which is where it is implemented.

## Why no broker ships with this

``confluent-kafka`` is deliberately NOT a dependency of ``uqs`` - it is the
optional ``kafka`` extra (``uqs.stack.kafka_extra``) - and no test
in the default lane needs a broker. That follows the rule
``etl/core/singlestore_odbc.q`` states for the ODBC driver - a public
single-host demo cannot require infrastructure nobody has, or the path
becomes undemonstrable - and it binds harder here, because a broker is a
great deal heavier than a driver.

So the q half stands alone: ``kafka_flow`` is driven by fixtures in
``tests/q/test_kafka_flow.q`` and proves its dedupe with no broker, no
network and no Python. This file is what you add when you have a real topic
and no native ``kfk`` library.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uqs.external import connection
from uqs.external.feeds import ExternalFeed, FeedOption, split
from uqs.external.kafka_streamer import KAFKA_RAW_TABLE
from uqs.external.lifecycle import DetachedProcess
from uqs.logger import get_logger
from uqs.model import load_plan
from uqs.paths import UqsError, UqsPaths
from uqs.stack import kafka_extra

log = get_logger()

#: The bootstrap servers, read from the variable the native q feed kafka_feed1
#: reads (src/etl/streaming/kafka_feed.q): one cluster, configured once.
BROKERS_ENV = "UQF_KAFKA_BROKERS"
#: The DEMO default when neither --brokers nor $UQF_KAFKA_BROKERS is given: a
#: local broker. kafka_feed1 has none, and refuses to start without the variable.
DEFAULT_BROKERS = "localhost:9092"

#: The topics, read from kafka_feed1's variable too. Unset, BOTH consumers take
#: every topic a decoder job declares (`.qetl.kafka.topic.define`), read from
#: the same q declarations - see `declared_topics` - so neither keeps a list.
TOPICS_ENV = "UQF_KAFKA_TOPICS"

#: The consumer group. Fixed rather than random BECAUSE the committed offsets
#: belong to the group: a fresh group id on every start would re-read the
#: whole topic each time, which the q dedupe would then have to absorb in
#: full. A stable group is what makes a restart resume.
#:
#: Deliberately NOT kafka_feed1's (`uqf-kafka-q`, and $UQF_KAFKA_GROUP is not
#: read here): the two consumers are alternatives, and sharing a group would
#: split the partitions between them (#1786).
DEFAULT_GROUP = "uqf-kafka-flow"


def declared_topics(paths: UqsPaths) -> dict[str, str]:
    """{topic: the decoder job that declares it}, from the tree's q - what
    kafka_feed1 calls `.qetl.kafka.topic.known[]`, read by the load plan."""
    return load_plan.load_plan(paths.repo_root).topics


def check_topics(paths: UqsPaths, topic: str | None) -> tuple[str, ...]:
    """The topics to consume: `topic` (comma separated), else every declared
    one - refused, as kafka_feed1 refuses them, when one names no decoder."""
    declared = declared_topics(paths)
    if not declared:
        raise UqsError(
            "no decoder job declares a Kafka topic (.qetl.kafka.topic.define) - "
            "there is nothing to consume; kafka_flow.q declares uqf.client.flow"
        )
    if topic is None:
        return tuple(sorted(declared))
    topics = split(topic)
    if bad := [t for t in topics if t not in declared]:
        raise UqsError(
            f"kafka: {', '.join(bad)} is declared by no decoder job - kafka_flow1 would "
            f"dead-letter every record. The declared topics are {', '.join(sorted(declared))}; "
            "check the spelling, or install the bundle whose decoder declares it"
        )
    return topics


def _process(paths: UqsPaths) -> DetachedProcess:
    return DetachedProcess(
        "the kafka feed", paths.kafka_feed_pid_path, paths.log_dir / "kafka_feed.log"
    )


def is_kafka_feed_running(paths: UqsPaths) -> bool:
    """Whether the consumer this repository started is still alive."""
    return _process(paths).running()


def start_kafka_feed(
    paths: UqsPaths,
    *,
    brokers: str = DEFAULT_BROKERS,
    topic: str | None = None,
    group: str = DEFAULT_GROUP,
    credential: str | None = None,
) -> int:
    """Start the consumer against the running stack's tickerplant.

    `topic` None is every declared topic; `credential` None resolves as
    external/connection.py says. Refuses rather than starting a second one:
    two consumers in the same group would split the partitions between them,
    so the second would look like it worked while each received half the topic.

    Refused, before anything starts, when confluent-kafka (the uqs `kafka`
    extra) is not installed: the detached streamer would only say so in its
    log, after the PID file was written - as is a topic no decoder declares.
    """
    kafka_extra.require()
    topics = ",".join(check_topics(paths, topic))
    port = connection.tickerplant_port(paths)
    tp_credential = connection.tp_credential(credential)

    runner = Path(__file__).resolve().parent / "kafka_streamer.py"
    # This interpreter, the one kafka_extra just checked: a release's .venv
    # holds the extra's wheels only when it was built --with-kafka-connector,
    # and `uv run` would try to sync that venv to the lock, offline (#1515).
    cmd = [
        sys.executable,
        str(runner),
        "--host",
        connection.TP_HOST,
        "--port",
        str(port),
        "--credential",
        tp_credential,
        "--brokers",
        brokers,
        "--topic",
        topics,
        "--group",
        group,
        "--table",
        KAFKA_RAW_TABLE,
    ]

    proc = _process(paths)
    pid = proc.start(cmd, cwd=paths.repo_root)
    log.info(
        f"started the kafka feed (pid {pid}), {topics} on {brokers} "
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
        publishes=KAFKA_RAW_TABLE, **{"decoded and deduplicated by": "kafka_flow1"}
    )


def _check(paths: UqsPaths, options: Mapping[str, Any]) -> None:
    check_topics(paths, options["topic"])


def _start(paths: UqsPaths, options: Mapping[str, Any]) -> int:
    return start_kafka_feed(
        paths,
        brokers=options["brokers"],
        topic=options["topic"],
        group=options["group"],
        credential=options["tp_credential"],
    )


FEED = ExternalFeed(
    "kafka",
    _start,
    # Looked up when called, so what this module holds at call time is what runs.
    lambda paths: stop_kafka_feed(paths),
    lambda paths: kafka_feed_status(paths),
    options=(
        FeedOption(
            "brokers",
            f"bootstrap.servers of the cluster; default ${BROKERS_ENV}, else {DEFAULT_BROKERS}",
            default=DEFAULT_BROKERS,
            env=BROKERS_ENV,
        ),
        FeedOption(
            "topic",
            f"Topic(s) to consume, comma separated; default ${TOPICS_ENV}, else every "
            "topic a decoder job declares",
            env=TOPICS_ENV,
        ),
        FeedOption(
            "group",
            f"Consumer group id - the committed offsets belong to it (default {DEFAULT_GROUP})",
            default=DEFAULT_GROUP,
        ),
        connection.TP_CREDENTIAL,
    ),
    check=_check,
    produces={
        KAFKA_RAW_TABLE: (
            "a Kafka consumer: the q feed kafka_feed1, or the legacy Python one "
            "(`uqs connector start kafka`). There is no backfill alternative: "
            "replaying a topic from offset 0 is Kafka's own answer to that, and it "
            "needs the same broker"
        ),
    },
)
