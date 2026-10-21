"""The HDB partition filler's subprocess boundary: run
scripts/gates/fill_hdb_partitions.q over the runtime's HDB, and never let it
fail or stall a bootstrap. Split from stack/runtime.py, which every `uqs`
command bootstraps through and which calls this on each one."""

from __future__ import annotations

import subprocess

from uqs.interpreter import q_command, q_interpreter
from uqs.logger import get_logger
from uqs.paths import UqsPaths
from uqs.stack.env import with_interpreter

log = get_logger(__name__)


#: The q script that writes an empty table into a partition that lacks it,
#: and a declared column into a table that lacks it. q rather than Python
#: because both have to be written with their schema and enumerated against
#: the HDB's sym file; a directory of the right name is not a table, a file
#: of the right name is not a column. Loads KX's dbmaint.q (lib/kx-dbmaint).
FILL_HDB_SCRIPT = "gates/fill_hdb_partitions.q"

#: How long the filler may run before bootstrap gives up on it, in seconds.
#: Generous, because a real repair writes one column file per partition per
#: missing column and a long history can take minutes; bounded, because every
#: `uqs` command bootstraps and a q that never exits must not stall them all.
FILL_HDB_TIMEOUT_S = 600


def fill_hdb_partitions(paths: UqsPaths) -> bool:
    """Write an empty copy of every declared table into every partition
    that lacks one, and every declared column into every table that lacks
    one. Returns whether the filler ran at all.

    Idempotent and additive: a table directory or column file that exists
    is never touched, and a table or column a partition holds that
    database.q no longer declares is left alone - that is history, and
    deleting history is not this function's business. A column whose
    declared TYPE changed is reported by `uqs data hdb-check`, not
    repaired here.

    NEVER FAILS A BOOTSTRAP. Every `uqs` command bootstraps, so a
    problem here - no q on the path, an HDB mid-write, a permissions
    fault - must not stop an operator stopping the stack or reading a log.
    It is reported and stepped over; `uqs data hdb-check` says the same
    thing on demand, and the smoke lane says it about a running stack.
    """
    hdb_root = paths.hdb_dir
    if not hdb_root.is_dir():
        return False
    script = paths.scripts_dir / FILL_HDB_SCRIPT
    if not script.is_file():  # pragma: no cover - a broken checkout
        log.warning("HDB partition filler not found at {}", script)
        return False
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        log.warning(
            "HDB partition filler skipped: no q interpreter ({!r} is not runnable)",
            q_command(env),
        )
        return False
    # stdin closed: q that signals while loading a script waits on stdin, so
    # an inherited terminal would hang the bootstrap silently (#1513).
    try:
        result = subprocess.run(
            [
                str(q),
                str(script),
                str(hdb_root),
                str(paths.generated_schema),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            cwd=paths.repo_root,
            timeout=FILL_HDB_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "HDB partition filler did not finish in {}s and was stopped - a query "
            "spanning every table may fail until `uqs data hdb-check --fix` is run",
            FILL_HDB_TIMEOUT_S,
        )
        return False
    if result.returncode != 0:
        log.warning(
            "could not fill HDB partitions ({}) - a query spanning every table "
            "may fail until `uqs data hdb-check` is run: {}",
            result.returncode,
            (result.stderr or result.stdout).strip()[:300],
        )
        return False
    for line in result.stdout.splitlines():
        # "added" too, or the column half of the repair happens silently and
        # an operator reading the log believes the database was untouched.
        if "wrote" in line or "filled" in line or "added" in line:
            log.info("hdb: {}", line.strip())
    return True
