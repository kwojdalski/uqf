"""The validation branches left untested in the frontend's trust boundary.

queries.coerce is what stands between a browser's JSON and a q expression, so
each of its refusals is a statement about what a caller cannot make q do. Most
were tested; the ones below were not, and several guard a Python quirk rather
than a caller mistake - `True` is an `int`, so a boolean slipping into a long
or a timespan column would otherwise be accepted as 1.

control.py's remaining branches are the refusals a misconfigured deployment
hits first, and health.py's are the shapes a probe reply can take.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from uqf_frontend import control, health, queries
from uqf_frontend.catalog import TABLES, QType
from uqf_frontend.config import Settings
from uqf_frontend.errors import ValidationFailed

# ------------------------------------------------------------ queries.coerce


@pytest.mark.parametrize("bad", [True, 1.5, "7"])
def test_a_long_column_takes_only_a_true_integer(bad):
    """`True` is an int in Python. Without the explicit bool check it would
    be sent to q as 1."""
    with pytest.raises(ValidationFailed, match="is a long"):
        queries.coerce(bad, QType.LONG, "qty", as_list=False)


def test_a_long_column_accepts_an_integer():
    assert queries.coerce(7, QType.LONG, "qty", as_list=False) == 7


@pytest.mark.parametrize("bad", [1, "true", None])
def test_a_boolean_column_takes_only_a_boolean(bad):
    with pytest.raises(ValidationFailed, match="is a boolean"):
        queries.coerce(bad, QType.BOOLEAN, "flag", as_list=False)


@pytest.mark.parametrize("bad", [True, "5s"])
def test_a_timespan_takes_a_number_of_nanoseconds_only(bad):
    with pytest.raises(ValidationFailed, match="is a timespan"):
        queries.coerce(bad, QType.TIMESPAN, "horizon", as_list=False)


def test_a_timespan_is_read_as_nanoseconds():
    # 1.5 seconds = 1_500_000_000 ns. Reading it as microseconds would be a
    # thousand times too long and still a plausible-looking duration.
    assert queries.coerce(1_500_000_000, QType.TIMESPAN, "h", as_list=False) == dt.timedelta(
        seconds=1.5
    )


def test_an_aware_datetime_is_normalised_to_utc():
    cet = dt.timezone(dt.timedelta(hours=2))
    out = queries.coerce(
        dt.datetime(2026, 9, 15, 12, 0, tzinfo=cet), QType.TIMESTAMP, "t", as_list=False
    )
    assert out == dt.datetime(2026, 9, 15, 10, 0, tzinfo=dt.UTC)


def test_a_naive_datetime_object_is_refused_like_a_naive_string():
    with pytest.raises(ValidationFailed, match="explicit timezone"):
        queries.coerce(dt.datetime(2026, 9, 15, 12, 0), QType.TIMESTAMP, "t", as_list=False)


def test_a_timestamp_must_be_a_string_or_datetime():
    with pytest.raises(ValidationFailed, match="expected an ISO-8601 string"):
        queries.coerce(1726394400, QType.TIMESTAMP, "t", as_list=False)


# -------------------------------------------------------- queries.build_filters


def _vector_column_table():
    for tbl in TABLES.values():
        vectors = set(tbl.columns) - set(tbl.filterable)
        if vectors:
            return tbl, sorted(vectors)[0]
    pytest.skip("no catalog table has a vector column")


def test_a_vector_column_cannot_be_filtered_on():
    """A column holding a list per row has no scalar comparison. Refused by
    name here, before any IPC, rather than left to throw inside q."""
    tbl, column = _vector_column_table()
    with pytest.raises(ValidationFailed, match="vector per row"):
        queries.build_filters(tbl, [(column, "eq", 1)])


def test_an_unknown_operator_is_refused_listing_the_supported_ones():
    tbl = TABLES["quotes"]
    column = sorted(tbl.filterable)[0]
    with pytest.raises(ValidationFailed, match="supported:"):
        queries.build_filters(tbl, [(column, "like", "x")])


# --------------------------------------------------------------- control


def test_a_stack_root_that_is_not_the_repository_is_refused(tmp_path):
    """Acting on the wrong tree is worse than refusing. A root without
    lib/torq/torq.sh is not this repository."""
    settings = Settings(enable_writes=True, stack_root=tmp_path)
    with pytest.raises(ValidationFailed, match="does not look like the repository"):
        control._paths(settings)


def test_a_valid_stack_root_builds_paths_under_it(tmp_path):
    (tmp_path / "lib" / "torq").mkdir(parents=True)
    (tmp_path / "lib" / "torq" / "torq.sh").write_text("#!/bin/sh\n")
    paths = control._paths(Settings(enable_writes=True, stack_root=tmp_path))
    assert paths.torqhome == tmp_path / "lib" / "torq"
    assert paths.torqdata == tmp_path / "scripts" / "output" / "uqf-stack"


def test_an_orchestrator_refusal_becomes_a_validation_error(monkeypatch):
    """A 422 naming the problem, not a 500 with a traceback."""
    from torq_orchestrator import core

    def refuse(*a, **k):
        raise core.UqfStackError("no such process: typo1")

    monkeypatch.setattr(core, "start", refuse)
    monkeypatch.setattr(core, "default_paths", lambda: "PATHS")
    with pytest.raises(ValidationFailed, match="typo1"):
        control.lifecycle(Settings(enable_writes=True), "start", "typo1")


def test_a_worker_config_key_is_required():
    with pytest.raises(ValidationFailed, match="key is required"):
        control.set_worker_config(object(), Settings(enable_writes=True), "", "x")


@pytest.mark.parametrize(
    ("field", "message"),
    [("worker", "worker name is required"), ("source_version", "ETL-09")],
)
def test_a_backfill_needs_a_worker_and_a_version(field, message):
    args = {
        "worker": "demo_deals_backfill",
        "source_version": "v1",
        "range_from": "2026-09-11T00:00:00Z",
        "range_to": "2026-09-12T00:00:00Z",
    }
    args[field] = ""
    with pytest.raises(ValidationFailed, match=message):
        control.start_backfill(Settings(enable_writes=True), **args)


def _backfill_args() -> dict[str, str]:
    return {
        "worker": "demo_deals_backfill",
        "source_version": "v1",
        "range_from": "2026-09-11T00:00:00Z",
        "range_to": "2026-09-12T00:00:00Z",
    }


def test_a_bootstrap_failure_before_a_backfill_is_reported(monkeypatch):
    from torq_orchestrator import core, runtime

    def refuse(paths, base_port):
        raise core.UqfStackError("lib/torq not vendored")

    monkeypatch.setattr(core, "default_paths", lambda: SimpleNamespace())
    monkeypatch.setattr(runtime, "bootstrap", refuse)
    with pytest.raises(ValidationFailed, match="not vendored"):
        control.start_backfill(Settings(enable_writes=True), **_backfill_args())


def test_a_missing_backfill_script_is_refused_before_spawning(monkeypatch, tmp_path):
    """Popen on a missing script would start q with nothing to run and
    report a pid - a backfill that looks launched and never ran."""
    from torq_orchestrator import core, runtime

    fake = SimpleNamespace(repo_root=tmp_path, scripts_dir=tmp_path / "scripts")
    monkeypatch.setattr(core, "default_paths", lambda: fake)
    monkeypatch.setattr(runtime, "bootstrap", lambda paths, base_port: {})
    with pytest.raises(ValidationFailed, match="torq_backfill.q not found"):
        control.start_backfill(Settings(enable_writes=True), **_backfill_args())


@pytest.mark.parametrize(
    ("raw", "plain"),
    [(b"txt", "txt"), ({b"k": [b"v"]}, {"k": ["v"]}), ((b"a", 1), ["a", 1]), (3, 3)],
)
def test_q_replies_are_made_json_safe(raw, plain):
    assert control._plain(raw) == plain


# ----------------------------------------------------------------- health


class _Frame:
    def __init__(self, rows):
        self._rows = rows

    def to_dicts(self):
        return self._rows


@pytest.mark.parametrize(
    ("value", "row"),
    [
        (_Frame([{"pid": 1}]), {"pid": 1}),
        (_Frame([]), {}),
        ([{"pid": 2}], {"pid": 2}),
        ([], {}),
        ("not a table", {}),
    ],
)
def test_the_first_row_is_taken_from_any_reply_shape(value, row):
    assert health._first_row(value) == row


@pytest.mark.parametrize(
    ("value", "out"),
    [(True, 1), (7, 7), (7.9, 7), ("42", 42), ("n/a", None), (None, None), (Path("x"), None)],
)
def test_int_narrows_without_guessing(value, out):
    """A value that cannot be an int is None - not an exception from a
    probe reply's unexpected shape, and not a wrong number."""
    assert health._int(value) == out
