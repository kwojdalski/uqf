"""The HDB columns whose type on disk differs from the declared one (#870).

`uqs data hdb-check` reports what is missing in Python, from the filesystem.
A column's TYPE needs q to read, so scripts/gates/hdb_column_types.q does
that part, against the schema this runtime declares - the one bootstrap
writes, built here directly, so a release checked before it ever started
judges the HDB by its own declarations, not by whatever an earlier release
left in the data directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.model.plant_schema import _generated_schema_content
from uqs.paths import UqsError, UqsPaths
from uqs.stack.env import with_interpreter

TYPES_SCRIPT = Path("gates") / "hdb_column_types.q"
DONE = "DONE"


def declared_schema(paths: UqsPaths) -> str:
    """The database.q this runtime's bootstrap writes (stack/runtime.py)."""
    if paths.runtime_declaration.overlays:
        return _generated_schema_content(paths)
    return (paths.torqapphome / "database.q").read_text()


def type_changes(paths: UqsPaths, schema_file: Path) -> list[dict[str, str]] | None:
    """Every column stored as another type than `schema_file` declares; None
    when there is no q to ask. Refused when the check does not finish."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        return None
    r = subprocess.run(
        [str(q), str(paths.scripts_dir / TYPES_SCRIPT), str(paths.hdb_dir), str(schema_file)],
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
    keys = ("partition", "table", "column", "on_disk", "declared")
    return [
        dict(zip(keys, line.split("|")[1:], strict=True))
        for line in lines
        if line.startswith("type_change|")
    ]
