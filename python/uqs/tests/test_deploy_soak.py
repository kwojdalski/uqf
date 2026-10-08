"""--soak (#869): after verify, data flows for a while, and a streaming job
that is failing - or never beat - rolls the deployment back before activation.

SOAK_PY, the check the server runs from the release, runs here for real
against stream_health records in a data root under tmp_path, with this tree's
own pipeline registry. The deployment around it uses test_deploy.py's fake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime

from uqs.deploy import stages

FEED, ETL, WORKER = "fxfeed1", "cross1", "deals_backfill1"
SINCE = 1_800_000_000  # 2027-01-15, the soak's start on the server's clock


def _at(epoch: float) -> str:
    """A record's `at`, as q's .z.p prints it."""
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y.%m.%dD%H:%M:%S.%f") + "123"


def _record(tmp_path, proc: str, at: float, failing: bool = False, failed: int = 0) -> None:
    status = tmp_path / "uqs" / "status"
    status.mkdir(parents=True, exist_ok=True)
    body = {"job": proc, "process": proc, "pid": 1, "at": _at(at), "ok": 7, "failed": failed,
            "failing": failing, "last_error": "type" if failed else ""}  # fmt: skip
    (status / f"stream_health_{proc}.txt").write_text(json.dumps(body))


def _soak(tmp_path, *processes: str) -> dict:
    env = {**os.environ, "UQS_DATA_ROOT": str(tmp_path), "UQS_RUNTIME": "uqf"}
    r = subprocess.run(
        [sys.executable, "-c", stages.SOAK_PY, str(SINCE), ",".join(processes)],
        capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_job_that_beat_during_the_soak_with_no_failure_is_ok(tmp_path):
    _record(tmp_path, FEED, SINCE + 5)
    assert _soak(tmp_path, FEED)[FEED]["verdict"] == "ok"


def test_a_failing_job_is_named_with_its_last_error(tmp_path):
    _record(tmp_path, ETL, SINCE + 5, failing=True, failed=3)
    got = _soak(tmp_path, ETL)[ETL]
    assert got["verdict"] == "failing" and "3 batch(es) failed, last: type" in got["detail"]


def test_a_record_from_before_the_soak_is_no_beat(tmp_path):
    """A file an earlier run left must not vouch for this one."""
    _record(tmp_path, FEED, SINCE - 60)
    assert _soak(tmp_path, FEED)[FEED]["verdict"] == "no beat"


def test_a_job_with_no_record_is_no_beat(tmp_path):
    assert _soak(tmp_path, ETL) == {
        ETL: {"verdict": "no beat", "detail": "it wrote no stream_health record"}
    }


def test_only_started_streaming_jobs_are_judged(tmp_path):
    """Infrastructure has no batches and a bounded worker never starts."""
    _record(tmp_path, FEED, SINCE + 5)
    assert set(_soak(tmp_path, FEED, "rdb1", "gateway1", WORKER)) == {FEED}
