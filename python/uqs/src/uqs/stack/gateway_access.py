"""What the data-access processes are started with, written by bootstrap.

gateway1's access list: procs.GATEWAY_ACCESS_OVERLAY points its `U` column at
it - the vendored logins plus the ordinary users of
scripts/torqconfig/permissions/gateway_users.csv.

The `-dataaccess` table list every rdb, hdb and gateway gets
(procs.DATAACCESS_EXTRAS): the tree's tableproperties.csv, keeping only the
tables this runtime's schema holds. TorQ refuses to start an hdb whose list
names a table its database lacks ("Missing table from HDB in schema"), and a
runtime of one profile, or of a bundle's jobs, holds only some of them.

EVERY plant table the runtime holds is listed, not only the file's rows: the
browser reads through getdata (#889), and getdata serves no table it is not
told about. A table the file does not list gets a row derived from its plant
definition - `time` as its time column, `sym` as its instrument column where
it has one. Listing a table is not granting it: the gateway's query policies
decide who may read what (scripts/torqcode/gateway/querypolicy.q).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from uqs.model import schemas
from uqs.model.runtime_members import plant_tables
from uqs.paths import UqsPaths

#: A `sym` column in a one-line `name:([]...)` definition.
_SYM_COLUMN = re.compile(r"[\[;]\s*sym:")

#: The tree's data-access table list, under UQF_SCRIPTS.
TABLE_PROPERTIES = Path("torqconfig") / "dataaccess" / "tableproperties.csv"


def gateway_users(paths: UqsPaths) -> list[dict[str, str]]:
    """The ordinary gateway users, with their passwords and roles."""
    users = paths.scripts_dir / "torqconfig" / "permissions" / "gateway_users.csv"
    if not users.is_file():
        return []
    with users.open(newline="") as f:
        return list(csv.DictReader(f))


def gateway_access_lines(paths: UqsPaths) -> list[str]:
    """gateway1's access list: every vendored login, then each ordinary user.

    An ordinary user already in the vendored list is not repeated - and is
    still held to their role, which handlers/pmusers.q checks by name.
    """
    vendored = paths.torqapphome / "appconfig" / "passwords" / "accesslist.txt"
    lines = [line.strip() for line in vendored.read_text().splitlines() if line.strip()]
    known = {line.split(":", 1)[0] for line in lines}
    lines += [
        f"{u['user']}:{u['password']}" for u in gateway_users(paths) if u["user"] not in known
    ]
    return lines


def derived_row(table: str) -> str:
    """A table-properties row for a plant table the file does not list."""
    sym = "sym" if _SYM_COLUMN.search(schemas.definition(table)) else ""
    return f",{table},time,{sym},{sym},,,,"


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
    kept = [ln for ln in rows if held is None or ln.split(",")[1] in held]
    listed = {ln.split(",")[1] for ln in kept}
    plant = schemas.table_names()
    unlisted = sorted((plant if held is None else held & plant) - listed)
    return lines[:1] + kept + [derived_row(t) for t in unlisted]


def write(paths: UqsPaths) -> None:
    """Both files, into the runtime's data directory."""
    paths.generated_gateway_access.write_text("\n".join(gateway_access_lines(paths)) + "\n")
    if lines := table_properties_lines(paths):
        (paths.torqdata / TABLE_PROPERTIES.name).write_text("\n".join(lines) + "\n")
