"""Installing jobs kept outside the tree - a "sidecar" folder - into it.

src/etl/init.q loads every .q file under src/etl/sources, src/etl/workers and
src/etl/streaming, in that order, and the registry reads the same folders. So
putting a file in the right one of those three IS the installation; nothing
has to be listed anywhere. What this adds is getting the folder right without
the operator having to know which file is which, and refusing the cases that
would leave the tree unable to load.

WHICH FOLDER is decided by what a file declares, never by its name or where
it sat in the sidecar, so a sidecar can be laid out any way its owner likes:

    .qsrc.register                  a source     -> src/etl/sources
    .qbw.define                     a worker     -> src/etl/workers
    .qstream.register, .qnorm.define a streaming job -> src/etl/streaming

A file declaring more than one kind is refused: the directories load in a
fixed order - a worker's source must already exist when the worker defines
itself - and one file cannot be in two of them.

COPY OR SYMLINK. A copy is a snapshot the tree owns. A symlink keeps the
sidecar the source of truth, so an edit there is live on the next load - and
so moving or deleting the sidecar breaks the tree. Both are ordinary files to
q's `\\l` and to the Python registry, which follow links.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from uqs.model.declarations import declaration_calls, strip_q_comments
from uqs.paths import SOURCE_DIR, STREAM_DIR, WORKER_DIR

#: A source registers itself through .qsrc.register, whose name argument is
#: usually a variable (`source_name`) rather than a literal - so a source is
#: recognised by the call, not by parsing the name out of it.
_SOURCE_CALL = re.compile(r"\.qsrc\.register\[")

#: Files a sidecar may carry that are not jobs: its tests. Recognised and
#: reported, never installed - a q test runs only once its namespace is listed
#: in tests/run_tests.q, which is a decision, not a copy.
_TEST_FILE = re.compile(r"^test_.*\.q$")


class Kind(StrEnum):
    SOURCE = "source"
    WORKER = "worker"
    STREAMING = "streaming job"


#: Where each kind goes, relative to the repository root.
DESTINATIONS: dict[Kind, Path] = {
    Kind.SOURCE: SOURCE_DIR,
    Kind.WORKER: WORKER_DIR,
    Kind.STREAMING: STREAM_DIR,
}


class Mode(StrEnum):
    COPY = "copy"
    SYMLINK = "symlink"


class Status(StrEnum):
    NEW = "new"  # nothing there yet
    SAME = "already installed"  # identical content, or a link to this file
    CONFLICT = "conflict"  # a different file of that name is there
    SKIPPED = "skipped"  # not a job file; see `reason`


@dataclass(frozen=True)
class Item:
    """One sidecar file, and what installing it would do."""

    source: Path
    kind: Kind | None
    names: tuple[str, ...]
    destination: Path | None
    status: Status
    reason: str = ""


def classify(text: str) -> tuple[set[Kind], tuple[str, ...]]:
    """The kinds a q file declares, and the job or worker names it declares."""
    kinds: set[Kind] = set()
    names: list[str] = []
    for fn, name, _fields in declaration_calls(text):
        kinds.add(Kind.WORKER if fn == "qbw.define" else Kind.STREAMING)
        names.append(name)
    if _SOURCE_CALL.search(strip_q_comments(text)):
        kinds.add(Kind.SOURCE)
    return kinds, tuple(names)


def _status(source: Path, destination: Path) -> Status:
    if not destination.exists() and not destination.is_symlink():
        return Status.NEW
    if destination.is_symlink() and destination.resolve() == source.resolve():
        return Status.SAME
    if destination.is_file() and filecmp.cmp(source, destination, shallow=False):
        return Status.SAME
    return Status.CONFLICT


def _declared_in_tree(repo_root: Path) -> dict[str, Path]:
    """{job or worker name: the tree file declaring it}. q refuses a second
    declaration of a name, so a sidecar file re-declaring one under another
    filename is a tree that no longer loads - caught here instead."""
    found: dict[str, Path] = {}
    for directory in (STREAM_DIR, WORKER_DIR):
        for path in sorted((repo_root / directory).glob("*.q")):
            for _fn, name, _fields in declaration_calls(path.read_text(errors="replace")):
                found.setdefault(name, path)
    return found


def plan(sidecar: Path, repo_root: Path) -> list[Item]:
    """What installing every .q file under `sidecar` would do, file by file.

    Walks the whole folder, so a sidecar laid out as sources/ workers/
    streaming/ and one that keeps everything flat are the same to it.
    """
    if not sidecar.is_dir():
        raise FileNotFoundError(f"{sidecar} is not a directory")
    in_tree = _declared_in_tree(repo_root)
    claimed: dict[Path, Path] = {}  # destination -> the sidecar file taking it
    items: list[Item] = []
    for path in sorted(sidecar.rglob("*.q")):
        if _TEST_FILE.match(path.name):
            items.append(Item(path, None, (), None, Status.SKIPPED, "a q test: see the next steps"))
            continue
        kinds, names = classify(path.read_text(errors="replace"))
        if not kinds:
            items.append(
                Item(path, None, (), None, Status.SKIPPED, "declares no source, worker or job")
            )
            continue
        if len(kinds) > 1:
            listed = " and ".join(sorted(k.value for k in kinds))
            items.append(
                Item(
                    path,
                    None,
                    names,
                    None,
                    Status.SKIPPED,
                    f"declares a {listed}; split it, one kind per file",
                )
            )
            continue
        kind = kinds.pop()
        destination = repo_root / DESTINATIONS[kind] / path.name
        if destination in claimed:
            reason = f"same filename as {claimed[destination].relative_to(sidecar)}"
            items.append(Item(path, kind, names, None, Status.SKIPPED, reason))
            continue
        clash = next((n for n in names if n in in_tree and in_tree[n] != destination), None)
        if clash is not None:
            reason = f"`{clash} is already declared by {in_tree[clash].relative_to(repo_root)}"
            items.append(Item(path, kind, names, None, Status.SKIPPED, reason))
            continue
        claimed[destination] = path
        items.append(Item(path, kind, names, destination, _status(path, destination)))
    return items


def install(item: Item, mode: Mode, *, overwrite: bool = False) -> None:
    """Put one planned file in place. A conflict needs `overwrite`."""
    if item.destination is None or item.status is Status.SKIPPED:
        raise ValueError(f"{item.source} is not installable: {item.reason}")
    if item.status is Status.SAME:
        return
    if item.status is Status.CONFLICT:
        if not overwrite:
            raise FileExistsError(f"{item.destination} exists and differs")
        item.destination.unlink()
    if mode is Mode.SYMLINK:
        os.symlink(item.source.resolve(), item.destination)
    else:
        shutil.copy2(item.source, item.destination)
