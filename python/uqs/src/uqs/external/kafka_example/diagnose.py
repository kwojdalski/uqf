"""The verifier's reading of a Kafka client error: which stage, and what to do.

This mirrors `.qetl.kafka.diag.classify` in src/etl/core/kafka_diagnostics.q,
which is what the native feed uses. The two are deliberately the same small
table: the q side is exercised against real broker text in
tests/q/test_kafka_diagnostics.q and this side against the same text in
python/uqs/tests/test_kafka_example.py, so a drift shows in one of them.
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass

#: librdkafka error name or code -> kind
NAMES = {
    "TOPIC_AUTHORIZATION_FAILED": "topic_auth",
    "GROUP_AUTHORIZATION_FAILED": "group_auth",
    "UNKNOWN_TOPIC_OR_PART": "unknown_topic",
    "_UNKNOWN_TOPIC": "unknown_topic",
    "_AUTHENTICATION": "sasl",
    "_ALL_BROKERS_DOWN": "all_down",
}
CODES = {
    29: "topic_auth",
    30: "group_auth",
    3: "unknown_topic",
    -188: "unknown_topic",
    -169: "sasl",
    -187: "all_down",
}
KIND_STAGE = {
    "topic_auth": "consume",
    "group_auth": "group",
    "unknown_topic": "consume",
    "sasl": "bootstrap",
    "all_down": "bootstrap",
    "other": "unclassified",
}
_CONNECT = ("failed to resolve", "connection refused", "connect to", "connection setup timed out")


@dataclass(frozen=True)
class Diagnosis:
    stage: str
    kind: str
    endpoint: str
    detail: str

    @property
    def cause(self) -> tuple[str, str, str]:
        return (self.stage, self.kind, self.endpoint)


def _endpoint(reason: str) -> tuple[str, str]:
    """(host:port, node) from librdkafka's `host:port/node: text` prefix."""
    quoted = re.search(r"'([^']+)'", reason)
    lead = reason.split(": ", 1)[0] if ": " in reason else ""
    host, _, node = lead.partition("/")
    endpoint = quoted.group(1) if quoted else host
    if " " in endpoint:
        endpoint = ""
    return endpoint, ("coordinator" if "coordinator" in lead.lower() else node)


def classify(code: int | str | None, reason: str, bootstrap: tuple[str, ...] = ()) -> Diagnosis:
    """Classify one client report. `bootstrap` is the list of host:port strings dialled first."""
    kind = (
        NAMES.get(code) if isinstance(code, str) else CODES.get(code) if code is not None else None
    )
    low = reason.lower()
    if kind is None:
        if "group authorization" in low or "not authorized to access group" in low:
            kind = "group_auth"
        elif "topic authorization" in low or "not authorized to access topic" in low:
            kind = "topic_auth"
        elif "sasl authentication" in low:
            kind = "sasl"
        elif "brokers are down" in low:
            kind = "all_down"
        elif any(c in low for c in _CONNECT) or "transport failure" in low:
            kind = "connect"
        else:
            kind = "other"
    endpoint, node = _endpoint(reason)
    if kind == "connect":
        on_boot = node == "bootstrap" or (endpoint in bootstrap and not node)
        stage = "bootstrap" if on_boot else "advertised"
    else:
        stage = KIND_STAGE[kind]
    return Diagnosis(stage, kind, endpoint, reason)


def probe_endpoint(host: str, port: int, timeout: float = 3.0) -> str | None:
    """None when host:port resolves and accepts a TCP connection, else why not.

    The resolver and the connect are separate steps because they are separate
    failures: "does not resolve" is an advertised-name problem, "refused" a
    port or tunnel one.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return f"cannot resolve {host!r}: {exc.strerror or exc}"
    last = "no address"
    for family, kind, proto, _, addr in infos:
        try:
            with socket.socket(family, kind, proto) as s:
                s.settimeout(timeout)
                s.connect(addr)
            return None
        except OSError as exc:
            last = f"cannot connect to {addr[0]}:{port}: {exc.strerror or exc}"
    return last
