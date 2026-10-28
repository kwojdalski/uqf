"""The Python runtime artifact (#1557, step 7): a relocatable CPython, for
`python_mode = "bundled"` and for an offline install with no Python of the
host's (scripts/install_release.py).

    uqs deploy component build python [--arch x86_64] [--python 3.14]

has uv install the CPython it would give any project - python-build-standalone,
`uv python install cpython-<python>-linux-<arch>-gnu`, into uv's own Python
directory, the binary links left alone - and packs that installation into
dist/ (or --output):

    python-runtime-<version>-<os>-<arch>.tar.gz           the archive
    python-runtime-<version>-<os>-<arch>.tar.gz.sha256    its checksum
    python-runtime-<version>-<os>-<arch>.manifest.json    its manifest

Like the TorQ and q runtimes it is CONTENT-ADDRESSED - sorted members, no
owner, gzip without a timestamp - and installed once on the
server, under shared/runtimes/python/<sha256>/ (deploy/runtime_install.py),
then run there. The release's .venv is made from its interpreter, by `-m venv`
and pip from the release's wheels - so a push needs no uv and no Python of the
server's own version (`venv_lines`).

Each member keeps the mtime it has in the installation, unlike the other
runtimes' fixed one: CPython's .pyc files record their source's mtime, and a
source that seems newer is compiled again and its .pyc REWRITTEN in place -
which would change a runtime installed write-once, and have the next push
refuse it. So the same installation still makes the same bytes, and its .pyc
files stay valid wherever it is unpacked with its mtimes (tar does that, and so
does scripts/install_release.py). python-build-standalone does not ship a .pyc
for every stdlib module, so the first run of one writes it into __pycache__;
the runtime's check (runtime_install.VERIFY_PY) accepts such a file for a
source the manifest lists, which CPython checks against that source, and still
refuses a shipped file changed.

The format carries regular files only, so the installation's symbolic links -
bin/python3 -> python3.14, the unversioned libpython, terminfo aliases - are
left out and listed in the manifest; the interpreter is the versioned file it
names. No licence KEY ever enters it (artifact.checked_archive refuses any .lic
member); CPython's own LICENSE.txt is a notice, and ships as installed.

The manifest records uv's name for the installation, the version, the
python-build-standalone release (its BUILD file), the platform, the glibc its
ELF files ask for, the interpreter, and every file's sha256.

A release can also CARRY one (`uqs deploy build --python-runtime FILE`), at
CARRIED, for scripts/install_release.py to make the .venv from on a server with
no Python 3.14.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from uqs.deploy.artifact import ARCH_ALIASES, PLATFORMS, ReleaseError, _sha256, checked_archive
from uqs.deploy.payload import Runner
from uqs.deploy.remote import q

FORMAT = 1
KIND = "python-runtime"
MANIFEST = "RUNTIME_MANIFEST.json"
RUNTIMES_DIR = "shared/runtimes/python"
#: The manifest's mtime: a fixed time, as the other runtimes'.
MTIME = 315532800  # 1980-01-01
#: Where a release carries one, for scripts/install_release.py.
CARRIED = ".release/python-runtime.tar.gz"
#: uv's name for an installation: cpython-3.14.9-linux-x86_64-gnu.
UV_KEY = re.compile(r"cpython-(\d+)\.(\d+)\.(\d+)-(linux|macos)-([a-z0-9_]+)-(gnu|none)")
#: uv's OS name, by the name a release target uses (`uname -s`, lower-cased).
OS_NAMES = {"linux": "linux", "macos": "darwin"}
#: The glibc symbol versions an ELF file asks for.
GLIBC = re.compile(rb"GLIBC_(\d+)\.(\d+)")
#: Run by the interpreter, here and on the server once installed: the
#: version, if it starts and its own extension modules load.
PROBE = (
    "import sys, ensurepip, venv, ssl, sqlite3, zlib; "
    "print('UQF_PYTHON', '%d.%d.%d' % sys.version_info[:3], sys.implementation.name)"
)
#: The environment pip runs in, offline: never an index, never a question.
PIP_ENV = "PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1"


def parse_probe(output: str) -> str:
    """The X.Y.Z PROBE printed, or "" when it printed nothing."""
    for line in reversed(output.splitlines()):
        parts = line.split()
        if len(parts) == 3 and parts[0] == "UQF_PYTHON" and parts[2] == "cpython":
            return parts[1]
    return ""


@dataclass
class PythonRuntime:
    path: Path
    sha256: str
    manifest: dict
    pinned: bool
    manifest_sha256: str
    #: the release this runtime was extracted from, when it is the one a
    #: release carries rather than one given as python_runtime (#1646)
    carried_by: str | None = None

    #: What runtime_install and prune need of any runtime.
    component: ClassVar[str] = "python"
    label: ClassVar[str] = "Python"
    runtimes_dir: ClassVar[str] = RUNTIMES_DIR

    @property
    def version(self) -> str:
        return self.manifest["version"]

    @property
    def minor(self) -> str:
        """major.minor: what a release's wheels are built for."""
        return ".".join(self.version.split(".")[:2])

    @property
    def platform(self) -> tuple[str, str]:
        return self.manifest["os"], self.manifest["arch"]

    def directory(self, dest: str) -> str:
        return f"{dest}/{RUNTIMES_DIR}/{self.sha256}"

    def interpreter(self, home: str) -> str:
        return f"{home}/{self.manifest['interpreter']}"

    def env(self, dest: str) -> list[tuple[str, str]]:
        """deploy.env's line naming it, so prune keeps it while a release's
        .venv - whose interpreter it is - is on the server."""
        return [("UQF_PYTHON_RUNTIME", self.directory(dest))]

    def fits(self, target: dict) -> None:
        """Refused unless built for `target`'s OS, architecture and Python."""
        want_arch = ARCH_ALIASES.get(target["arch"], target["arch"])
        if self.platform != (target["os"], want_arch) or self.minor != target["python"]:
            raise ReleaseError(
                "artifact",
                f"{self.path.name} is Python {self.version} for {' '.join(self.platform)}, and "
                f"the release's wheels are for Python {target['python']} on {target['os']} "
                f"{want_arch} - build the runtime with that --arch and --python",
            )

    def carried(self) -> dict:
        """The release manifest's record of a runtime it carries."""
        m = self.manifest
        keys = ("version", "os", "arch", "interpreter", "glibc", "key")
        return {"archive": CARRIED, "sha256": self.sha256, **{k: m[k] for k in keys}}

    def record(self, dest: str, install: str) -> dict:
        """The components entry for a bundled Python (components.observed)."""
        home = self.directory(dest)
        m = self.manifest
        floor = f", glibc {m['glibc']} or later" if m["glibc"] else ""
        return {
            "path": self.interpreter(home),
            "version": m["version"],
            "implementation": "cpython",
            "build": m["build"],
            "sha256": self.sha256,
            "runtime": home,
            "install": install,
            **({"carried_by": self.carried_by} if self.carried_by else {}),
            "requires": [f"a {m['os']} {m['arch']} host{floor}; no uv, no Python of its own"],
        }


def read_runtime(path: Path) -> PythonRuntime:
    """A Python runtime artifact, checked whole as a release is
    (artifact.checked_archive), its interpreter among its files."""
    c = checked_archive(path, MANIFEST)
    m = c.manifest
    if m.get("kind") != KIND or m.get("format") != FORMAT:
        raise ReleaseError(
            "artifact",
            f"{path.name} is not a Python runtime of format {FORMAT} (kind {m.get('kind')!r}, "
            f"format {m.get('format')!r}) - build one with `uqs deploy component build python`",
        )
    if m.get("interpreter") not in m["files"]:
        raise ReleaseError("artifact", f"{path.name} lacks its interpreter {m.get('interpreter')}")
    return PythonRuntime(path, c.sha256, m, c.pinned, c.manifest_sha256)


def for_release(path: Path | str | None, target: dict) -> PythonRuntime | None:
    """The runtime `uqs deploy build --python-runtime` carries, read whole and
    fitted to the release's `target`; None when none is given."""
    if path is None:
        return None
    rt = read_runtime(Path(path))
    rt.fits(target)
    return rt


def from_release(release: Path, manifest: dict, out_dir: Path) -> PythonRuntime | None:
    """The runtime the release at `release` carries (#1646), extracted into
    `out_dir` and read whole; None when it carries none.

    Pinned by the release: its manifest records the runtime's sha256, the
    release's own checks cover those bytes, and the copy is refused unless it
    has that sha256 - so the `.sha256` written beside it is the release's
    record, never a digest taken on trust."""
    record = (manifest.get("python") or {}).get("runtime")
    if not record:
        return None
    want = record["sha256"]
    name = f"python-runtime-{record['version']}-{record['os']}-{record['arch']}.tar.gz"
    path = out_dir / want / name
    if not (path.is_file() and _sha256(path) == want):
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{name}.partial")
        with tarfile.open(release, "r:gz") as tar:
            member = tar.extractfile(record["archive"])
            if member is None:
                raise ReleaseError("artifact", f"{release.name} lacks {record['archive']}")
            with member, partial.open("wb") as copy:
                copy.write(member.read())
        if _sha256(partial) != want:
            partial.unlink()
            raise ReleaseError(
                "artifact", f"{release.name}'s {record['archive']} is not the sha256 {want} "
                "its manifest records",
            )  # fmt: skip
        partial.replace(path)
    Path(f"{path}.sha256").write_text(f"{want}  {name}\n")
    rt = read_runtime(path)
    rt.carried_by = release.name
    return rt


def fetch(python: str, arch: str, runner: Runner = subprocess.run) -> Path:
    """The CPython uv installs for linux `arch`, Python `python` (X.Y): its
    installation directory under `uv python dir`. Nothing is put on PATH."""
    if arch not in PLATFORMS:
        raise ReleaseError("arguments", f"--arch {arch!r} must be one of {', '.join(PLATFORMS)}")
    if not re.fullmatch(r"3\.\d{1,2}", python):
        raise ReleaseError("arguments", f"--python {python!r} must be major.minor, e.g. 3.14")
    key = f"cpython-{python}-linux-{arch}-gnu"
    steps = {
        "installing": ["uv", "python", "install", "--no-bin", "--no-registry", key],
        "locating": ["uv", "python", "dir"],
    }
    out = ""
    for what, argv in steps.items():
        r = runner(argv, capture_output=True, text=True, check=False)
        if r.returncode:
            tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-10:])
            raise ReleaseError("runtime", f"{what} {key} with uv failed:\n{tail}")
        out = r.stdout
    root = Path(out.strip().splitlines()[-1])
    found = []
    for d in root.iterdir() if root.is_dir() else ():
        m = UV_KEY.fullmatch(d.name)
        if m and not d.is_symlink() and f"{m[1]}.{m[2]}" == python and m[5] == arch:
            found.append((int(m[3]), d))
    if not found:
        raise ReleaseError("runtime", f"uv installed {key}, and {root} holds no such directory")
    return max(found)[1]


def _glibc(data: bytes) -> tuple[int, int] | None:
    """The newest glibc symbol version an ELF file asks for."""
    if not data.startswith(b"\x7fELF"):
        return None
    versions = [(int(a), int(b)) for a, b in GLIBC.findall(data)]
    return max(versions) if versions else None


def build_runtime(source: Path, out_dir: Path) -> PythonRuntime:
    """The Python runtime artifact of a uv-installed CPython at `source`, into
    `out_dir`: every regular file; every symbolic link left out, and listed."""
    m = UV_KEY.fullmatch(source.name)
    if not m or m[4] not in OS_NAMES:
        raise ReleaseError("runtime", f"{source} is not a CPython uv installed: no uv key name")
    minor = f"{m[1]}.{m[2]}"
    interpreter = f"bin/python{minor}"
    if not (source / interpreter).is_file() or (source / interpreter).is_symlink():
        raise ReleaseError("runtime", f"{source} has no interpreter at {interpreter}")
    members: dict[str, tuple[bytes, int, int]] = {}
    links: list[str] = []
    floor: tuple[int, int] | None = None
    for root, dirs, files in os.walk(source):
        for name in sorted(dirs + files):
            path = Path(root) / name
            rel = path.relative_to(source).as_posix()
            if path.is_symlink():
                links.append(rel)
            elif path.is_file():
                data = path.read_bytes()
                mode = 0o755 if os.access(path, os.X_OK) else 0o644
                members[rel] = (data, mode, int(path.stat().st_mtime))
                found = _glibc(data)
                floor = max(floor, found) if floor and found else floor or found
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
    build = source / "BUILD"
    manifest = {
        "format": FORMAT,
        "kind": KIND,
        "implementation": "cpython",
        "key": source.name,
        "version": f"{minor}.{m[3]}",
        "build": build.read_text().strip() if build.is_file() else None,
        "os": OS_NAMES[m[4]],
        "arch": m[5],
        "libc": m[6],
        "glibc": f"{floor[0]}.{floor[1]}" if floor else None,
        "interpreter": interpreter,
        "links_left_out": sorted(links),
        "files": {rel: hashlib.sha256(m[0]).hexdigest() for rel, m in sorted(members.items())},
    }
    stem = f"python-runtime-{manifest['version']}-{manifest['os']}-{manifest['arch']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.tar.gz"
    data = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for rel, (body, mode, mtime) in sorted(members.items()):
                _add(tar, rel, body, mode, mtime)
            _add(tar, MANIFEST, data, 0o644, MTIME)
    Path(f"{path}.sha256").write_text(f"{_sha256(path)}  {path.name}\n")
    (out_dir / f"{stem}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return read_runtime(path)


def _add(tar: tarfile.TarFile, name: str, data: bytes, mode: int, mtime: int) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), mode, mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    tar.addfile(info, io.BytesIO(data))


def venv_lines(interpreter: str, py: dict) -> list[str]:
    """prepare's offline install with a bundled Python, run in the release:
    .venv from `interpreter` by `-m venv`, then - as scripts/install_release.py
    does - the locked dependencies with their hashes required, and uqs, by pip
    from the release's wheels (`py`, the manifest's python entry). No uv."""
    pip = f"{PIP_ENV} .venv/bin/python -m pip install --quiet --no-index"
    return [
        f"{q(interpreter)} -m venv .venv",
        f"{pip} --find-links {q(py['wheel_dir'])} --require-hashes -r {q(py['requirements'])}",
        f"{pip} --no-deps {q(py['app_wheel'])}",
        *(f"{pip} --no-deps {q(w)}" for w in py.get("extra_wheels", [])),
    ]
