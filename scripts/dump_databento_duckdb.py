#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb>=1.5,<2", "pyarrow>=20"]
# ///
"""Load Databento MBP-10 parquet files into a DuckDB database.

The source for testing the ETL framework against real market data: the
masters_thesis project keeps one parquet file per symbol per trading day,
``<SYM>/<SYM>_<YYYY-MM-DD>_raw_mbp-10_us_hours.parquet``, straight from
Databento's MBP-10 schema. This copies them into one table, ``mbp10``, so a
pipeline can read windows of it the way it would read a real database.

A PEP 723 script rather than a workspace dependency: DuckDB is needed only to
build this test database, so it stays out of the shared lockfile. Run it with
``uv run scripts/dump_databento_duckdb.py``.

Timestamps are the one deliberate change. Databento stamps ``ts_event`` and
``ts_recv`` in nanoseconds, and DuckDB reads a timezone-aware parquet
timestamp as ``TIMESTAMPTZ``, which is microseconds: ``...016039462`` comes
back as ``...016039``, and events within one microsecond lose their order.
So each file is read through Arrow, the two columns cast to epoch
nanoseconds, and stored as ``TIMESTAMP_NS`` - UTC, as Databento's are.

The other columns are kept exactly as Databento writes them - the per-level
``bid_px_00``..``ask_ct_09`` layout included - because deciding what a
pipeline publishes is the pipeline transform's job, not the loader's. Rows
are ordered by (symbol, ts_event) so DuckDB's zone maps can skip most of the
file for a single-symbol time window.

Re-running replaces the database file, so it always matches the files.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# PEP 723 dependency, deliberately not in the workspace venv ty resolves against.
import duckdb  # ty: ignore[unresolved-import]
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

TIME_COLUMNS = ("ts_event", "ts_recv")

FILE_PATTERN = re.compile(
    r"^(?P<sym>[A-Z.]+)_(?P<day>\d{4}-\d{2}-\d{2})_raw_mbp-10_us_hours\.parquet$"
)


def repo_root() -> Path:
    """The main checkout's root, even when run from a git worktree.

    ``--git-common-dir`` names the shared ``.git`` of the main checkout, so
    the default paths below resolve the same from any worktree - a worktree's
    own top level lives under ``.claude/worktrees/`` and would put the
    database, and the sibling-project lookup, somewhere nobody looks.
    """
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent,
    ).stdout.strip()
    return Path(common).parent


def parse_args(argv: list[str]) -> argparse.Namespace:
    root = repo_root()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        type=Path,
        default=root.parent / "masters_thesis" / "data" / "raw" / "stocks" / "daily",
        help="directory of <SYM>/<SYM>_<day>_raw_mbp-10_us_hours.parquet files",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=root / "output" / "duckdb" / "databento.duckdb",
        help="DuckDB database file to write",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        help="load only these symbols (default: every symbol directory found)",
    )
    return parser.parse_args(argv)


def symbol_of(path: Path) -> str:
    """The symbol in an MBP-10 file name, or an error naming the file."""
    match = FILE_PATTERN.match(path.name)
    if match is None:
        raise SystemExit(f"unexpected file name: {path}")
    return match["sym"]


def find_files(source: Path, symbols: list[str] | None) -> list[Path]:
    """Every MBP-10 file under source, refusing a name that does not parse.

    A file that does not match the pattern is an error rather than skipped:
    a silently skipped day is a gap the pipeline test would then report as
    the pipeline's fault.
    """
    if not source.is_dir():
        raise SystemExit(f"source directory not found: {source}")
    files = sorted(source.glob("*/*.parquet"))
    by_symbol = {f: symbol_of(f) for f in files}
    if symbols:
        wanted = set(symbols)
        files = [f for f in files if by_symbol[f] in wanted]
        missing = wanted - {by_symbol[f] for f in files}
        if missing:
            raise SystemExit(f"no files for symbol(s): {', '.join(sorted(missing))}")
    if not files:
        raise SystemExit(f"no MBP-10 parquet files under {source}")
    return files


def as_epoch_ns(table: pa.Table) -> pa.Table:
    """The table with each nanosecond timestamp column as int64 epoch-ns."""
    for name in TIME_COLUMNS:
        i = table.schema.get_field_index(name)
        table = table.set_column(i, name, pc.cast(table[name], pa.int64()))
    return table


def load(files: list[Path], out: Path) -> None:
    """Load files into out's mbp10 table, sorted, replacing any previous one.

    Rows are staged in a SEPARATE database file and only the sorted copy is
    written to out. DuckDB does not return a dropped table's pages to the
    filesystem, so staging inside out left it twice the size of its data.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_suffix(".staging.duckdb")
    staging.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    replace = ", ".join(f"make_timestamp_ns({c}) AS {c}" for c in TIME_COLUMNS)
    try:
        with duckdb.connect(str(out)) as con:
            con.execute(f"ATTACH '{staging}' AS stg")
            for n, path in enumerate(files):
                batch = as_epoch_ns(pq.read_table(path))  # noqa: F841 - read by name in the SQL below
                verb = "CREATE TABLE stg.mbp10 AS" if n == 0 else "INSERT INTO stg.mbp10"
                con.execute(f"{verb} SELECT * REPLACE ({replace}) FROM batch")
                print(f"  read {path.name}", flush=True)
            con.execute(
                "CREATE TABLE mbp10 AS SELECT * FROM stg.mbp10 ORDER BY symbol, ts_event, sequence"
            )
            con.execute("DETACH stg")
            summary = con.execute(
                """
                SELECT symbol,
                       count(*) AS rows,
                       count(DISTINCT CAST(ts_event AS DATE)) AS days,
                       CAST(min(ts_event) AS VARCHAR) AS first_event,
                       CAST(max(ts_event) AS VARCHAR) AS last_event
                FROM mbp10 GROUP BY symbol ORDER BY symbol
                """
            ).fetchall()
    finally:
        staging.unlink(missing_ok=True)
    total = sum(row[1] for row in summary)
    print(f"wrote {total:,} rows from {len(files)} file(s) to {out}")
    for symbol, rows, days, first, last in summary:
        print(f"  {symbol:<6} {rows:>12,} rows  {days} day(s)  {first} .. {last}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    load(find_files(args.source, args.symbols), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
