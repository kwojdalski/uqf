"""`uqs clean`: removing the data directory, or the parts of it a pattern
selects - and saying how much that frees before it does.

Apart from paths.py, which says where the data directory is; this decides
what in it goes. Split out when paths.py passed the module budget taking on
TORQHOME, TORQAPPHOME and UQS_DATA_ROOT (#773).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths

log = get_logger(__name__)


#: One entry `clean` would remove: its path, and the bytes it holds.
CleanTarget = tuple[Path, int]


def _entry_size(entry: Path) -> int:
    """Bytes under `entry`, following no symlinks and raising on nothing.

    A file that vanishes mid-walk (a live stack rotating a log) contributes
    zero rather than failing the whole listing - the size is here to tell an
    operator how much is about to go, not to be an audited total.
    """
    if entry.is_file() or entry.is_symlink():
        try:
            return entry.lstat().st_size
        except OSError:
            return 0
    total = 0
    for child in entry.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.lstat().st_size
        except OSError:
            continue
    return total


def clean_targets(paths: UqsPaths, match: str | None = None) -> list[CleanTarget]:
    """What `clean` would remove, deepest-matching-first, with sizes.

    Without `match` this is the whole data directory as a single entry, which
    is what `clean` has always removed. With one, the tree is walked top-down
    and each path is tested as a POSIX-style path RELATIVE to the data
    directory (`logs`, `logs/out_rdb1.log`), so the pattern reads the way the
    operator sees the tree rather than against an absolute path whose prefix
    is different on every machine.

    A directory that matches is taken whole and not descended into: matching
    `^logs$` means the operator asked for the logs, not for a list of 937
    files that happens to be the same thing. A directory that does not match
    is descended, so `logs/out_rdb1` can be reached without naming `logs`.

    `re.search`, not `re.fullmatch`: `--match logs` should find the logs.
    Anchor with `^`/`$` to be exact.
    """
    root = paths.torqdata
    if not root.exists():
        return []
    if match is None:
        return [(root, _entry_size(root))]
    try:
        pattern = re.compile(match)
    except re.error as exc:
        raise UqsError(f"--match is not a valid regular expression: {exc}") from exc

    found: list[CleanTarget] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            relative = entry.relative_to(root).as_posix()
            if pattern.search(relative):
                found.append((entry, _entry_size(entry)))
            elif entry.is_dir() and not entry.is_symlink():
                walk(entry)

    walk(root)
    return found


def clean(paths: UqsPaths, match: str | None = None, dry_run: bool = False) -> list[CleanTarget]:
    """Remove the data directory, or the parts of it `match` selects.

    Returns what was removed - or, with `dry_run`, what would have been, having
    removed nothing. The caller reports; this decides and acts, so that the
    listing a dry run shows is produced by the same walk that the real
    removal uses and cannot describe a different set.
    """
    targets = clean_targets(paths, match)
    if not targets:
        if match is not None:
            log.info("nothing under {} matches {!r}", paths.torqdata, match)
        else:
            log.info("{} does not exist, nothing to clean", paths.torqdata)
        return []
    for entry, _size in targets:
        if dry_run:
            log.info("would remove {}", entry)
            continue
        log.info("Removing {}", entry)
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)
    return targets
