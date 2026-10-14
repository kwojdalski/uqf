"""What the data-access processes are started with, written by bootstrap.

gateway1's and sctp1's access lists: procs.ACCESS_LIST_OVERLAY points their
`U` columns at them - the vendored logins plus, for gateway1, the ordinary
users of scripts/torqconfig/permissions/gateway_users.csv, and for sctp1 the
outside real-time subscribers of subscriber_users.csv (#984).

The `-dataaccess` table list every rdb, hdb and gateway gets
(procs.DATAACCESS_EXTRAS): the tree's tableproperties.csv, keeping only the
tables this runtime's schema holds. TorQ refuses to start an hdb whose list
names a table its database lacks ("Missing table from HDB in schema"), and a
runtime of one profile, or of a bundle's jobs, holds only some of them.

EVERY table a desk may browse is listed: the browser reads through getdata
(#889), and getdata serves no table it is not told about. What a desk may
browse is the plant tables plus the datasets bounded workers write (#1363),
and the file is DERIVED from both - `table_properties_rows`, written by
scripts/generate/tableproperties.py and held by its --check. A plant table gets
`time` as its time column and `sym` as its instrument column where it has one;
a worker dataset gets what q's transform output says (generated/q_facts.py).
Listing a table is not granting it: the gateway's query policies decide who
may read what (scripts/torqcode/gateway/querypolicy.q).

A worker dataset that is not a plant table is in the HDB only once a backfill
has written it, and TorQ exits at start when its list names a table the HDB
lacks. So its row is passed on to a runtime only while the HDB's newest
partition holds the dataset (`table_properties_lines`).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from uqs.generated import q_facts
from uqs.model import schemas
from uqs.model.runtime_members import plant_tables
from uqs.paths import UqsPaths

_DAY = re.compile(r"\d{4}\.\d{2}\.\d{2}")

#: The tree's data-access table list, under UQF_SCRIPTS.
TABLE_PROPERTIES = Path("torqconfig") / "dataaccess" / "tableproperties.csv"


def _users(paths: UqsPaths, name: str) -> list[dict[str, str]]:
    users = paths.scripts_dir / "torqconfig" / "permissions" / name
    if not users.is_file():
        return []
    with users.open(newline="") as f:
        return list(csv.DictReader(f))


def gateway_users(paths: UqsPaths) -> list[dict[str, str]]:
    """The ordinary gateway users, with their passwords and roles."""
    return _users(paths, "gateway_users.csv")


def _access_lines(paths: UqsPaths, users: list[dict[str, str]]) -> list[str]:
    """Every vendored login, then each of `users` not already among them."""
    vendored = paths.torqapphome / "appconfig" / "passwords" / "accesslist.txt"
    lines = [line.strip() for line in vendored.read_text().splitlines() if line.strip()]
    known = {line.split(":", 1)[0] for line in lines}
    return lines + [f"{u['user']}:{u['password']}" for u in users if u["user"] not in known]


def gateway_access_lines(paths: UqsPaths) -> list[str]:
    """gateway1's access list: every vendored login, then each ordinary user.

    An ordinary user already in the vendored list is not repeated - and is
    still held to their role, which handlers/pmusers.q checks by name.
    """
    return _access_lines(paths, gateway_users(paths))


def subscriber_access_lines(paths: UqsPaths) -> list[str]:
    """sctp1's access list: every vendored login, then each outside
    real-time subscriber - who may subscribe to the chained tickerplant and
    log in nowhere else."""
    return _access_lines(paths, _users(paths, "subscriber_users.csv"))


#: The header TorQ's readtableproperties reads with a fixed type string.
TABLE_PROPERTIES_HEADER = (
    "proctype,tablename,primarytimecolumn,attributecolumn,instrumentcolumn,"
    "rolltimeoffset,rolltimezone,datatimezone,partitionfield"
)

#: The plant tables whose instrument column is not `sym`, and what it is. Not
#: derivable: a column named `book` is a fact about order_quality alone.
#: `table_properties_rows` refuses an entry whose column the plant definition
#: lacks, so renaming it in plant_tables.q fails there and not at query time.
INSTRUMENT_COLUMN_OVERRIDES = {"order_quality": "book"}


def _plant_columns(table: str) -> set[str]:
    """The column names of a plant table's one-line `([]c:t;...)` definition."""
    body = schemas.definition(table).split("]", 1)[1]
    return set(re.findall(r"(?:^|[(;\s])([a-z_][a-z0-9_]*):", body))


def _row(table: str, time_column: str, instrument: str) -> str:
    return f",{table},{time_column},{instrument},{instrument},,,,"


def derived_row(table: str) -> str:
    """A table-properties row for a plant table: `time`, and its instrument
    column - `sym` where it has one (INSTRUMENT_COLUMN_OVERRIDES otherwise)."""
    columns = _plant_columns(table)
    if "time" not in columns:
        raise ValueError(f"plant_tables.q gives {table} no `time` column to read it by")
    if column := INSTRUMENT_COLUMN_OVERRIDES.get(table):
        if column not in columns:
            raise ValueError(
                f"INSTRUMENT_COLUMN_OVERRIDES names {column!r} for {table}, "
                "which plant_tables.q does not give it"
            )
        return _row(table, "time", column)
    return _row(table, "time", "sym" if "sym" in columns else "")


def worker_datasets() -> set[str]:
    """The datasets bounded workers write that are not plant tables."""
    return set(q_facts.BOUNDED_DATASET_TIME_COLUMNS) - schemas.table_names()


def table_properties_rows() -> list[str]:
    """Every row of the tree's table list, header first: one for each plant
    table, then one for each bounded worker's dataset that is not a plant
    table, read from q's transform output (generated/q_facts.py). A dataset
    that IS a plant table takes the plant's row, because the plant definition
    is the shape stored."""
    rows = [derived_row(t) for t in sorted(schemas.table_names())]
    rows += [
        _row(
            d,
            q_facts.BOUNDED_DATASET_TIME_COLUMNS[d],
            q_facts.BOUNDED_DATASET_INSTRUMENT_COLUMNS[d],
        )
        for d in sorted(worker_datasets())
    ]
    return [TABLE_PROPERTIES_HEADER, *rows]


def hdb_tables(paths: UqsPaths) -> set[str]:
    """The tables the HDB's newest date partition holds: what TorQ's `meta`
    sees there, and so what its -dataaccess list may name."""
    if not paths.hdb_dir.is_dir():
        return set()
    days = sorted(d for d in paths.hdb_dir.iterdir() if d.is_dir() and _DAY.fullmatch(d.name))
    return {t.name for t in days[-1].iterdir() if t.is_dir()} if days else set()


def table_properties_lines(paths: UqsPaths) -> list[str]:
    """The tree's table list, with only the tables `paths`' runtime holds and
    a derived row for each held plant table it does not list; [] for a tree
    without one."""
    source = paths.scripts_dir / TABLE_PROPERTIES
    if not source.is_file():
        return []
    lines = source.read_text().splitlines()
    held = plant_tables(paths.runtime_declaration)
    rows = [ln for ln in lines[1:] if ln.strip()]
    in_hdb = hdb_tables(paths)
    datasets = worker_datasets()

    def keep(name: str) -> bool:
        if name in datasets:
            return name in in_hdb
        return held is None or name in held

    kept = [ln for ln in rows if keep(ln.split(",")[1])]
    listed = {ln.split(",")[1] for ln in kept}
    plant = schemas.table_names()
    unlisted = sorted((plant if held is None else held & plant) - listed)
    return lines[:1] + kept + [derived_row(t) for t in unlisted]


def write(paths: UqsPaths) -> None:
    """Both files, into the runtime's data directory."""
    paths.generated_gateway_access.write_text("\n".join(gateway_access_lines(paths)) + "\n")
    subscribers = paths.torqdata / "subscriber_accesslist.txt"
    subscribers.write_text("\n".join(subscriber_access_lines(paths)) + "\n")
    if lines := table_properties_lines(paths):
        (paths.torqdata / TABLE_PROPERTIES.name).write_text("\n".join(lines) + "\n")
