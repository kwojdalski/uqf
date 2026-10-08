"""A release never ships an operator's prepared bundle as tree content (#881)."""

from __future__ import annotations

from pathlib import Path

from uqs.deploy.artifact import ReleaseError


def refuse(root: Path, files: list[str], runtime: str, composition: set) -> None:
    """Refuse a tree whose tracked files carry a bundle block from outside
    this release's composition (#881).

    `uqs runtime prepare` installs a bundle by writing a `/ BEGIN bundle`
    block into tracked files (plant_tables.q, uqs_catalog.q). A release is
    built from the working tree, so - with --allow-dirty, or after a commit
    nothing refused - every bundle prepared locally ships: its tables count
    as the tree's own in every runtime's schema, with no job files and no
    ledger entry. A block of the composition itself is re-installed by the
    build and is no harm; any other is refused, naming the file."""
    from uqs.stack.bundle_blocks import block_names

    foreign = []
    for rel in files:
        if not rel.endswith(".q"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        foreign += [f"{rel}: bundle {n}" for n in block_names(text) if n not in composition]
    if foreign:
        raise ReleaseError(
            "bundle",
            f"the tree carries bundle blocks outside runtime {runtime}'s composition, "
            "which would ship as tree content:\n  "
            + "\n  ".join(foreign)
            + "\nBuild from a checkout no bundle was prepared into, or uninstall them first.",
        )
