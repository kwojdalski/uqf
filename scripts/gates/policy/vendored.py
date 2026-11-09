"""Every vendored tree under lib/ holds exactly the bytes its record says (#2157).

lib/ holds copies of other projects - TorQ, its finance starter pack, KX's ml
and dbmaint, kdb-parquet, qAutomatedTrading. They are never edited here: an
edit survives until the next upgrade and is then silently lost, and a copy
that no longer matches its upstream cannot be upgraded by diffing. lib/ was
only excluded from the linters, so nothing stopped an edit.

THE RECORD is lib/VENDORED.toml, one `[tree.<dir>]` table per directory
under lib/: the upstream URL, the pinned commit, the licence, and which
upstream paths were copied (`include`), left out (`omit`), renamed
(`renames`) or added here (`local`, e.g. a NOTICE.md). Beside it,
lib/.vendored/<dir>.tsv lists every copied file as `mode<TAB>blob<TAB>path`
- the upstream file's git blob id at that commit, under its path here - and
the table's `digest` is the sha256 of that listing, so neither can change
without the other. Both are written only by scripts/dev/revendor.py, from a
fetch of upstream at the commit.

THE RULE recomputes `git hash-object` for every file under each tree in the
working tree (tracked, or untracked and not ignored) and compares it with
the listing. It fails, naming each file, on a changed byte, a changed mode,
a missing file, a file upstream does not have, a directory under lib/ with
no record, and a listing whose digest does not match the record.

DRIFT. lib/.vendored/<dir>.drift.tsv, where present, lists files whose bytes
here are known to differ from upstream: `path<TAB>upstream blob<TAB>local
blob<TAB>kind`. Those files must hold exactly the local blob recorded, so the
drift is frozen, not allowed to grow: the table's `drift_digest` is the
sha256 of the drift file, as `digest` is of the listing, and a drift file
that does not match it - or exists for a tree with no `drift_digest` - is
refused, so an edit cannot pass by being declared as drift (#2409). It is
never written by a tool: it exists because some trees were
whitespace-normalised by this repository's own hooks when they were vendored,
before lib/ was excluded from them, and reverting or accepting that is the
owner's call. Re-vendoring a tree
(scripts/dev/revendor.py) restores upstream's bytes and deletes its drift.

    python3 scripts/gates/check.py vendored
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: The record, from the repository root.
RECORD = "lib/VENDORED.toml"
#: Where each tree's file listing and drift live, from the repository root.
LISTINGS = "lib/.vendored"
#: Entries directly under lib/ that are the record itself, not a tree.
RECORD_ENTRIES = frozenset({"VENDORED.toml", ".vendored"})

#: path here -> (mode, blob)
Listing = dict[str, tuple[str, str]]


@dataclass(frozen=True)
class Drift:
    upstream: str
    local: str
    kind: str


@dataclass(frozen=True)
class Tree:
    name: str
    upstream: str
    commit: str
    license: str
    license_file: str
    digest: str
    upstream_tree: str
    files: int
    include: tuple[str, ...] = ()
    omit: tuple[str, ...] = ()
    renames: Mapping[str, str] = field(default_factory=dict)
    local: tuple[str, ...] = ()
    #: sha256 of lib/.vendored/<dir>.drift.tsv; "" when the tree has none.
    drift_digest: str = ""

    @property
    def path(self) -> str:
        return f"lib/{self.name}"

    @property
    def listing_file(self) -> str:
        return f"{LISTINGS}/{self.name}.tsv"

    @property
    def drift_file(self) -> str:
        return f"{LISTINGS}/{self.name}.drift.tsv"


# ------------------------------------------------------------------ hashing


def blob_id(data: bytes) -> str:
    """What `git hash-object` prints for these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def file_entry(path: Path) -> tuple[str, str]:
    """(git mode, blob id) of one file in the working tree."""
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        return "120000", blob_id(os.readlink(path).encode())
    mode = "100755" if st.st_mode & stat.S_IXUSR else "100644"
    return mode, blob_id(path.read_bytes())


def format_listing(listing: Mapping[str, tuple[str, str]]) -> str:
    return "".join(f"{mode}\t{blob}\t{p}\n" for p, (mode, blob) in sorted(listing.items()))


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


# ------------------------------------------------------------------- record


def load_record(root: Path) -> list[Tree]:
    data = tomllib.loads((root / RECORD).read_text())
    trees = []
    for name, t in sorted(data.get("tree", {}).items()):
        trees.append(
            Tree(
                name=name,
                upstream=t["upstream"],
                commit=t["commit"],
                license=t["license"],
                license_file=t["license_file"],
                digest=t["digest"],
                upstream_tree=t["upstream_tree"],
                files=t["files"],
                include=tuple(t.get("include", ())),
                omit=tuple(t.get("omit", ())),
                renames=dict(t.get("renames", {})),
                local=tuple(t.get("local", ())),
                drift_digest=t.get("drift_digest", ""),
            )
        )
    return trees


def parse_listing(text: str) -> Listing:
    out: Listing = {}
    for line in text.splitlines():
        mode, blob, p = line.split("\t", 2)
        out[p] = (mode, blob)
    return out


def parse_drift(text: str) -> dict[str, Drift]:
    out: dict[str, Drift] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p, up, local, kind = line.split("\t", 3)
        out[p] = Drift(up, local, kind)
    return out


def read_drift(root: Path, tree: Tree) -> dict[str, Drift]:
    f = root / tree.drift_file
    return parse_drift(f.read_text()) if f.exists() else {}


def _under(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def upstream_listing(entries: Iterable[tuple[str, str, str, str]], tree: Tree) -> Listing:
    """What lib/<tree> should hold, from upstream's `git ls-tree -r` entries
    (mode, type, blob, path): `include`d, not `omit`ted, `renames` applied.
    A submodule must be omitted explicitly - it is someone else's tree."""
    out: Listing = {}
    for mode, kind, blob, p in entries:
        if tree.include and not _under(p, tree.include):
            continue
        if _under(p, tree.omit):
            continue
        if kind != "blob":
            raise ValueError(f"{tree.name}: upstream {p} is a {kind}; list it in `omit`")
        out[tree.renames.get(p, p)] = (mode, blob)
    return out


# ------------------------------------------------------------- working tree


def git_files(root: Path, sub: str) -> list[str]:
    """Files under `sub`: tracked, or untracked and not ignored."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "-c", "-o", "--exclude-standard", "--", sub],
        check=True,
        capture_output=True,
    ).stdout
    return sorted({p for p in out.decode().split("\0") if p})


def tracked_lib_entries(root: Path) -> set[str]:
    """Names directly under lib/ that hold a tracked or untracked-unignored file."""
    return {p.split("/")[1] for p in git_files(root, "lib") if p.count("/") >= 1}


def local_listing(root: Path, tree: Tree) -> tuple[Listing, list[str]]:
    """(path -> (mode, blob)) of lib/<tree> as it is on disk, and the tracked
    paths that are missing from disk."""
    prefix = tree.path + "/"
    listing: Listing = {}
    missing: list[str] = []
    for p in git_files(root, tree.path):
        rel = p[len(prefix) :]
        f = root / p
        if not f.exists() and not f.is_symlink():
            missing.append(rel)
            continue
        listing[rel] = file_entry(f)
    return listing, missing


# --------------------------------------------------------------------- rule


def tree_problems(root: Path, tree: Tree) -> list[str]:
    listing_path = root / tree.listing_file
    if not listing_path.exists():
        return [
            f"{tree.path}: no file listing at {tree.listing_file}"
            f" - run scripts/dev/revendor.py {tree.name}"
        ]
    text = listing_path.read_text()
    if digest(text) != tree.digest:
        return [
            f"{tree.listing_file}: digest {digest(text)} does not match {RECORD}'s {tree.digest}"
            " - the listing and the record change together, through scripts/dev/revendor.py"
        ]
    expected = parse_listing(text)
    found: list[str] = []
    if len(expected) != tree.files:
        found.append(
            f"{tree.listing_file}: lists {len(expected)} files, {RECORD} says {tree.files}"
        )
    for p in tree.local:
        if p in expected:
            found.append(f"{tree.path}/{p}: listed as `local` in {RECORD} but upstream has it")
    if tree.license_file not in expected and tree.license_file not in tree.local:
        found.append(
            f"{tree.path}: licence file {tree.license_file} is not among the vendored files"
        )
    drift_path = root / tree.drift_file
    drift_text = drift_path.read_text() if drift_path.exists() else None
    if drift_text is None and tree.drift_digest:
        found.append(
            f"{tree.drift_file}: missing, but {RECORD} records drift_digest {tree.drift_digest}"
        )
    elif drift_text is not None and not tree.drift_digest:
        return [
            *found,
            f"{tree.drift_file}: {RECORD} records no drift for {tree.name} - recorded drift"
            " is frozen and cannot be added",
        ]
    elif drift_text is not None and digest(drift_text) != tree.drift_digest:
        return [
            *found,
            f"{tree.drift_file}: digest {digest(drift_text)} does not match {RECORD}'s"
            f" drift_digest {tree.drift_digest} - recorded drift is frozen and cannot grow",
        ]
    drift = parse_drift(drift_text or "")
    for p, d in drift.items():
        if p not in expected:
            found.append(f"{tree.drift_file}: {p} is not a vendored file")
        elif expected[p][1] != d.upstream:
            found.append(
                f"{tree.drift_file}: {p} upstream blob {d.upstream}"
                f" is not the listing's {expected[p][1]}"
            )
        else:
            expected[p] = (expected[p][0], d.local)
    here, missing = local_listing(root, tree)
    for p in tree.local:
        if p not in here:
            found.append(f"{tree.path}/{p}: listed as `local` in {RECORD} but missing")
        here.pop(p, None)
    for p in missing:
        if p not in tree.local:
            found.append(
                f"{tree.path}/{p}: missing (upstream blob {expected.get(p, ('', '?'))[1]})"
            )
            expected.pop(p, None)
    for p in sorted(expected.keys() - here.keys()):
        found.append(f"{tree.path}/{p}: missing (upstream blob {expected[p][1]})")
    for p in sorted(here.keys() - expected.keys()):
        found.append(f"{tree.path}/{p}: not in upstream {tree.commit[:12]} (blob {here[p][1]})")
    for p in sorted(here.keys() & expected.keys()):
        (want_mode, want), (mode, got) = expected[p], here[p]
        if got != want:
            what = "differs from its recorded drift" if p in drift else "differs from upstream"
            found.append(f"{tree.path}/{p}: {what} (expected blob {want}, found {got})")
        elif mode != want_mode:
            found.append(f"{tree.path}/{p}: mode {mode}, upstream {want_mode}")
    return found


def problems(root: Path) -> list[str]:
    if not (root / RECORD).exists():
        return [f"{RECORD}: missing - every tree under lib/ needs a record"]
    trees = load_record(root)
    found: list[str] = []
    names = {t.name for t in trees}
    for entry in sorted(tracked_lib_entries(root) - names - RECORD_ENTRIES):
        found.append(
            f"lib/{entry}: not recorded in {RECORD} - vendor it with scripts/dev/revendor.py"
        )
    for t in trees:
        if not (root / t.path).is_dir():
            found.append(f"{t.path}: recorded in {RECORD} but not present")
            continue
        found.extend(tree_problems(root, t))
    return found


def summary(root: Path) -> str:
    trees = load_record(root)
    drifted = sum(len(read_drift(root, t)) for t in trees)
    return (
        f"{len(trees)} vendored trees match their recorded upstream"
        f" ({drifted} files of recorded drift)"
    )
