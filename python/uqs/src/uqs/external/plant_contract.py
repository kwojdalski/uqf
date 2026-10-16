"""plant_contract.py - a plant table's column order, read from its q declaration.

`.u.upd` places columns by position, not by name, so a publisher must send
them in the order `src/etl/plant_tables.q` declares. Every streamer that
publishes onto a plant table reads that order here, so there is one parser of
the declaration's format (model/schemas.py) and a reorder in q reaches every publisher.
"""

from __future__ import annotations

import os

from uqs.model import schemas

#: The plant's declarations of the raw tables.
PLANT_TABLES_Q = "src/etl/plant_tables.q"


def plant_table_fields(repo_root: str, table: str, *, text: str | None = None) -> list[str]:
    """`table`'s columns in declared order, without `time`.

    `time` is dropped because `.u.upd` stamps it on receipt. `text` stands in
    for the file's contents so a test can present a reordered plant.
    """
    if text is None:
        with open(os.path.join(repo_root, PLANT_TABLES_Q), encoding="utf-8") as fh:
            text = fh.read()
    # The one parser of the declaration format, release-prefix handling and
    # the column split included (#1392); a miss names the table.
    found = schemas.definitions_in(text)
    fields = schemas.column_names(schemas.definition_from(found, table, PLANT_TABLES_Q))
    return [f for f in fields if f != "time"]
