"""A release artifact's format, and reading one back whole (#778).

One definition of the format, on both sides: `uqs deploy build` writes it
(uqs.deploy.build) and `uqs deploy push` reads it back (read_artifact) before
anything touches a server.

    uqf-<release>.tar.gz          the archive
    uqf-<release>.tar.gz.sha256   its checksum, `sha256sum -c` format
    uqf-<release>.manifest.json   its manifest, readable without opening it
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

FORMAT = 1
MANIFEST = "RELEASE_MANIFEST.json"
RELEASE_DIR = ".release"
REQUIREMENTS = f"{RELEASE_DIR}/requirements.txt"
WHEEL_DIR = f"{RELEASE_DIR}/wheels"

#: The architectures a release can target, and the wheel platform tags pip
#: may take for each, newest glibc baseline first. A pure-Python wheel
#: (py3-none-any) fits every one of them.
PLATFORMS = {
    "x86_64": ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"),
    "aarch64": ("manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64"),
}

#: What `uname -m` may call an architecture, by the name used here.
ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


class ReleaseError(Exception):
    """A release that cannot be built or trusted; `stage` says where."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass
class Target:
    os: str
    arch: str
    python: str
    q: str | None = None  # the kdb+ the q was converted for (#861); None: as written

    @property
    def platforms(self) -> tuple[str, ...]:
        return PLATFORMS[self.arch]

    def as_dict(self) -> dict:
        return {
            "os": self.os,
            "arch": self.arch,
            "python": self.python,
            "platforms": list(self.platforms),
            **({"q": self.q} if self.q else {}),
        }


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

    `facts` are what the server reported: os (uname -s), arch (uname -m),
    python (the major.minor uv finds there) and qversion (its q's .z.K).
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
    major = facts.get("qversion", "").split(".")[0]
    if major.isdigit() and int(major) < 5 and target.get("q") is None:
        problems.append(
            f"the server's q is kdb+ {facts['qversion']}, which has no nested contexts, and "
            "the release's q is as written - build it with `uqs deploy build --q-target 4.0`"
        )
    if facts.get("python") != target["python"]:
        problems.append(
            f"the server's uv finds Python {facts.get('python') or 'none'}, "
            f"the release's wheels are for {target['python']}"
        )
    return problems
