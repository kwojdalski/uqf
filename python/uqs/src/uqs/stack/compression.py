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
