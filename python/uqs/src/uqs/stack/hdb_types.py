"""The HDB columns whose type on disk differs from the declared one (#870).

`uqs data check` reports what is missing from the partitions q finds
(stack/hdb_layout.py). A column's TYPE needs q to read, so
scripts/gates/hdb_column_types.q does that part, in exactly those partitions,
against the schema this runtime declares - the one bootstrap writes, built
here directly, so a release checked before it ever started judges the HDB by
its own declarations, not by whatever an earlier release left in the data
directory.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.model.plant_schema import _generated_schema_content
from uqs.paths import UqsError, UqsPaths
from uqs.stack.env import with_interpreter

TYPES_SCRIPT = Path("gates") / "hdb_column_types.q"
DONE = "DONE"


def declared_schema(paths: UqsPaths) -> str:
    """The database.q this runtime uses - the one place that choice is made.

    The bootstrap writes it (stack/runtime.py), `uqs inspect` and the HDB
    migration compare against it, and the runtime report lists its tables.
    """
    if paths.runtime_declaration.overlays:
        return _generated_schema_content(paths)
    return (paths.torqapphome / "database.q").read_text()


def type_changes(
    paths: UqsPaths, schema_file: Path, partitions: Mapping[str, Path]
) -> list[dict[str, str]] | None:
    """Every column stored as another type than `schema_file` declares, in
    `partitions` ({name: its directory}, as hdb_layout.inspect found them);
    None when there is no q to ask. Refused when the check does not finish."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        return None
    name_of = {str(d): name for name, d in partitions.items()}
    with tempfile.TemporaryDirectory() as tmp:
        listed = Path(tmp) / "partitions.txt"
        listed.write_text("".join(f"{d}\n" for d in name_of))
        r = subprocess.run(
            [str(q), str(paths.scripts_dir / TYPES_SCRIPT), str(listed), str(schema_file)],
            capture_output=True,
            text=True,
            check=False,
            cwd=paths.repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=600,
        )
    lines = r.stdout.splitlines()
    if DONE not in lines:
        said = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise UqsError(f"the HDB column type check did not finish: {' '.join(said)}")
    found = []
    for line in lines:
        if line.startswith("type_change|"):
            # from the right: a directory may hold a `|`, a q name never does
            d, table, column, on_disk, declared = line.removeprefix("type_change|").rsplit("|", 4)
            found.append({
                "partition": name_of.get(d, d),
                "table": table,
                "column": column,
                "on_disk": on_disk,
                "declared": declared,
            })  # fmt: skip
    return found
