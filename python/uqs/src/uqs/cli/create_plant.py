"""What the plant carries, read from the tree for `uqs job new`: every table's
definition and every table's name - this tree's own (src/etl/plant_tables.q,
or a bundle's planning root) and the vendored starter pack's."""

from __future__ import annotations

from pathlib import Path

from uqs.cli.create_backfill import defined_tables
from uqs.model.schemas import _DEFINITION
from uqs.paths import TABLES_FILE, UqsPaths


def plant_definitions(paths: UqsPaths, root: Path | None = None) -> dict[str, str]:
    """{table: its one-line `name:([]...)` definition}, the tree at `root`'s (default:
    the tree's) and the vendored starter pack's - what a normalizer's sources are read from."""
    out: dict[str, str] = {}
    for path in (paths.torqapphome / "database.q", (root or paths.repo_root) / TABLES_FILE):
        if path.is_file():
            out.update((m.group(1), m.group(0)) for m in _DEFINITION.finditer(path.read_text()))
    return out


def plant_tables(paths: UqsPaths, root: Path | None = None) -> set[str]:
    """Every table the plant carries: the tree at `root`'s (default: the tree's), plus
    the vendored starter pack's (`quote`, `trade`), which the generated database.q merges in."""
    vendored = paths.torqapphome / "database.q"
    theirs = (
        {m.group(1) for m in _DEFINITION.finditer(vendored.read_text())}
        if vendored.is_file()
        else set()
    )
    return defined_tables(root or paths.repo_root) | theirs
