"""Reading the configuration snapshot store back: what `uqs data snapshot` shows (#1999).

A markout result row carries one `config_id`; the snapshot it names
(`src/etl/core/config_snapshot.q`) holds the contract and code revisions, the
validated parameters, the declared rates and the domain's rules revision it was
produced under. The store is q's: a table in the status directory, written under a
lock, digest-checked on every load. This is only the reader, and it follows
`stack/runs.py` for the same reason: a bounded worker exits when its range is
done, so nothing is running to ask. A short-lived q loads the few files the store
needs, reads the directory and prints JSON. Python keeps no copy of the store, of
the digest rule or of what a snapshot's fields are.

Nothing here writes. The only writers are the jobs' pins and the HDB migration, both q.
"""

from __future__ import annotations

import re

from uqs.paths import UqsError, UqsPaths, UqsUsageError
from uqs.stack import runs

#: The files reading the store needs, in load order - tests/q/read_snapshots.q's.
_LOADS = (
    "src/init.q",
    "src/etl/core/status.q",
    "src/etl/core/backfill_state.q",
    "src/etl/core/config_snapshot.q",
)

#: What a snapshot id looks like, and the only thing that reaches a q expression.
_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def listing(paths: UqsPaths, dataset: str | None = None) -> list[dict]:
    """Every snapshot, oldest first: id, kind, datasets, digest, created_at, actor,
    reason and digest_ok. With `dataset`, the snapshots whose content says they
    configure it. A damaged store is shown, not refused: digest_ok says which."""
    rows = runs.query(paths, ".qetl.snapshot.list[`]", attach=False, loads=_LOADS)
    if dataset is None:
        return rows
    return [r for r in rows if dataset in _as_list(r.get("datasets"))]


def ids(paths: UqsPaths) -> list[str]:
    """The id of every snapshot recorded, for completion and for refusing an unknown one."""
    return [r["snapshot_id"] for r in listing(paths)]


def show(paths: UqsPaths, snapshot_id: str) -> tuple[dict, list[dict]]:
    """One snapshot, digest checked: its row, and one `field`/`text` pair per recorded
    field and parameter.

    Raises UqsUsageError naming the id when nothing is recorded under it, and UqsError
    with q's own message when the stored digest does not match the stored content.
    """
    if not _ID.match(snapshot_id):
        raise UqsUsageError(
            f"{snapshot_id!r} is not a snapshot id - they look like markout_5cefbe2bdcc61b7f; "
            "see `uqs data snapshot list`"
        )
    row = next((r for r in listing(paths) if r["snapshot_id"] == snapshot_id), None)
    if row is None:
        raise UqsUsageError(
            f"no snapshot {snapshot_id} is recorded - see `uqs data snapshot list`; a result "
            "must name the snapshot that produced it, and there is no default to fall back to"
        )
    try:
        fields = runs.query(paths, f".qetl.snapshot.show`{snapshot_id}", attach=False, loads=_LOADS)
    except UqsError as exc:
        raise UqsError(_digest_refusal(str(exc), snapshot_id)) from exc
    return row, fields


def _digest_refusal(message: str, snapshot_id: str) -> str:
    """q's refusal of a snapshot whose digest does not match, kept loud: its own words
    when it has them, else the id and the message."""
    if "does not match its content" in message:
        start = message.index("snapshot: ")
        return message[start:].splitlines()[0]
    return f"reading snapshot {snapshot_id} failed: {message}"


def _as_list(value: object) -> list:
    """A q list as JSON arrives: a list, or a lone atom for a one-item symbol list."""
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]
