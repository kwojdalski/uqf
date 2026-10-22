"""What a bundle may not shadow in the tree it installs into (#1545).

Split from stack/bundles.py, which holds the plan these checks refuse inside.
"""

from __future__ import annotations

import re
from pathlib import Path

from uqs.model.load_plan import load_plan
from uqs.paths import UqsError, paths_for_root
from uqs.stack.bundle_blocks import BundleError
from uqs.stack.install import Item

#: `name:([]...)` as bundles.py reads a plant table.
_TABLE = re.compile(r"^(?:\.qetl\.plant\.)?([a-z_][a-z0-9_]*):\(\[\]")


def known_tables(root: Path, tree_text: str) -> set[str]:
    """The plant tables `tree_text` defines plus those of the TorQ starter pack's
    database.q (quote, trade...), which a bundle table of the same name would
    redefine at the next load. The pack adds none when absent (no lib/)."""
    path = paths_for_root(root).torqapphome / "database.q"
    text = tree_text + (path.read_text() if path.is_file() else "")
    return {m.group(1) for ln in text.splitlines() if (m := _TABLE.match(ln))}


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
