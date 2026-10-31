"""What the plant carries, for `uqs job new`: every table's definition and
every table's name - this tree's own (src/etl/plant_tables.q, or a bundle's
planning root) and the vendored starter pack's - as q evaluated them (#1781)."""

from __future__ import annotations

from pathlib import Path

from uqs.model import plant_metadata
from uqs.paths import UqsPaths


def _tables(paths: UqsPaths, root: Path | None) -> dict[str, plant_metadata.Table]:
    meta = plant_metadata.load(root or paths.repo_root)
    return {**meta.vendored(paths.torqapphome / "database.q"), **meta.own()}


def plant_definitions(paths: UqsPaths, root: Path | None = None) -> dict[str, str]:
    """{table: its `name:([]...)` definition}, the tree at `root`'s (default:
    the tree's) and the vendored starter pack's - what a normalizer's sources are read from."""
    return {name: t.definition() for name, t in _tables(paths, root).items()}


def plant_tables(paths: UqsPaths, root: Path | None = None) -> set[str]:
    """Every table the plant carries: the tree at `root`'s (default: the tree's), plus
    the vendored starter pack's (`quote`, `trade`), which the generated database.q merges in."""
    return set(_tables(paths, root))
