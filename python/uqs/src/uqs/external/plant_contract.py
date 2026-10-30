"""plant_contract.py - a plant table's column order, as q evaluates its declaration.

`.u.upd` places columns by position, not by name, so a publisher must send
them in the order `src/etl/plant_tables.q` declares. Every streamer that
publishes onto a plant table reads that order here, from the schema metadata
q generated (uqs.model.plant_metadata, #1781) - so a reorder in q reaches
every publisher, and no spelling of the definition hides it.
"""

from __future__ import annotations

from pathlib import Path

from uqs.model import plant_metadata, schemas

#: The plant's declarations of the raw tables.
PLANT_TABLES_Q = "src/etl/plant_tables.q"


def plant_table_fields(repo_root: str, table: str, *, text: str | None = None) -> list[str]:
    """`table`'s columns in declared order, without `time`.

    `time` is dropped because `.u.upd` stamps it on receipt. Read from the
    tree's schema metadata. `text` is a bundle's `tables.q` BEFORE it is
    installed - nothing has evaluated it yet, so it is read as text, in the
    one `name:([]...)` line form a bundle may use (stack/bundles.py).
    """
    if text is None:
        fields = plant_metadata.load(Path(repo_root)).table(table).column_names
    else:
        found = schemas.definitions_in(text)
        fields = schemas.column_names(schemas.definition_from(found, table, PLANT_TABLES_Q))
    return [f for f in fields if f != "time"]
