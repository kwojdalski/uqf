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
from uqs.stack import alive, generated

log = get_logger()

#: The tickerplant's log directory under the data directory: env.py's KDBTPLOG.
#: Today's may be the only copy of data not yet in the HDB, so a `--match` takes
#: it only by naming it (#2742) - `logs` is a substring of `tplogs`.
TPLOG_DIR = "tplogs"

#: How many running processes a refusal names before it says how many more.
_NAMED = 5


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
    Anchor with `^`/`$` to be exact. Except under `tplogs/` (TPLOG_DIR): there
    the match must start at the path's start and cover that whole component,
    so `logs` does not take the tickerplant logs and `^tplogs` or
    `tplogs/tp1` does (#2742).
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

    def selects(relative: str) -> bool:
        hit = pattern.search(relative)
        if hit is None:
            return False
        if relative.split("/", 1)[0] != TPLOG_DIR:
            return True
        return hit.start() == 0 and hit.end() >= len(TPLOG_DIR)

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            relative = entry.relative_to(root).as_posix()
            if selects(relative):
                found.append((entry, _entry_size(entry)))
            elif entry.is_dir() and not entry.is_symlink():
                walk(entry)

    walk(root)
    return found


def refuse_while_running(paths: UqsPaths) -> None:
    """Refuse while any of this stack's processes is up (#2742).

    The ones `uqs stop` would stop (alive.stoppable): started from this
    tree's process file, so another checkout's stack on the same base port
    does not block this one. A running stack is writing the data directory -
    the tickerplant its log, which may be the only copy of today's data not
    yet in the HDB - so removing it under them loses data, and stopping first
    is the way to clean on purpose.
    """
    ours, _elsewhere = alive.stoppable(paths, timeout=10)
    up = sorted(name for name, pids in ours.items() if pids)
    if not up:
        return
    named = ", ".join(up[:_NAMED]) + (f" and {len(up) - _NAMED} more" if len(up) > _NAMED else "")
    raise UqsError(
        f"the stack is running ({named}): clean would remove data its processes are "
        f"writing, the tickerplant log among it. Stop it first (`uqs stop`), then clean"
    )


def clean(paths: UqsPaths, match: str | None = None, dry_run: bool = False) -> list[CleanTarget]:
    """Remove the data directory, or the parts of it `match` selects.

    Returns what was removed - or, with `dry_run`, what would have been, having
    removed nothing. The caller reports; this decides and acts, so that the
    listing a dry run shows is produced by the same walk that the real
    removal uses and cannot describe a different set.

    A real removal holds the stack's lifecycle lock, refusing at once if a
    start, stop or restart holds it, and refuses while the stack runs
    (refuse_while_running): a dry run only lists, so it does neither.
    """
    targets = clean_targets(paths, match)
    if not targets:
        if match is not None:
            log.info("nothing under {} matches {!r}", paths.torqdata, match)
        else:
            log.info("{} does not exist, nothing to clean", paths.torqdata)
        return []
    if dry_run:
        return targets  # the caller lists them: logged too, each was shown twice (#1880)
    with generated.lifecycle_lock(paths, wait=0):
        refuse_while_running(paths)
        for entry, _size in targets:
            log.info("Removing {}", entry)
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
    return targets
