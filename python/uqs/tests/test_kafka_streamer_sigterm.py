"""`uqs connector stop` lets the Kafka streamer leave its group (#2629).

Python's default SIGTERM terminates without unwinding, so the streamer's
`finally: consumer.close()` never ran and the member held its partitions
until the broker's session timeout. This runs the real `main()` as a
detached process, with kola and confluent_kafka stubbed, stops it the way
`uqs connector stop` does, and checks the consumer was closed by the time
`stop` returned.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from uqs.external.lifecycle import DetachedProcess

REPO = Path(__file__).resolve().parents[3]

KOLA_STUB = """
class Q:
    def __init__(self, *args, **kwargs):
        pass

    def connect(self):
        pass
"""

KAFKA_STUB = """
import os, pathlib, time

_MARKS = pathlib.Path(os.environ["STUB_MARKS"])


class Consumer:
    def __init__(self, conf):
        pass

    def subscribe(self, topics):
        pass

    def poll(self, timeout):
        (_MARKS / "polling").touch()
        time.sleep(timeout)
        return None

    def close(self):
        (_MARKS / "closed").touch()
"""


def test_stop_runs_the_streamers_finally_and_waits_for_it(tmp_path):
    stubs, marks = tmp_path / "stubs", tmp_path / "marks"
    stubs.mkdir()
    marks.mkdir()
    (stubs / "kola.py").write_text(KOLA_STUB)
    (stubs / "confluent_kafka.py").write_text(KAFKA_STUB)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(stubs), os.environ.get("PYTHONPATH", "")]),
        "STUB_MARKS": str(marks),
        "UQF_TP_CREDENTIAL": "user:secret",
    }
    proc = DetachedProcess("the kafka feed", tmp_path / "feed.pid", tmp_path / "feed.log")
    cmd = [sys.executable, "-m", "uqs.external.kafka_streamer", "--port", "1"]
    cmd += ["--brokers", "b:9092", "--topic", "t", "--group", "g", "--table", "kafka_message"]
    cmd += ["--repo-root", str(REPO)]
    proc.start(cmd, cwd=tmp_path, env=env)
    try:
        deadline = time.monotonic() + 60
        while not (marks / "polling").exists():
            assert proc.running(), proc.log_path.read_text()
            assert time.monotonic() < deadline, "the streamer never reached its poll loop"
            time.sleep(0.05)
    finally:
        proc.stop()
    assert (marks / "closed").exists(), "SIGTERM skipped consumer.close()"
