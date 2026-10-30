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

The databento bundle's own Python tests (#1776): scripts/dev/bundle_suite.py
installs the bundle into a throwaway tree and runs them there, against the
INSTALLED copies in src/etl/connectors/databento/ - the module the CLI
discovered, and its streamer beside it on sys.path.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import rename_support  # python/uqs/tests' helper: bundle_suite.py runs this from there
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import external as cli_external
from uqs.external.lifecycle import process_identity
from uqs.paths import TABLES_FILE, UqsError, UqsPaths, repo_root

#: The installed connector, as discovery imported it - what the CLI runs.
databento_feed = sys.modules[cli_external.FEEDS["databento"].start.__module__]
#: Its streamer, beside it: bundle_suite.py puts the installed folder on sys.path.
streamer = importlib.import_module("databento_streamer")
contract_fields, rows_from_records = streamer.contract_fields, streamer.rows_from_records

runner = CliRunner()


def _fields() -> list[str]:
    """The installed source contract's fields, where the streamer reads them."""
    return contract_fields(str(repo_root()))


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
    fields = _fields()
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


def test_the_fields_line_up_with_the_plant_table_column_for_column():
    """The test above checks the fields against numbers written in Python.
    This one checks them against what `.u.upd` writes into: the
    `databento_mbp10` table the bundle put in plant_tables.q. The streamer derives its level
    columns rather than reading them (LEVELS and the four prefixes), so a q
    change to the level count or a level column would otherwise misalign
    every row after it with nothing raising.

    The plant stamps `time`, and the table calls the venue's symbol `sym`
    where the source contract says `symbol`; the rest is position for
    position.
    """
    from uqs.model import schemas

    found = schemas.definitions_in((repo_root() / TABLES_FILE).read_text())
    table = schemas.column_names(
        schemas.definition_from(found, "databento_mbp10", str(TABLES_FILE))
    )
    expected = ["symbol" if c == "sym" else c for c in table if c != "time"]
    assert _fields() == expected


def test_no_time_column_is_produced():
    """The tickerplant stamps its own on receipt and rejects a row that
    carries one - one column too wide is a length error, not a warning."""
    fields = _fields()
    assert "time" not in fields
    assert "time" not in rows_from_records([_record()], fields)


def test_every_column_is_a_list_even_for_one_record():
    """`.u.upd` derives the row count from column length, so a bare atom
    for a single record is the bug that reads as a silent no-op."""
    fields = _fields()
    cols = rows_from_records([_record()], fields)
    assert set(cols) == set(fields)
    assert all(isinstance(v, list) for v in cols.values())
    assert all(len(v) == 1 for v in cols.values())


def test_every_record_contributes_one_entry_per_column():
    fields = _fields()
    cols = rows_from_records([_record(), _record(), _record()], fields)
    assert all(len(v) == 3 for v in cols.values())


# ------------------------------------------------------- the level fold


def test_levels_land_in_their_own_numbered_columns():
    fields = _fields()
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
    fields = _fields()
    cols = rows_from_records([_record(levels=3)], fields)
    assert cols["bid_px_02"] == [102.0]
    assert cols["bid_px_03"] == [None]
    assert cols["ask_sz_09"] == [None]
    assert all(len(v) == 1 for v in cols.values()), "still one entry per column"


def test_a_record_with_no_levels_still_produces_every_column():
    fields = _fields()
    cols = rows_from_records([_record(levels=0)], fields)
    assert len(cols) == len(fields)
    assert cols["bid_px_00"] == [None]
    assert cols["price"] == [271.2], "the scalar fields are unaffected"


# ------------------------------------------------------- the lifecycle


def _paths(tmp_path: Path) -> UqsPaths:
    orch = tmp_path / "python" / "uqs"
    orch.mkdir(parents=True)
    (tmp_path / "output" / "uqs" / "logs").mkdir(parents=True)
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "output" / "uqs",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=orch,
    )


def test_starting_without_an_api_key_refuses_and_names_the_variable(tmp_path, monkeypatch):
    """Refuse before spawning, not after the first call fails - the order
    `.qetl.job.bounded.init` follows for the same reason."""
    monkeypatch.delenv(databento_feed.DATABENTO_API_KEY_ENV, raising=False)
    with pytest.raises(UqsError) as exc:
        databento_feed.start_databento_feed(_paths(tmp_path))
    assert databento_feed.DATABENTO_API_KEY_ENV in str(exc.value)


def test_a_stale_pid_file_does_not_report_running(tmp_path):
    """A pid file outlives a crash. Reporting `running` from its mere
    existence is how a dead feed looks healthy."""
    paths = _paths(tmp_path)
    databento_feed.pid_path(paths).write_text("999999")
    assert databento_feed.is_databento_feed_running(paths) is False


def test_an_unparseable_pid_file_is_not_running(tmp_path):
    paths = _paths(tmp_path)
    databento_feed.pid_path(paths).write_text("not-a-pid")
    assert databento_feed.is_databento_feed_running(paths) is False


def test_the_live_process_reports_running(tmp_path):
    paths = _paths(tmp_path)
    databento_feed.pid_path(paths).write_text(f"{os.getpid()}\n{process_identity(os.getpid())}\n")
    assert databento_feed.is_databento_feed_running(paths) is True


def test_stopping_when_nothing_runs_is_quiet_and_clears_the_pid(tmp_path):
    paths = _paths(tmp_path)
    databento_feed.pid_path(paths).write_text("999999")
    databento_feed.stop_databento_feed(paths)
    assert not databento_feed.pid_path(paths).exists()


def test_status_names_what_folds_the_rows(tmp_path):
    """The handler publishes raw MBP-10; someone reading `status` should
    not have to guess where eq_orderbook comes from."""
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
    runner = Path(str(databento_feed.__file__)).resolve().parent / "databento_streamer.py"
    assert runner.is_file(), f"the streamer databento_feed launches is missing: {runner}"


def test_its_pid_file_is_where_uqs_kept_it(tmp_path):
    """The same file on disk as before the move (#1776): a feed started by an
    older uqs is still found, and stopped, by this one."""
    paths = _paths(tmp_path)
    assert databento_feed.pid_path(paths) == paths.orchestrator_dir / "databento_feed.pid"


def test_the_connector_runs_from_its_installed_copy():
    """Never from the bundle's folder: the q half and this one are one version."""
    installed = repo_root() / "src" / "etl" / "connectors" / "databento" / "databento_feed.py"
    assert Path(str(databento_feed.__file__)).resolve() == installed.resolve()


# --------------------------------------------------- through the CLI (#1739)


def _patch(monkeypatch, name: str, result=None):
    calls: list[dict] = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(databento_feed, name, fake)
    return calls


@pytest.mark.parametrize("option", ["--databento-dataset", "--dataset"])
def test_databento_start_takes_its_dataset(monkeypatch, option):
    """#1739: --dataset is the dataset a bounded worker fills (`job new`), so
    the Databento one is --databento-dataset; --dataset is its old spelling."""
    renames = rename_support.said(monkeypatch)
    calls = _patch(monkeypatch, "start_databento_feed", result=7)
    result = runner.invoke(cli.app, ["connector", "start", "databento", option, "GLBX.MDP3"])
    assert result.exit_code == 0, result.output
    assert calls[0]["dataset"] == "GLBX.MDP3"
    old = option == "--dataset"
    assert renames == ([rename_support.renamed("--dataset", "--databento-dataset")] if old else [])


def test_databento_starts_with_its_own_defaults(monkeypatch):
    calls = _patch(monkeypatch, "start_databento_feed", result=8)
    result = runner.invoke(cli.app, ["connector", "start", "databento", "--symbols", "A, B"])
    assert result.exit_code == 0, result.output
    assert calls[0]["symbols"] == ("A", "B")
    assert calls[0]["dataset"] == databento_feed.DEFAULT_DATASET
    assert "databento feed started (pid 8)" in result.stdout


def test_the_dry_run_masks_the_api_key(monkeypatch):
    monkeypatch.setattr(databento_feed, "databento_feed_status", lambda p: {"running": "False"})
    argv = ["connector", "start", "databento", "-n", "--api-key", "K", "--symbols", "A,B"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert "api_key=***, symbols=A,B" in result.stdout and "K," not in result.stdout
