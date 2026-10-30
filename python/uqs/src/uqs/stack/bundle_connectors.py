"""A sidecar bundle's connectors: its Python, installed with its q (#1776).

A bundle may carry the process outside the plant that feeds its raw table - a
`NAME_feed.py` declaring `FEED = ExternalFeed(...)` (external/feeds.py) and
whatever that starts beside itself (`NAME_streamer.py`). Every `*.py` in the
bundle's folder but its `test_*.py` is one.

INSTALLED THE WAY ITS q IS, FROM THE SAME SNAPSHOT. The files are copied into
src/etl/connectors/<bundle>/ and listed in the bundle's ledger entry under
`connectors`, and only those copies are ever imported - never the bundle's own
folder. So a connector and the job, table and schema it feeds are always one
version: a streamer that writes columns by position, run against a table from
another version of the bundle, would put prices into a size column without an
error. Editing a bundle changes nothing until it is reinstalled; reinstalling
replaces the copies, and a release carries them as it carries the q files.

Checked when the bundle is planned, before anything is written: a feed's name
must not be one uqs or another installed bundle already declares, and every
table it says it `produces` must be one the bundle's own tables.q defines.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from uqs.model.connectors import CONNECTOR_DIR, bundle_connectors, feed_names, feed_produces
from uqs.paths import INSTALLED_CONNECTORS
from uqs.stack.bundle_blocks import BundleError


def sources(folder: Path) -> list[Path]:
    """The bundle's connector files: every top-level `*.py` but its tests."""
    return sorted(p for p in folder.glob("*.py") if not p.name.startswith("test_"))


def check(name: str, folder: Path, root: Path, tables: list[str]) -> list[Path]:
    """The connector files of bundle `name` at `folder`, each feed module checked
    against the tree at `root` - or BundleError, naming the file."""
    files = sources(folder)
    taken: dict[str, str] = {}
    for path in sorted((root / CONNECTOR_DIR).glob("*_feed.py")):
        taken |= dict.fromkeys(feed_names(path), "uqs")
    for key, path in bundle_connectors(root).items():
        owner = key.split("/")[0]
        if owner != name and path.is_file():
            try:
                taken |= dict.fromkeys(feed_names(path), f"bundle {owner}")
            except OSError, ValueError, SyntaxError:
                continue  # that bundle's own fault, reported when it is discovered
    for path in (p for p in files if p.name.endswith("_feed.py")):
        where = f"{name}/{path.name}"
        try:
            names, produced = feed_names(path), feed_produces(path)
        except (ValueError, SyntaxError) as exc:
            raise BundleError(f"{where}: {exc}") from None
        if not names:
            raise BundleError(f"{where} declares no feed: a *_feed.py declares FEED or FEEDS")
        for feed in names:
            if feed in taken:
                raise BundleError(f"{where}: connector {feed!r} is already {taken[feed]}'s")
        if strays := sorted(set(produced) - set(tables)):
            raise BundleError(
                f"{where}: produces {', '.join(strays)}, which the bundle's tables.q does not "
                "define - a connector publishes only onto its own bundle's tables"
            )
    return files


def install(name: str, files: list[Path], root: Path, previous: list[str]) -> list[str]:
    """Copy `files` into the tree, replacing what this bundle's last install put
    there (`previous`, from its ledger entry); return their paths in the tree."""
    for rel in previous:
        (root / rel).unlink(missing_ok=True)
    target = root / INSTALLED_CONNECTORS / name
    if files:
        target.mkdir(parents=True, exist_ok=True)
    installed = []
    for source in files:
        shutil.copy2(source, target / source.name)
        installed.append(str(INSTALLED_CONNECTORS / name / source.name))
    shutil.rmtree(target / "__pycache__", ignore_errors=True)
    if target.is_dir() and not any(target.iterdir()):
        target.rmdir()
    return installed
