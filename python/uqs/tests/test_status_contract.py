"""`uqs job backfill --wait` reads the worker status file by q's rules (#1362).

The wait is one of four readers of the file `.qetl.status.write_status`
writes, and the one that went unchecked: it built the file name inline and
hard-coded `idle`/`completed` as success and `failed` as failure, so a state q
added would have read as `abandoned`, exit 1, for a run that succeeded.

It now takes the states and the name from uqs.generated.q_facts, which the
q-facts hook regenerates from the loaded tree under KDB-X. These tests are the
net where no KDB-X runs (hosted CI): they read q's SOURCE, so a state added
in status.q fails here until q_facts is regenerated - and, once it is, the
wait's verdict for every terminal state is q's own.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from uqs.generated import q_facts
from uqs.paths import default_paths
from uqs.stack import backfill_wait

CORE = Path(__file__).resolve().parents[3] / "src" / "etl" / "core"
STATUS_Q = CORE / "status.q"
PROVIDER_FACTS = (
    Path(__file__).resolve().parents[2]
    / "uqf_airflow_provider"
    / "src"
    / "uqf_airflow_provider"
    / "q_facts.py"
)

FROM = datetime(2026, 9, 13, tzinfo=UTC)
TO = datetime(2026, 9, 15, tzinfo=UTC)
LAUNCH = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def q_status_states() -> set[str]:
    line = next(
        ln
        for ln in STATUS_Q.read_text().splitlines()
        if re.match(r"(?:\.[A-Za-z_][\w.]*\.)?status_states:", ln)
    )
    return set(re.findall(r"`(\w+)", line))


def q_terminal_states() -> set[str]:
    """States `legal_transitions` lets go only to `starting` or `failed`."""
    src = STATUS_Q.read_text()
    block = src[src.index("legal_transitions:") :]
    rows = re.findall(r"\(`(\w+);\s*((?:`\w+)+)\)", block[: block.index("))") + 2])
    assert rows, "status.q no longer spells legal_transitions the way this test reads it"
    return {s for s, nexts in rows if set(re.findall(r"`(\w+)", nexts)) <= {"starting", "failed"}}


def q_exit_successes() -> set[str]:
    """The states `.qetl.job.bounded.exit_code` exits 0 for."""
    src = (CORE / "bounded_worker.q").read_text()
    line = next(
        ln for ln in src.splitlines() if re.match(r"(?:\.[A-Za-z_][\w.]*\.)?exit_code:", ln)
    )
    m = re.search(r"state in ((?:`\w+)+);\s*0i", line)
    assert m, "bounded_worker.q's exit_code is no longer spelled the way this test reads it"
    return set(re.findall(r"`(\w+)", m.group(1)))


def test_the_status_states_are_qs():
    assert set(q_facts.STATUS_STATES) == q_status_states()


def test_the_terminal_states_are_qs():
    """A terminal state the wait did not know would never end it."""
    assert set(q_facts.STATUS_TERMINAL_STATES) == q_terminal_states()


def test_the_success_states_are_the_ones_qs_worker_exits_0_for():
    """`--wait` exits with the worker's verdict, so must agree with its exit code."""
    assert set(q_facts.STATUS_SUCCESS_STATES) == q_exit_successes() & q_status_states()


def test_the_status_file_name_is_qs():
    src = STATUS_Q.read_text()
    prefix = re.search(r'^(?:\.[A-Za-z_][\w.]*\.)?file_prefix:"([^"]+)"', src, re.M)
    suffix = re.search(r'^(?:\.[A-Za-z_][\w.]*\.)?file_suffix:"([^"]+)"', src, re.M)
    assert prefix and suffix, "status.q no longer names the file the way this test reads it"
    assert (q_facts.STATUS_FILE_PREFIX, q_facts.STATUS_FILE_SUFFIX) == (
        prefix.group(1),
        suffix.group(1),
    )


def test_the_airflow_providers_copy_is_the_same_facts():
    """The provider cannot import uqs, so the generator writes it a copy."""
    ns: dict = {}
    exec(PROVIDER_FACTS.read_text(), ns)  # noqa: S102 - a generated module of literals
    for name in dir(q_facts):
        if name.startswith("STATUS_"):
            assert ns[name] == getattr(q_facts, name), name


@pytest.mark.parametrize("state", sorted(q_status_states()))
def test_the_wait_ends_on_every_terminal_state_with_qs_verdict(tmp_path, monkeypatch, state):
    """Each state q can record: a terminal one ends the wait, exit 0 exactly
    when q's worker exits 0; any other keeps it waiting."""
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    name = f"{q_facts.STATUS_FILE_PREFIX}w1{q_facts.STATUS_FILE_SUFFIX}"
    (tmp_path / name).write_text(
        json.dumps(
            {
                "state": state,
                "source_version": "v1",
                "range_from": "2026-09-13T00:00:00.000000000",
                "range_to": "2026-09-15T00:00:00.000000000",
                "updated_at": "2026-10-04T12:00:05.000000000",
                "pid": 0,
                "host": "elsewhere.example",
                "error": "boom",
            }
        )
    )

    class StillWaiting(Exception):
        pass

    def sleep(_):
        raise StillWaiting

    try:
        outcome = backfill_wait.wait_for_outcome(
            default_paths(), "w1", "v1", FROM, TO, LAUNCH, sleep=sleep, now=lambda: LAUNCH
        )
    except StillWaiting:
        outcome = None
    if state not in q_terminal_states():
        assert outcome is None
    else:
        assert outcome is not None
        assert outcome[:2] == (state, 0 if state in q_exit_successes() else 1)


def test_a_file_on_this_host_whose_process_is_gone_is_abandoned(tmp_path, monkeypatch):
    """The shared liveness rule, through the wait (host as q's `.z.h` writes it)."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    name = f"{q_facts.STATUS_FILE_PREFIX}w1{q_facts.STATUS_FILE_SUFFIX}"
    (tmp_path / name).write_text(
        json.dumps(
            {
                "state": "running",
                "source_version": "v1",
                "range_from": "2026-09-13T00:00:00.000000000",
                "range_to": "2026-09-15T00:00:00.000000000",
                "updated_at": "2026-10-04T12:00:05.000000000",
                "pid": proc.pid,
                "host": socket.gethostname().upper(),
                "error": "",
            }
        )
    )
    outcome = backfill_wait.wait_for_outcome(
        default_paths(), "w1", "v1", FROM, TO, LAUNCH, sleep=lambda _: None, now=lambda: LAUNCH
    )
    assert outcome[:2] == ("abandoned", 1)
