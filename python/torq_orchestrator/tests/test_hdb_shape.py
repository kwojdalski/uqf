"""Is the HDB rectangular (#348).

A partitioned kdb+ database needs every table in every partition, and one
missing directory fails the whole query rather than returning an empty
result — so the error names whichever table sorts first, not the partition
that is actually short. These hold the check that names the cause.
"""

from __future__ import annotations

from pathlib import Path

from torq_orchestrator import hdb_shape

SCHEMA = """\
quote:([]time:`timestamp$(); sym:`symbol$(); bid:`float$())
trades:([]time:`timestamp$(); sym:`symbol$(); px:`float$())
arbitrage:([]time:`timestamp$(); sym:`symbol$(); active:`boolean$())
"""


def _hdb(root: Path, layout: dict[str, list[str]]) -> Path:
    for part, tables in layout.items():
        for table in tables:
            (root / part / table).mkdir(parents=True)
    (root / "sym").write_bytes(b"")
    return root


def test_declared_tables_come_from_the_generated_schema():
    """The generated database.q, because it is what the tickerplant loads
    and therefore what every process expects the HDB to hold."""
    assert hdb_shape.declared_tables(SCHEMA) == {"quote", "trades", "arbitrage"}


def test_partitions_are_dated_directories_and_nothing_else(tmp_path):
    """`sym` is the enumeration domain every symbol column points into, not
    a partition; counting it would report it short of every table."""
    root = _hdb(tmp_path, {"2015.01.07": ["quote"], "2026.09.20": ["quote"]})
    (root / "par.txt").write_text("/some/where\n")
    assert hdb_shape.partitions(root) == ["2015.01.07", "2026.09.20"]


def test_partitions_are_oldest_first(tmp_path):
    """The report reads as a history: the oldest partition is short by the
    most, and that ordering is most of the diagnosis."""
    root = _hdb(tmp_path, {"2026.09.20": ["quote"], "2015.01.07": ["quote"]})
    assert hdb_shape.partitions(root) == ["2015.01.07", "2026.09.20"]


def test_a_rectangular_database_has_no_gaps(tmp_path):
    root = _hdb(
        tmp_path,
        {
            "2015.01.07": ["quote", "trades", "arbitrage"],
            "2026.09.20": ["quote", "trades", "arbitrage"],
        },
    )
    assert hdb_shape.gaps(root, hdb_shape.declared_tables(SCHEMA)) == {}


def test_the_partition_that_is_short_is_named_with_what_it_lacks(tmp_path):
    """The whole point: the kdb+ error names a table, this names the
    partition. "2015.01.07 is missing 2 tables" explains the failure;
    "arbitrage is missing" does not."""
    root = _hdb(
        tmp_path,
        {"2015.01.07": ["quote"], "2026.09.20": ["quote", "trades", "arbitrage"]},
    )
    short = hdb_shape.gaps(root, hdb_shape.declared_tables(SCHEMA))
    assert short == {"2015.01.07": {"trades", "arbitrage"}}


def test_a_table_nobody_declares_any_more_is_not_reported(tmp_path):
    """An old table someone stopped publishing is history. Deleting history
    is not this check's business, and reporting it would train the reader
    to ignore the output."""
    root = _hdb(
        tmp_path,
        {"2015.01.07": ["quote", "trades", "arbitrage", "retired_table"]},
    )
    assert hdb_shape.gaps(root, hdb_shape.declared_tables(SCHEMA)) == {}


def test_a_missing_hdb_is_not_a_gap(tmp_path):
    """A stack that has never run has no HDB, which is not a fault."""
    assert hdb_shape.partitions(tmp_path / "nothing") == []
    assert hdb_shape.gaps(tmp_path / "nothing", {"quote"}) == {}


def test_the_report_names_the_partition_and_counts_the_gap():
    report = hdb_shape.describe({"2015.01.07": {"a", "b"}})
    assert "2015.01.07" in report
    assert "2 missing" in report


def test_a_long_gap_is_summarised_rather_than_dumped():
    """Twenty-two missing tables on each of seven partitions is a wall of
    text nobody reads; the count is the number that matters."""
    report = hdb_shape.describe({"2015.01.07": {f"t{i}" for i in range(22)}})
    assert "+16 more" in report
    assert "22 missing" in report


def test_a_rectangular_report_says_so_plainly():
    assert hdb_shape.describe({}) == "every partition holds every declared table"


def test_the_smoke_lane_reports_an_unrectangular_hdb_as_its_own_kind():
    """Not an empty table and not a process error: a partition short of a
    table fails every cross-table query at once, and calling it one of the
    other two would point the reader at the wrong thing."""
    from torq_orchestrator import stack_smoke

    finding = stack_smoke.SmokeFinding(
        stack_smoke.FindingKind.HDB_NOT_RECTANGULAR,
        "1 partition(s)",
        "2015.01.07: 2 missing",
    )
    assert "hdb-not-rectangular" in str(finding)


# --- columns: the same fault one level down ------------------------------

COLUMN_SCHEMA = """\
book:([]time:`timestamp$(); sym:`g#`symbol$(); px:`float$(); venue:`symbol$())
quotes:([]time:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:())
"""


def _splay(table_dir: Path, columns: list[str], nested: tuple[str, ...] = ()) -> Path:
    """A splayed table on disk: one file per column, plus `.d`.

    `nested` names the columns to write the way kdb+ writes a list of
    lists — two files, `c` and `c#` — because that shape is the reason
    `table_columns` cannot be a plain directory listing.
    """
    table_dir.mkdir(parents=True)
    for column in columns:
        (table_dir / column).write_bytes(b"")
        if column in nested:
            (table_dir / f"{column}#").write_bytes(b"")
    (table_dir / ".d").write_bytes(b"")
    return table_dir


def test_declared_columns_are_read_in_order_with_attributes_tolerated():
    """`` sym:`g#`symbol$() `` declares a column named sym; the `g#` sits on
    the value, and a reader that stopped at the backtick would invent one."""
    declared = hdb_shape.declared_columns(COLUMN_SCHEMA)
    assert declared["book"] == ["time", "sym", "px", "venue"]
    assert declared["quotes"] == ["time", "sym", "bid_prices", "bid_sizes"]


def test_a_nested_column_is_one_column_not_two(tmp_path):
    """kdb+ writes `bid_prices` and `bid_prices#` for a list of lists. No
    schema declares the second, so a bare listing would report a column
    missing that is present and demand one that cannot exist."""
    table = _splay(tmp_path / "book", ["time", "bid_prices"], nested=("bid_prices",))
    assert hdb_shape.table_columns(table) == {"time", "bid_prices"}


def test_the_dot_d_file_is_not_a_column(tmp_path):
    table = _splay(tmp_path / "book", ["time", "px"])
    assert ".d" not in hdb_shape.table_columns(table)


def test_a_partition_short_of_a_column_is_named_with_the_column(tmp_path):
    """The gap this whole change exists for: every table present, and the
    query still fails on `./2026.01.01/book/venue`."""
    _splay(tmp_path / "2026.01.01" / "book", ["time", "sym", "px"])
    _splay(tmp_path / "2026.01.02" / "book", ["time", "sym", "px", "venue"])
    thin = hdb_shape.column_gaps(tmp_path, {"book": ["time", "sym", "px", "venue"]})
    assert thin == {"2026.01.01": {"book": {"venue"}}}


def test_a_table_the_partition_lacks_entirely_is_not_a_column_gap(tmp_path):
    """That is `gaps`' finding. Reporting it here as well would make a fresh
    checkout print every fault twice, and every column of every table."""
    _splay(tmp_path / "2026.01.01" / "quote", ["time", "sym"])
    thin = hdb_shape.column_gaps(tmp_path, {"quote": ["time", "sym"], "book": ["time", "venue"]})
    assert thin == {}


def test_a_column_nobody_declares_any_more_is_not_reported(tmp_path):
    """Same rule as an undeclared table: it is history, and deleting history
    is not this check's business."""
    _splay(tmp_path / "2026.01.01" / "book", ["time", "sym", "old_venue"])
    thin = hdb_shape.column_gaps(tmp_path, {"book": ["time", "sym"]})
    assert thin == {}


def test_the_column_report_names_partition_and_table_together(tmp_path):
    """That pair is what the fix acts on and what the kdb+ error names."""
    report = hdb_shape.describe_columns({"2026.01.01": {"book": {"venue", "fee"}}})
    assert "2026.01.01/book" in report
    assert "fee, venue" in report


def test_a_complete_database_says_so_plainly():
    assert hdb_shape.describe_columns({}) == "every table holds every declared column"
