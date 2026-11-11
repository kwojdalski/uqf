"""Is this checkout runnable as it stands: the data directory where this
release looks for it, and TorQ, its starter pack and the tools torq.sh needs
where UqsPaths says they are. Refused, naming the fix, before anything starts.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from uqs.paths import TORQAPPHOME_ENV, TORQHOME_ENV, UqsError, UqsPaths
from uqs.runtimes import DEFAULT_RUNTIME

#: Where the data directory has lived before, newest first, relative to the
#: repository root. Kept only so the guard below can recognise them; nothing
#: reads from them.
_FORMER_DATA_DIRS = (
    Path("scripts") / "output" / "uqs",
    Path("scripts") / "output" / "uqf-stack",
)


def check_data_dir_was_migrated(paths: UqsPaths) -> None:
    """Refuse to run against a fresh data directory while an old one exists.

    The data directory holds the HDB, the tickerplant logs and the write-down
    database - 6.5GB of it on the machine this was written on - and it has
    moved twice: renamed with the package (`scripts/output/uqf-stack` to
    `scripts/output/uqs`), then out of `scripts/` into `output/uqs`. Either
    move changes where every process looks, and nothing would have ERRORED:
    bootstrap regenerates process.csv and database.q on every command, so
    the stack would have started cleanly
    against an empty HDB and every historical query would have returned no
    rows. A silent empty answer is the worst failure this tree has, and it is
    the one a rename of a data path produces by default.

    So: old present and new absent means the move has not happened, and that
    is refused with the command that does it. Both present is not refused -
    someone may have copied rather than moved, and choosing for them would be
    worse than letting them proceed.

    THIS BLOCKS `stop` TOO, AND THAT IS DELIBERATE. `stop` reaches here
    through `bootstrap`, and exempting it would be worse than the
    inconvenience: `process.csv` lives INSIDE the data directory, so a
    bootstrap allowed through would create the new directory, both would then
    exist, this check would never fire again, and the old HDB would be
    orphaned in silence - the exact outcome it exists to prevent.

    Which is why the message must not say "stop the stack first": that is an
    order this function makes impossible. The move needs no downtime. Every
    location is inside the one checkout, so `mv` is a rename on one filesystem
    - inodes are unchanged and every open file descriptor follows - and a
    restart afterwards is for reopening at the new path, not for safety.
    """
    if paths.torqdata.exists() or paths.runtime != DEFAULT_RUNTIME:  # former: default's only
        return
    for relative in _FORMER_DATA_DIRS:
        former = paths.repo_root / relative
        if not former.is_dir():
            continue
        raise UqsError(
            f"{former} exists but {paths.torqdata} does not: the data directory "
            f"has moved, and nothing has moved it yet.\n\n"
            f"Run this now - it is safe with the stack up, because both paths are "
            f"on one filesystem, so it is a rename and every running process keeps "
            f"the files it already has open:\n"
            f"    mkdir -p {paths.torqdata.parent} && mv {former} {paths.torqdata}\n\n"
            f"Then restart the stack when convenient, so each process reopens at "
            f"the new path.\n\n"
            f"Skipping this would not fail - the stack would start against an "
            f"empty HDB and every historical query would return no rows."
        )


def check_prerequisites(paths: UqsPaths) -> None:
    check_data_dir_was_migrated(paths)
    if not (paths.torqhome / "torq.q").is_file():
        raise UqsError(
            f"{paths.torqhome} not found or missing torq.q - is lib/torq vendored, "
            f"or {TORQHOME_ENV} set to a TorQ installation?"
        )
    if not (paths.torqapphome / "database.q").is_file():
        raise UqsError(
            f"{paths.torqapphome} not found or missing database.q - "
            f"is lib/torq-finance-starter-pack vendored, or {TORQAPPHOME_ENV} set to one?"
        )
    if shutil.which("envsubst") is None:
        raise UqsError(
            "'envsubst' not found on PATH - torq.sh needs it (macOS: brew install gettext)"
        )
