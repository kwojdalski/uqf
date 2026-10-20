"""The Kafka client side of the example: configuration, topics, ACLs.

`confluent-kafka` is imported inside the functions that need it, so importing
the example's modules never requires it. Credentials come from the broker
directory's 0600 file (or the environment for a broker that is not ours) and
never appear in a log line, an exception message or a command line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from uqs.external.kafka_example.batch import PARTITIONS, TOPICS
from uqs.external.kafka_example.broker import Broker
from uqs.paths import UqsError

GROUP_1 = "uqf-example-g1"
GROUP_2 = "uqf-example-g2"
GROUP_3 = "uqf-example-g3"
GROUPS = (GROUP_1, GROUP_2, GROUP_3)


def client_config(broker: Broker, identity: str | None = None, **extra: Any) -> dict[str, Any]:
    """librdkafka settings for `identity` against `broker`; plaintext when it is not secure.

    Raises when a secure broker is asked for an identity it has no credentials for.
    """
    cfg: dict[str, Any] = {"bootstrap.servers": broker.bootstrap}
    if broker.secure:
        users = broker.credentials()
        if identity not in users:
            raise UqsError(f"no credentials for identity {identity!r} on this broker")
        cfg.update(
            {
                "security.protocol": "SASL_PLAINTEXT",
                "sasl.mechanism": "PLAIN",
                "sasl.username": identity,
                "sasl.password": users[identity],
            }
        )
    cfg.update(extra)
    return cfg


def create_topics(broker: Broker, *, timeout: float = 30.0) -> None:
    """Create the example topics (PARTITIONS each) as `admin`; one that exists is left alone."""
    from confluent_kafka.admin import AdminClient, NewTopic  # ty: ignore[unresolved-import]

    admin = AdminClient(client_config(broker, "admin" if broker.secure else None))
    futures = admin.create_topics([NewTopic(t, PARTITIONS, 1) for t in TOPICS])
    for topic, fut in futures.items():
        try:
            fut.result(timeout)
        except Exception as exc:  # noqa: BLE001 - only "already exists" is acceptable
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise UqsError(f"could not create {topic}: {exc}") from exc


def grant(broker: Broker, *, timeout: float = 30.0) -> dict[str, str]:
    """Set the ACLs of the four identities of a secure broker; return what each may do.

      reader    topic Read+Describe and group Read on every group in GROUPS
      nogroup   topic Read+Describe, no group permission at all
      notopic   group Read on every group in GROUPS, no topic permission
      admin     a super user (no ACLs needed)

    The two refusals are different broker errors on purpose: a group permission
    is not a topic permission, and the diagnostics must tell them apart.
    """
    from confluent_kafka.admin import (  # ty: ignore[unresolved-import]
        AclBinding,
        AclOperation,
        AclPermissionType,
        AdminClient,
        ResourcePatternType,
        ResourceType,
    )

    if not broker.secure:
        raise UqsError("this broker has no authorizer - start it with --secure")
    admin = AdminClient(client_config(broker, "admin"))

    def acl(user: str, rtype: Any, name: str, op: Any) -> Any:
        return AclBinding(
            rtype,
            name,
            ResourcePatternType.LITERAL,
            f"User:{user}",
            "*",
            op,
            AclPermissionType.ALLOW,
        )

    bindings = []
    for user in ("reader", "nogroup"):
        for topic in TOPICS:
            for op in (AclOperation.READ, AclOperation.DESCRIBE):
                bindings.append(acl(user, ResourceType.TOPIC, topic, op))
    for user in ("reader", "notopic"):
        for group in GROUPS:
            bindings.append(acl(user, ResourceType.GROUP, group, AclOperation.READ))
    for fut in admin.create_acls(bindings).values():
        fut.result(timeout)
    return {
        "reader": "read topics, use groups",
        "nogroup": "read topics, NO group",
        "notopic": "use groups, NO topic",
        "admin": "super user",
    }


def credentials_file(broker: Broker) -> Path:
    """Where the passwords are - for messages, so a password is never itself printed."""
    return broker.directory / "credentials.json"
