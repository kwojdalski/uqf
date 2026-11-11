"""The HDB partition filler's subprocess boundary: run
scripts/hdb/fill.q over the runtime's HDB, and never let it
fail or stall a bootstrap. Split from stack/runtime.py, which every `uqs`
command bootstraps through and which calls this on each one."""

from __future__ import annotations

import subprocess
from pathlib import Path

from uqs.interpreter import q_command, q_interpreter
from uqs.logger import get_logger
from uqs.paths import UqsPaths
from uqs.stack import runs
from uqs.stack.env import with_interpreter

log = get_logger()


#: The q script that writes an empty table into a partition that lacks it,
#: and a declared column into a table that lacks it. q rather than Python
#: because both have to be written with their schema and enumerated against
#: the HDB's sym file; a directory of the right name is not a table, a file
#: of the right name is not a column. Loads KX's dbmaint.q (lib/kx-dbmaint).
FILL_HDB_SCRIPT = "hdb/fill.q"

#: How long the filler may run before bootstrap gives up on it, in seconds.
#: Generous, because a real repair writes one column file per partition per
#: missing column and a long history can take minutes; bounded, because every
#: `uqs` command bootstraps and a q that never exits must not stall them all.
FILL_HDB_TIMEOUT_S = 600


#: The one switch the script takes after the HDB and the schema: fold the markout
#: configuration columns into snapshots (#1999). Only `uqs data check --apply`, which asks
#: first, passes it; the bootstrap fill never deletes a column.
MIGRATE_ARG = "migrate"


def fill_hdb_partitions(paths: UqsPaths, *, migrate: bool = False, hdb: Path | None = None) -> bool:
    """Write an empty copy of every declared table into every partition
    that lacks one, and every declared column into every table that lacks
    one. Returns whether the filler ran at all.

    Idempotent and additive: a table directory or column file that exists
    is never touched, and a table or column a partition holds that
    database.q no longer declares is left alone - that is history, and
    deleting history is not this function's business. A column whose
    declared TYPE changed is reported by `uqs data check`, not
    repaired here.

    Two exceptions, and only with ``migrate``. (#1999) The three configuration
    columns a markout row used to repeat (contract_version, rate_timing,
    report_ccy) are folded into legacy configuration snapshots, recorded in the
    status directory, a ``config_id`` column names each row's, and the three
    columns are dropped. (#2288) market_data's older partitions get the flat
    level-0 top (``bid``, ``ask``) derived from their ladders, are rewritten sorted
    by sym, source, source_time with p# on sym, and get their row numbers
    (``rid``) - what a markout's as-of join against the mapped partition needs.
    ``uqs data check --apply`` passes it after asking; the bootstrap does not, and
    leaves an existing partition as it was.

    `hdb` is another HDB root to fill from this runtime's schema (`data seed
    --hdb DIR`, #2237); None, this runtime's own.

    NEVER FAILS A BOOTSTRAP. Every `uqs` command bootstraps, so a
    problem here - no q on the path, an HDB mid-write, a permissions
    fault - must not stop an operator stopping the stack or reading a log.
    It is reported and stepped over; `uqs data check` says the same
    thing on demand, and the smoke lane says it about a running stack.
    """
    hdb_root = hdb if hdb is not None else paths.hdb_dir
    if not hdb_root.is_dir():
        return False
    script = paths.scripts_dir / FILL_HDB_SCRIPT
    if not script.is_file():  # pragma: no cover - a broken checkout
        log.warning("HDB partition filler not found at {}", script)
        return False
    # the snapshot store lives in the status directory, which the filler reads from here
    env = {**with_interpreter(paths), "UQF_STATUS_DIR": str(runs.status_dir(paths))}
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
                *([MIGRATE_ARG] if migrate else []),
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
            "spanning every table may fail until `uqs data check --apply` is run",
            FILL_HDB_TIMEOUT_S,
        )
        return False
    if result.returncode != 0:
        log.warning(
            "could not fill HDB partitions ({}) - a query spanning every table "
            "may fail until `uqs data check` is run: {}",
            result.returncode,
            (result.stderr or result.stdout).strip()[:300],
        )
        return False
    for line in result.stdout.splitlines():
        # "added" too, or the column half of the repair happens silently and
        # an operator reading the log believes the database was untouched; and
        # "dropped" and the legacy snapshots, which the markout migration reports
        # (#1999), and "rewritten", a partition put in its declared order (#2288) - a
        # deleted column or a rewritten partition must never be silent.
        if any(
            word in line
            for word in ("wrote", "filled", "added", "dropped", "legacy snapshot", "rewritten")
        ):
            log.info("hdb: {}", line.strip())
    return True
