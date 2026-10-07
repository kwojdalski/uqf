"""gateway1's access list, which bootstrap writes beside the generated config.

procs.GATEWAY_ACCESS_OVERLAY points gateway1's `U` column at it: the vendored
logins plus the ordinary users of scripts/torqconfig/permissions/gateway_users.csv.
"""

from __future__ import annotations

import csv

from uqs.paths import UqsPaths


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
