"""Starting and stopping the consumer that carries Kafka records onto kafka_message.

Two interchangeable consumers publish the same envelope (docs/services/kafka.md):

  python   `uqs.external.kafka_streamer`, run as a detached process of its own.
           Needs confluent-kafka and kola; runs wherever the Python does.
  native   `kafka_feed1`, the q process holding KX's kfk. Needs a KDB-X or kdb+
           host with the library built (UQF_KFK_HOME), so it is started through
           `uqs start`, with its configuration in the environment.

Both are stopped with SIGTERM (the python one directly, the native one by
`uqs stop`), which lets the client leave its group: a killed member would hold
its partitions until the session timeout and a quick restart would get none.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from uqs.external import kafka_streamer
from uqs.external.kafka_example.broker import Broker
from uqs.external.kafka_example.lifecycle_wait import wait_gone
from uqs.external.lifecycle import DetachedProcess
from uqs.paths import UqsError

#: The credential every external publisher presents to stp1 (see external/kafka_feed.py).
STP_CREDENTIAL = "feed:pass"


@dataclass
class Feed:
    """One consumer of the example topics, in one group."""

    kind: str
    broker: Broker
    repo_root: Path
    stp_port: int
    topics: tuple[str, ...]
    group: str
    #: for the native feed: the `uqs` command prefix and the kfk directory
    uqs: list[str] = field(default_factory=list)
    kfk_home: str = ""

    def _process(self) -> DetachedProcess:
        return DetachedProcess(
            f"the {self.kind} feed",
            self.broker.directory / f"feed-{self.kind}.pid",
            self.broker.directory / f"feed-{self.kind}.log",
        )

    def log_tail(self, n: int = 600) -> str:
        path = self._process().log_path
        return path.read_text(errors="replace")[-n:] if path.is_file() else ""

    def start(self) -> None:
        topics = ",".join(self.topics)
        if self.kind == "python":
            runner = Path(kafka_streamer.__file__)
            cmd = [
                sys.executable, str(runner),
                "--host", "localhost", "--port", str(self.stp_port),
                "--credential", STP_CREDENTIAL,
                "--brokers", self.broker.bootstrap,
                "--topic", topics, "--group", self.group,
                "--table", kafka_streamer.KAFKA_RAW_TABLE,
                "--repo-root", str(self.repo_root),
            ]  # fmt: skip
            self._process().start(cmd, cwd=self.repo_root)
            return
        if not self.uqs or not self.kfk_home:
            raise UqsError("the native feed needs the `uqs` command prefix and UQF_KFK_HOME")
        env = {
            **os.environ,
            "UQF_KFK_HOME": self.kfk_home,
            "UQF_KAFKA_BROKERS": self.broker.bootstrap,
            "UQF_KAFKA_TOPICS": topics,
            "UQF_KAFKA_GROUP": self.group,
        }
        done = subprocess.run(  # noqa: S603
            [*self.uqs, "start", "kafka_feed1"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if done.returncode:
            raise UqsError(
                f"uqs start kafka_feed1 failed: {done.stdout[-400:]}{done.stderr[-400:]}"
            )

    def stop(self, wait: float = 20.0) -> None:
        """Stop it and wait until it has gone - a restart must not race its predecessor."""
        if self.kind == "native":
            subprocess.run(
                [*self.uqs, "stop", "kafka_feed1"], capture_output=True, timeout=120, check=False
            )  # noqa: S603
            return
        proc = self._process()
        pid = proc.pid()
        proc.stop()
        if pid is not None and not wait_gone(pid, wait):
            raise UqsError(
                f"the python feed (pid {pid}) did not exit within {wait:.0f}s of SIGTERM"
            )
