"""A disposable Apache Kafka broker for the example: KRaft, one node, a scratch directory.

Nothing here is installed system-wide, and nothing outlives `stop`: the broker
runs as one detached JVM whose pid this module recorded, in a directory it
created and marked, and `stop` signals only that pid and removes only a
directory carrying the marker.

ADVERTISED LISTENERS ARE EXPLICIT. A broker tells every client, in its
metadata, which address to connect to next; that address - not the bootstrap
one - is what the group coordinator and partition leaders are reached at. So
`advertised_host` is a parameter with no clever default: 127.0.0.1 for the
direct local path, `localhost` for an SSH tunnel (docs/guides/kafka-crypto-example.md),
and an unresolvable name to reproduce the advertised-endpoint failure.

AUTHORISATION IS OPTIONAL AND DISPOSABLE. With `secure=True` the listener is
SASL/PLAIN with an authorizer that denies by default, and `credentials.json`
(mode 0600, in the scratch directory) holds fresh random passwords for four
identities. A password is never printed or logged; commands name the file.

The distribution is fetched from archive.apache.org, checked against the
published SHA-512, and unpacked into a cache directory; a copy already unpacked
(`KAFKA_HOME`) is used as it is.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from uqs.external.kafka_example.lifecycle_wait import wait_gone
from uqs.external.lifecycle import DetachedProcess, process_identity
from uqs.paths import UqsError

VERSION = "4.3.1"
SCALA = "2.13"
ARCHIVE = "https://archive.apache.org/dist/kafka"
#: Marks a directory this module made, so `clean` never removes one it did not.
MARKER = ".uqf-kafka-example"
#: The identities of a secure broker: what each may do is set in `acls.py`.
IDENTITIES = ("admin", "reader", "nogroup", "notopic")
#: A name that can never resolve (RFC 2606 reserves .invalid): the decoy listener advertises it.
UNRESOLVABLE_HOST = "broker-unresolvable.invalid"


def free_port() -> int:
    """A TCP port nothing is listening on right now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def fetch_distribution(cache: Path, version: str = VERSION) -> Path:
    """The unpacked Kafka distribution, downloading and verifying it when absent.

    The tarball's SHA-512 is read from Apache's own `.sha512` file and compared
    before anything is unpacked; a mismatch deletes the download and refuses.
    """
    name = f"kafka_{SCALA}-{version}"
    home = cache / name
    if (home / "bin" / "kafka-server-start.sh").is_file():
        return home
    cache.mkdir(parents=True, exist_ok=True)
    tgz = cache / f"{name}.tgz"
    base = f"{ARCHIVE}/{version}/{name}.tgz"
    if not tgz.is_file():
        urllib.request.urlretrieve(base, tgz)  # noqa: S310 - a fixed https URL
    with urllib.request.urlopen(base + ".sha512", timeout=60) as resp:  # noqa: S310
        published = "".join(re.findall(r"[0-9A-Fa-f]{8}", resp.read().decode())).lower()
    digest = hashlib.sha512()
    with tgz.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != published:
        tgz.unlink()
        raise UqsError(
            f"{tgz.name}: SHA-512 does not match archive.apache.org's - download deleted"
        )
    with tarfile.open(tgz) as tar:
        tar.extractall(cache, filter="data")  # noqa: S202 - checksum verified above
    return home


def _jaas(users: dict[str, str]) -> str:
    entries = " ".join(f'user_{u}="{p}"' for u, p in users.items())
    return (
        "org.apache.kafka.common.security.plain.PlainLoginModule required "
        f'username="admin" password="{users["admin"]}" {entries};'
    )


def server_properties(
    *,
    data: Path,
    port: int,
    controller_port: int,
    advertised_host: str,
    users: dict[str, str] | None,
    decoy_port: int | None = None,
) -> str:
    """The broker's server.properties. Pure, so the listener arithmetic is tested.

    The CONTROLLER listener is always plaintext on 127.0.0.1 and never
    advertised to clients; the client listener is the one `advertised_host` names.
    With `decoy_port` a second listener, DECOY, accepts connections on that port
    but advertises UNRESOLVABLE_HOST: a client that bootstraps through it gets
    metadata pointing at a name that cannot resolve - the advertised-endpoint
    failure, reproduced against a broker whose real listener works.
    """
    protocol = "SASL_PLAINTEXT" if users else "PLAINTEXT"
    decoy = f",DECOY://127.0.0.1:{decoy_port}" if decoy_port else ""
    decoy_adv = f",DECOY://{UNRESOLVABLE_HOST}:{decoy_port}" if decoy_port else ""
    decoy_map = f",DECOY:{protocol}" if decoy_port else ""
    lines = [
        "process.roles=broker,controller",
        "node.id=1",
        f"controller.quorum.voters=1@127.0.0.1:{controller_port}",
        f"listeners={protocol}://127.0.0.1:{port},CONTROLLER://127.0.0.1:{controller_port}{decoy}",
        f"advertised.listeners={protocol}://{advertised_host}:{port}{decoy_adv}",
        "controller.listener.names=CONTROLLER",
        f"inter.broker.listener.name={protocol}",
        f"listener.security.protocol.map=CONTROLLER:PLAINTEXT,{protocol}:{protocol}{decoy_map}",
        f"log.dirs={data}",
        "num.partitions=2",
        "offsets.topic.replication.factor=1",
        "offsets.topic.num.partitions=1",
        "transaction.state.log.replication.factor=1",
        "transaction.state.log.min.isr=1",
        "group.initial.rebalance.delay.ms=0",
        "auto.create.topics.enable=false",
    ]
    if users:
        lines += [
            "sasl.enabled.mechanisms=PLAIN",
            "sasl.mechanism.inter.broker.protocol=PLAIN",
            f"listener.name.{protocol.lower()}.plain.sasl.jaas.config={_jaas(users)}",
            *([f"listener.name.decoy.plain.sasl.jaas.config={_jaas(users)}"] if decoy_port else []),
            "authorizer.class.name=org.apache.kafka.metadata.authorizer.StandardAuthorizer",
            # ANONYMOUS is the plaintext controller listener's principal: it is the broker itself.
            "super.users=User:admin;User:ANONYMOUS",
        ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Broker:
    """A broker this module started: where it is, and how to reach it."""

    directory: Path
    port: int
    advertised_host: str
    secure: bool
    decoy_port: int | None = None

    @property
    def decoy_bootstrap(self) -> str | None:
        """Bootstrap through the decoy listener, whose metadata advertises an unresolvable name."""
        return f"127.0.0.1:{self.decoy_port}" if self.decoy_port else None

    @property
    def bootstrap(self) -> str:
        """The bootstrap address a client on this host dials."""
        return f"127.0.0.1:{self.port}"

    @property
    def advertised(self) -> str:
        """The address the broker tells clients to use afterwards."""
        return f"{self.advertised_host}:{self.port}"

    def process(self) -> DetachedProcess:
        return DetachedProcess(
            "the example broker", self.directory / "broker.pid", self.directory / "broker.log"
        )

    def credentials(self) -> dict[str, str]:
        """identity -> password, from the 0600 file; empty for a plaintext broker."""
        path = self.directory / "credentials.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def save(self) -> None:
        state = {
            "port": self.port,
            "advertised_host": self.advertised_host,
            "secure": self.secure,
            "decoy_port": self.decoy_port,
        }
        (self.directory / "broker.json").write_text(json.dumps(state))

    @classmethod
    def load(cls, directory: Path) -> Broker:
        if not (directory / MARKER).is_file():
            raise UqsError(f"{directory} is not a directory the kafka example made")
        s = json.loads((directory / "broker.json").read_text())
        return cls(directory, s["port"], s["advertised_host"], s["secure"], s.get("decoy_port"))


def start(
    *,
    cache: Path,
    advertised_host: str = "127.0.0.1",
    secure: bool = False,
    decoy: bool = False,
    base: Path | None = None,
    port: int | None = None,
    timeout: float = 90.0,
) -> Broker:
    """Format and start a broker in a new scratch directory; return once it answers.

    Raises with the tail of the broker's own log when it does not come up within
    `timeout`, and stops the half-started JVM first: a failed start leaves
    nothing running.
    """
    java = shutil.which("java")
    if java is None:
        raise UqsError("java is not on PATH - Kafka needs a JVM (17 or newer)")
    home = fetch_distribution(cache)
    directory = Path(tempfile.mkdtemp(prefix="uqf-kafka-example-", dir=base))
    (directory / MARKER).write_text("created by uqs.external.kafka_example.broker\n")
    users = {u: secrets.token_urlsafe(18) for u in IDENTITIES} if secure else None
    if users:
        creds = directory / "credentials.json"
        creds.touch(mode=0o600)
        creds.write_text(json.dumps(users))
    broker = Broker(
        directory, port or free_port(), advertised_host, secure, free_port() if decoy else None
    )
    props = directory / "server.properties"
    props.write_text(
        server_properties(
            data=directory / "data",
            port=broker.port,
            controller_port=free_port(),
            advertised_host=advertised_host,
            users=users,
            decoy_port=broker.decoy_port,
        )
    )
    props.chmod(0o600)  # the JAAS line holds the passwords
    env = {**os.environ, "KAFKA_HEAP_OPTS": "-Xms256m -Xmx512m", "LOG_DIR": str(directory / "logs")}
    script = str(home / "bin" / "kafka-storage.sh")
    cluster = (
        subprocess.run(  # noqa: S603
            [script, "random-uuid"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    storage = subprocess.run(  # noqa: S603
        [script, "format", "-t", cluster, "-c", str(props)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    if storage.returncode != 0:
        raise UqsError(
            f"kafka-storage format failed: {storage.stdout[-400:]}{storage.stderr[-400:]}"
        )
    broker.save()
    proc = broker.process()
    proc.start([str(home / "bin" / "kafka-server-start.sh"), str(props)], cwd=directory, env=env)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not proc.running():
            break
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", broker.port)) == 0:
                return broker
        time.sleep(0.5)
    tail = (
        (directory / "broker.log").read_text()[-1200:]
        if (directory / "broker.log").is_file()
        else ""
    )
    stop(broker, remove=False)
    raise UqsError(
        f"the broker did not accept connections within {timeout:.0f}s; its log ends:\n{tail}"
    )


def stop(broker: Broker, *, remove: bool = True, wait: float = 30.0) -> None:
    """Stop the broker this directory's pid file names, then remove the directory.

    SIGTERM to the recorded pid only, after the identity check `DetachedProcess`
    makes; SIGKILL only if that same pid is still alive after `wait`. The
    directory is removed only when it carries the marker.
    """
    proc = broker.process()
    pid = proc.pid()
    identity = process_identity(pid) if pid is not None and proc.running() else None
    pid = proc.stop()
    if pid is not None and not wait_gone(pid, wait):
        if identity is not None and process_identity(pid) == identity:
            os.kill(pid, 9)
    if remove and (broker.directory / MARKER).is_file():
        shutil.rmtree(broker.directory, ignore_errors=True)
