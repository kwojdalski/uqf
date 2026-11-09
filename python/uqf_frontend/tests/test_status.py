"""Reading q's backfill status files.

The format is defined in src/etl/core/status.q and consumed here. Its fields
are q's own: status.FIELDS is the generated STATUS_FIELDS (#1795), which the
q-facts hook holds to `.qetl.status.fields`.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import status
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.errors import ValidationFailed
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway
from uqf_frontend.models.ops import RunState

STATUS_Q = Path(__file__).resolve().parents[3] / "src" / "etl" / "core" / "status.q"


def written_now() -> str:
    """`updated_at` as q writes it, at this moment: a file this live process
    wrote. One dated before the process started would name a recycled pid,
    which reads as gone (#2652)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f000")


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


def test_reader_states_match_the_q_writer():
    src = STATUS_Q.read_text()
    line = next(
        ln for ln in src.splitlines() if re.match(r"(?:\.[A-Za-z_][\w.]*\.)?status_states:", ln)
    )
    assert set(re.findall(r"`(\w+)", line)) == set(status.STATES)


def test_the_api_states_are_the_reader_states():
    """models.RunState is what the browser's RunState is held to by
    test_api_types.py, and STATES is held to q above - so q, the reader, the
    API and the browser name one set of states (#820)."""
    assert set(typing.get_args(RunState)) == set(status.STATES)


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
    names it `file_prefix`,instance,`file_suffix` (`.qetl.status.file_path`), so a
    change there leaves every reader looking for files that are never
    written, reporting "no status yet" for a worker that is running.
    """
    src = STATUS_Q.read_text()
    prefix = re.search(r'^(?:\.[A-Za-z_][\w.]*\.)?file_prefix:"([^"]+)"', src, re.M)
    suffix = re.search(r'^(?:\.[A-Za-z_][\w.]*\.)?file_suffix:"([^"]+)"', src, re.M)
    assert prefix and suffix, "status.q no longer names the file the way this test reads it"
    assert (status.FILENAME_PREFIX, status.FILENAME_SUFFIX) == (prefix.group(1), suffix.group(1))


# --- reading ---------------------------------------------------------------


def test_reads_a_completed_run(tmp_path):
    write_status_file(tmp_path, "crypto_markout1")
    statuses, unreadable = status.read_dir(tmp_path)
    assert unreadable == []
    assert len(statuses) == 1
    s = statuses[0]
    assert (s.worker, s.instance_id, s.state) == (
        "markout_backfill",
        "crypto_markout1",
        "completed",
    )
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
        "crypto_markout1",
        state="failed",
        error="source read failed: connection refused",
        cursor="2026-09-13T12:00:00.000000000",
        rows_published=500,
    )
    s = status.read_dir(tmp_path)[0][0]
    assert s.state == "failed" and not s.ok
    assert s.error == "source read failed: connection refused"
    assert (s.range_from, s.range_to) == (
        "2026-09-13T00:00:00.000000000Z",
        "2026-09-14T00:00:00.000000000Z",
    )
    assert s.cursor == "2026-09-13T12:00:00.000000000Z"
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


def test_an_unknown_state_is_an_unreadable_file_not_a_row(tmp_path):
    """#2583: the API types a state as one of STATES, so a row in another
    failed the whole response. The file is reported unreadable, naming the
    state - the likeliest cause is a writer newer than this reader."""
    write_status_file(tmp_path, "future1", state="quiescing")
    statuses, unreadable = status.read_dir(tmp_path)
    assert statuses == []
    assert "unrecognised state 'quiescing'" in unreadable[0]["error"]


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
    with pytest.raises(ValidationFailed, match="UQF_FRONTEND_STACK_ROOT"):
        status.read_dir(None)


def test_a_missing_directory_is_a_validation_failure_not_a_crash(tmp_path):
    with pytest.raises(ValidationFailed, match="does not exist"):
        status.read_dir(tmp_path / "absent")


# --- summary ---------------------------------------------------------------


def test_summary_counts_failed_separately_from_running(tmp_path):
    write_status_file(tmp_path, "a", state="completed")
    write_status_file(tmp_path, "b", state="running", updated_at=written_now())
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


def _stack_root(base: Path) -> Path:
    """A stack root uqs accepts - its two markers - with nothing bootstrapped:
    the checkout's own may hold a setenv.sh, which these tests must not read."""
    root = base / "stack_root"
    (root / "src" / "etl").mkdir(parents=True, exist_ok=True)
    (root / "python" / "uqs").mkdir(parents=True, exist_ok=True)
    return root


def test_a_stack_root_defaults_the_status_dir_to_the_workers(tmp_path, monkeypatch):
    """#1367: workers write where uqs.stack.runs.status_dir says. A frontend told
    only the stack root must read there, not report itself unconfigured."""
    from uqs import paths as stack_paths
    from uqs.stack import runs

    monkeypatch.delenv("UQF_STATUS_DIR", raising=False)
    root = _stack_root(tmp_path)
    settings = Settings(stack_root=root)
    assert status.status_directory(settings) == runs.status_dir(stack_paths.paths_for_root(root))
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    assert status.status_directory(settings) == tmp_path


def test_the_status_dir_the_stack_was_started_with_is_the_one_read(tmp_path, monkeypatch):
    """#2038: a worker writes where the shell that ran `uqs start` pointed
    UQF_STATUS_DIR. setenv.sh records it, and the frontend - started from
    another shell, without it - reads that one, not $TORQDATA/status."""
    from uqs import paths as stack_paths
    from uqs.stack import runtime

    root = _stack_root(tmp_path)
    paths = stack_paths.paths_for_root(root)
    paths.torqdata.mkdir(parents=True)
    workers = tmp_path / "uqf-status"
    monkeypatch.setenv("UQF_STATUS_DIR", str(workers))
    runtime.write_setenv(paths, 6050)
    monkeypatch.delenv("UQF_STATUS_DIR")
    assert status.status_directory(Settings(stack_root=root)) == workers


def test_without_a_stack_root_there_is_no_status_dir():
    """#1536: no second setting - the view reports itself unconfigured."""
    assert status.status_directory(Settings()) is None


def test_the_endpoint_reads_the_workers_directory_given_only_a_stack_root(tmp_path, monkeypatch):
    workers = tmp_path / "status"
    workers.mkdir()
    monkeypatch.setenv("UQF_STATUS_DIR", str(workers))
    write_status_file(workers, "crypto_markout1")
    client = TestClient(
        create_app(
            gateway=FakeGateway(),
            settings=Settings(stack_root=_stack_root(tmp_path)),
            fleet=FakeFleet(),
        )
    )
    body = client.get("/ops/backfill").json()
    assert body["workers"][0]["instance_id"] == "crypto_markout1"
    assert body["source"] == str(workers)


# --- the endpoint ----------------------------------------------------------


def client_for(status_dir, monkeypatch):
    """A client over a stack nothing has bootstrapped, its workers writing to
    *status_dir* (UQF_STATUS_DIR, the workers' own variable); None, no stack
    root at all."""
    if status_dir is not None:
        monkeypatch.setenv("UQF_STATUS_DIR", str(status_dir))
    root = None if status_dir is None else _stack_root(status_dir.parent)
    return TestClient(
        create_app(
            gateway=FakeGateway(),
            settings=Settings(stack_root=root),
            fleet=FakeFleet(),
        )
    )


def test_one_unknown_state_does_not_hide_the_other_workers(tmp_path, monkeypatch):
    """#2583: one `paused` file beside a good one was a 500 for the view."""
    write_status_file(tmp_path, "w1", state="completed")
    write_status_file(tmp_path, "w2", state="paused")
    r = client_for(tmp_path, monkeypatch).get("/ops/backfill")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [w["state"] for w in body["workers"]] == ["completed"]
    (bad,) = body["unreadable"]
    assert "w2" in bad["file"] and "unrecognised state 'paused'" in bad["error"]


def test_every_time_ops_backfill_sends_carries_its_zone(tmp_path, monkeypatch):
    """q's `.j.j` writes a timestamp with no zone, and the API passed it on:
    a window shown in the Backfills view could not be pasted into the Backfill
    form, which refuses a zone-less bound, and a reader could not tell it was
    UTC (#2104). Every instant goes with its `Z`, as /coverage's do."""
    write_status_file(tmp_path, "crypto_markout1", state="running")
    write_status_file(tmp_path, "crypto_markout2", cursor="")
    rows = client_for(tmp_path, monkeypatch).get("/ops/backfill").json()["workers"]
    by_id = {w["instance_id"]: w for w in rows}
    first = by_id["crypto_markout1"]
    assert (first["range_from"], first["range_to"], first["cursor"], first["updated_at"]) == (
        "2026-09-13T00:00:00.000000000Z",
        "2026-09-14T00:00:00.000000000Z",
        "2026-09-14T00:00:00.000000000Z",
        "2026-09-15T18:41:14.475818000Z",
    )
    assert by_id["crypto_markout2"]["cursor"] is None, "q's null timestamp is no cursor"


def test_a_status_file_whose_window_is_not_a_time_is_unreadable(tmp_path):
    write_status_file(tmp_path, "bad", range_from="soon")
    statuses, unreadable = status.read_dir(tmp_path)
    assert statuses == [] and "soon" in unreadable[0]["error"]


def test_endpoint_returns_workers_and_a_poll_cadence(tmp_path, monkeypatch):
    write_status_file(tmp_path, "crypto_markout1")
    body = client_for(tmp_path, monkeypatch).get("/ops/backfill").json()
    assert body["summary"]["workers"] == 1
    assert body["workers"][0]["instance_id"] == "crypto_markout1"
    assert body["poll_seconds"] > 0
    assert body["source"] == str(tmp_path)


def test_owed_reactions_reach_the_api_with_the_failure_they_explain(tmp_path, monkeypatch):
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
    row = client_for(tmp_path, monkeypatch).get("/ops/backfill").json()["workers"][0]
    assert row["reactions_owed"] == 2
    assert "rebuild_positions" in row["error"]


def test_endpoint_reports_unconfigured_as_422(tmp_path, monkeypatch):
    resp = client_for(None, monkeypatch).get("/ops/backfill")
    assert resp.status_code == 422
    assert "UQF_FRONTEND_STACK_ROOT" in resp.json()["detail"]


def test_endpoint_carries_no_airflow_owned_fields(tmp_path, monkeypatch):
    """These files carry q's facts only. Retry counts, task ordering,
    timeouts and concurrency are Airflow's, and inferring them here is the
    cross-layer inference the framework forbids.
    """
    write_status_file(tmp_path, "crypto_markout1")
    row = client_for(tmp_path, monkeypatch).get("/ops/backfill").json()["workers"][0]
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
    write_status_file(tmp_path, "live1", state="running", updated_at=written_now())
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
