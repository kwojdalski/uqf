"""The desk catalog entry a scaffold writes for a new table.

Split from `jobs.py`, which plans the job itself: describing a table for the
desk is not about the job that publishes into it.

**This used to write three files** - a description, a row per column, and an
entry in a drift test's list. Two of those are gone: the columns and their
types now come from `meta` on a running process, so there is nothing to
copy and nothing to keep in step. What is left is the one thing that cannot
be derived, the prose, and it goes to `.qcat` in
scripts/processes/uqs_catalog.q beside the tables it describes.

A consequence worth naming: the columns' types are no longer written down
here, so nothing in this writer refuses an exotic one. The browser's catalog
(uqf_frontend.catalog.QType) is where the set of browsable types lives; the
scaffold's types are held to it by uqf_frontend's test_browsable_types.py, so
a type `uqs job new` accepts is one the browser can show. A described table
whose column the catalog still cannot type is skipped, with the reason logged
and named when asked for, rather than failing the other tables (#1364).
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

    A browsable table also needs a querypolicy.csv row or a `.qcat.unbounded`
    reason (tests/q/test_catalog.q, #889), so a SCAFFOLDED reason goes with
    the description: whether the table is read bounded is a decision nobody
    has made yet, and the placeholder says so where the to-do list finds it
    rather than failing the catalog gate unexplained (#2461).

    `cols` is accepted and unused - the signature is shared with the other
    scaffold writers, and the columns are `meta`'s answer now, not ours.
    """
    notes.append(
        f"describe {table}: replace its two SCAFFOLDED lines in {CATALOG_FILE} - the "
        "description, and the .qcat.unbounded reason (or swap that for a querypolicy.csv "
        "row) - or, if the desk should not see it, delete both and add the table to "
        ".qcat.hidden with the reason"
    )
    entry = (
        f".qcat.describe[`{table}]:\n"
        f'    "SCAFFOLDED: what one row of {table} is, for someone choosing a table";\n'
        f".qcat.unbounded[`{table}]:\n"
        f'    "SCAFFOLDED: how many rows {table} gets a day, and why a whole select of it '
        'is safe - or a querypolicy.csv row instead";\n'
    )
    return [FileAction(CATALOG_FILE, entry, mode=WriteMode.APPEND)]
