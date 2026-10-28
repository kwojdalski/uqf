"""The q runtime artifact (#1557, step 6): the pinned PeachQ, for
`q_mode = "bundled"`.

    uqs deploy component build q

finds the PeachQ scripts/peachq.json pins - in its cache, or built into it
(scripts/peachq.py, the same resolver every PeachQ lane uses) - and writes,
into dist/ (or --output):

    q-runtime-peachq-<commit>-<os>-<arch>.tar.gz           the archive
    q-runtime-peachq-<commit>-<os>-<arch>.tar.gz.sha256    its checksum
    q-runtime-peachq-<commit>-<os>-<arch>.manifest.json    its manifest

Like the TorQ runtime (deploy/torq_runtime.py) it is its own artifact,
CONTENT-ADDRESSED - one member, the binary, at a fixed mtime with no owner,
gzip without a timestamp - and installed once on the server, under
shared/runtimes/q/<sha256>/ (deploy/runtime_install.py). deploy.env then sets
QCMD to its `q` and QHOME to its directory, and nothing else: which q that
is, the binary answers when asked (#1753), and a PeachQ runtime's stack takes
QCMD because it is PeachQ (uqs.interpreter.peachq_binary).

ONLY PEACHQ IS EVER BUNDLED. KX's q is licensed to the server it runs on: a
KDB-X or kdb+ q stays `q_mode = "existing"`, a path on the server with its
licence beside it. So the binary must answer, when run, that it is PeachQ;
the archive holds that binary and nothing else; and the reader refuses any
other implementation, and - as artifact.checked_archive refuses for every
artifact - any licence file.

The manifest records where the binary came from: the repository, the commit
(and, when $UQF_PEACHQ_COMMIT built another, the pinned one), the platform
and build options, and the .z.K it reported. It is always built from
scripts/peachq.py's cache, never from $QCMD, a binary of no recorded
provenance: a runtime says what it was built from. RUNTIME_MANIFEST.json,
installed beside the binary, is how scripts/peachq.py's `is_pinned` knows it
for the pinned build on the server.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from uqs.deploy.artifact import ARCH_ALIASES, ReleaseError, _sha256, checked_archive
from uqs.interpreter import PEACHQ, Q_INTERPRETER_ENV, peachq_module
from uqs.paths import UqsError

FORMAT = 1
KIND = "q-runtime"
MANIFEST = "RUNTIME_MANIFEST.json"
#: The one member: the binary, at the top of the runtime directory.
BINARY = "q"
RUNTIMES_DIR = "shared/runtimes/q"
MTIME = 315532800  # 1980-01-01, as the TorQ runtime's
#: Run by the binary, here at build time and on the server once installed:
#: one line, `UQF_Q <implementation> <.z.K>`. `.pq.load_natives` is PeachQ's
#: (interpreter.IDENTIFY_SCRIPT); KDB-X has no such name.
PROBE = (
    '-1 "UQF_Q ",$[-7h=type @[value;`.pq.load_natives;{0N}];"kdbx";"peachq"]," ",string .z.K;\n'
    "exit 0\n"
)
#: Never bundled, whatever the binary says: KX's q is the server's to provide.
KX_REFUSAL = (
    "KX's q is never bundled - it is licensed to the server it runs on; deploy it with "
    'q_mode = "existing" (--qcmd/--qhome) and its licence in QHOME'
)


def parse_probe(output: str) -> tuple[str, str]:
    """(implementation, version) from PROBE's output; ("", "") if it said nothing."""
    for line in reversed(output.splitlines()):
        parts = line.split()
        if len(parts) == 3 and parts[0] == "UQF_Q":
            return parts[1], parts[2]
    return "", ""


@dataclass
class QRuntime:
    path: Path
    sha256: str
    manifest: dict
    pinned: bool
    manifest_sha256: str

    #: What runtime_install and prune need of any runtime.
    component: ClassVar[str] = "q"
    label: ClassVar[str] = "q"
    runtimes_dir: ClassVar[str] = RUNTIMES_DIR

    @property
    def version(self) -> str:
        return self.manifest["version"]

    @property
    def platform(self) -> tuple[str, str]:
        return self.manifest["os"], self.manifest["arch"]

    def directory(self, dest: str) -> str:
        return f"{dest}/{RUNTIMES_DIR}/{self.sha256}"

    def env(self, dest: str) -> list[tuple[str, str]]:
        """deploy.env's q: QCMD and QHOME, and nothing else (#1753). A
        runtime declared on PeachQ takes QCMD because it answers as PeachQ,
        so the server never builds one."""
        home = self.directory(dest)
        return [(Q_INTERPRETER_ENV, f"{home}/{BINARY}"), ("QHOME", home)]

    def record(self, dest: str, install: str) -> dict:
        """The components entry for a bundled q (components.observed)."""
        home = self.directory(dest)
        m = self.manifest
        return {
            "path": f"{home}/{BINARY}",
            "home": home,
            "version": m["version"],
            "implementation": m["implementation"],
            "commit": m["commit"],
            "unpinned": m.get("unpinned"),
            "sha256": self.sha256,
            "runtime": home,
            "install": install,
            "requires": [f"a {m['os']} {m['arch']} host; no licence - PeachQ is MIT-licensed"],
        }


def read_runtime(path: Path) -> QRuntime:
    """A q runtime artifact, checked whole as a release is
    (artifact.checked_archive), holding PeachQ and nothing else."""
    c = checked_archive(path, MANIFEST)
    m = c.manifest
    if m.get("kind") != KIND or m.get("format") != FORMAT:
        raise ReleaseError(
            "artifact",
            f"{path.name} is not a q runtime of format {FORMAT} (kind {m.get('kind')!r}, "
            f"format {m.get('format')!r}) - build one with `uqs deploy component build q`",
        )
    if m.get("implementation") != PEACHQ:
        raise ReleaseError(
            "artifact", f"{path.name} bundles {m.get('implementation')!r}, not PeachQ: {KX_REFUSAL}"
        )
    if set(m["files"]) != {BINARY}:
        raise ReleaseError("artifact", f"{path.name} holds {sorted(m['files'])}, not one q binary")
    return QRuntime(path, c.sha256, m, c.pinned, c.manifest_sha256)


Probe = Callable[[Path], str]


def run_probe(binary: Path) -> str:
    """PROBE's output from `binary`, run here with an empty QHOME of its own:
    no licence is looked for, and none is found."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "probe.q"
        script.write_text(PROBE)
        try:
            r = subprocess.run(
                [str(binary), str(script), "-q"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={**os.environ, "QHOME": tmp},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseError("runtime", f"{binary} did not run: {exc}") from None
    return r.stdout


def pinned_peachq(scripts_dir: Path, env: Mapping[str, str] | None = None) -> Path:
    """The pinned PeachQ's cache entry - built into the cache if need be - by
    scripts/peachq.py, loaded by path (uqs.interpreter.peachq_module). Never
    $QCMD, even when it is PeachQ: a runtime is built from the pin (or
    $UQF_PEACHQ_COMMIT), so its manifest can say what it was built from."""
    source = os.environ if env is None else env
    try:
        module = peachq_module(scripts_dir)
    except UqsError as exc:
        raise ReleaseError("runtime", str(exc)) from None
    try:
        return module.resolve(dict(source)).parent
    except module.PeachQError as exc:
        raise ReleaseError("runtime", f"no pinned PeachQ to bundle: {exc}") from None


def build_runtime(entry: Path, out_dir: Path, probe: Probe = run_probe) -> QRuntime:
    """The q runtime artifact of a PeachQ cache entry (`entry`/q and
    `entry`/manifest.json, as scripts/peachq.py publishes them), into
    `out_dir`. Only the binary is packed: nothing else in `entry` ships."""
    binary = entry / BINARY
    try:
        built = json.loads((entry / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise ReleaseError("runtime", f"{entry} is not a PeachQ build: {exc}") from None
    impl, version = parse_probe(probe(binary))
    if impl != PEACHQ:
        why = f"says it is {impl}, not PeachQ - {KX_REFUSAL}" if impl else "did not say what it is"
        raise ReleaseError("runtime", f"{binary} {why}")
    system, _, machine = built["platform"].partition("-")
    manifest = {
        "format": FORMAT,
        "kind": KIND,
        "implementation": PEACHQ,
        "version": version,
        **{k: built[k] for k in ("repository", "commit", "platform", "make_target", "options")},
        "os": system.lower(),
        "arch": ARCH_ALIASES.get(machine.lower(), machine.lower()),
        "files": {BINARY: _sha256(binary)},
    }
    if built.get("unpinned"):
        manifest["unpinned"] = built["unpinned"]
    stem = f"q-runtime-peachq-{built['commit'][:12]}-{manifest['os']}-{manifest['arch']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.tar.gz"
    data = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
            _add(tar, BINARY, binary.read_bytes(), 0o755)
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
