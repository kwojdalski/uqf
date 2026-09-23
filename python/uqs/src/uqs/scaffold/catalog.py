"""The desk catalog entry a scaffold writes for a new table.

Split from `jobs.py`, which plans the job itself: a new table has to reach
three files the front end owns - its description, its columns, and the drift
test's list of catalogued plant tables - and none of that is about the job.
"""

from __future__ import annotations

from uqs.paths import CATALOG_COLUMNS, CATALOG_DRIFT_TEST, CATALOG_TABLES
from uqs.scaffold.plan import FileAction, WriteMode

#: A new table is invisible to the desk front end until the catalog describes
#: it (FE-07), and test_catalog_drift.py fails until then. `catalog_actions`
#: now writes the entry itself; this note is the fallback for a table with a
#: column the catalog has no type for, which only a person can resolve.
_CATALOG_NOTE = (
    "describe {table} in python/uqf_frontend/catalog/tables.csv - authored prose, "
    "checked by pytest (or add it to _NOT_IN_CATALOG with the reason)"
)


#: A column's q literal -> the catalog's type for it. A type absent here has
#: no catalog equivalent (the desk front end does not coerce it), so a table
#: with one gets the note rather than a catalog entry that could not load.
_CATALOG_TYPE = {
    "`timestamp$()": "timestamp",
    "`symbol$()": "symbol",
    "`g#`symbol$()": "symbol",
    "`float$()": "float",
    "`long$()": "long",
    "`boolean$()": "boolean",
    "`timespan$()": "timespan",
    "()": "list",
}


def catalog_actions(table: str, cols: list[tuple[str, str]], notes: list[str]) -> list[FileAction]:
    """The desk catalog entry for a new `table`, or a note when it cannot be one.

    The columns are mechanical - the same `--columns` the table is defined
    from - so they are written, along with the drift test's list entry. The
    DESCRIPTION is not: it is read by someone deciding whether this is the
    table they want, and the table's own name there would pass every test and
    tell them nothing. So it is written as a SCAFFOLDED placeholder, which
    `test_no_scaffold_left.py` fails on until someone replaces it.
    """
    unsupported = [c for c, lit in cols if lit not in _CATALOG_TYPE]
    if unsupported:
        notes.append(
            _CATALOG_NOTE.format(table=table)
            + f" - not scaffolded: {', '.join(unsupported)} has no catalog type"
        )
        return []
    notes.append(
        f"describe {table}: replace its SCAFFOLDED row in {CATALOG_TABLES} - or, if the "
        "desk should not see it, remove its catalog rows and add it to _NOT_IN_CATALOG "
        "with the reason"
    )
    rows = "".join(f"{table},{c},{_CATALOG_TYPE[lit]}\n" for c, lit in cols)
    return [
        FileAction(
            CATALOG_TABLES,
            f'{table},"SCAFFOLDED: what one row of {table} is, for someone choosing a table"\n',
            mode=WriteMode.APPEND,
        ),
        FileAction(CATALOG_COLUMNS, rows, mode=WriteMode.APPEND),
        FileAction(CATALOG_DRIFT_TEST, table, mode=WriteMode.APPEND),
    ]
