"""uqs summary's Batches column (#832): a streaming job whose batches throw is
`failing`, though its process is up and its heartbeat ok.

The file is written by src/etl/core/stream_health.q and read here with its
keys as literals, so the last test has q write one and this read it back -
the boundary the two copies of those keys meet at. It needs q, and skips
without one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.paths import default_paths
from uqs.stack import runs, stream_health


def _write(directory: Path, **record) -> None:
    full = {
        "job": "cross",
        "process": "cross1",
        "pid": 4242,
        "host": "h",
        "ok": 10,
        "failed": 0,
        "failing": False,
        "last_error": "",
        "last_failure_at": "",
        **record,
    }
    (directory / f"stream_health_{full['job']}.txt").write_text(json.dumps(full))


def _row(process: str, status: str = "up", pid: str = "4242") -> dict[str, str]:
    return {"Process": process, "Status": status, "PID": pid}


@pytest.fixture
def status(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    return tmp_path


def test_a_failing_job_is_marked_with_its_count_and_time(status):
    _write(status, failing=True, failed=3, last_failure_at="2026.10.08D10:00:00.000000000")
    rows = [_row("cross1")]
    failing = stream_health.attach_batches_column(rows, default_paths())
    assert rows[0]["Batches"] == "failing (3)"
    assert [r["job"] for r in failing] == ["cross"]


def test_a_job_whose_last_beat_saw_no_failure_is_ok(status):
    _write(status, failing=False, failed=3)
    rows = [_row("cross1")]
    assert stream_health.attach_batches_column(rows, default_paths()) == []
    assert rows[0]["Batches"] == "ok"


def test_another_runs_file_never_marks_the_current_process(status):
    """A file left by an earlier run of the job names another pid."""
    _write(status, failing=True, failed=3, pid=1)
    rows = [_row("cross1", pid="4242")]
    assert stream_health.attach_batches_column(rows, default_paths()) == []
    assert rows[0]["Batches"] == "-"


def test_a_down_process_or_one_with_no_job_shows_a_dash(status):
    _write(status, failing=True, failed=3)
    rows = [_row("cross1", status="down", pid=""), _row("rdb1")]
    stream_health.attach_batches_column(rows, default_paths())
    assert [r["Batches"] for r in rows] == ["-", "-"]


def test_an_unreadable_file_is_skipped(status):
    (status / "stream_health_broken.txt").write_text("{not json")
    _write(status, failing=True, failed=1)
    assert list(stream_health.read(default_paths())) == ["cross1"]


def test_q_writes_what_this_reads(status):
    if q_interpreter(os.environ) is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    rows = runs.query(
        default_paths(),
        "{`.proc.procname set `cross1;"  # what TorQ names the process
        ' .qetl.stream_health.record[`cross;0b;"stale shape"];'
        " .qetl.stream_health.write[`cross;1b];"
        " ([] pid:enlist .z.i)}[]",
        directory=status,
        attach=False,
        loads=("src/init.q", "src/etl/init.q"),
    )
    (record,) = stream_health.read(default_paths()).values()
    assert record["job"] == "cross" and record["failing"] is True
    assert record["failed"] == 1 and record["last_error"] == "stale shape"
    cell = stream_health.cell(record, str(int(rows[0]["pid"])))
    assert cell == "failing (1)"
