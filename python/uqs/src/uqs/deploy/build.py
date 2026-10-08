"""`uqs deploy build`: a versioned uqf release artifact, built once, for
`uqs deploy push` to put on any number of servers (#778, #835).

    uqs deploy build

writes into dist/ at the repository root (or --output), for the committed
revision, the archive, its .sha256 and its manifest
(uqs.deploy.artifact). <release> is the UTC build time and the first 12
characters of the revision.

WHAT IS IN IT:

  the tree      an allowlist of tracked files - q source, configuration, the
                Python packages - at their repository paths, because the q
                loaders and uqs find each other by layout. Never TorQ (lib/),
                credentials, licences, .envrc, virtual environments, runtime
                data, logs, tests or build output.
  .release/     requirements.txt, uqs's dependencies pinned with hashes from
                the committed uv.lock, and wheels/: the uqs wheel and every
                pinned dependency's wheel for the TARGET, so the server
                installs without a network.
  RELEASE_MANIFEST.json
                the revision, whether the tree was dirty, the target OS,
                architecture and Python, and every member's sha256.

SIDECAR BUNDLES (#800), only when --bundle is given. The tracked files are
copied into a staging directory, each bundle is installed THERE by the uqs
bundle installer (`uqs job install`'s, in uqs.stack.bundle_build), the derived
files are regenerated there, and the release is packaged from that staged
tree - so the checkout is never touched, and every hash in the manifest is of
a file as installed. The manifest then gains `bundles`: each bundle's version,
revision, installed files with their hashes, and its jobs' identities - with
every streaming job's dependency closure, which `uqs deploy push --jobs`
starts.

The builder needs uv (to export the lock and build the uqs wheel) and the
network (to fetch dependency wheels); it needs no SSH access and no
deployment credentials, so CI can run it. It never runs on the server.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from uqs.deploy.artifact import (
    FORMAT,
    MANIFEST,
    PLATFORMS,
    REQUIREMENTS,
    WHEEL_DIR,
    Artifact,
    ReleaseError,
    Target,
    _sha256,
)
from uqs.deploy.payload import ALLOWLIST, Runner, is_excluded, python_payload, tracked_files
from uqs.paths import repo_root

#: Where a staged tree records the bundles installed into it (uqs.stack.bundles).
BUNDLE_LEDGER = "src/etl/installed_bundles.json"
GENERATOR = "scripts/generate/generate_operational_docs.py"
#: Tracked beyond the allowlist, copied into a staged tree for the generator
#: to read and rewrite, and never packaged.
STAGED_EXTRA = ("docs",)

_PYTHON = re.compile(r"3\.\d{1,2}")


def log(message: str) -> None:
    print(f"uqs deploy build: {message}", file=sys.stderr, flush=True)


def revision(root: Path, runner: Runner = subprocess.run) -> tuple[str, bool]:
    head = runner(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    status = runner(
        ["git", "-C", str(root), "status", "--porcelain", "--", *ALLOWLIST],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode or status.returncode:
        raise ReleaseError(
            "package", "this is not a git checkout - a release must name its revision"
        )
    return head.stdout.strip(), bool(status.stdout.strip())


def release_id(rev: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{rev[:12]}"


def default_python(root: Path) -> str:
    """The Python a release targets: the lower bound of requires-python."""
    text = (root / "python" / "uqs" / "pyproject.toml").read_text()
    m = re.search(r'requires-python\s*=\s*">=\s*(3\.\d+)', text)
    if not m:
        raise ReleaseError(
            "arguments", "cannot read requires-python from python/uqs/pyproject.toml"
        )
    return m.group(1)


# ---------------------------------------------------------------- bundles


def _staged_files(staged: Path) -> list[str]:
    """Every file under the allowlist in a staged tree, minus the excluded."""
    found = []
    for path in staged.rglob("*"):
        rel = path.relative_to(staged).as_posix()
        allowed = any(rel == a or rel.startswith(f"{a}/") for a in ALLOWLIST)
        if path.is_file() and allowed and not is_excluded(rel):
            found.append(rel)
    return sorted(found)


def stage_bundles(
    root: Path,
    files: list[str],
    bundles: Sequence[str],
    staged: Path,
    runner: Runner = subprocess.run,
) -> tuple[list[str], dict]:
    """Install `bundles` into a copy of the tree under `staged`; return the
    staged tree's files and the manifest's `bundles` record.

    The uqs installer runs from `root`'s environment but imports uqs from the
    STAGED tree (PYTHONPATH), so the tree it finds and writes is the copy.
    """
    folders = []
    for b in bundles:
        folder = Path(b).resolve()
        if not (folder / "bundle.json").is_file():
            raise ReleaseError("bundle", f"{b} holds no bundle.json - it is not a bundle")
        folders.append(str(folder))
    # The docs too, though a release never ships them: the generator
    # rewrites the derived tables in them, and refuses a tree without them.
    for rel in sorted(set(files) | set(tracked_files(root, runner, STAGED_EXTRA))):
        (staged / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, staged / rel)
    env = dict(os.environ, PYTHONPATH=str(staged / "python" / "uqs" / "src"))
    # The staged tree carries no lib/ (a release never does): the generator
    # reads the vendored process table from the checkout's, as a deployed
    # release reads the server's - unless the builder named its own.
    env.setdefault("TORQHOME", str(root / "lib" / "torq"))
    env.setdefault("TORQAPPHOME", str(root / "lib" / "torq-finance-starter-pack"))
    python = ["uv", "run", "--frozen", "--quiet", "--project", str(root), "python"]

    def step(what: str, *argv: str) -> str:
        try:
            r = runner([*python, *argv], cwd=staged, env=env, capture_output=True, text=True)
        except OSError as exc:
            raise ReleaseError("bundle", f"{what}: could not run uv: {exc}") from None
        if r.returncode:
            tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-15:])
            raise ReleaseError("bundle", f"{what} failed (exit {r.returncode}):\n{tail}")
        return r.stdout

    record = json.loads(
        step("installing the bundles", "-m", "uqs.stack.bundle_build", "install", *folders)
    )
    step("regenerating the derived files", str(staged / GENERATOR))
    streaming = [
        job["procname"]
        for entry in record.values()
        for job in entry["jobs"]
        if job["kind"] == "streaming"
    ]
    if streaming:
        needs = json.loads(
            step("resolving the jobs' dependencies", "-m", "uqs.stack.bundle_build", "needs",
                 *streaming)
        )  # fmt: skip
        for entry in record.values():
            for job in entry["jobs"]:
                if job["kind"] == "streaming":
                    job["needs"] = needs[job["procname"]]
    return _staged_files(staged), record


def build_artifact(
    root: Path,
    out_dir: Path,
    *,
    rid: str,
    rev: str,
    dirty: bool,
    files: list[str],
    target: Target,
    python_dir: Path,
    bundles: dict | None = None,
) -> Artifact:
    """The archive, its .sha256 and its manifest, written into `out_dir`.

    `python_dir` holds requirements.txt and wheels/, from python_payload;
    `root` is the checkout, or the staged tree when bundles were installed.
    """
    members: dict[str, Path] = {f: root / f for f in files}
    members[REQUIREMENTS] = python_dir / "requirements.txt"
    wheels = sorted((python_dir / "wheels").glob("*.whl"))
    for w in wheels:
        members[f"{WHEEL_DIR}/{w.name}"] = w
    app = [f"{WHEEL_DIR}/{w.name}" for w in wheels if w.name.startswith("uqs-")]
    manifest = {
        "format": FORMAT,
        "release": rid,
        "revision": rev,
        "dirty": dirty,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": target.as_dict(),
        "python": {
            "requirements": REQUIREMENTS,
            "wheel_dir": WHEEL_DIR,
            "app_wheel": app[0] if app else None,
            "wheels": len(wheels),
        },
        "files": {name: _sha256(path) for name, path in sorted(members.items())},
    }
    if bundles:
        manifest["bundles"] = bundles
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"uqf-{rid}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, src in sorted(members.items()):
            tar.add(src, arcname=name, recursive=False)
        data = json.dumps(manifest, indent=2, sort_keys=True).encode()
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(data)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))
    digest = _sha256(path)
    Path(f"{path}.sha256").write_text(f"{digest}  {path.name}\n")
    (out_dir / f"uqf-{rid}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return Artifact(path=path, sha256=digest, manifest=manifest)


#: Where an artifact goes unless --output says otherwise: the Python convention,
#: gitignored at the root, and excluded from every release (payload.EXCLUDED).
DEFAULT_OUTPUT = "dist"


def build(
    output: Path | str | None = None,
    *,
    arch: str = "x86_64",
    python: str | None = None,
    allow_dirty: bool = False,
    bundles: Sequence[str] = (),
    root: Path | None = None,
    runner: Runner = subprocess.run,
) -> Artifact:
    """Build the artifact for the checkout at `root` into `output`, by
    default `root`/dist."""
    root = root or repo_root()
    output = Path(output) if output is not None else root / DEFAULT_OUTPUT
    if arch not in PLATFORMS:
        raise ReleaseError("arguments", f"--arch {arch!r} must be one of {', '.join(PLATFORMS)}")
    if python is not None and not _PYTHON.fullmatch(python):
        raise ReleaseError("arguments", f"--python {python!r} must be major.minor, e.g. 3.14")
    rev, dirty = revision(root, runner)
    if dirty and not allow_dirty:
        raise ReleaseError(
            "package", "the tree has uncommitted changes - commit them, or pass --allow-dirty"
        )
    target = Target(os="linux", arch=arch, python=python or default_python(root))
    rid = release_id(rev)
    files = tracked_files(root, runner)
    with tempfile.TemporaryDirectory() as tmp:
        tree, record = root, None
        if bundles:
            tree = Path(tmp) / "tree"
            log(f"installing {len(bundles)} bundle(s) into a staged tree")
            files, record = stage_bundles(root, files, bundles, tree, runner)
        log(f"Python {target.python} wheels for linux/{target.arch}")
        python_payload(root, Path(tmp) / "python", target, runner)
        log(f"packaging {len(files)} files")
        return build_artifact(
            tree,
            output,
            rid=rid,
            rev=rev,
            dirty=dirty,
            files=files,
            target=target,
            python_dir=Path(tmp) / "python",
            bundles=record,
        )
