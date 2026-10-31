"""What a bundle may not shadow in the tree it installs into (#1545).

Split from stack/bundles.py, which holds the plan these checks refuse inside.
"""

from __future__ import annotations

from pathlib import Path

from uqs.model import plant_metadata
from uqs.model.load_plan import load_plan
from uqs.paths import UqsError, paths_for_root
from uqs.stack.bundle_blocks import BundleError
from uqs.stack.install import Item


def known_tables(root: Path, bundle: str) -> set[str]:
    """The plant tables the tree at `root` defines outside `bundle`'s own block -
    every one but those its ledger entry says it installed - plus those of the
    TorQ starter pack's database.q (quote, trade...), which a bundle table of the
    same name would redefine at the next load. The pack adds none when absent.

    The tree's side as q evaluated it (#1781): trusted code, already installed.
    The bundle's own tables.q is read as text by bundles.table_lines, never run."""
    from uqs.stack.bundles import read_ledger

    try:
        meta = plant_metadata.load(root)
        theirs = meta.vendored(paths_for_root(root).torqapphome / "database.q")
    except plant_metadata.SchemaMetadataError as exc:
        raise BundleError(str(exc)) from None
    mine = set(read_ledger(root).get(bundle, {}).get("tables", []))
    return (set(meta.own()) - mine) | set(theirs)


def evaluate_tables(root: Path, bundle: str) -> None:
    """After `bundle` is installed: q evaluates the tree's plant tables again
    (#1781), so the next plan, and every reader, sees what it installed. A tree
    that carries no schema metadata at all (a stand-in) is left as it is."""
    from uqs.model import plant_metadata_gen

    try:
        plant_metadata_gen.ensure(root, create=False)
    except plant_metadata.SchemaMetadataError as exc:
        raise BundleError(f"bundle {bundle} is installed, but {exc}") from None


def refuse(root: Path, items: list[Item], stale: list[Path]) -> None:
    """Refuse a bundle file defining a source or transform the tree (or another
    of its own files) already defines (#1545). q upserts on a second define,
    so which one runs would depend on file order and on whether the whole tree
    or one worker loads. The load plan is read with the bundle's files in
    place of what is on disk, and its own stale files gone."""
    extra = {
        i.destination.relative_to(root).as_posix(): i.source.read_text()
        for i in items
        if i.destination
    }
    extra.update({p.relative_to(root).as_posix(): "" for p in stale})
    try:
        load_plan(root, extra)
    except UqsError as exc:
        raise BundleError(str(exc)) from None
