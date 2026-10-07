"""build_release.py - build a versioned uqf release artifact, once, for
scripts/deploy.py to put on any number of servers (#778).

    python3 scripts/build_release.py --output dist/

writes, for the committed revision:

    dist/uqf-<release>.tar.gz          the artifact
    dist/uqf-<release>.tar.gz.sha256   its checksum, `sha256sum -c` format
    dist/uqf-<release>.manifest.json   its manifest, readable without opening it

<release> is the UTC build time and the first 12 characters of the revision.

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

The builder needs uv (to export the lock and build the uqs wheel) and the
network (to fetch dependency wheels); it needs no SSH access and no
deployment credentials, so CI can run it. It never runs on the server.

The artifact is read back by read_artifact, which deploy.py calls: one
definition of the format, on both sides.

Standard library only, and parseable by Python 3.10, like deploy.py.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: timezone.utc, not datetime.UTC: UTC is 3.11+, and this runs under 3.10 too.
_UTC = timezone.utc  # noqa: UP017

FORMAT = 1
MANIFEST = "RELEASE_MANIFEST.json"
RELEASE_DIR = ".release"
REQUIREMENTS = f"{RELEASE_DIR}/requirements.txt"
WHEEL_DIR = f"{RELEASE_DIR}/wheels"

#: What a release is made of: tracked files under these paths, and nothing
#: else. The three Python packages are all here because uv's workspace lock
#: needs every member's pyproject to resolve, though only uqs is installed.
ALLOWLIST = (
    "pyproject.toml",
    "uv.lock",
    "src",
    "scripts",
    "python/uqs",
    "python/uqf_frontend",
    "python/uqf_airflow_provider",
)

#: Never shipped, even when tracked under the allowlist: secrets and
#: secret-bearing configuration, licences, environments, data, logs and
#: generated output. gateway_users.csv holds the demo gateway passwords; a
#: server that wants ordinary gateway users supplies its own in
#: shared/config/ (see deploy.py's SHARED_CONFIG), and uqs runs without one.
EXCLUDED_PATTERNS = (
    r"(^|/)\.env($|\.)(?!example$)",
    r"(^|/)\.envrc$",
    r"(^|/)\.venv/",
    r"(^|/)node_modules/",
    r"(^|/)__pycache__/",
    r"(^|/)output/",
    r"(^|/)dist/",
    r"(^|/)tests/",
    r"\.(lic|pem|key|log|pyc)$",
    r"(^|/)id_(rsa|ed25519|ecdsa)",
    r"(^|/)passwords/",
    r"^scripts/torqconfig/permissions/gateway_users\.csv$",
    r"^lib/",
    rf"^{re.escape(RELEASE_DIR)}/",
)
_EXCLUDED = [re.compile(p) for p in EXCLUDED_PATTERNS]

#: The architectures a release can target, and the wheel platform tags pip
#: may take for each, newest glibc baseline first. A pure-Python wheel
#: (py3-none-any) fits every one of them.
PLATFORMS = {
    "x86_64": ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"),
    "aarch64": ("manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64"),
}

#: What `uname -m` may call an architecture, by the name used here.
ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}

_PYTHON = re.compile(r"3\.\d{1,2}")


class ReleaseError(Exception):
    """A release that cannot be built or trusted; `stage` says where."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


Runner = Callable[..., subprocess.CompletedProcess]


def log(message: str) -> None:
    print(f"build_release: {message}", file=sys.stderr, flush=True)


# ------------------------------------------------------------- the payload


def is_excluded(path: str) -> bool:
    return any(p.search(path) for p in _EXCLUDED)


def tracked_files(root: Path, runner: Runner = subprocess.run) -> list[str]:
    r = runner(
        ["git", "-C", str(root), "ls-files", "-z", "--", *ALLOWLIST],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode:
        raise ReleaseError("package", f"git ls-files failed: {r.stderr.strip()}")
    return sorted(f for f in r.stdout.split("\0") if f and not is_excluded(f))


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
    stamp = (now or datetime.now(_UTC)).strftime("%Y%m%dT%H%M%SZ")
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


@dataclass
class Target:
    os: str
    arch: str
    python: str

    @property
    def platforms(self) -> tuple[str, ...]:
        return PLATFORMS[self.arch]

    def as_dict(self) -> dict:
        return {
            "os": self.os,
            "arch": self.arch,
            "python": self.python,
            "platforms": list(self.platforms),
        }


# ------------------------------------------------------------ Python bits


def python_payload(root: Path, into: Path, target: Target, runner: Runner = subprocess.run) -> None:
    """requirements.txt and every wheel the target needs, under `into`.

    The pins come from the committed uv.lock (`--frozen`, never re-resolved),
    with hashes, so the server's install refuses a wheel that is not the one
    locked. Dependency wheels are binary-only for the target's platform and
    Python - a source distribution would need a compiler and the network.
    """
    wheels = into / "wheels"
    wheels.mkdir(parents=True)
    requirements = into / "requirements.txt"
    steps = [
        (
            "exporting the locked dependencies",
            [
                "uv", "export", "--package", "uqs", "--no-dev", "--frozen",
                "--no-emit-workspace", "--format", "requirements-txt",
                "--quiet", "-o", str(requirements),
            ],
        ),
        (
            "building the uqs wheel",
            ["uv", "build", "--package", "uqs", "--wheel", "--quiet", "--out-dir", str(wheels)],
        ),
        (
            "fetching the dependency wheels",
            [
                "uvx", "--from", "pip", "pip", "download", "--quiet",
                "--requirement", str(requirements), "--dest", str(wheels),
                "--only-binary=:all:", "--implementation", "cp",
                "--python-version", target.python,
                *[arg for tag in target.platforms for arg in ("--platform", tag)],
            ],
        ),
    ]  # fmt: skip
    for what, argv in steps:
        try:
            r = runner(argv, cwd=root, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise ReleaseError("python", f"{what}: could not run {argv[0]}: {exc}") from None
        if r.returncode:
            tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-15:])
            raise ReleaseError("python", f"{what} failed (exit {r.returncode}):\n{tail}")
    apps = sorted(wheels.glob("uqs-*.whl"))
    if len(apps) != 1:
        raise ReleaseError("python", f"expected one uqs wheel, found {len(apps)}")
    if not requirements.is_file():
        raise ReleaseError("python", "uv export wrote no requirements file")


# --------------------------------------------------------------- building


def _digest(stream) -> str:
    """The sha256 of a binary stream, read in 1 MiB chunks."""
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1 << 20), b""):
        h.update(chunk)
    return h.hexdigest()


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return _digest(f)


@dataclass
class Artifact:
    path: Path
    sha256: str
    manifest: dict

    @property
    def release(self) -> str:
        return self.manifest["release"]

    @property
    def files(self) -> list[str]:
        return sorted(self.manifest["files"])

    @property
    def app_wheel(self) -> str:
        return self.manifest["python"]["app_wheel"]


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
) -> Artifact:
    """The archive, its .sha256 and its manifest, written into `out_dir`.

    `python_dir` holds requirements.txt and wheels/, from python_payload.
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
        "created_at": datetime.now(_UTC).isoformat(timespec="seconds"),
        "target": target.as_dict(),
        "python": {
            "requirements": REQUIREMENTS,
            "wheel_dir": WHEEL_DIR,
            "app_wheel": app[0] if app else None,
            "wheels": len(wheels),
        },
        "files": {name: _sha256(path) for name, path in sorted(members.items())},
    }
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


# ---------------------------------------------------------------- reading


def _unsafe(name: str) -> bool:
    parts = Path(name).parts
    return name.startswith("/") or ".." in parts or not parts


def read_artifact(path: Path, expected_sha256: str | None = None) -> Artifact:
    """An artifact, checked whole before anything trusts it.

    The archive's sha256 must match its .sha256 file when one sits beside it
    (and `expected_sha256` when given). Every member must be a regular file
    at a relative path, listed in the manifest with the hash it has; nothing
    may be missing and nothing extra. A member that fails any of these is
    named - an artifact is never half-trusted.
    """
    if not path.is_file():
        raise ReleaseError("artifact", f"no artifact at {path}")
    digest = _sha256(path)
    sidecar = Path(f"{path}.sha256")
    if sidecar.is_file():
        recorded = sidecar.read_text().split()[0] if sidecar.read_text().split() else ""
        if recorded != digest:
            raise ReleaseError(
                "artifact", f"{path.name} does not match {sidecar.name}: it is {digest}"
            )
    if expected_sha256 and expected_sha256 != digest:
        raise ReleaseError("artifact", f"{path.name} is {digest}, not {expected_sha256}")
    try:
        tar = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseError("artifact", f"{path.name} is not a readable tar.gz: {exc}") from None
    with tar:
        seen: dict[str, str] = {}
        manifest = None
        for member in tar:
            if _unsafe(member.name):
                raise ReleaseError("artifact", f"member {member.name!r} is not a relative path")
            if not member.isfile():
                raise ReleaseError("artifact", f"member {member.name!r} is not a regular file")
            if member.name in seen or (member.name == MANIFEST and manifest is not None):
                raise ReleaseError("artifact", f"member {member.name!r} appears twice")
            f = tar.extractfile(member)
            assert f is not None  # a regular file always extracts
            if member.name == MANIFEST:
                try:
                    manifest = json.load(f)
                except ValueError:
                    raise ReleaseError("artifact", f"{MANIFEST} is not JSON") from None
                continue
            seen[member.name] = _digest(f)
    if manifest is None:
        raise ReleaseError("artifact", f"{path.name} carries no {MANIFEST}")
    if manifest.get("format") != FORMAT:
        raise ReleaseError(
            "artifact", f"manifest format {manifest.get('format')!r}, this deploy reads {FORMAT}"
        )
    listed = manifest.get("files", {})
    for name in sorted(set(listed) | set(seen)):
        if name not in seen:
            raise ReleaseError("artifact", f"{name} is in the manifest but not the archive")
        if name not in listed:
            raise ReleaseError("artifact", f"{name} is in the archive but not the manifest")
        if listed[name] != seen[name]:
            raise ReleaseError("artifact", f"{name} does not match its manifest checksum")
    if not manifest.get("python", {}).get("app_wheel"):
        raise ReleaseError("artifact", "the manifest names no uqs wheel")
    return Artifact(path=path, sha256=digest, manifest=manifest)


def compatible(target: dict, facts: dict[str, str]) -> list[str]:
    """Why a server cannot run a release built for `target`; empty when it can.

    `facts` are what the server reported: os (uname -s), arch (uname -m) and
    python (the major.minor uv finds there).
    """
    problems = []
    os_name = facts.get("os", "").lower()
    if os_name != target["os"]:
        problems.append(f"the server runs {facts.get('os') or 'an unknown OS'}, not {target['os']}")
    arch = ARCH_ALIASES.get(facts.get("arch", ""), facts.get("arch", ""))
    if arch != target["arch"]:
        problems.append(
            f"the server is {facts.get('arch') or 'an unknown architecture'}, "
            f"the release is built for {target['arch']}"
        )
    if facts.get("python") != target["python"]:
        problems.append(
            f"the server's uv finds Python {facts.get('python') or 'none'}, "
            f"the release's wheels are for {target['python']}"
        )
    return problems


# ------------------------------------------------------------------ driver


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="build_release.py", description="Build a uqf release artifact for deploy.py."
    )
    p.add_argument("--output", required=True, help="directory to write the artifact into")
    p.add_argument(
        "--arch", default="x86_64", choices=sorted(PLATFORMS), help="the servers' architecture"
    )
    p.add_argument("--python", help="the servers' Python, major.minor; default requires-python's")
    p.add_argument(
        "--allow-dirty", action="store_true", help="build uncommitted changes (recorded)"
    )
    a = p.parse_args(argv)
    if a.python is not None and not _PYTHON.fullmatch(a.python):
        raise ReleaseError("arguments", f"--python {a.python!r} must be major.minor, e.g. 3.14")
    return a


def build(
    argv: Sequence[str] | None = None,
    *,
    root: Path = ROOT,
    runner: Runner = subprocess.run,
    out=sys.stdout,
) -> Artifact:
    a = parse_args(argv)
    rev, dirty = revision(root, runner)
    if dirty and not a.allow_dirty:
        raise ReleaseError(
            "package", "the tree has uncommitted changes - commit them, or pass --allow-dirty"
        )
    target = Target(os="linux", arch=a.arch, python=a.python or default_python(root))
    rid = release_id(rev)
    files = tracked_files(root, runner)
    with tempfile.TemporaryDirectory() as tmp:
        log(f"Python {target.python} wheels for linux/{target.arch}")
        python_payload(root, Path(tmp), target, runner)
        log(f"packaging {len(files)} files")
        artifact = build_artifact(
            root,
            Path(a.output),
            rid=rid,
            rev=rev,
            dirty=dirty,
            files=files,
            target=target,
            python_dir=Path(tmp),
        )
    print(artifact.path, file=out)
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    if shutil.which("uv") is None:
        log("FAILED: uv is not on PATH - it exports the lock and builds the uqs wheel")
        return 1
    try:
        build(argv)
    except ReleaseError as exc:
        log(f"FAILED at {exc.stage}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
