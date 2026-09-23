"""The desk catalog entry a scaffold writes for a new table.

Split from `jobs.py`, which plans the job itself: describing a table for the
desk is not about the job that publishes into it.

**This used to write three files** - a description, a row per column, and an
entry in a drift test's list. Two of those are gone: the columns and their
types now come from `meta` on a running process, so there is nothing to
copy and nothing to keep in step. What is left is the one thing that cannot
be derived, the prose, and it goes to `.qcat` in
scripts/processes/uqs_catalog.q beside the tables it describes.

A consequence worth naming: a table with an exotic column type is no longer
un-cataloguable. The old writer refused when a column had no catalog type,
because it had to write that type down. It does not any more.
"""

from __future__ import annotations

from uqs.paths import CATALOG_FILE
from uqs.scaffold.plan import FileAction, WriteMode


def catalog_actions(table: str, cols: list[tuple[str, str]], notes: list[str]) -> list[FileAction]:
    """The desk catalog entry for a new `table`.

    The DESCRIPTION is written as a SCAFFOLDED placeholder rather than
    guessed: it is read by someone deciding whether this is the table they
    want, and the table's own name restated there would pass every test and
    tell them nothing. `test_no_scaffold_left.py` fails until someone
    replaces it.

    `cols` is accepted and unused - the signature is shared with the other
    scaffold writers, and the columns are `meta`'s answer now, not ours.
    """
    notes.append(
        f"describe {table}: replace its SCAFFOLDED line in {CATALOG_FILE} - or, if the "
        "desk should not see it, delete that line and add the table to .qcat.hidden "
        "with the reason"
    )
    entry = (
        f".qcat.describe[`{table}]:\n"
        f'    "SCAFFOLDED: what one row of {table} is, for someone choosing a table";\n'
    )
    return [FileAction(CATALOG_FILE, entry, mode=WriteMode.APPEND)]
