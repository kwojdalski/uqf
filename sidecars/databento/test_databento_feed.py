"""Tests for the live Databento feed handler.

The handler's only real logic is turning Databento records into columns the
tickerplant will accept, and every way that can go wrong is silent:

  - a column in the wrong ORDER transposes two same-typed fields, so prices
    arrive as sizes and nothing raises;
  - a price read raw is a fixed-point int, 1e9 times too large (#2190);
  - an epoch-ns int sent as a long, or rebased by hand, is thirty years off;
  - a record with no symbol mapping would publish a null sym;
  - a dict instead of a list of columns is refused by the segmented STP;
  - a `time` column makes every message one column too wide.

The records are the real `databento_dbn` classes the live client yields, not
look-alikes: the streamer once passed every test here on a `FakeRecord` with
float prices and a `symbol` field, neither of which Databento sends (#2190).

The databento bundle's own Python tests (#1776): scripts/dev/bundle_suite.py
installs the bundle into a throwaway tree and runs them there, against the
INSTALLED copies in src/etl/connectors/databento/ - the module the CLI
discovered, and its streamer beside it on sys.path.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime
from pathlib import Path

import databento_dbn as dbn
import polars as pl
import pytest
import rename_support  # python/uqs/tests' helper: bundle_suite.py runs this from there
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import external as cli_external
from uqs.external.kafka_streamer import ACCEPT_Q
from uqs.external.lifecycle import process_identity
from uqs.paths import TABLES_FILE, UqsError, UqsPaths, repo_root

#: The installed connector, as discovery imported it - what the CLI runs.
databento_feed = sys.modules[cli_external.FEEDS["databento"].start.__module__]
#: Its streamer, beside it: bundle_suite.py puts the installed folder on sys.path.
streamer = importlib.import_module("databento_streamer")
contract_fields = streamer.contract_fields

runner = CliRunner()

#: AAPL's Databento instrument_id in the extract the source fixture is cut from.
AAPL = 15144
#: 2026-02-25 14:30:00.016039462 UTC, as Databento sends it: ns since 1970.
TS_EVENT = 1772029800016039462


def _fields() -> list[str]:
    """The installed source contract's fields, where the streamer reads them."""
    return contract_fields(str(repo_root()))


def _plant_table() -> str:
    """`databento_mbp10`'s definition in the plant tables the bundle installed."""
    from uqs.model import schemas

    found = schemas.definitions_in((repo_root() / TABLES_FILE).read_text())
    return schemas.definition_from(found, "databento_mbp10", str(TABLES_FILE))


def _level(i: int, *, empty: bool = False) -> dbn.BidAskPair:
    """Level `i`: one cent away from a 271.45 x 271.66 touch, sizes up by 100.
    Prices in Databento's fixed-point units; an empty level is UNDEF_PRICE and
    size 0, as Databento pads a thin book."""
    if empty:
        return dbn.BidAskPair(
            bid_px=dbn.UNDEF_PRICE, ask_px=dbn.UNDEF_PRICE, bid_sz=0, ask_sz=0, bid_ct=0, ask_ct=0
        )
    cent = 10_000_000
    return dbn.BidAskPair(
        bid_px=271_450_000_000 - i * cent,
        ask_px=271_660_000_000 + i * cent,
        bid_sz=500 + 100 * i,
        ask_sz=480 + 100 * i,
        bid_ct=1,
        ask_ct=1,
    )


def _mbp10(*, instrument_id: int = AAPL, depth: int = 10, **kw) -> dbn.MBP10Msg:
    """An add on the bid at 271.20, `depth` populated levels then empty ones."""
    return dbn.MBP10Msg(
        publisher_id=2,
        instrument_id=instrument_id,
        ts_event=kw.get("ts_event", TS_EVENT),
        price=kw.get("price", 271_200_000_000),
        size=200,
        action=kw.get("action", dbn.Action.ADD),
        side=kw.get("side", dbn.Side.BID),
        depth=0,
        ts_recv=TS_EVENT + 60_538,
        flags=0,
        sequence=28151775,
        levels=[_level(i, empty=i >= depth) for i in range(10)],
    )


def _mapping(instrument_id: int = AAPL, symbol: str = "AAPL") -> dbn.SymbolMappingMsg:
    return dbn.SymbolMappingMsg(
        publisher_id=2,
        instrument_id=instrument_id,
        ts_event=TS_EVENT - 1,
        stype_in=dbn.SType.from_str("raw_symbol"),
        stype_in_symbol=symbol,
        stype_out=dbn.SType.from_str("instrument_id"),
        stype_out_symbol=symbol,
        start_ts=0,
        end_ts=0,
    )


class RecordingQ:
    """A stand-in for kola.Q that remembers what it was asked to run."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.answer: object = True

    def sync(self, *args):
        self.calls.append(args)
        return self.answer


def _stream(records, *, client_map=None) -> tuple[RecordingQ, list[str]]:
    """Every record through `publish`, as main's loop sends them."""
    q, log = RecordingQ(), []
    decoder = streamer.RecordDecoder(_fields(), client_map)
    for rec in records:
        streamer.publish(q, "databento_mbp10", decoder, rec, log.append)
    return q, log


def _published_row(q: RecordingQ) -> dict:
    """The one row `q` was sent, by field name."""
    assert len(q.calls) == 1
    fn, table, cols = q.calls[0]
    assert (fn, table) == (ACCEPT_Q, "databento_mbp10")
    return {name: col.to_list()[0] for name, col in zip(_fields(), cols, strict=True)}


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

    table = schemas.column_names(_plant_table())
    expected = ["symbol" if c == "sym" else c for c in table if c != "time"]
    assert _fields() == expected


def test_each_field_is_published_in_the_plant_tables_own_type():
    """`insert` refuses a column of the wrong type outright, so the dtype the
    streamer picks by name is held against the table's literal, column for
    column: a float where a long belongs is a `type` error per record."""
    from uqs.model import schemas

    q_type = {
        streamer.TIMESTAMP: "timestamp",
        streamer.SYMBOL: "symbol",
        streamer.FLOAT: "float",
        streamer.LONG: "long",
    }
    for (column, literal), field in zip(
        [p for p in schemas.column_pairs(_plant_table()) if p[0] != "time"], _fields(), strict=True
    ):
        assert literal.endswith(f"`{q_type[streamer.field_dtype(field)]}$()"), (column, literal)


# --------------------------------------------- a real record, decoded


def test_a_real_record_becomes_one_row_in_real_units():
    """The worked example, by hand: 271_200_000_000 fixed-point is 271.20
    dollars, not 2.712e11; ts_event's ns since 1970 is 14:30:00.016039462 on
    2026-02-25, to the nanosecond; the Side/Action enums are their letters;
    the symbol is the one the mapping message named."""
    q, log = _stream([_mapping(), _mbp10()])
    assert log == []
    row = _published_row(q)
    assert row["price"] == 271.2
    assert isinstance(row["price"], float)
    assert row["ts_event"] == datetime(2026, 2, 25, 14, 30, 0, 16039)
    assert row["symbol"] == "AAPL"
    assert (row["action"], row["side"]) == ("A", "B")
    assert (row["size"], row["sequence"]) == (200, 28151775)
    assert (row["bid_px_00"], row["ask_px_00"]) == (271.45, 271.66)
    assert (row["bid_sz_00"], row["ask_sz_00"]) == (500, 480)
    assert row["bid_px_09"] == pytest.approx(271.36)
    assert row["ask_sz_09"] == 1380


def test_the_timestamp_keeps_its_nanoseconds():
    """A Python datetime stops at microseconds; the column is ns-precise, and
    ts_event is part of the source's row key."""
    q, _ = _stream([_mapping(), _mbp10()])
    ts = q.calls[0][2][0]
    assert ts.dtype == pl.Datetime("ns")
    assert ts.cast(pl.Int64).to_list() == [TS_EVENT]


def test_the_row_is_a_list_of_typed_columns_not_a_dict():
    """The segmented STP's `(enlist ...),x` refuses a dictionary (#1492), and a
    Python list of ints and Nones has no single q type: one polars Series per
    field, in contract order, each one long, no `time`."""
    q, _ = _stream([_mapping(), _mbp10()])
    cols = q.calls[0][2]
    assert isinstance(cols, list)
    assert [c.name for c in cols] == _fields()
    assert [c.dtype for c in cols] == [streamer.field_dtype(f) for f in _fields()]
    assert all(len(c) == 1 for c in cols)
    assert "time" not in [c.name for c in cols]


def test_every_side_and_action_is_its_plant_letter():
    """The letters the ODBC source stores for the same events (the source
    fixture's `A`T`A`C and `B`N`A`A), so live and backfilled rows agree."""
    actions = {"ADD": "A", "CANCEL": "C", "MODIFY": "M", "TRADE": "T", "FILL": "F", "CLEAR": "R"}
    sides = {"BID": "B", "ASK": "A", "NONE": "N"}
    for action, a in actions.items():
        for side, s in sides.items():
            rec = _mbp10(action=getattr(dbn.Action, action), side=getattr(dbn.Side, side))
            q, _ = _stream([_mapping(), rec])
            row = _published_row(q)
            assert (row["action"], row["side"]) == (a, s), (action, side)


def test_a_thin_books_empty_levels_are_nulls_not_zeros():
    """Databento pads a thin book with UNDEF_PRICE and size 0. Zero is a price,
    and a zero-size level reads as a real one: both are null."""
    q, _ = _stream([_mapping(), _mbp10(depth=3)])
    row = _published_row(q)
    assert row["bid_px_02"] == pytest.approx(271.43)
    assert (row["bid_px_03"], row["bid_sz_03"], row["ask_px_09"], row["ask_sz_09"]) == (None,) * 4


def test_an_undefined_trade_price_is_null():
    """A book clear carries UNDEF_PRICE (i64 max): 9.2e9 dollars if read raw."""
    q, _ = _stream([_mapping(), _mbp10(price=dbn.UNDEF_PRICE, action=dbn.Action.CLEAR)])
    assert _published_row(q)["price"] is None


# --------------------------------------------- symbols, and what is refused


def test_a_record_with_no_symbol_mapping_is_refused_with_a_reason():
    """Never published with a null sym: the row would land in the book
    keyed by nothing. The log names the instrument_id."""
    q, log = _stream([_mapping(), _mbp10(instrument_id=99)])
    assert q.calls == []
    assert len(log) == 1 and "instrument_id 99 has no symbol" in log[0]


def test_the_live_clients_own_symbology_map_names_a_record():
    """main passes `Live.symbology_map`, which the client fills from the same
    mapping messages; a mapping the client saw first is enough."""
    q, log = _stream([_mbp10()], client_map={AAPL: "AAPL"})
    assert log == []
    assert _published_row(q)["symbol"] == "AAPL"


def test_records_that_are_not_book_events_publish_nothing():
    """The live client yields mappings and system messages too, each with a
    ts_event; before #2190 every one went out as a row of nulls."""
    q, log = _stream([_mapping(), dbn.SystemMsg(ts_event=TS_EVENT, msg="Heartbeat")])
    assert q.calls == [] and log == []


def test_a_row_the_plant_refuses_is_not_reported_as_published():
    """A bare `.u.upd` answers success for a row the STP sent to its error log
    (#2698); `ACCEPT_Q` answers the error text, and publish must raise on it
    rather than return True."""
    q = RecordingQ()
    decoder = streamer.RecordDecoder(_fields(), {AAPL: "AAPL"})
    assert streamer.publish(q, "databento_mbp10", decoder, _mbp10(), pytest.fail) is True
    q.answer = "type"
    with pytest.raises(UqsError, match="refused the databento_mbp10 row - NOT stored: type"):
        streamer.publish(q, "databento_mbp10", decoder, _mbp10(), pytest.fail)


def test_a_gateway_error_is_logged_not_published():
    q, log = _stream([dbn.ErrorMsg(ts_event=TS_EVENT, err="auth failed")])
    assert q.calls == []
    assert log == ["databento record refused: gateway error: auth failed"]


# --------------------------------------- through a q insert, as the STP does


def test_a_published_row_lands_in_the_plant_table_through_the_stps_insert(
    start_q, q_binary, tmp_path
):
    """End to end on a real q: the plant's `databento_mbp10` and the segmented
    STP's own `updtab` (lib/torq/code/segmentedtickerplant/stplog.q), which
    prepends `time` and inserts. `insert` refuses any column of the wrong
    type, so the row arriving at all proves the types; the values prove the
    units."""
    import kola

    boot = tmp_path / "tp.q"
    boot.write_text(
        _plant_table()
        + "\nupdtab:{(enlist(count first x)#y),x}"
        + "\n.u.upd:{[t;x] t insert updtab[x;.z.p]}\n"
    )
    with start_q(str(boot)) as port:
        conn = kola.Q("127.0.0.1", port, user="", passwd="")
        try:
            for rec in (_mapping(), _mbp10(depth=3)):
                streamer.publish(
                    conn,
                    "databento_mbp10",
                    streamer.RecordDecoder(_fields(), {AAPL: "AAPL"}),
                    rec,
                    pytest.fail,
                )
            # As one symbol: kola hands a q string back as bytes, not text;
            # and \c widened so the rendering is not cut at the console's.
            got = conn.sync(
                '{system"c 25 500"; `$-3!(count databento_mbp10;'
                " exec first ts_event, first sym, first action, first side, first price,"
                " first bid_px_00, first bid_px_03, first bid_sz_03 from databento_mbp10)}",
                None,
            )
        finally:
            conn.disconnect()
    assert got == (
        "(1;`ts_event`sym`action`side`price`bid_px_00`bid_px_03`bid_sz_03!"
        "(2026.02.25D14:30:00.016039462;`AAPL;`A;`B;271.2;271.45;0n;0N))"
    )


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
