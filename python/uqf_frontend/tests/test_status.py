"""Reading q's backfill status files.

The load-bearing test here is test_reader_field_set_matches_the_q_writer: the
format is defined in src/etl/core/status.q and consumed here, and nothing
but a test keeps the two in step.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import status
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.errors import ValidationFailed
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway

STATUS_Q = Path(__file__).resolve().parents[3] / "src" / "etl" / "core" / "status.q"


def write_status_file(directory: Path, instance: str, **overrides) -> Path:
    """Build a file in exactly the shape .qetl.status.write_status emits."""
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
        # this live process by default, so nothing reads as abandoned
        "pid": os.getpid(),
        "host": socket.gethostname().lower(),
        "run_id": "",
    }
    payload.update(overrides)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{status.FILENAME_PREFIX}{instance}{status.FILENAME_SUFFIX}"
    path.write_text(json.dumps(payload))
    return path


# --- the contract between the two languages -------------------------------


def test_reader_field_set_matches_the_q_writer():
    """The format lives in src/etl/core/status.q. If a field is added on
    one side only, the two silently disagree - q writes something the reader
    drops, or the reader demands something q never sends. This parses the q
    source so that cannot happen quietly.
    """
    src = STATUS_Q.read_text()
    block = src[src.index("write_status:{") :]
    payload = block[block.index("payload:") : block.index("values_:")]
    q_fields = re.findall(r"`(\w+)", payload)
    assert set(q_fields) == set(status.FIELDS), (
        f"q writer and Python reader disagree; only in q: "
        f"{set(q_fields) - set(status.FIELDS)}, only in Python: "
        f"{set(status.FIELDS) - set(q_fields)}"
    )


def test_reader_states_match_the_q_writer():
    src = STATUS_Q.read_text()
    line = next(ln for ln in src.splitlines() if ln.startswith("status_states:"))
    assert set(re.findall(r"`(\w+)", line)) == set(status.STATES)


def q_terminal_states() -> set[str]:
    """States `legal_transitions` lets go only to `starting` (begin again) or
    `failed` - the q definition of a run that has ended."""
    src = STATUS_Q.read_text()
    block = src[src.index("legal_transitions:") :]
    rows = re.findall(r"\(`(\w+);\s*((?:`\w+)+)\)", block[: block.index("))") + 2])
    assert rows, "status.q no longer spells legal_transitions the way this test reads it"
    return {s for s, nexts in rows if set(re.findall(r"`(\w+)", nexts)) <= {"starting", "failed"}}


def test_terminal_states_match_the_q_transitions():
    """#609: TERMINAL_STATES is a copy of what q's transitions imply. A state
    q makes terminal that this reader thinks is still running is a run the
    browser shows in progress forever."""
    assert set(status.TERMINAL_STATES) == q_terminal_states()


def test_status_file_name_matches_the_q_writer():
    """The file name is how a reader finds a status file at all. `status.q`
    builds it in place (`"/airflow_status_",string[instance_id],".txt"`), so a
    change there leaves every reader looking for files that are never
    written, reporting "no status yet" for a worker that is running.
    """
    src = STATUS_Q.read_text()
    built = re.findall(r'"/(\w+)",string\[instance_id\],"([.\w]+)"', src)
    assert built, "status.q no longer builds the path the way this test reads it"
    assert set(built) == {(status.FILENAME_PREFIX, status.FILENAME_SUFFIX)}


# --- reading ---------------------------------------------------------------


def test_reads_a_completed_run(tmp_path):
    write_status_file(tmp_path, "demo_markout1")
    statuses, unreadable = status.read_dir(tmp_path)
    assert unreadable == []
    assert len(statuses) == 1
    s = statuses[0]
    assert (s.worker, s.instance_id, s.state) == ("markout_backfill", "demo_markout1", "completed")
    assert s.rows_published == 1234
    assert s.terminal and s.ok
    assert s.error is None, "q writes '' for no error; the reader normalises it to None"


def test_idle_is_successful_and_distinct_from_completed(tmp_path):
    """ "ran, found no work" must not look like a failure, or an
    orchestrator retries a successful no-op forever.
    """
    write_status_file(tmp_path, "idle1", state="idle", rows_published=0, windows_completed=0)
    s = status.read_dir(tmp_path)[0][0]
    assert s.state == "idle"
    assert s.ok, "idle is a success"
    assert s.terminal, "idle is terminal for that run"


def test_failed_run_surfaces_its_error_window_and_cursor(tmp_path):
    """#58's acceptance criterion: a partially failed run shows its window,
    cursor and error.
    """
    write_status_file(
        tmp_path,
        "demo_markout1",
        state="failed",
        error="source read failed: connection refused",
        cursor="2026-09-13T12:00:00.000000000",
        rows_published=500,
    )
    s = status.read_dir(tmp_path)[0][0]
    assert s.state == "failed" and not s.ok
    assert s.error == "source read failed: connection refused"
    assert (s.range_from, s.range_to) == (
        "2026-09-13T00:00:00.000000000",
        "2026-09-14T00:00:00.000000000",
    )
    assert s.cursor == "2026-09-13T12:00:00.000000000"
    assert s.rows_published == 500, "partial progress is preserved"


def test_running_is_not_terminal(tmp_path):
    write_status_file(tmp_path, "r1", state="running")
    s = status.read_dir(tmp_path)[0][0]
    assert not s.terminal and s.ok


def test_newest_first(tmp_path):
    write_status_file(tmp_path, "old", updated_at="2026-09-15T10:00:00.000000000")
    write_status_file(tmp_path, "new", updated_at="2026-09-15T12:00:00.000000000")
    statuses, _ = status.read_dir(tmp_path)
    assert [s.instance_id for s in statuses] == ["new", "old"]


def test_one_damaged_file_does_not_hide_the_healthy_ones(tmp_path):
    """The same rule fleet.py and the usage fan-out follow."""
    write_status_file(tmp_path, "good")
    (tmp_path / f"{status.FILENAME_PREFIX}bad{status.FILENAME_SUFFIX}").write_text("{truncated")
    statuses, unreadable = status.read_dir(tmp_path)
    assert len(statuses) == 1 and statuses[0].instance_id == "good"
    assert len(unreadable) == 1
    assert "bad" in unreadable[0]["file"]


def test_a_file_missing_a_field_is_reported_not_silently_defaulted(tmp_path):
    path = write_status_file(tmp_path, "partial")
    payload = json.loads(path.read_text())
    del payload["cursor"]
    path.write_text(json.dumps(payload))
    statuses, unreadable = status.read_dir(tmp_path)
    assert statuses == []
    assert "cursor" in unreadable[0]["error"]


def test_unknown_state_warns_but_keeps_the_row(tmp_path):
    """Likeliest cause is a writer newer than this reader; discarding the row
    would lose the fact that the worker is alive and reporting.
    """
    write_status_file(tmp_path, "future1", state="quiescing")
    s = status.read_dir(tmp_path)[0][0]
    assert s.state == "quiescing"
    assert any("unrecognised state" in w for w in s.warnings)


def test_failed_without_an_error_warns(tmp_path):
    write_status_file(tmp_path, "f1", state="failed", error="")
    s = status.read_dir(tmp_path)[0][0]
    assert any("no error was recorded" in w for w in s.warnings)


def test_files_not_matching_the_convention_are_ignored(tmp_path):
    write_status_file(tmp_path, "real")
    (tmp_path / "notes.txt").write_text("not a status file")
    (tmp_path / "airflow_status_pending.txt.tmp").write_text("{partial")
    statuses, unreadable = status.read_dir(tmp_path)
    assert len(statuses) == 1
    assert unreadable == [], "a .tmp from an in-flight atomic write must not be read"


# --- configuration ---------------------------------------------------------


def test_unconfigured_names_the_variable(tmp_path):
    with pytest.raises(ValidationFailed, match="UQF_FRONTEND_STATUS_DIR"):
        status.read_dir(None)


def test_a_missing_directory_is_a_validation_failure_not_a_crash(tmp_path):
    with pytest.raises(ValidationFailed, match="does not exist"):
        status.read_dir(tmp_path / "absent")


# --- summary ---------------------------------------------------------------


def test_summary_counts_failed_separately_from_running(tmp_path):
    write_status_file(tmp_path, "a", state="completed")
    write_status_file(tmp_path, "b", state="running")
    write_status_file(tmp_path, "c", state="failed", error="boom")
    write_status_file(tmp_path, "d", state="idle")
    statuses, _ = status.read_dir(tmp_path)
    assert status.summarise(statuses) == {
        "workers": 4,
        "running": 1,
        "abandoned": 0,
        "completed": 1,
        "idle": 1,
        "failed": 1,
        "with_warnings": 0,
    }


# --- the endpoint ----------------------------------------------------------


def client_for(status_dir):
    return TestClient(
        create_app(
            gateway=FakeGateway(),
            settings=Settings(status_dir=status_dir),
            fleet=FakeFleet(),
        )
    )


def test_endpoint_returns_workers_and_a_poll_cadence(tmp_path):
    write_status_file(tmp_path, "demo_markout1")
    body = client_for(tmp_path).get("/ops/backfill").json()
    assert body["summary"]["workers"] == 1
    assert body["workers"][0]["instance_id"] == "demo_markout1"
    assert body["poll_seconds"] > 0
    assert body["source"] == str(tmp_path)


def test_owed_reactions_reach_the_api_with_the_failure_they_explain(tmp_path):
    """#632: a run whose windows landed but whose reactions did not ends q's
    `partial`, which the file records as `failed`. The count says why - a
    derived dataset is stale - where the bare state could not."""
    write_status_file(
        tmp_path,
        "deals1",
        state="failed",
        reactions_owed=2,
        error="2 reaction(s) owed over 1 window(s) (rebuild_positions) - a dataset "
        "derived from this one is stale; the next run re-fires them first",
    )
    row = client_for(tmp_path).get("/ops/backfill").json()["workers"][0]
    assert row["reactions_owed"] == 2
    assert "rebuild_positions" in row["error"]


def test_endpoint_reports_unconfigured_as_422(tmp_path):
    resp = client_for(None).get("/ops/backfill")
    assert resp.status_code == 422
    assert "UQF_FRONTEND_STATUS_DIR" in resp.json()["detail"]


def test_endpoint_carries_no_airflow_owned_fields(tmp_path):
    """These files carry q's facts only. Retry counts, task ordering,
    timeouts and concurrency are Airflow's, and inferring them here is the
    cross-layer inference the framework forbids.
    """
    write_status_file(tmp_path, "demo_markout1")
    row = client_for(tmp_path).get("/ops/backfill").json()["workers"][0]
    for forbidden in ("retries", "retry_count", "try_number", "timeout", "concurrency", "queue"):
        assert forbidden not in row


# --- a run whose process is gone ------------------------------------------


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    proc.wait()
    return proc.pid


def test_a_running_file_whose_process_is_gone_reads_as_abandoned(tmp_path):
    """The heartbeat dies with its process, so a killed run read as running
    forever - in this view, the headline count, and the Airflow sensor."""
    write_status_file(tmp_path, "dead1", state="running", pid=_dead_pid())
    write_status_file(tmp_path, "live1", state="running")
    statuses, _ = status.read_dir(tmp_path)
    by_id = {s.instance_id: s for s in statuses}
    assert by_id["dead1"].abandoned is True
    assert by_id["live1"].abandoned is False
    counts = status.summarise(statuses)
    assert (counts["running"], counts["abandoned"]) == (1, 1)


def test_another_hosts_pid_and_a_finished_run_are_never_abandoned(tmp_path):
    write_status_file(tmp_path, "far1", state="running", pid=_dead_pid(), host="elsewhere")
    write_status_file(tmp_path, "done1", state="completed", pid=_dead_pid())
    statuses, _ = status.read_dir(tmp_path)
    assert not any(s.abandoned for s in statuses)


def test_the_run_id_reaches_the_api_and_an_empty_one_is_none(tmp_path):
    write_status_file(tmp_path, "r1", run_id="0f1e2d3c-0000-0000-0000-000000000001")
    write_status_file(tmp_path, "r2")
    statuses, _ = status.read_dir(tmp_path)
    assert {s.instance_id: s.run_id for s in statuses} == {
        "r1": "0f1e2d3c-0000-0000-0000-000000000001",
        "r2": None,
    }
