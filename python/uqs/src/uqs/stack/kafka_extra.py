"""confluent-kafka, the uqs `kafka` extra (#1515), and the refusal when it is absent.

The Python Kafka connector (`uqs connector start kafka`, kafka_streamer) and
the crypto example's tooling (`python -m uqs.external.kafka_example`) import
confluent-kafka; nothing else in uqs does, so it is an optional extra rather
than a dependency. Every entry point checks for it here, before it starts
anything, and names the way to get it: `uv sync --extra kafka` in a checkout,
`uqs deploy build --with-kafka-connector` for a release, whose server installs
it offline from the release's own wheels.

The native q feed (kafka_feed1) and the decoders need none of this.
"""

from __future__ import annotations

import importlib.util

from uqs.paths import UqsError

EXTRA = "kafka"
MODULE = "confluent_kafka"


def missing(what: str) -> str:
    return (
        f"{what} needs confluent-kafka, the uqs `{EXTRA}` extra, and this environment lacks "
        f"it: run `uv sync --extra {EXTRA}` in a checkout, or build the release with "
        "`uqs deploy build --with-kafka-connector`. The native q feed (kafka_feed1) "
        "needs neither"
    )


def available() -> bool:
    return importlib.util.find_spec(MODULE) is not None


def require(what: str = "the Python Kafka connector") -> None:
    """Refuse, naming the extra, when confluent-kafka cannot be imported."""
    if not available():
        raise UqsError(missing(what))
