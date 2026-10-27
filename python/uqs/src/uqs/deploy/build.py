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
  .release/kafka/
                only with --kafka-home (#1515): KX's kfk for the target, with
                its libraries and notices (uqs.deploy.kafka).
  .release/python-runtime.tar.gz
                only with --python-runtime (#1557): a CPython for the target,
                which scripts/install_release.py makes .venv from.
  RELEASE_MANIFEST.json
                the revision, whether the tree was dirty, the target OS,
                architecture, Python and the glibc its wheels need, and every
                member's sha256.

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
import tarfile
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from uqs.deploy import foreign_blocks, kafka, portable, python_runtime
from uqs.deploy.artifact import (
    FORMAT,
    INSTALLER,
    MANIFEST,
    PACKAGING_MODE,
    PLATFORMS,
    REQUIREMENTS,
    WHEEL_DIR,
    Artifact,
    ReleaseError,
    Target,
    _sha256,
    glibc_floor,
)
from uqs.deploy.payload import (
    ALLOWLIST,
    Runner,
    default_python,
    is_excluded,
    python_payload,
    release_id,
    revision,
    tracked_files,
)
from uqs.logger import get_logger
from uqs.paths import repo_root
from uqs.runtimes import DEFAULT_RUNTIME

log = get_logger()

#: Where a staged tree records the bundles installed into it (uqs.stack.bundles).
BUNDLE_LEDGER = "src/etl/installed_bundles.json"
GENERATOR = "scripts/generate/generate_operational_docs.py"
#: Tracked beyond the allowlist, copied into a staged tree for the generator
#: to read and rewrite, and never packaged.
STAGED_EXTRA = ("docs",)

_PYTHON = re.compile(r"3\.\d{1,2}")


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


def tree_python(root: Path, tree: Path, runner: Runner, stage: str):
    """A runner of `python <argv>` from `root`'s environment that imports uqs
    from `tree` (cwd and PYTHONPATH), so every `repo_root()` - and all uqs
    reads at import from it: the job graph, the bundle ledger,
    plant_tables.q - is the copy's. A failure is a ReleaseError at `stage`."""
    env = dict(os.environ, PYTHONPATH=str(tree / "python" / "uqs" / "src"))
    # A staged tree carries no lib/ (a release never does): the step reads
    # the vendored TorQ and starter pack from the checkout's, as a deployed
    # release reads the server's - unless the builder named its own.
    env.setdefault("TORQHOME", str(root / "lib" / "torq"))
    env.setdefault("TORQAPPHOME", str(root / "lib" / "torq-finance-starter-pack"))
    python = ["uv", "run", "--frozen", "--quiet", "--project", str(root), "python"]

    def step(what: str, *argv: str) -> str:
        try:
            r = runner([*python, *argv], cwd=tree, env=env, capture_output=True, text=True)
        except OSError as exc:
            raise ReleaseError(stage, f"{what}: could not run uv: {exc}") from None
        if r.returncode:
            tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-15:])
            raise ReleaseError(stage, f"{what} failed (exit {r.returncode}):\n{tail}")
        return r.stdout

    return step


def stage_bundles(
    root: Path,
    files: list[str],
    bundles: Sequence[str],
    staged: Path,
    runner: Runner = subprocess.run,
    runtime: str = DEFAULT_RUNTIME,
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
    step = tree_python(root, staged, runner, "bundle")
    record = json.loads(
        step(
            "installing the bundles",
            *("-m", "uqs.stack.bundle_build", "install", "--runtime", runtime, *folders),
        )
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
    runtime: str = DEFAULT_RUNTIME,
    portable: dict | None = None,
    hdb_shape: dict[str, list[str]] | None = None,
    kafka_sel: kafka.Selected | None = None,
    carried: python_runtime.PythonRuntime | None = None,
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
    kafka_sel = kafka_sel or kafka.Selected()
    members.update(kafka_sel.members())
    members.update({python_runtime.CARRIED: carried.path} if carried else {})
    manifest = {
        "format": FORMAT,
        "release": rid,
        "revision": rev,
        "dirty": dirty,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": {**target.as_dict(), "glibc": glibc_floor(w.name for w in wheels)},
        "python": {
            "requirements": REQUIREMENTS,
            "wheel_dir": WHEEL_DIR,
            "app_wheel": app[0] if app else None,
            "wheels": len(wheels),
            **({"runtime": carried.carried()} if carried else {}),
        },
        "files": {name: _sha256(path) for name, path in sorted(members.items())},
        # the runtime the release runs (#852), whose bundles it carries; always
        # written, as release_runtime refuses a manifest without one (#1648)
        "runtime": runtime,
        # what the artifact is (#1646): the tree with every Python dependency as a
        # locked wheel, installable with no network - by `uqs deploy push`, or on
        # the server by the installer it carries
        "packaging": {"mode": PACKAGING_MODE, "installer": INSTALLER},
    }
    if bundles:
        manifest["bundles"] = bundles
    if portable:
        # what was converted for the server's kdb+ (#861); target["q"] says which
        manifest["portable"] = portable
    kafka_sel.annotate(manifest)
    if hdb_shape:
        # the tables and columns this release declares, for a dry run to judge
        # the server's HDB by before any of the release is there (#870)
        manifest["hdb_shape"] = hdb_shape
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
    runtime: str | None = None,
    q_target: str | None = None,
    q_exclude: Sequence[str] = (),
    allow_computed_names: bool = False,
    kafka_home: Path | str | None = None,
    kafka_connector: bool = False,
    python_runtime_file: Path | str | None = None,
) -> Artifact:
    """Build the artifact for the checkout at `root` into `output`, by
    default `root`/dist. Kafka is opt-in (uqs.deploy.kafka.select), and so is
    carrying a Python runtime (`python_runtime_file`, python_runtime.for_release).

    With `q_target`, its q is converted for that kdb+ (#861), in a staged
    copy: the checkout is never written (uqs.deploy.portable).

    Its bundles are `runtime`'s composition (#852): the bundles
    runtime_bundles.json declares for it, plus `bundles` - resolved by the same
    stack.runtime_bundles.resolve as `uqs runtime prepare`."""
    root = root or repo_root()
    runtime = runtime or DEFAULT_RUNTIME
    members = composition_members(runtime, root, bundles)
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
    target = Target(os="linux", arch=arch, python=python or default_python(root), q=q_target)
    carried = python_runtime.for_release(python_runtime_file, target.as_dict())
    rid = release_id(rev)
    files = tracked_files(root, runner)
    foreign_blocks.refuse(root, files, runtime, {m.bundle.name for m in members})
    with tempfile.TemporaryDirectory() as tmp:
        kafka_sel = kafka.select(kafka_home, kafka_connector, arch, Path(tmp) / "kafka", runner)
        tree, record = root, None
        if members:
            tree = Path(tmp) / "tree"
            log.info(
                "installing {} bundle(s) for runtime {} into a staged tree", len(members), runtime
            )
            folders = [str(m.bundle.root) for m in members]
            files, record = stage_bundles(root, files, folders, tree, runner, runtime)
            for m in members:
                record[m.bundle.name]["source"] = m.source
        converted = None
        if q_target:
            if tree == root:
                tree = Path(tmp) / "tree"
                portable.stage_copy(root, files, tree)
            log.info("converting the q for kdb+ {}", q_target)
            converted = portable.convert(tree, files, q_target, q_exclude, allow_computed_names)
            log.info(
                "{} q file(s) converted, {} excluded",
                len(converted["transformed"]),
                len(converted["excluded"]),
            )
        log.info("Python {} wheels for linux/{}", target.python, target.arch)
        python_payload(root, Path(tmp) / "python", target, runner, kafka_sel.extras)
        log.info("packaging {} files", len(files))
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
            # always written (#883): a reader must never have to guess it
            runtime=runtime,
            portable=converted,
            hdb_shape=declared_shape(tree, runtime, root, runner),
            kafka_sel=kafka_sel,
            carried=carried,
        )


def declared_shape(
    tree: Path, runtime: str, root: Path, runner: Runner = subprocess.run
) -> dict[str, list[str]] | None:
    """{table: columns} the release at `tree` declares for `runtime`, or None
    - with a warning - when it cannot be composed; push then judges the HDB
    in prepare only.

    Composed by the uqs of `tree`, in a subprocess (tree_python): which tables
    a runtime holds is read at import from the tree uqs was imported from, and
    only the staged tree has the bundles installed (#1158, #1162, #1163). The
    base schema is TORQAPPHOME's, else `root`'s vendored one."""
    step = tree_python(root, tree, runner, "hdb_shape")
    argv = ("-m", "uqs.stack.hdb_shape", "declared", "--runtime", runtime)
    try:
        return json.loads(step("composing the hdb_shape", *argv))
    except (ReleaseError, ValueError) as exc:
        log.warning("the manifest records no hdb_shape: {}", exc)
        return None


def composition_members(runtime: str, root: Path, bundles: Sequence[str] = ()) -> list:
    """`runtime`'s bundles, declared and explicit, or the refusal as a
    ReleaseError - before anything is copied or built."""
    from uqs.stack import runtime_bundles
    from uqs.stack.bundle_blocks import BundleError

    try:
        return runtime_bundles.resolve(runtime, root, [Path(b) for b in bundles])
    except BundleError as exc:
        raise ReleaseError("bundle", str(exc)) from None
