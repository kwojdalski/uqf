"""Reading a running process's table schemas over IPC.

`uqf-stack schema` answers "what tables are in the database, and what shape
are they" without anyone hand-writing `meta` at a q prompt.

**It reads the LIVE database, not the declarations.** `scripts/processes/uqf_stack_tables.q`
says what the tickerplant is configured to carry, and the contract surface
says what this tree declares; neither is evidence that a table exists in the
process you are about to query. A schema command that reported the
declarations would be confidently wrong exactly when it mattered - a
tickerplant that failed to load its schema file, an RDB that has not
replayed, a table nobody publishes into.

So the source is `tables`/`meta` on a real handle, and the three views it
offers are all derived from that one query.
"""

from __future__ import annotations

from typing import Any

from torq_orchestrator.logger import get_logger
from torq_orchestrator.model.pipelines import DEFAULT_BASE_PORT
from torq_orchestrator.paths import UqfStackError, UqfStackPaths
from torq_orchestrator.stack.listing import _list_processes
from torq_orchestrator.stack.runtime import query

log = get_logger(__name__)

#: The process to read schemas from when the caller does not say. rdb1 holds
#: today's data and every table the tickerplant carries, which makes it the
#: one someone asking "what is in the database" almost always means. hdb1
#: has the history but only the tables that have been written down by EOD.
DEFAULT_PROC = "rdb1"

#: q's type characters, as `meta` reports them. Kept here rather than
#: imported from uqf_frontend.catalog: that enum exists to decide how the
#: gateway COERCES a filter value and only covers the types it can accept,
#: while this is for a human reading a table shape and has to name whatever
#: q hands back, including the ones no filter will ever take.
TYPE_NAMES = {
    "b": "boolean",
    "g": "guid",
    "x": "byte",
    "h": "short",
    "i": "int",
    "j": "long",
    "e": "real",
    "f": "float",
    "c": "char",
    "s": "symbol",
    "p": "timestamp",
    "m": "month",
    "d": "date",
    "z": "datetime",
    "n": "timespan",
    "u": "minute",
    "v": "second",
    "t": "time",
    # A general (mixed or nested) column. q reports it as a SPACE, which is
    # invisible in any rendering - the same character that silently broke the
    # contract surface's CSV until it was quoted. Named here so a reader sees
    # something rather than an empty cell and assumes the column is untyped.
    " ": "general",
    "": "general",
}

#: Attribute characters `meta` reports, expanded. An attribute is a promise
#: about the column's order or uniqueness that q relies on for lookup speed,
#: so it is worth showing: a `g` that has silently gone is a performance
#: cliff with no error attached.
ATTR_NAMES = {
    "g": "grouped",
    "s": "sorted",
    "p": "parted",
    "u": "unique",
}


def _rows(result: Any) -> list[dict[str, Any]]:
    """kola returns a polars DataFrame for a table result; normalise it.

    Imported lazily for the same reason `query` imports kola lazily - the
    orchestrator's non-IPC commands must not pay for, or fail on, a missing
    dataframe library.
    """
    import polars as pl

    if isinstance(result, pl.DataFrame):
        return result.to_dicts()
    if isinstance(result, list):
        return list(result)
    raise UqfStackError(f"expected a table from the process, got {type(result).__name__}")


def resolve_port(paths: UqfStackPaths, procname: str, base_port: int = DEFAULT_BASE_PORT) -> int:
    """A declared process's resolved port.

    From the registry rather than by adding an offset here: the offsets live
    in one place (`pipelines.PIPELINE_OFFSETS` and the vendored csv) and a
    second copy in this module would be a second place for them to be wrong.
    """
    for row in _list_processes(paths, base_port):
        if row["procname"] == procname:
            return int(row["port"])
    known = ", ".join(sorted(r["procname"] for r in _list_processes(paths, base_port)))
    raise UqfStackError(f"{procname!r} is not a declared process - known: {known}")


def type_name(type_char: str) -> str:
    """A `meta` type character as a readable name.

    CASE IS THE VECTOR/ATOM DISTINCTION, and it is the whole reason this is a
    function rather than a dictionary lookup. `f` is a float column - one
    float per row. `F` is a float VECTOR column - a list per row, which is
    the shape `quotes` and `mkt_orderbook` are built around and the shape
    every pricing function in src/ expects. Rendering `F` as "float" would
    describe a different table.
    .
    A column's case can also CHANGE: an empty vector column reports as
    general (a space), because q cannot know the element type until a row
    exists. So the same table reads `general` before its first publish and
    `float vector` after - not a bug, and worth knowing before treating one
    reading as the schema.
    """
    if not type_char.strip():
        return "general"
    lowered = type_char.lower()
    base = TYPE_NAMES.get(lowered)
    if base is None:
        return type_char
    return base if type_char.islower() else f"{base} vector"


def _decode(value: Any) -> str:
    """One `meta` cell as text.

    kola hands back bytes for q char columns and a str for symbols, so both
    spellings reach here for what is, to a reader, the same thing.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def table_names(port: int, host: str = "localhost", **creds: Any) -> list[str]:
    """Every table in the process's root namespace, in q's own order."""
    result = query("([] name:string tables `)", port, host=host, **creds)
    return [_decode(row["name"]) for row in _rows(result)]


def overview(port: int, host: str = "localhost", **creds: Any) -> list[dict[str, Any]]:
    """One row per table: name, row count, column count.

    Row count comes from the process rather than being inferred, because
    "declared but empty" and "carrying data" are the distinction someone runs
    this command to see - an RDB that has not replayed looks identical to a
    healthy one in every other view.
    """
    # `ncols`, not `cols`: `cols` is a q BUILTIN, and using it as a column
    # name in a table literal throws 'assign. The repository's check_q_traps
    # has a rule for exactly this, but it globs `git ls-files '*.q'` - q
    # embedded in a Python string is invisible to it, which is how this one
    # got as far as a live process before failing.
    expr = (
        "([] name:string tables `; rows:count each value each tables `; "
        "ncols:count each cols each tables `)"
    )
    return [
        {
            "table": _decode(row["name"]),
            "rows": int(row["rows"]),
            "columns": int(row["ncols"]),
        }
        for row in _rows(query(expr, port, host=host, **creds))
    ]


def match_tables(pattern: str, port: int, host: str = "localhost", **creds: Any) -> list[str]:
    """Live table names matching a shell-style pattern.

    `fnmatch`, not a regex: `crypto*` is what someone types, and the whole
    point is to avoid making them escape anything. Matching is done in
    Python against the names the process reported rather than by sending a
    `like` to q - the names have already crossed the wire by then, so there
    is nothing to gain from a second round trip and one fewer place for a
    caller's string to reach an expression.

    An exact name with no metacharacters matches itself, so callers do not
    need to know whether what they were handed is a pattern.
    """
    import fnmatch

    names = table_names(port, host=host, **creds)
    return [name for name in names if fnmatch.fnmatchcase(name, pattern)]


def columns(table: str, port: int, host: str = "localhost", **creds: Any) -> list[dict[str, str]]:
    """One table's columns, as `meta` reports them.

    The table name is interpolated rather than parameterised because q's
    `meta` takes a symbol, not a string, and there is no bind-parameter form
    over this transport. It is validated against the live table list first,
    so the only names that reach the expression are ones the process already
    reported - which is the same one-escape-path discipline `.qodbc` follows
    where a driver cannot parameterise.
    """
    available = table_names(port, host=host, **creds)
    if table not in available:
        raise UqfStackError(
            f"{table!r} is not a table on this process - it has: {', '.join(sorted(available))}"
        )

    rows = _rows(query(f"0!meta `{table}", port, host=host, **creds))
    out = []
    for row in rows:
        type_char = _decode(row.get("t"))
        attr = _decode(row.get("a"))
        out.append(
            {
                "column": _decode(row.get("c")),
                "type": type_name(type_char),
                "q": type_char if type_char.strip() else " ",
                "attribute": ATTR_NAMES.get(attr, attr),
            }
        )
    return out
