"""Contract tests: this reader's field set and states must match
`.qetl.status.write_status` in src/etl/core/status.q, exactly like
python/uqf_frontend/tests/test_status.py checks its own reader. The two
readers are independent by design (see status_reader.py's module docstring)
so each is checked against the q source directly, never against the other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from uqf_airflow_provider.status_reader import (
    FIELDS,
    FILENAME_PREFIX,
    FILENAME_SUFFIX,
    STATES,
    MalformedStatusFile,
    read_status_file,
    status_file_path,
)

STATUS_Q = Path(__file__).resolve().parents[3] / "src" / "etl" / "core" / "status.q"


def write_status_file(directory: Path, instance: str, **overrides) -> Path:
    payload = {
        "worker": "markout_backfill",
        "instance_id": instance,
        "state": "completed",
        "source_version": "v1",
        "range_from": "2026-09-13T00:00:00.000000000",
        "range_to": "2026-09-14T00:00:00.000000000",
        "cursor": "2026-09-14T00:00:00.000000000",
        "rows_published": 1234,
        "windows_completed": 1,
        "reactions_owed": 0,
        "error": "",
        "updated_at": "2026-09-15T18:41:14.475818000",
        "pid": 4242,
        "host": "qhost",
        "run_id": "",
    }
    payload.update(overrides)
    directory.mkdir(parents=True, exist_ok=True)
    path = status_file_path(directory, instance)
    path.write_text(json.dumps(payload))
    return path


def test_states_match_the_q_writer():
    """`.qetl.status.status_states` is the single source of truth for what a
    worker may ever report; this reader must recognise exactly that set.
    """
    src = STATUS_Q.read_text()
    line = next(ln for ln in src.splitlines() if ln.startswith("status_states:"))
    assert set(re.findall(r"`(\w+)", line)) == set(STATES)


def test_every_field_this_reader_requires_is_one_q_writes():
    """A subset, not equality: this reader is narrower than the file on
    purpose. A required field q stopped writing would make every file
    malformed, and the sensor fail every task."""
    src = STATUS_Q.read_text()
    block = src[src.index("write_status:{") :]
    payload = block[block.index("payload:") : block.index("values_:")]
    q_fields = set(re.findall(r"`(\w+)", payload))
    assert set(FIELDS) <= q_fields, f"required here, never written: {set(FIELDS) - q_fields}"


def test_status_file_name_matches_the_q_writer():
    """The file name is how a reader finds a status file at all. `status.q`
    builds it in place (`"/airflow_status_",string[instance_id],".txt"`), so a
    change there leaves every reader looking for files that are never
    written, reporting "no status yet" for a worker that is running.
    """
    src = STATUS_Q.read_text()
    built = re.findall(r'"/(\w+)",string\[instance_id\],"([.\w]+)"', src)
    assert built, "status.q no longer builds the path the way this test reads it"
    assert set(built) == {(FILENAME_PREFIX, FILENAME_SUFFIX)}


def test_reads_a_completed_run(tmp_path):
    write_status_file(tmp_path, "markout1")
    status = read_status_file(status_file_path(tmp_path, "markout1"))
    assert status.worker == "markout_backfill"
    assert status.instance_id == "markout1"
    assert status.state == "completed"
    assert status.error is None
    assert status.terminal


def test_idle_is_terminal_and_distinct_from_failed(tmp_path):
    write_status_file(tmp_path, "idle1", state="idle")
    status = read_status_file(status_file_path(tmp_path, "idle1"))
    assert status.state == "idle"
    assert status.terminal


def test_running_is_not_terminal(tmp_path):
    write_status_file(tmp_path, "run1", state="running")
    status = read_status_file(status_file_path(tmp_path, "run1"))
    assert not status.terminal


def test_failed_run_carries_its_error(tmp_path):
    write_status_file(tmp_path, "fail1", state="failed", error="source read failed")
    status = read_status_file(status_file_path(tmp_path, "fail1"))
    assert status.state == "failed"
    assert status.error == "source read failed"


def test_missing_field_is_reported_not_raised_as_a_bare_keyerror(tmp_path):
    path = tmp_path / "airflow_status_bad1.txt"
    path.write_text(json.dumps({"worker": "w", "instance_id": "bad1"}))
    with pytest.raises(MalformedStatusFile, match="missing field"):
        read_status_file(path)


def test_unrecognised_state_is_reported(tmp_path):
    write_status_file(tmp_path, "new1", state="paused")
    with pytest.raises(MalformedStatusFile, match="unrecognised state"):
        read_status_file(status_file_path(tmp_path, "new1"))


def test_non_json_body_is_reported_not_raised_as_a_bare_jsondecodeerror(tmp_path):
    path = tmp_path / "airflow_status_trunc1.txt"
    path.write_text("{not json")
    with pytest.raises(MalformedStatusFile):
        read_status_file(path)
