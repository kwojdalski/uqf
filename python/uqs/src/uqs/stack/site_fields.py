"""A site's own process.csv columns, declared, never invented (#1191).

A site launcher may read a column TorQ's process.csv does not have: one
customised torq.sh reads `taskset_no`, the CPU affinity its q wrapper takes
(`3`, or `3:5`). Its lookup finds the column by its header, and with no such
header it builds `{if(NR == 2) print $}` - an awk syntax error for every
process it starts.

`UQS_PROCESS_FIELDS` declares those columns, comma- or space-separated, and
`uqs deploy push --process-fields` writes it into a target's deploy.env. Each
declared column is appended to the generated header, after `qcmd`, and every
row carries it: empty unless process_overrides.csv gives that process a value
(`uqs config set tp1 taskset_no 3`). A value is never derived or guessed. With
nothing declared, process.csv's columns are TorQ's own, exactly as before.
"""

from __future__ import annotations

import os
import re

from uqs.model.pipelines import PROCESS_CSV_FIELDS
from uqs.paths import UqsError

#: The variable that declares a site's extra process.csv columns.
PROCESS_FIELDS_ENV = "UQS_PROCESS_FIELDS"

#: A column torq.sh's `awk -F,` header match can find: no comma, quote or space.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse(value: str, source: str = PROCESS_FIELDS_ENV) -> tuple[str, ...]:
    """The column names in `value`, in order and once each - refused, naming
    `source`, when one is not a plain name or is already TorQ's own."""
    names = tuple(dict.fromkeys(n for n in re.split(r"[\s,]+", value.strip()) if n))
    for name in names:
        if not _NAME.fullmatch(name):
            raise UqsError(f"{source}: {name!r} is not a column name - letters, digits and _ only")
        if name in PROCESS_CSV_FIELDS:
            raise UqsError(f"{source}: {name!r} is already one of process.csv's own columns")
    return names


def declared() -> tuple[str, ...]:
    """The site columns UQS_PROCESS_FIELDS declares; () when it is unset."""
    return parse(os.environ.get(PROCESS_FIELDS_ENV, ""))


def process_csv_fields() -> tuple[str, ...]:
    """The generated process.csv's columns: TorQ's own, then the site's."""
    return PROCESS_CSV_FIELDS + declared()


def refuse_unknown_field(field: str, context: str) -> None:
    """Refuse `field` unless it is a column of the generated process.csv -
    naming where a site's own column is declared, since that is what a
    `taskset_no` refused here is missing."""
    fields = process_csv_fields()
    if field not in fields:
        raise UqsError(
            f"{context}: {field!r} is not one of process.csv's columns {list(fields)} - "
            f"a site's own column, such as taskset_no, is declared in {PROCESS_FIELDS_ENV}"
        )
