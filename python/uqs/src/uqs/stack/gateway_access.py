"""What the data-access processes are started with, written by bootstrap.

gateway1's access list: procs.GATEWAY_ACCESS_OVERLAY points its `U` column at
it - the vendored logins plus the ordinary users of
scripts/torqconfig/permissions/gateway_users.csv.

The `-dataaccess` table list every rdb, hdb and gateway gets
(procs.DATAACCESS_EXTRAS): the tree's tableproperties.csv, keeping only the
tables this runtime's schema holds. TorQ refuses to start an hdb whose list
names a table its database lacks ("Missing table from HDB in schema"), and a
runtime of one profile, or of a bundle's jobs, holds only some of them.
"""

from __future__ import annotations

import csv
from pathlib import Path

from uqs.model.runtime_members import plant_tables
from uqs.paths import UqsPaths

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


def table_properties_lines(paths: UqsPaths) -> list[str]:
    """The tree's table list, with only the tables `paths`' runtime holds;
    [] for a tree without one."""
    source = paths.scripts_dir / TABLE_PROPERTIES
    if not source.is_file():
        return []
    lines = source.read_text().splitlines()
    held = plant_tables(paths.runtime_declaration)
    rows = [ln for ln in lines[1:] if ln.strip()]
    return lines[:1] + [ln for ln in rows if held is None or ln.split(",")[1] in held]


def write(paths: UqsPaths) -> None:
    """Both files, into the runtime's data directory."""
    paths.generated_gateway_access.write_text("\n".join(gateway_access_lines(paths)) + "\n")
    if lines := table_properties_lines(paths):
        (paths.torqdata / TABLE_PROPERTIES.name).write_text("\n".join(lines) + "\n")
