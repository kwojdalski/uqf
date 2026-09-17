"""Tests for building the catalog from CSV.

The catalog is the security boundary: it decides which tables and columns a
caller may name. So the failure that matters is not "it raised" but "it
built something smaller than it should have and served it as the whitelist"
— a table whose columns went missing accepts no filters at all, which reads
to a caller like their query was wrong rather than like the data file was.

Every test here builds a catalog from files it writes itself, rather than
mutating the real one.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from types import ModuleType

import pytest

from uqf_frontend.catalog import CATALOG_DIR, TABLES, QType


def _write(dir_: Path, tables: list[dict], columns: list[dict]) -> None:
    with (dir_ / "tables.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["table", "description"], lineterminator="\n")
        w.writeheader()
        w.writerows(tables)
    with (dir_ / "columns.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["table", "column", "type"], lineterminator="\n")
        w.writeheader()
        w.writerows(columns)


def _load_from(dir_: Path) -> ModuleType:
    """Build a fresh catalog from *dir_*, without touching the real one.

    The source is patched and exec'd rather than imported, because TABLES is
    built at module scope — which is the property under test: a broken
    catalog must fail at STARTUP, not on the first query that needs it.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "uqf_frontend" / "catalog.py"
    source = src.read_text().replace(
        'CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"',
        f"CATALOG_DIR = Path({str(dir_)!r})",
    )
    # A REAL module registered in sys.modules, not a bare dict: @dataclass
    # resolves its annotations through sys.modules[cls.__module__], which is
    # None for a namespace that was never registered, and fails with a bare
    # "'NoneType' has no attribute '__dict__'" that says nothing about why.
    name = f"catalog_probe_{dir_.name}"
    module = ModuleType(name)
    module.__file__ = str(src)
    sys.modules[name] = module
    try:
        exec(compile(source, str(src), "exec"), module.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(name, None)
    return module


# ----------------------------------------------------------- it builds


def test_the_real_catalog_loads_and_is_not_empty():
    assert TABLES, "the catalog is empty — every query would be refused"
    assert CATALOG_DIR.is_dir()


def test_filterable_is_derived_not_stored():
    # Deliberately not a column in columns.csv: it is exactly "type is not
    # LIST", and storing it would create a second place for it to disagree
    # with the type sitting beside it.
    for table in TABLES.values():
        expected = {c for c, t in table.columns.items() if t is not QType.LIST}
        assert table.filterable == expected, table.name


def test_vector_columns_are_not_filterable():
    # The property that matters for the boundary: a per-row list has no
    # sensible scalar comparison, so offering one would invite confusion.
    quotes = TABLES["quotes"]
    for col in ("bid_prices", "bid_sizes", "ask_prices", "ask_sizes"):
        assert col not in quotes.filterable


# ------------------------------------------------- it refuses, loudly


def test_an_unknown_type_is_refused_by_name(tmp_path):
    # A typo in the type column must not silently drop the column from the
    # whitelist — that would turn a data-file error into what looks like a
    # caller naming a column that does not exist.
    _write(
        tmp_path,
        [{"table": "t", "description": "d"}],
        [{"table": "t", "column": "c", "type": "flaot"}],
    )
    with pytest.raises(ValueError, match="unknown type"):
        _load_from(tmp_path)


def test_a_table_with_no_columns_is_refused(tmp_path):
    # An empty `columns` builds a table that accepts no filter at all. It
    # would serve, and every query against it would fail as "unknown
    # column".
    _write(tmp_path, [{"table": "t", "description": "d"}], [])
    with pytest.raises(ValueError, match="no columns.csv rows"):
        _load_from(tmp_path)


def test_a_table_with_no_description_is_refused(tmp_path):
    _write(
        tmp_path,
        [],
        [{"table": "t", "column": "c", "type": "symbol"}],
    )
    with pytest.raises(ValueError, match="absent from tables.csv"):
        _load_from(tmp_path)


def test_a_missing_file_says_what_it_means(tmp_path):
    # Serving without the whitelist would be serving with no whitelist, so
    # the message says that rather than just naming a path.
    with pytest.raises(FileNotFoundError, match="whitelist"):
        _load_from(tmp_path)


def test_every_type_in_the_csv_is_a_known_qtype():
    # The real file, not a fixture: a type added to columns.csv without a
    # matching QType member would break coercion at query time.
    known = {t.value for t in QType}
    with (CATALOG_DIR / "columns.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            assert row["type"] in known, f"{row['table']}.{row['column']}: {row['type']}"


def test_the_two_files_describe_the_same_tables():
    with (CATALOG_DIR / "tables.csv").open(newline="") as f:
        described = {row["table"] for row in csv.DictReader(f)}
    with (CATALOG_DIR / "columns.csv").open(newline="") as f:
        columned = {row["table"] for row in csv.DictReader(f)}
    assert described == columned
