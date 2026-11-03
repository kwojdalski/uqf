"""A backfill rehearsal's start: the launcher without a bootstrap (#1902).

`uqs job backfill --dry-run` (and `--mode plan`, `--mode validate`) went
through runtime.run_torq_sh, which bootstraps first: on a fresh tree that
wrote 1,094 paths - process.csv, database.q, the DQE copy and filled HDB
partitions - from a command promising to change nothing. A rehearsal starts
its worker against the data directory as the fleet's last start left it
instead, and refuses, naming `uqs start`, what it would have had to write.

Not a scratch data directory: the worker plans from the coverage ledger in
the HDB, and an empty one would rehearse windows a run never fetches.
"""

from __future__ import annotations

import csv
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack import deployment, qtree, runtime, start_policy
from uqs.stack.launcher import torq_launcher

log = get_logger()


def refuse_unbootstrapped(paths: UqsPaths, args: Sequence[str]) -> None:
    """Refuse a start that would have to write the data directory before it
    could run (#1902): one bootstrap has not set up, one naming a process the
    generated process.csv does not list, a flattened tree whose published copy
    is stale, and a deployment set missing what the start reads. Each of these
    is something `run_torq_sh` writes first; a rehearsal must not, so it says
    what is missing instead."""
    procs = start_policy.started_procs(args).split()
    start_it = f"`uqs start` first: it bootstraps {paths.torqdata}"
    if not paths.hdb_dir.is_dir() or not paths.generated_procs.is_file():
        raise UqsError(
            f"{paths.torqdata} is not bootstrapped, and a rehearsal writes nothing to set it "
            f"up - run {start_it}, and a backfill needs the fleet up for discovery anyway"
        )
    with paths.generated_procs.open(newline="") as f:
        listed = {row.get("procname") for row in csv.DictReader(f)}
    if unlisted := [p for p in procs if p != "all" and p not in listed]:
        raise UqsError(
            f"{paths.generated_procs} does not list {', '.join(unlisted)}: it was written before "
            f"{'it' if len(unlisted) == 1 else 'they'} existed, and a rehearsal does not rewrite "
            f"it - run {start_it}"
        )
    if qtree.is_flattened(paths) and not qtree.is_current(paths):
        raise UqsError(
            f"the {paths.runtime} runtime's converted q tree is older than this tree, and a "
            f"rehearsal does not rebuild it - run {start_it}"
        )
    now, planned = deployment.members(paths), deployment.planned(paths, args)
    if now is not None and planned is not None:
        if missing := sorted(set(planned) - set(now) - set(procs)):
            raise UqsError(
                f"the deployment set ({paths.generated_deployment}) leaves out "
                f"{', '.join(missing)}, which this reads, and a rehearsal does not add "
                f"them - start them first (`uqs start {' '.join(missing)}`)"
            )


def run_bootstrapped(
    paths: UqsPaths, args: list[str], base_port: int | None = None
) -> subprocess.CompletedProcess[str]:
    """run_torq_sh for a rehearsal - a backfill's validate, plan or dry run
    (#1902): the launcher against the data directory as the last start
    bootstrapped it, writing nothing there. No bootstrap, no deployment
    record, no q-tree conversion: what one of those would have to write is
    refused by refuse_unbootstrapped instead. Only setenv.sh carries this
    call's own environment (its base port), so it goes in a temporary
    directory that is removed once the launcher returns - torq.sh sources it
    as it starts, and the process it starts inherits the environment, not
    the file.

    What the started process itself writes - its log and its status file
    (src/etl/core/bounded_worker.q) - is the rehearsal's output, not set-up."""
    start_policy.refuse_start(paths, args, base_port)
    args = start_policy.with_publisher_extras(paths, args)
    launcher = torq_launcher(paths)
    refuse_unbootstrapped(paths, args)
    cmd = [str(launcher), *args]
    with tempfile.TemporaryDirectory(prefix="uqs-setenv-") as tmp:
        env = {
            **os.environ,
            **runtime.write_setenv(paths, base_port, target=Path(tmp) / "setenv.sh"),
        }
        log.debug("running (rehearsal, no bootstrap): {}", " ".join(cmd))
        return subprocess.run(cmd, env=env, text=True, check=False)
