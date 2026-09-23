"""Tests for the live Databento feed handler.

The handler's only real logic is turning Databento records into columns the
tickerplant will accept, and every way that can go wrong is silent:

  - a column in the wrong ORDER transposes two same-typed fields, so prices
    arrive as sizes and nothing raises;
  - a bare atom instead of a list makes `.u.upd` derive the wrong row count;
  - a `time` column makes every message one column too wide;
  - a record with fewer than ten levels, which is an ordinary thin book,
    produces short rows unless the gaps are padded.

None of those needs an API key, a network or a tickerplant to test, which
is exactly why `rows_from_records` was separated from the socket. The
subscription loop itself is not tested here: it is four lines of plumbing
around a third-party client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from torq_orchestrator.external import databento_feed
from torq_orchestrator.external.databento_streamer import contract_fields, rows_from_records
from torq_orchestrator.paths import UqfStackError, UqfStackPaths

REPO = Path(__file__).resolve().parents[3]


@dataclass
class FakeLevel:
    bid_px: float
    bid_sz: int
    ask_px: float
    ask_sz: int


@dataclass
class FakeRecord:
    ts_event: int
    symbol: str
    action: str
    side: str
    price: float
    size: int
    sequence: int
    levels: list[FakeLevel]


def _record(levels: int = 10, *, symbol: str = "AAPL", price: float = 271.2) -> FakeRecord:
    """One Databento-shaped record with `levels` levels populated.

    Explicit keywords rather than **kwargs: a dict splatted into a
    dataclass widens every field to the union of the dict's value types,
    which the type checker cannot narrow and rightly refuses.
    """
    return FakeRecord(
        ts_event=1,
        symbol=symbol,
        action="A",
        side="B",
        price=price,
        size=200,
        sequence=7,
        levels=[FakeLevel(100.0 + i, 10 + i, 200.0 + i, 20 + i) for i in range(levels)],
    )


# ------------------------------------------------- the column contract


def test_the_field_order_comes_from_the_q_declaration():
    """Not a list in Python. `.u.upd` positions by index, so a field added
    to the source contract and forgotten here would transpose two columns
    of the same type without raising anything.
    """
    fields = contract_fields(str(REPO))
    assert fields[:7] == [
        "ts_event",
        "symbol",
        "action",
        "side",
        "price",
        "size",
        "sequence",
    ]
    assert len(fields) == 7 + 40, "seven scalars plus ten levels of four"
    assert fields[7:11] == ["bid_px_00", "bid_sz_00", "ask_px_00", "ask_sz_00"]
    assert fields[-1] == "ask_sz_09"


def test_no_time_column_is_produced():
    """The tickerplant stamps its own on receipt and rejects a row that
    carries one - one column too wide is a length error, not a warning."""
    fields = contract_fields(str(REPO))
    assert "time" not in fields
    assert "time" not in rows_from_records([_record()], fields)


def test_every_column_is_a_list_even_for_one_record():
    """`.u.upd` derives the row count from column length, so a bare atom
    for a single record is the bug that reads as a silent no-op."""
    fields = contract_fields(str(REPO))
    cols = rows_from_records([_record()], fields)
    assert set(cols) == set(fields)
    assert all(isinstance(v, list) for v in cols.values())
    assert all(len(v) == 1 for v in cols.values())


def test_every_record_contributes_one_entry_per_column():
    fields = contract_fields(str(REPO))
    cols = rows_from_records([_record(), _record(), _record()], fields)
    assert all(len(v) == 3 for v in cols.values())


# ------------------------------------------------------- the level fold


def test_levels_land_in_their_own_numbered_columns():
    fields = contract_fields(str(REPO))
    cols = rows_from_records([_record()], fields)
    assert cols["bid_px_00"] == [100.0]
    assert cols["bid_px_09"] == [109.0]
    assert cols["ask_sz_00"] == [20]
    assert cols["ask_sz_09"] == [29]


def test_a_thin_book_is_padded_with_nulls_not_zeros():
    """A record with three levels is ordinary - an opening auction, an
    illiquid name. It still has to produce forty columns, and the empty
    ones must be null: zero is a price, and a zero bid would read as a
    real quote to everything downstream.
    """
    fields = contract_fields(str(REPO))
    cols = rows_from_records([_record(levels=3)], fields)
    assert cols["bid_px_02"] == [102.0]
    assert cols["bid_px_03"] == [None]
    assert cols["ask_sz_09"] == [None]
    assert all(len(v) == 1 for v in cols.values()), "still one entry per column"


def test_a_record_with_no_levels_still_produces_every_column():
    fields = contract_fields(str(REPO))
    cols = rows_from_records([_record(levels=0)], fields)
    assert len(cols) == len(fields)
    assert cols["bid_px_00"] == [None]
    assert cols["price"] == [271.2], "the scalar fields are unaffected"


# ------------------------------------------------------- the lifecycle


def _paths(tmp_path: Path) -> UqfStackPaths:
    orch = tmp_path / "python" / "torq_orchestrator"
    orch.mkdir(parents=True)
    (tmp_path / "scripts" / "output" / "uqf-stack" / "logs").mkdir(parents=True)
    return UqfStackPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "scripts" / "output" / "uqf-stack",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=orch,
    )


def test_starting_without_an_api_key_refuses_and_names_the_variable(tmp_path, monkeypatch):
    """Refuse before spawning, not after the first call fails - the order
    `.qbw.init` follows for the same reason."""
    monkeypatch.delenv(databento_feed.DATABENTO_API_KEY_ENV, raising=False)
    with pytest.raises(UqfStackError) as exc:
        databento_feed.start_databento_feed(_paths(tmp_path))
    assert databento_feed.DATABENTO_API_KEY_ENV in str(exc.value)


def test_a_stale_pid_file_does_not_report_running(tmp_path):
    """A pid file outlives a crash. Reporting `running` from its mere
    existence is how a dead feed looks healthy."""
    paths = _paths(tmp_path)
    paths.databento_feed_pid_path.write_text("999999")
    assert databento_feed.is_databento_feed_running(paths) is False


def test_an_unparseable_pid_file_is_not_running(tmp_path):
    paths = _paths(tmp_path)
    paths.databento_feed_pid_path.write_text("not-a-pid")
    assert databento_feed.is_databento_feed_running(paths) is False


def test_the_live_process_reports_running(tmp_path):
    paths = _paths(tmp_path)
    paths.databento_feed_pid_path.write_text(str(os.getpid()))
    assert databento_feed.is_databento_feed_running(paths) is True


def test_stopping_when_nothing_runs_is_quiet_and_clears_the_pid(tmp_path):
    paths = _paths(tmp_path)
    paths.databento_feed_pid_path.write_text("999999")
    databento_feed.stop_databento_feed(paths)
    assert not paths.databento_feed_pid_path.exists()


def test_status_names_what_folds_the_rows(tmp_path):
    """The handler publishes raw MBP-10; someone reading `status` should
    not have to guess where databento_book comes from."""
    status = databento_feed.databento_feed_status(_paths(tmp_path))
    assert status["publishes"] == "databento_mbp10"
    assert "databento1" in status["folded by"]


def test_the_streamer_script_it_launches_exists():
    """`start_databento_feed` builds the streamer's path from `__file__`, and
    nothing else in this suite executes that line - it needs an API key and a
    live tickerplant. So the one thing that can rot about it is unguarded:
    when the package was foldered, the path briefly became
    `external/external/databento_streamer.py` and every test still passed.

    The failure it would have produced is the expensive kind: `uv run python
    <missing path>` exits non-zero with the feed's PID file already written,
    so the orchestrator reports a running feed that is not running."""
    runner = Path(databento_feed.__file__).resolve().parent / "databento_streamer.py"
    assert runner.is_file(), f"the streamer databento_feed launches is missing: {runner}"
