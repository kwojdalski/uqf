"""What `uqs data compress --report` reads: the HDB's compression, now (#2149).

Nothing here computes a ratio or walks a partition. scripts/gates/
hdb_compression_report.q loads TorQ's own `.cmp` and sort.csv reader and uqf's
wrapper over them (scripts/processes/torq_compress.q), and prints
`.qtorq.compress.report` - per table and column the algorithm, block size and
ratio `-21!` reads, the largest uncompressed partitions, and the partitions not
in the order, or without the attribute, that sort.csv declares. This module
picks the files that script is pointed at and parses what it prints.

WHICH sort.csv. The one the wdb sorts by at end of day: TorQ's
`.proc.getconfigfile` rule, the first config layer that has the file
(stack/source_settings.py). The plant schema (src/etl/plant_tables.q) declares
only `g#sym`, an in-memory index the tickerplant and RDB keep and a partition on
disk does not; the attribute a partition carries is the one sort.csv names.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import source_settings
from uqs.stack.env import with_interpreter

REPORT_SCRIPT = Path("gates") / "hdb_compression_report.q"
SORT_FILE = "sort.csv"
DONE = "DONE"
PREFIX = "report|"


def sort_csv(paths: UqsPaths) -> Path:
    """The sort.csv the stack's wdb reads; refused when no layer has one."""
    found = source_settings.selected(paths, SORT_FILE)
    if found is None:
        searched = ", ".join(str(p) for p in source_settings.layers(paths, SORT_FILE))
        raise UqsError(f"no {SORT_FILE} in any config layer - looked in {searched}")
    return found


def report(paths: UqsPaths, top: int, *, hdb: Path | None = None) -> dict[str, Any]:
    """`.qtorq.compress.report` over the HDB, as a dict of three lists of rows:
    columns, largest_uncompressed and unsorted. Refused when there is no q or
    the report does not finish."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        raise UqsError("the compression report needs q: no interpreter found")
    root = hdb or paths.hdb_dir
    r = subprocess.run(
        [
            str(q),
            str(paths.scripts_dir / REPORT_SCRIPT),
            str(root),
            str(sort_csv(paths)),
            str(top),
            str(paths.torqhome),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=paths.repo_root,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=3600,
    )
    lines = r.stdout.splitlines()
    body = [ln[len(PREFIX) :] for ln in lines if ln.startswith(PREFIX)]
    if DONE not in lines or len(body) != 1:
        said = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise UqsError(f"the compression report did not finish: {' '.join(said)}")
    return json.loads(body[0])
