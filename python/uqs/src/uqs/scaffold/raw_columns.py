"""A source's physical table, as `--raw-columns` describes it: what its adapter READS.

Split from columns.py, whose type table this reads. The rules differ from a plant table's
because the table is someone else's: names keep the source's own case, nothing is grouped,
no `time` is added, and `any` is a general column whose type is not checked.
"""

from __future__ import annotations

import re

from uqs.paths import UqsError
from uqs.scaffold.columns import TYPES, refuse_q_word, refuse_twice, type_named

#: A physical column, as the source spells it: its case is the site's.
PHYSICAL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def parse_raw_columns(spec: str) -> list[tuple[str, str]]:
    """A `--raw-columns` string as [(name, literal)]: what a source's adapter
    READS from its physical table, apart from what it returns.

    Names keep the source's own case (`CREATED_AT`), nothing is grouped and no
    `time` is added - this describes someone else's table, not a plant one.
    `any` (or `list`) is a general column, whose type is not checked: a
    payload whose q type depends on the driver.
    """
    names = ", ".join(sorted([t.name for t in TYPES] + ["any"]))
    out: list[tuple[str, str]] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        col, sep, qtype = (s.strip() for s in part.partition(":"))
        if not sep:
            raise UqsError(
                f"raw column {part!r} must be name:type, e.g. 'CREATED_AT:timestamp'. "
                f"Types: {names}"
            )
        if not PHYSICAL_NAME.fullmatch(col):
            raise UqsError(
                f"raw column {col!r} must start with a letter and hold only letters, digits "
                "and underscores"
            )
        refuse_q_word(col, "raw column")
        ctype = type_named("list" if qtype == "any" else qtype)
        if ctype is None:
            raise UqsError(f"raw column {col!r} has unknown type {qtype!r}. Types: {names}")
        refuse_twice(col, out, "raw column")
        out.append((col, ctype.literal))
    if not out:
        raise UqsError("--raw-columns is empty: name the columns the adapter reads")
    return out


def raw_contract(table: str, columns: list[tuple[str, str]]) -> str:
    """The q dict a source declares as `raw`: one physical table, its contract."""
    body = "; ".join(f"{c}:{literal}" for c, literal in columns)
    return f"enlist[`{table}]!enlist ([] {body})"
