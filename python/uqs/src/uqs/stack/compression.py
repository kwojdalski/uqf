"""The compression policy TorQ's `.cmp` reads (#2152).

The policy is declared in q (src/etl/compression_policy.q, checked by
src/etl/core/compression.q) and rendered into scripts/torqconfig/
compressionconfig.csv by `scripts/generate/derive.py compression-config`, which
runs TorQ's own `.cmp.checkcsv` over it. This copies that file into TORQDATA on
every bootstrap, beside the generated process.csv and reporter.csv.

Not left under scripts/torqconfig for TorQ to find: `.proc.getconfigfile` takes
the first of the application's appconfig, KDBSERVCONFIG and KDBCONFIG that holds
a file, and the starter pack's appconfig has a compressionconfig.csv of its own,
so the generated one would be ignored. scripts/processes/uqs_compression_config.q
points `.cmp.inputcsv` at the copy, loaded before compress.q reads it (the
reporter's and DQE's pattern, stack/reporter.py and stack/dqe.py). The starter
pack's file is never read or written.
"""

from __future__ import annotations

from pathlib import Path

from uqs.paths import UqsPaths
from uqs.stack import generated

#: Where the derived file is committed, under scripts/.
COMPRESSION_CONFIG = Path("torqconfig") / "compressionconfig.csv"

#: What housekeeping1 loads around TorQ's own housekeeping.q, as (before, after)
#: (VENDORED_LOAD_OVERLAY in stack/procs.py): the process `uqs data compress`
#: runs TorQ's .cmp in (stack/compress.py, #2147). TorQ's HDB-maintenance process,
#: running housekeeping.q unchanged, which loads .cmp with TorQ's common code.
#: BEFORE it, uqs_compression_config.q points `.cmp.inputcsv` at this module's
#: copy of the policy. AFTER it: the coverage ledger's reader, as gateway1 loads
#: it, and log.q, which the HDB reload logs through; io_hdb.q, for the HDB write
#: lock the scheduled run holds (.qetl.io.locked, #2176); torq_pipeline.q, for
#: .qtorq.reload_hdb; the wrapper over .cmp; and the survey that scopes a run. The
#: HDB connection the reload needs is housekeeping.LOAD_BEFORE's
#: (uqs_housekeeping_config.q's .qtorq.hk.connect), made when it reloads.
CMP_PROCESS_LOAD = (
    ("${UQF_SCRIPTS}/processes/uqs_compression_config.q",),
    (
        "${UQF_ROOT}/src/etl/core/log.q",
        "${UQF_ROOT}/src/etl/core/status.q",
        "${UQF_ROOT}/src/etl/core/backfill_state.q",
        "${UQF_ROOT}/src/etl/core/io_hdb.q",
        "${UQF_ROOT}/src/etl/core/intervals.q",
        "${UQF_ROOT}/src/etl/core/materialisation.q",
        "${UQF_SCRIPTS}/processes/torq_pipeline.q",
        "${UQF_SCRIPTS}/processes/torq_compress.q",
        "${UQF_SCRIPTS}/processes/torq_compress_survey.q",
    ),
)


def with_cmp(
    load: tuple[tuple[str, ...], tuple[str, ...]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """housekeeping1's other (before, after) overlay, with CMP_PROCESS_LOAD's added -
    each file once: #2150's housekeeping rule loads the policy, log.q, torq_pipeline.q
    and the wrapper before housekeeping.q already, and a file loaded there stays there."""
    before = load[0] + tuple(f for f in CMP_PROCESS_LOAD[0] if f not in load[0])
    after = load[1] + tuple(f for f in CMP_PROCESS_LOAD[1] if f not in before + load[1])
    return (before, after)


def target(paths: UqsPaths) -> Path:
    """The copy `.cmp` is pointed at, beside the other generated files."""
    return paths.torqdata / COMPRESSION_CONFIG.name


def write_compression_config(paths: UqsPaths) -> bool:
    """Copy the derived policy into TORQDATA; False for a tree that has none."""
    source = paths.scripts_dir / COMPRESSION_CONFIG
    if not source.is_file():
        return False
    generated.write_text(target(paths), source.read_text())
    return True
