"""An offline install, end to end (#1557 step 7, #1646): a real artifact,
carrying a bundled Python, beside a bundled PeachQ, installed with the network
cut off - by the push's own server scripts, and by scripts/install_release.py.

What is real:

  - the Python: the CPython uv installed that runs these tests (sys.base_prefix,
    python-build-standalone), packed by `python_runtime.build_runtime` exactly
    as `uqs deploy component build python` packs one - the host's OS and
    architecture, since a test can only run what this host runs;
  - the artifact: `build.build_artifact`, as `uqs deploy build` calls it, over
    this checkout's release files (payload.tracked_files), with the uqs wheel
    built from this tree by `uv build --offline` and, as its
    locked dependencies, the wheels of what `uqs --version` imports - typer,
    rich, loguru and their requirements - packed from this environment's
    installed distributions, with their hashes in requirements.txt;
  - the server side: the push's own scripts run by bash - the write-once
    runtime installs, the probes, transfer and prepare - and the installer run
    from the unpacked release; the .venv each makes runs `uqs --version`.

The PeachQ is a stand-in binary that answers its probe (test_deploy_q_runtime's).

How the network is cut, for every server script and every command after it:

  1. a sandbox with no network, where this host has one: macOS `sandbox-exec`
     denying network*, or Linux `unshare --net`. `JAIL` is empty where neither
     works, and the test then rests on 2 and 3 alone;
  2. an environment of a fresh HOME, no proxy variables, PIP_NO_INDEX=1,
     PIP_CONFIG_FILE=/dev/null, UV_OFFLINE=1, UV_PYTHON_DOWNLOADS=never;
  3. tripwires that record any attempt and fail it: a sitecustomize on
     PYTHONPATH that replaces every socket connect and name lookup in each
     Python started - the bundled one included - and `uv`, `uvx`, `curl` and
     `wget` stand-ins first on PATH.

The test asserts the tripwire log is empty, and then proves the tripwires are
live: a fetch from the bundled Python is caught and logged, and - when the
sandbox is on - the same fetch with no tripwire fails too. What it does not
prove: a C extension or a subprocess started with -I/-E (ensurepip's own pip
is one) bypasses the sitecustomize, so outside the sandbox their silence rests
on --no-index alone; and the host is this one, not a clean Linux server.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata as md
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from test_deploy_q_runtime import peachq_entry

from uqs.deploy import artifact, bundled, payload, python_runtime, q_runtime, runtime_install
from uqs.deploy import build as release_build
from uqs.deploy.artifact import HOST_TOOLS
from uqs.deploy.config import make_config
from uqs.deploy.stages import Deployment

ROOT = Path(__file__).resolve().parents[3]
PY = f"{sys.version_info.major}.{sys.version_info.minor}"
SOURCE = Path(sys.base_prefix)
RID = "20261026T000000Z-0123456789ab"
#: What `uqs --version` imports beyond uqs itself: uqs.cli's import-time
#: third-party packages, measured; their requirements join them.
ROOTS = ("typer", "rich", "loguru", "packaging")
#: A sitecustomize: every connect and name lookup is logged, then refused.
GUARD = """
import os, socket
def _blocked(*args, **kwargs):
    with open(os.environ["UQF_NET_LOG"], "a") as log:
        log.write(f"python {os.getpid()}: {args[1:] or args!r}\\n")
    raise OSError("network blocked: an offline install reached out")
socket.socket.connect = socket.socket.connect_ex = _blocked
socket.getaddrinfo = socket.create_connection = _blocked
"""
FETCH = "import urllib.request; urllib.request.urlopen('http://pypi.org/simple/', timeout=5)"

pytestmark = [
    pytest.mark.skipif(
        python_runtime.UV_KEY.fullmatch(SOURCE.name) is None or shutil.which("uv") is None,
        reason="the tests' Python is not a CPython uv installed, or uv is not on PATH",
    ),
    # One worker for the module: `built` takes ~43s, and xdist builds a
    # module-scoped fixture once per worker that runs one of its tests (#1696).
    pytest.mark.xdist_group("offline-build"),
]


def _jail() -> list[str]:
    """A command prefix that runs its command with no network, or []."""
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        argv = ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"]
    elif shutil.which("unshare"):
        argv = ["unshare", "--net", "--map-root-user"]
    else:
        return []
    ok = subprocess.run([*argv, "true"], capture_output=True, check=False).returncode == 0
    return argv if ok else []


JAIL = _jail()


class Offline:
    """Where every server step runs: JAIL, a clean environment, tripwires."""

    def __init__(self, tmp: Path) -> None:
        self.log = tmp / "network.log"
        guard = tmp / "guard"
        (guard / "bin").mkdir(parents=True)
        (guard / "sitecustomize.py").write_text(GUARD)
        for tool in ("uv", "uvx", "curl", "wget"):
            fake = guard / "bin" / tool
            fake.write_text(f'#!/bin/sh\necho "{tool} $*" >> "$UQF_NET_LOG"\nexit 1\n')
            fake.chmod(0o755)
        # The host contract's tools this machine lacks (macOS has no envsubst,
        # rlwrap or timeout): the installer checks they are on PATH, and
        # nothing here runs them. A Linux server's own are preflight's to judge.
        for tool in HOST_TOOLS:
            if not shutil.which(tool):
                stand_in = guard / "bin" / tool
                stand_in.write_text("#!/bin/sh\nexit 0\n")
                stand_in.chmod(0o755)
        (tmp / "home").mkdir()
        keep = ("PATH", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME", "SHELL")
        self.env = {k: os.environ[k] for k in keep if k in os.environ}
        self.env.update(
            PATH=f"{guard / 'bin'}:{os.environ['PATH']}",
            HOME=str(tmp / "home"),
            PYTHONPATH=str(guard),
            UQF_NET_LOG=str(self.log),
            PIP_NO_INDEX="1",
            PIP_CONFIG_FILE=os.devnull,
            UV_OFFLINE="1",
            UV_PYTHON_DOWNLOADS="never",
        )
        assert not any("proxy" in k.lower() for k in self.env)
        self.puts: list[str] = []

    def command(self, argv: list[str], cwd: Path | None = None, **env: str):
        return subprocess.run(
            [*JAIL, *argv], cwd=cwd, env={**self.env, **env},
            capture_output=True, text=True, timeout=600, check=False,
        )  # fmt: skip

    # the Transport a Deployment drives
    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        return self.command(["bash", "-c", script])

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        self.puts.append(remote_path)
        shutil.copyfile(local, remote_path)

    def attempts(self) -> str:
        return self.log.read_text() if self.log.exists() else ""


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _closure(roots: tuple[str, ...]) -> dict[str, md.Distribution]:
    """The installed distributions `roots` need, their extras' aside."""
    found: dict[str, md.Distribution] = {}
    todo = list(roots)
    while todo:
        name = _canonical(todo.pop())
        if name in found:
            continue
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue  # a requirement whose marker excludes this host
        found[name] = dist
        needs = [r for r in dist.requires or [] if "extra ==" not in r]
        todo += [m[0] for r in needs if (m := re.match(r"[\w.-]+", r))]
    return found


def _repack(dist: md.Distribution, out: Path) -> Path:
    """An installed distribution as the wheel it was installed from: its files,
    its dist-info, and a RECORD of what the wheel holds."""
    info = next(f for f in dist.files or [] if f.name == "METADATA").parent.as_posix()
    tags = [ln.split(":", 1)[1].strip() for ln in (dist.read_text("WHEEL") or "").splitlines()
            if ln.startswith("Tag:")]  # fmt: skip
    # the compressed tag set, py2.py3-none-any, as the wheel's own name had it
    parts = [".".join(dict.fromkeys(t.split("-")[i] for t in tags)) for i in range(3)]
    name = re.sub(r"[-_.]+", "_", dist.metadata["Name"]).lower()
    wheel = out / f"{name}-{dist.version}-{'-'.join(parts)}.whl"
    skip = {f"{info}/{n}" for n in ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json")}
    record = []
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as z:
        for f in dist.files or []:
            rel = f.as_posix()
            if rel.startswith("..") or "__pycache__" in rel or rel in skip:
                continue
            data = Path(f.locate()).read_bytes()
            z.writestr(rel, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            record.append(f"{rel},sha256={digest.decode()},{len(data)}")
        z.writestr(f"{info}/RECORD", "\n".join([*record, f"{info}/RECORD,,"]) + "\n")
    return wheel


def _python_payload(into: Path) -> None:
    """requirements.txt and wheels/, as payload.python_payload lays them out:
    the uqs wheel of this tree, and its dependencies' with their hashes."""
    wheels = into / "wheels"
    wheels.mkdir(parents=True)
    r = subprocess.run(
        ["uv", "build", "--package", "uqs", "--wheel", "--offline", "--quiet",
         "--out-dir", str(wheels)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if r.returncode:
        pytest.skip(f"uv cannot build the uqs wheel offline here: {r.stderr.strip()[-300:]}")
    lines = []
    for dist in _closure(ROOTS).values():
        wheel = _repack(dist, wheels)
        sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        lines.append(f"{dist.metadata['Name']}=={dist.version} --hash=sha256:{sha}")
    (into / "requirements.txt").write_text("\n".join(sorted(lines)) + "\n")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    """The runtimes and the release, built once, the network still allowed."""
    tmp = tmp_path_factory.mktemp("offline-build")
    prt = python_runtime.build_runtime(SOURCE, tmp / "dist")
    host = f"{platform.system()}-{platform.machine()}"
    qrt = q_runtime.build_runtime(peachq_entry(tmp / "peachq", platform=host), tmp / "dist")
    _python_payload(tmp / "py")
    arch = artifact.ARCH_ALIASES.get(platform.machine(), platform.machine())
    pkg = release_build.build_artifact(
        ROOT, tmp / "dist", rid=RID, rev="0123456789abcdef", dirty=True,
        files=payload.tracked_files(ROOT, subprocess.run),
        target=artifact.Target(os=platform.system().lower(), arch=arch, python=PY),
        python_dir=tmp / "py", runtime="peachq", carried=prt,
    )  # fmt: skip
    return {"python": prt, "q": qrt, "pkg": artifact.read_artifact(pkg.path)}


def _uqs_version(offline: Offline, release: Path) -> str:
    r = offline.command([str(release / ".venv" / "bin" / "uqs"), "--version"], cwd=release)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip()


def _venv_home(release: Path) -> Path:
    cfg = (release / ".venv" / "pyvenv.cfg").read_text()
    found = re.search(r"^home = (.*)$", cfg, re.M)
    assert found, cfg
    return Path(found[1]).resolve()


def _tripwires_are_live(offline: Offline, python: Path) -> None:
    """A fetch is caught by the tripwire; with the sandbox on, also without it."""
    before = offline.attempts()
    assert offline.command([str(python), "-c", FETCH]).returncode != 0
    assert offline.attempts() != before, "the tripwire saw nothing: it is not loaded"
    if JAIL:
        bare = offline.command([str(python), "-c", FETCH], PYTHONPATH="")
        assert bare.returncode != 0, "the sandbox let a fetch through"


def test_a_push_installs_the_release_offline_on_its_bundled_python(built, tmp_path):
    offline, pkg = Offline(tmp_path), built["pkg"]
    dest = tmp_path / "uqf"
    cfg = make_config(
        artifact=str(pkg.path), host="uqf-server", dest=str(dest), profile="capture",
        init_data=True, torq_home="/opt/torq",
        q_mode="bundled", q_runtime=str(built["q"].path),
        python_mode="bundled", python_runtime=str(built["python"].path),
    )  # fmt: skip
    dep = Deployment(cfg, offline, target=pkg.manifest["target"], release=RID)
    dep.runtime = artifact.release_runtime(pkg.manifest)
    assert bundled.attach(cfg, dep, pkg) == []
    qrt, prt = dep.q_runtime, dep.python_runtime
    assert qrt is not None and prt is not None and prt.sha256 == built["python"].sha256
    for rt in (qrt, prt):
        assert runtime_install.install(dep, rt, RID) == "installed"
    assert runtime_install.probe(dep, qrt) == "5"
    assert runtime_install.probe_python(dep, prt) == prt.version
    release = Path(dep.transfer(pkg, RID))
    dep.prepare(str(release), pkg)

    home = Path(prt.directory(str(dest)))
    assert _venv_home(release) == (home / "bin").resolve(), ".venv is the bundled Python's"
    assert _uqs_version(offline, release) == "uqs 0.1.0"
    assert offline.attempts() == "", "no download was attempted"
    # run, it rewrote nothing of itself - no stale .pyc - so the next push reuses it
    for rt in (qrt, prt):
        assert runtime_install.state(dep, rt) == runtime_install.REUSE
    _tripwires_are_live(offline, release / ".venv" / "bin" / "python")


def test_the_installer_installs_the_release_offline_on_the_python_it_carries(built, tmp_path):
    offline, pkg = Offline(tmp_path), built["pkg"]
    release = tmp_path / "releases" / RID
    release.mkdir(parents=True)
    with tarfile.open(pkg.path) as tar:
        tar.extractall(release, filter="data")
    r = offline.command([sys.executable, "scripts/install_release.py"], cwd=release)
    assert r.returncode == 0, r.stdout + r.stderr
    carried = (release / ".release" / "python" / "bin").resolve()
    assert _venv_home(release) == carried, ".venv is the carried Python's, not the host's"
    assert _uqs_version(offline, release) == "uqs 0.1.0"
    assert offline.attempts() == "", "no download was attempted"
    # run, it still matches its manifest file for file: no .pyc was rewritten
    from test_install_release import installer

    installer.carried_python(release, pkg.manifest["python"]["runtime"])
    _tripwires_are_live(offline, release / ".venv" / "bin" / "python")
