"""The ETL-15 boundary, exercised directly: this mapping may only ever look at
`state` and `error` — the q-owned facts — and must never surface an
Airflow-owned concept (retry count, queue position, timeout, concurrency).
"""

from __future__ import annotations

from uqf_airflow_provider.status_reader import WorkerStatus
from uqf_airflow_provider.translate import PokeOutcome, failure_reason, translate

AIRFLOW_OWNED_FIELDS = {"retries", "try_number", "timeout", "concurrency", "queue"}


def make_status(state: str, error: str | None = None) -> WorkerStatus:
    return WorkerStatus(
        worker="markout_backfill",
        instance_id="markout1",
        state=state,
        error=error,
        updated_at="2026-09-15T18:41:14.475818000",
    )


def test_starting_and_running_are_pending():
    assert translate(make_status("starting")) is PokeOutcome.PENDING
    assert translate(make_status("running")) is PokeOutcome.PENDING


def test_idle_and_completed_are_both_success():
    """C-07: "ran, found no work" (idle) is as successful as "ran, did
    work" (completed) — neither may read as pending or failed.
    """
    assert translate(make_status("idle")) is PokeOutcome.SUCCESS
    assert translate(make_status("completed")) is PokeOutcome.SUCCESS


def test_failed_is_failure():
    assert translate(make_status("failed", error="boom")) is PokeOutcome.FAILURE


def test_failure_reason_carries_only_q_owned_fields():
    status = make_status("failed", error="source read failed: connection refused")
    reason = failure_reason(status)
    assert "source read failed: connection refused" in reason
    assert status.worker in reason and status.instance_id in reason
    for field in AIRFLOW_OWNED_FIELDS:
        assert field not in reason


def test_failure_reason_without_an_error_string_still_says_something():
    status = make_status("failed", error=None)
    reason = failure_reason(status)
    assert "no error string was recorded" in reason


def test_worker_status_dataclass_exposes_no_airflow_owned_field():
    """Asserted at the type level, not just the reason string: the dataclass
    itself must not grow a retries/timeout/queue attribute over time, which
    would let some future caller read an Airflow fact off a q file.
    """
    field_names = set(WorkerStatus.__dataclass_fields__)
    assert field_names.isdisjoint(AIRFLOW_OWNED_FIELDS)
