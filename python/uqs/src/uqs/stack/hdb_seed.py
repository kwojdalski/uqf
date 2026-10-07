"""Seeding one runtime's HDB from another's: `uqs data seed` (#766).

Each runtime keeps its own HDB, and a fresh one holds only the starter pack's
two sample partitions. Comparing torq against uqf on the same history means
copying uqf's partitions of the tables both declare. The copying is
scripts/gates/seed_hdb_partitions.q, which re-enumerates every symbol column
against the target's sym file - a plain file copy would decode, in the
target, to whatever its sym file holds at the source's indices.
"""

from __future__ import annotations

import subprocess
from datetime import date

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack.runtime import fill_hdb_partitions

#: The q that copies, relative to the scripts directory.
SEED_SCRIPT = "gates/seed_hdb_partitions.q"


def seed(
    target: UqsPaths,
    source: UqsPaths,
    tables: list[str] | None = None,
    first: date | None = None,
    last: date | None = None,
    overwrite: bool = False,
) -> str:
    """Copy `source`'s HDB partitions of `tables` (every table the target
    declares when None), dated `first`..`last` inclusive, into `target`'s.

    The target must be bootstrapped (its schema written); the source is only
    read. Returns the script's report; raises naming the table and column
    when a schema differs, with nothing written. Then fills the target's
    partitions, so a table the source lacked a column of is rectangular.
    """
    if target.runtime == source.runtime:
        raise UqsError(f"--from {source.runtime} is the runtime being seeded - name another")
    src_hdb, dst_hdb = source.hdb_dir, target.hdb_dir
    if not src_hdb.is_dir():
        raise UqsError(
            f"the {source.runtime} runtime has no HDB at {src_hdb} - start it once, "
            "or backfill into it, before seeding from it"
        )
    if not (dst_hdb.is_dir() and target.generated_schema.is_file()):
        raise UqsError(f"the {target.runtime} runtime is not bootstrapped: no {dst_hdb}")
    if first is not None and last is not None and first > last:
        raise UqsError(f"--dates {first}..{last} is empty: it starts after it ends")
    q = q_interpreter()
    if q is None:
        raise UqsError("seeding needs q, and no interpreter is runnable - set QCMD")
    argv = [
        str(q),
        str(target.scripts_dir / SEED_SCRIPT),
        str(src_hdb),
        str(dst_hdb),
        str(target.generated_schema),
        ",".join(tables) if tables else "-",
        first.strftime("%Y.%m.%d") if first else "-",
        last.strftime("%Y.%m.%d") if last else "-",
        "1" if overwrite else "0",
    ]
    result = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=target.repo_root)
    if result.returncode != 0:
        why = (result.stderr or result.stdout).strip().splitlines()
        raise UqsError(why[-1] if why else f"seeding failed (exit {result.returncode})")
    fill_hdb_partitions(target)
    return result.stdout.strip()
