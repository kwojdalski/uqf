"""Sorting a listing by one of its columns: `--sort` and `--reverse`, the same
on every command that prints rows (#1728, #1858).

Split from cli/shared.py, which every command module imports for the app and
its refusals; this is the part only the listing commands use.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cells import cell
from uqs.cli.shared import _die
from uqs.paths import UqsError

#: The sort and its direction, the same on every listing (#1728, #1858).
SortOpt = Annotated[
    str | None,
    typer.Option("--sort", help="Sort by this column (case-insensitive, numeric-aware)."),
]
ReverseOpt = Annotated[
    bool, typer.Option("--reverse", help="Sort descending. Only meaningful with --sort.")
]


def _sort_key(value: object):
    """Sort key for one cell, numeric where the whole column is numeric.

    Returned as a tuple so empties group together at one end rather than
    sorting as the empty string among real values - a process with no
    override set is not "before aaa", it is absent.
    """
    text = cell(value).strip()
    if not text:
        return (1, 0.0, "")
    try:
        return (0, float(text), "")
    except ValueError:
        return (0, 0.0, text.casefold())


def _sorted_items[Row: dict](items: list[Row], sort: str | None, reverse: bool) -> list[Row]:
    """`items` ordered by one column, or untouched when none is named.

    The column is matched case-insensitively against the keys the rows
    actually have, because those differ per command and per kind - `list
    processes` has procname/proctype/port/startwithall, `list env` has
    name/value, `status` has Process/Status/PID/... - so there is no fixed
    set to validate against and an unknown name has to name the real ones
    back. Shared by `uqs list --sort` and `uqs status --sort`.

    Numeric columns sort numerically. `port` is a string like "6051", and
    lexicographically "6100" sorts before "659" - which looks like the sort
    silently did nothing on the one column most worth sorting.
    """
    if not sort or not items:
        return items
    known = {column.casefold(): column for column in items[0]}
    column = known.get(sort.strip().casefold())
    if column is None:
        _die(
            UqsError(
                f"cannot sort by {sort!r}: no such column. Available: {', '.join(sorted(items[0]))}"
            )
        )
        return items
    return sorted(items, key=lambda item: _sort_key(item.get(column, "")), reverse=reverse)
