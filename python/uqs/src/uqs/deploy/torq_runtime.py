"""The TorQ runtime artifact (#1557, step 5): lib/torq and
lib/torq-finance-starter-pack exactly as vendored, for `torq_mode = "bundled"`.

    uqs deploy build-runtime torq

writes, into dist/ (or --output):

    torq-runtime-<version>-<id>.tar.gz           the archive
    torq-runtime-<version>-<id>.tar.gz.sha256    its checksum
    torq-runtime-<version>-<id>.manifest.json    its manifest

It is its own artifact, not a part of a release: TorQ changes far more
rarely than uqf, so many releases share one runtime. The archive is
CONTENT-ADDRESSED: built from the same vendored trees it is the same bytes -
sorted members, a fixed mtime, no owner, gzip without a timestamp, and a
manifest that records nothing of when or from which uqf commit it was built.
The server installs it once, under shared/runtimes/torq/<sha256>/
(deploy/runtime_install.py), and every release built against the same TorQ
reuses that directory.

The trees are read, never written: only their tracked files, from a checkout
where `git status` shows them unchanged, so each member is byte-identical to
the committed blob. A symlink or a submodule is refused - the format carries
regular files only, which artifact.checked_archive holds every member to.

The manifest records each tree's version (from its dependency.csv, None
when there is none), its git tree id and the last commit that touched it, and
every member's sha256.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from uqs.deploy.artifact import ReleaseError, _sha256, checked_archive
from uqs.deploy.payload import Runner

FORMAT = 1
KIND = "torq-runtime"
MANIFEST = "RUNTIME_MANIFEST.json"
#: Each tree, by its directory in the runtime: its path in the checkout, the
#: dependency.csv that names its version, and the app name in that file.
TREES = {
    "torq": ("lib/torq", "config/dependency.csv", "TorQ"),
    "torq-finance-starter-pack": (
        "lib/torq-finance-starter-pack",
        "appconfig/dependency.csv",
        "TorQ Finance Starter Pack",
    ),
}
#: Where a server keeps the runtimes, under --dest: one directory per sha256.
RUNTIMES_DIR = "shared/runtimes/torq"
#: Every member's mtime: a fixed time, so the same trees make the same bytes.
MTIME = 315532800  # 1980-01-01
#: git's modes for a regular file; anything else is refused.
_FILE_MODES = {"100644": 0o644, "100755": 0o755}


@dataclass
class Runtime:
    path: Path
    sha256: str
    manifest: dict
    #: whether a .sha256 sat beside it (artifact.Artifact.pinned)
    pinned: bool
    #: the sha256 of RUNTIME_MANIFEST.json as shipped: the server checks an
    #: installed runtime against exactly this manifest
    manifest_sha256: str

    #: What runtime_install and prune need of any runtime (q_runtime.QRuntime too).
    component: ClassVar[str] = "torq"
    label: ClassVar[str] = "TorQ"
    runtimes_dir: ClassVar[str] = RUNTIMES_DIR

    @property
    def version(self) -> str | None:
        return self.manifest["trees"]["torq"]["version"]

    def directory(self, dest: str) -> str:
        return f"{dest}/{RUNTIMES_DIR}/{self.sha256}"

    def homes(self, dest: str) -> tuple[str, str]:
        """TORQHOME and TORQAPPHOME, as deploy.env names them."""
        home = self.directory(dest)
        return f"{home}/torq", f"{home}/torq-finance-starter-pack"

    def record(self, dest: str, install: str) -> dict:
        """The components entry for a bundled TorQ (components.observed)."""
        torq_home, app_home = self.homes(dest)
        return {
            "path": torq_home,
            "app_home": app_home,
            "launcher": None,
            "version": self.version,
            "sha256": self.sha256,
            "runtime": self.directory(dest),
            "starter_pack_version": self.manifest["trees"]["torq-finance-starter-pack"]["version"],
            "install": install,
            "requires": ["envsubst and rlwrap, for torq.sh"],
        }


def read_runtime(path: Path) -> Runtime:
    """A TorQ runtime artifact, checked whole as a release is
    (artifact.checked_archive): sha256, every member, nothing extra."""
    c = checked_archive(path, MANIFEST)
    if c.manifest.get("kind") != KIND or c.manifest.get("format") != FORMAT:
        raise ReleaseError(
            "artifact",
            f"{path.name} is not a TorQ runtime of format {FORMAT} (kind "
            f"{c.manifest.get('kind')!r}, format {c.manifest.get('format')!r}) - build one "
            "with `uqs deploy build-runtime torq`",
        )
    tops = {name.split("/", 1)[0] for name in c.manifest["files"]}
    if tops - set(TREES):
        raise ReleaseError("artifact", f"{path.name} holds {sorted(tops - set(TREES))} - not TorQ")
    return Runtime(path, c.sha256, c.manifest, c.pinned, c.manifest_sha256)


def _git(root: Path, runner: Runner, *argv: str) -> str:
    r = runner(["git", "-C", str(root), *argv], capture_output=True, text=True, check=False)
    if r.returncode:
        raise ReleaseError("runtime", f"git {' '.join(argv)} failed: {(r.stderr or '').strip()}")
    return r.stdout


def _version(file: Path, app: str) -> str | None:
    """The version dependency.csv gives `app`; None when it says nothing."""
    if not file.is_file():
        return None
    for row in csv.DictReader(io.StringIO(file.read_text())):
        if (row.get("app") or "").strip() == app:
            return (row.get("version") or "").strip() or None
    return None


def _tree(root: Path, runner: Runner, source: str) -> tuple[dict, dict[str, int]]:
    """One vendored tree: its manifest record and {path: mode} of its files."""
    files: dict[str, int] = {}
    for line in _git(root, runner, "ls-files", "-s", "-z", "--", source).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode = meta.split()[0]
        if mode not in _FILE_MODES:
            raise ReleaseError("runtime", f"{path} is not a regular file (git mode {mode})")
        files[path] = _FILE_MODES[mode]
    if not files:
        raise ReleaseError("runtime", f"{source} holds no tracked files - is TorQ vendored?")
    record = {
        "source": source,
        "tree": _git(root, runner, "rev-parse", f"HEAD:{source}").strip(),
        "commit": _git(root, runner, "log", "-1", "--format=%H", "--", source).strip(),
    }
    return record, files


def build_runtime(root: Path, out_dir: Path, runner: Runner = subprocess.run) -> Runtime:
    """The TorQ runtime artifact from the checkout at `root`, into `out_dir`."""
    sources = [source for source, _, _ in TREES.values()]
    changed = _git(root, runner, "status", "--porcelain", "--untracked-files=all", "--", *sources)
    if changed.strip():
        raise ReleaseError(
            "runtime",
            "lib/ differs from what is committed - a runtime ships TorQ exactly as vendored:\n"
            + changed.strip(),
        )
    trees: dict[str, dict] = {}
    members: dict[str, tuple[Path, int]] = {}
    for name, (source, version_file, app) in TREES.items():
        record, files = _tree(root, runner, source)
        record["version"] = _version(root / source / version_file, app)
        trees[name] = record
        for path, mode in files.items():
            members[f"{name}/{path[len(source) + 1 :]}"] = (root / path, mode)
    manifest = {
        "format": FORMAT,
        "kind": KIND,
        "trees": trees,
        "files": {m: _sha256(src) for m, (src, _) in sorted(members.items())},
    }
    ident = hashlib.sha256(" ".join(t["tree"] for t in trees.values()).encode()).hexdigest()
    stem = f"torq-runtime-{trees['torq']['version'] or 'unknown'}-{ident[:12]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.tar.gz"
    data = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for name, (src, mode) in sorted(members.items()):
                _add(tar, name, src.read_bytes(), mode)
            _add(tar, MANIFEST, data, 0o644)
    digest = _sha256(path)
    Path(f"{path}.sha256").write_text(f"{digest}  {path.name}\n")
    (out_dir / f"{stem}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return read_runtime(path)


def _add(tar: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), mode, MTIME
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    tar.addfile(info, io.BytesIO(data))
