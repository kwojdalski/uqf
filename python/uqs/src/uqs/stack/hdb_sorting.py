"""The HDB partitions that are not in their declared sort order (#2288).

`uqs data check` reports what is missing from the partitions q finds
(stack/hdb_layout.py) and which columns are stored as another type
(stack/hdb_types.py). A table that declares a sort rule - market_data is stored
sorted by sym, source, source_time with p# on sym, so a markout's as-of join
binary-searches the memory-mapped columns instead of copying them - has a third
way to be wrong: its rows are in another order, or lack the attribute, or its
row numbers (`rid`) do not hold each row's position. Only q can read the
columns, so scripts/hdb/sort_order.q does that part, in exactly those
partitions, against the plant's own declarations.

Dry: it reads and reports. The migration that fixes what it finds is
scripts/hdb/fill.q's, run by `uqs data check --apply`, after
asking.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack.env import with_interpreter

SORT_SCRIPT = Path("hdb") / "sort_order.q"
DONE = "DONE"
PREFIX = "unsorted|"


def unsorted(
    paths: UqsPaths, hdb_root: Path, partitions: Mapping[str, Path]
) -> list[dict[str, str]] | None:
    """Every table directory of `partitions` ({name: its directory}, as
    hdb_layout.inspect found them) that is not in the order its table declares:
    {"partition", "table", "why"}. None when there is no q to ask. Refused when
    the check does not finish."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        return None
    name_of = {str(d): name for name, d in partitions.items()}
    with tempfile.TemporaryDirectory() as tmp:
        listed = Path(tmp) / "partitions.txt"
        listed.write_text("".join(f"{d}\n" for d in name_of))
        r = subprocess.run(
            [str(q), str(paths.scripts_dir / SORT_SCRIPT), str(hdb_root), str(listed)],
            capture_output=True,
            text=True,
            check=False,
            cwd=paths.repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=3600,
        )
    lines = r.stdout.splitlines()
    if DONE not in lines:
        said = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise UqsError(f"the HDB sort-order check did not finish: {' '.join(said)}")
    found = []
    for line in lines:
        if line.startswith(PREFIX):
            # from the right: a directory may hold a `|`, a q name and the reason never do
            d, table, why = line.removeprefix(PREFIX).rsplit("|", 2)
            found.append({"partition": name_of.get(d, d), "table": table, "why": why})
    return found
