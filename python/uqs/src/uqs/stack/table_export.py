"""Writing a listing or a query's result to a file: what `--export` does.

Split from stack/runtime.py, which drives torq.sh and queries processes; this
only writes rows that a command already has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from uqs.cells import cell
from uqs.paths import UqsError


def export_table(rows: Any, path: Path) -> None:
    """Write *rows* to *path* as CSV or Parquet, format inferred from the
    file extension. *rows* is either a list[dict] (list_items/config show's
    own shape) or a polars.DataFrame (what kola's query() returns for a
    table-shaped q result - kola is a Polars interface to q, so this is
    already the native return type for `select ... from t`, no conversion
    needed). Anything else (a q scalar/atom from query(), e.g. `count t`)
    isn't rows, so it's rejected rather than silently wrapped into a bogus
    one-cell table.

    CSV holds no nested value, so a list or struct column is written as the
    text its table cell shows (#2517); Parquet keeps it nested.
    """
    import polars as pl

    if isinstance(rows, pl.DataFrame):
        df = rows
    elif isinstance(rows, list):
        df = pl.DataFrame(rows)
    else:
        raise UqsError(
            f"can't export a {type(rows).__name__} result to a table - --export needs "
            "tabular output (a process/config list, or a query returning a table)"
        )

    suffix = path.suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise UqsError(f"unsupported export extension {suffix!r} - use .csv or .parquet")
    # a missing or unwritable directory is a refusal, not polars' traceback (#2606)
    try:
        if suffix == ".csv":
            nested = [c for c, t in df.schema.items() if t.is_nested()]
            df = df.with_columns(
                pl.Series(c, [cell(v) for v in df[c].to_list()], dtype=pl.String) for c in nested
            )
            df.write_csv(path)
        else:
            df.write_parquet(path)
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise UqsError(f"cannot write {path}: {exc}") from None
