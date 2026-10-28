"""A bundled Python (#1557, step 7): the runtime artifact `uqs deploy
component build python` makes of the CPython uv installs - every regular file,
content-addressed, its links left out and listed - and a push with
`python_mode = "bundled"`: what it holds the runtime to, what it sends the
server (no uv, .venv by -m venv and pip from the runtime's interpreter) and
what it records. A release can carry one for scripts/install_release.py.

The installation here is a stand-in laid out as uv lays one out, with an
interpreter whose bytes ask for glibc symbol versions as an ELF file does. The
real interpreter, installed and run offline, is test_offline_install.py's.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_deploy import SERVER, FakeRemote, _done, _verified, parse_args
from test_deploy_q_runtime import release

from uqs.deploy import artifact, driver, prune, python_runtime
from uqs.deploy import build as release_build
from uqs.deploy.artifact import ReleaseError, glibc_floor
from uqs.deploy.config import DeployError, make_config

BASE: dict[str, Any] = {"artifact": "x", "host": "h", "dest": "/srv/uqf", "profile": "capture"}
KEY = "cpython-3.14.9-linux-x86_64-gnu"


def installation(root: Path, key: str = KEY, licence: bool = False) -> Path:
    """A CPython as uv installs one, under `root`/`key`: an interpreter asking
    for glibc 2.17 and 2.28, a link to it, a library, CPython's notice."""
    home = root / key
    minor = ".".join(key.split("-")[1].split(".")[:2]) if key.startswith("cpython-") else "3.14"
    (home / "bin").mkdir(parents=True)
    (home / "lib" / "python3.14").mkdir(parents=True)
    interpreter = home / "bin" / f"python{minor}"
    interpreter.write_bytes(b"\x7fELF\x02\x01\x01\0GLIBC_2.17\0GLIBC_2.28\0GLIBC_PRIVATE\0")
    interpreter.chmod(0o755)
    (home / "bin" / "python3").symlink_to(f"python{minor}")
    (home / "lib" / "python3.14" / "os.py").write_text("# os\n")
    (home / "lib" / "python3.14" / "LICENSE.txt").write_text("PSF LICENSE AGREEMENT\n")
    (home / "BUILD").write_text("20261021\n")
    if licence:
        (home / "kc.lic").write_text("a KX licence\n")
    return home


def runtime(tmp_path: Path, key: str = KEY) -> python_runtime.PythonRuntime:
    return python_runtime.build_runtime(installation(tmp_path / "uv", key), tmp_path / "dist")


# --- the build ------------------------------------------------------------------------


def test_the_runtime_is_every_file_and_no_link(tmp_path):
    rt = runtime(tmp_path)
    m = rt.manifest
    assert rt.path.name == "python-runtime-3.14.9-linux-x86_64.tar.gz"
    assert (m["kind"], m["key"], m["version"], m["build"]) == (
        "python-runtime", KEY, "3.14.9", "20261021")  # fmt: skip
    assert (rt.platform, m["libc"], m["glibc"]) == (("linux", "x86_64"), "gnu", "2.28")
    assert m["interpreter"] == "bin/python3.14" and m["links_left_out"] == ["bin/python3"]
    files = ["BUILD", "bin/python3.14", "lib/python3.14/LICENSE.txt", "lib/python3.14/os.py"]
    assert sorted(m["files"]) == files, "CPython's notice ships; links and licence keys never"
    assert rt.minor == "3.14" and rt.pinned


def test_the_same_installation_makes_the_same_bytes(tmp_path):
    source = installation(tmp_path / "uv")
    a = python_runtime.build_runtime(source, tmp_path / "a")
    b = python_runtime.build_runtime(source, tmp_path / "b")
    assert a.sha256 == b.sha256


def test_a_licence_key_never_enters_it(tmp_path):
    source = installation(tmp_path / "uv", licence=True)
    with pytest.raises(ReleaseError, match="kc.lic' is a licence file - none is ever shipped"):
        python_runtime.build_runtime(source, tmp_path / "dist")


def test_only_a_cpython_uv_installed_is_packed(tmp_path):
    with pytest.raises(ReleaseError, match="is not a CPython uv installed"):
        python_runtime.build_runtime(installation(tmp_path / "uv", "python3.14"), tmp_path / "d")


def test_a_q_runtime_is_not_a_python_runtime(tmp_path):
    from test_deploy_q_runtime import runtime as q_runtime_of

    qrt = q_runtime_of(tmp_path / "q")
    with pytest.raises(ReleaseError, match="is not a Python runtime of format 1"):
        python_runtime.read_runtime(qrt.path)


def test_uv_installs_it_and_nothing_goes_on_path(tmp_path):
    root = tmp_path / "uv-python"
    installation(root, "cpython-3.14.5-linux-x86_64-gnu")
    installation(root, KEY)
    installation(root, "cpython-3.14.9-linux-aarch64-gnu")
    (root / "cpython-3.14-linux-x86_64-gnu").symlink_to(root / KEY)
    ran: list[list[str]] = []

    def uv(argv, **_kw):
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=f"{root}\n", stderr="")

    assert python_runtime.fetch("3.14", "x86_64", uv) == root / KEY
    assert ran == [
        ["uv", "python", "install", "--no-bin", "--no-registry", "cpython-3.14-linux-x86_64-gnu"],
        ["uv", "python", "dir"],
    ]
    with pytest.raises(ReleaseError, match="--arch 'arm64' must be one of x86_64, aarch64"):
        python_runtime.fetch("3.14", "arm64", uv)


@pytest.mark.parametrize(
    ("target", "fits"),
    [
        ({"os": "linux", "arch": "x86_64", "python": "3.14"}, True),
        ({"os": "linux", "arch": "amd64", "python": "3.14"}, True),
        ({"os": "linux", "arch": "aarch64", "python": "3.14"}, False),
        ({"os": "linux", "arch": "x86_64", "python": "3.13"}, False),
    ],
)
def test_it_fits_only_the_release_it_was_built_for(tmp_path, target, fits):
    rt = runtime(tmp_path)
    if fits:
        rt.fits(target)
        return
    with pytest.raises(ReleaseError, match="is Python 3.14.9 for linux x86_64, and the release"):
        rt.fits(target)


# --- glibc: the floor the wheels set ---------------------------------------------------


@pytest.mark.parametrize(
    ("wheels", "floor"),
    [
        (["rich-15.0.0-py3-none-any.whl"], None),
        (["a-1-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"], "2.17"),
        (
            ["a-1-cp314-cp314-manylinux_2_28_x86_64.whl", "b-1-cp39-abi3-manylinux2014_x86_64.whl"],
            "2.28",
        ),  # fmt: skip
        (["a-1-cp314-cp314-manylinux_2_34_x86_64.manylinux_2_28_x86_64.whl"], "2.28"),
    ],
)
def test_the_glibc_floor_is_the_highest_any_wheel_needs(wheels, floor):
    assert glibc_floor(wheels) == floor


def test_preflight_refuses_a_glibc_below_the_floor():
    target = {"os": "linux", "arch": "x86_64", "python": "3.14", "glibc": "2.28"}
    facts = {"os": "Linux", "arch": "x86_64", "python": "3.14", "qversion": "5.0"}
    assert "is not glibc, and the release's wheels need glibc 2.28" in " ".join(
        artifact.compatible(target, facts)
    )
    assert "is glibc 2.17" in " ".join(artifact.compatible(target, {**facts, "glibc": "2.17"}))
    assert artifact.compatible(target, {**facts, "glibc": "2.28"}) == []


# --- a release that carries one --------------------------------------------------------


def test_a_release_carries_it_for_the_installer(tmp_path):
    rt = runtime(tmp_path)
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "init.q").write_text("/ the tree\n")
    py = tmp_path / "py"
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("")
    (py / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
    pkg = release_build.build_artifact(
        root, tmp_path / "out", rid="20261007T000000Z-0123456789ab", rev="0123456789abcdef",
        dirty=False, files=["src/init.q"], python_dir=py, carried=rt,
        target=artifact.Target(os="linux", arch="x86_64", python="3.14"),
    )  # fmt: skip
    carried = pkg.manifest["python"]["runtime"]
    assert carried["archive"] == python_runtime.CARRIED and carried["sha256"] == rt.sha256
    assert pkg.manifest["files"][python_runtime.CARRIED] == rt.sha256
    assert pkg.manifest["target"]["glibc"] is None, "a pure-Python set of wheels needs none"
    with pytest.raises(ReleaseError, match="the release's wheels are for Python 3.13"):
        python_runtime.for_release(rt.path, {"os": "linux", "arch": "x86_64", "python": "3.13"})


# --- the config ------------------------------------------------------------------------


def test_bundled_python_needs_a_runtime_and_pins_it_like_the_others():
    # without python_runtime it may be the release's own (#1646): the push decides
    assert make_config(**BASE, python_mode="bundled").python_runtime is None
    with pytest.raises(DeployError, match="python_runtime - bundled mode's - is set"):
        make_config(**BASE, python_runtime="py.tar.gz")
    cfg = make_config(**BASE, python_mode="bundled", python_runtime="py.tar.gz",
                      python_runtime_sha256="AB" * 32, accept_digest="rebuilt")  # fmt: skip
    assert cfg.python_runtime_sha256 == "ab" * 32 and cfg.accept_digest == "rebuilt"


# --- the push --------------------------------------------------------------------------

#: The server: the runtime directories absent, then installed; the installed
#: Python answers with the version it was built as.
ABSENT = {
    "the runtime arrived as": _done("runtime=ok\n"),
    "echo runtime=absent": _done("runtime=absent\n"),
    " -I -c ": _done("UQF_PYTHON 3.14.9 cpython\n"),
    "glibc=$(getconf": _done(SERVER + "glibc=2.35\ndata=present\n"),
    "deploy_smoke.q": _done("DEPLOY_SMOKE_OK\n"),
    "uqs data hdb-check": _done('{"hdb": "/data/hdb", "present": false}\n'),
    "--ports-free": _done(json.dumps({"busy": {}}) + "\nDEPLOY_VERIFY_OK\n"),
    "uqs deploy verify --profile": _verified(True),
}


def push(tmp_path: Path, rt: python_runtime.PythonRuntime, *extra: str, state: dict | None = None):
    remote, out = FakeRemote({**ABSENT, **(state or {})}), io.StringIO()
    pkg = release(tmp_path / "release")
    argv = [str(pkg), "--host", "uqf-server", "--dest", "/opt/uqf", "--profile", "capture",
            "--torq-home", "/opt/torq", "--python-mode", "bundled",
            "--python-runtime", str(rt.path)]  # fmt: skip
    code = driver.deploy(parse_args([*argv, *extra]), remote, out=out)
    return code, remote, out.getvalue()


def test_the_venv_is_made_from_the_bundled_python_with_no_uv(tmp_path):
    rt = runtime(tmp_path)
    code, remote, _ = push(tmp_path, rt)
    assert code == 0
    home = f"/opt/uqf/shared/runtimes/python/{rt.sha256}"
    (prepare,) = remote.stage("prepare")
    assert f"{home}/bin/python3.14 -m venv .venv" in prepare
    assert "PIP_NO_INDEX=1" in prepare and "--require-hashes" in prepare
    assert "uv venv" not in prepare and "uv pip" not in prepare
    assert f"export UQF_PYTHON_RUNTIME={home}" in prepare, "deploy.env names it, for prune"


def test_preflight_looks_for_no_uv_and_still_checks_the_host(tmp_path):
    rt = runtime(tmp_path)
    _, remote, _ = push(tmp_path, rt)
    (preflight,) = [s for s in remote.stage("preflight") if "glibc=$(getconf" in s]
    assert "command -v uv" not in preflight and "uv python find" not in preflight
    assert "pgrep lsof hostname" in preflight and "hostname -I" in preflight
    assert "DEPLOY_Q_OK" in preflight, "an existing q is still resolved there"


def test_it_is_installed_and_run_before_the_release_arrives(tmp_path):
    rt = runtime(tmp_path)
    _, remote, out = push(tmp_path, rt)
    stages = [s for s, _ in remote.scripts]
    assert stages.index("runtime") < stages.index("transfer")
    assert remote.puts[0].endswith(rt.path.name)
    assert remote.ran(f"/opt/uqf/shared/runtimes/python/{rt.sha256}/bin/python3.14 -I -c")
    py = json.loads(out)["components"]["python"]
    assert (py["mode"], py["sha256"], py["version"], py["install"]) == (
        "bundled", rt.sha256, "3.14.9", "installed")  # fmt: skip
    assert py["requires"] == [
        "a linux x86_64 host, glibc 2.28 or later; no uv, no Python of its own"
    ]


def test_a_python_that_runs_as_another_version_is_refused(tmp_path):
    rt = runtime(tmp_path)
    code, _, out = push(tmp_path, rt, state={" -I -c ": _done("Traceback\n")})
    report = json.loads(out)
    assert code == 1 and report["stage"] == "runtime"
    assert "did not run there, not 3.14.9" in report["error"]


def test_a_runtime_for_another_python_is_refused_before_anything_runs(tmp_path):
    rt = runtime(tmp_path, "cpython-3.13.5-linux-x86_64-gnu")
    with pytest.raises(DeployError, match="is Python 3.13.5 for linux x86_64"):
        push(tmp_path, rt)


def test_the_dry_run_shows_it(tmp_path):
    rt = runtime(tmp_path)
    code, remote, out = push(tmp_path, rt, "--dry-run")
    assert code == 0 and remote.puts == []
    assert f"python bundled (--python-mode): /opt/uqf/shared/runtimes/python/{rt.sha256}" in out
    assert "into a .venv made from the bundled Python 3.14.9 by -m venv and pip (no uv)" in out


def test_an_unpinned_python_runtime_needs_allow_unpinned(tmp_path):
    rt = runtime(tmp_path)
    Path(f"{rt.path}.sha256").unlink()
    with pytest.raises(DeployError, match="no python_runtime_sha256 pins it"):
        push(tmp_path, rt)


# --- the server: write-once, and kept while a release names it ---------------------


def test_it_installs_write_once_and_prune_keeps_it_while_named(tmp_path):
    from test_deploy_torq_runtime_server import R1, R2, LocalRemote, _dep

    from uqs.deploy import runtime_install

    rt, dest = runtime(tmp_path), tmp_path / "uqf"
    assert runtime_install.install(_dep(dest), rt, R1) == "installed"
    home = Path(rt.directory(str(dest)))
    assert sorted(p.name for p in (home / "bin").iterdir()) == ["python3.14"], "no link"
    assert not os.access(home / "bin" / "python3.14", os.W_OK)
    assert runtime_install.install(_dep(dest), rt, R2) == "reused"
    for rid in (R1, R2):
        (dest / "releases" / rid).mkdir(parents=True)
        (dest / "releases" / rid / "deploy-report.json").write_text('{"status": "deployed"}')
    dep = _dep(dest)
    dep.python_runtime = rt
    (dest / "releases" / R2 / "deploy.env").write_text("\n".join(dep.env_lines()) + "\n")
    (dest / "current").symlink_to(f"releases/{R2}")
    out = io.StringIO()
    assert prune.prune(dep.cfg, LocalRemote(), keep=0, out=out) == 0
    kept = {k["runtime"]: k["why"] for k in json.loads(out.getvalue())["runtimes"]["kept"]}
    assert kept[f"python/{rt.sha256}"] == f"named by {R2}"
    assert home.is_dir()


# --- the host contract preflight checks -----------------------------------------------

CONTRACT_TOOLS = artifact.HOST_TOOLS


def _contract(tmp_path: Path, missing: str = "", python3: str = "") -> subprocess.CompletedProcess:
    """PREFLIGHT's host-contract checks alone, on a PATH holding each tool but
    `missing`: a stand-in that succeeds, or `python3` as given."""
    from uqs.deploy.preflight import PREFLIGHT as text

    start = text.index("# The host contract")
    end = text.index('echo "glibc=')
    tools = tmp_path / "bin"
    tools.mkdir()
    for tool in CONTRACT_TOOLS:
        if tool != missing:
            stand_in = tools / tool
            stand_in.write_text(python3 if tool == "python3" and python3 else "#!/bin/sh\nexit 0\n")
            stand_in.chmod(0o755)
    body = 'fail() { echo "$*" >&2; exit 1; }\n' + text[start:end] + "echo contract=ok\n"
    bash = subprocess.run(["sh", "-c", "command -v bash"], capture_output=True, text=True).stdout
    return subprocess.run(
        [bash.strip(), "-c", body], env={"PATH": str(tools)}, capture_output=True, text=True,
        check=False,
    )  # fmt: skip


def test_the_host_contract_passes_a_host_with_every_tool(tmp_path):
    r = _contract(tmp_path)
    assert r.returncode == 0 and "contract=ok" in r.stdout, r.stderr


@pytest.mark.parametrize("tool", ["lsof", "pgrep", "gzip", "envsubst"])
def test_the_host_contract_names_a_missing_tool(tmp_path, tool):
    r = _contract(tmp_path, missing=tool)
    assert r.returncode == 1 and f"{tool} is not on PATH - the release needs it" in r.stderr


def test_the_host_contract_refuses_a_python3_before_3_8(tmp_path):
    r = _contract(tmp_path, python3="#!/bin/sh\nexit 1\n")
    assert r.returncode == 1 and "python3 is older than 3.8" in r.stderr


# --- the runtime a release carries, reused by a push (#1646) -----------------------


def carrying(tmp_path: Path, rt: python_runtime.PythonRuntime | None) -> Path:
    """A small release of the peachq runtime, carrying `rt` when given."""
    root = tmp_path / "tree"
    files = ["pyproject.toml", "src/init.q", "scripts/deploy_smoke.q"]
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    py = tmp_path / "py"
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("rich==15.0.0 --hash=sha256:00\n")
    (py / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
    pkg = release_build.build_artifact(
        root, tmp_path / "dist", rid="20261007T000000Z-0123456789ab", rev="0123456789abcdef",
        dirty=False, files=files, python_dir=py, carried=rt, runtime="peachq",
        target=artifact.Target(os="linux", arch="x86_64", python="3.14"),
    )  # fmt: skip
    return pkg.path


def push_carried(tmp_path: Path, pkg: Path, *extra: str, state: dict | None = None):
    remote, out = FakeRemote({**ABSENT, **(state or {})}), io.StringIO()
    argv = [str(pkg), "--host", "uqf-server", "--dest", "/opt/uqf", "--profile", "capture",
            "--torq-home", "/opt/torq", "--python-mode", "bundled"]  # fmt: skip
    code = driver.deploy(parse_args([*argv, *extra]), remote, out=out)
    return code, remote, out.getvalue()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache" / "uqf" / "runtimes" / "python"


def test_a_push_with_no_python_runtime_uses_the_one_the_release_carries(tmp_path, cache):
    rt = runtime(tmp_path)
    pkg = carrying(tmp_path / "release", rt)
    code, remote, out = push_carried(tmp_path, pkg)
    report = json.loads(out)
    assert code == 0 and report["exceptions"] == [], "the release's own record pins it"
    py = report["components"]["python"]
    assert (py["mode"], py["sha256"], py["carried_by"]) == ("bundled", rt.sha256, pkg.name)
    home = f"/opt/uqf/shared/runtimes/python/{rt.sha256}"
    (prepare,) = remote.stage("prepare")
    assert f"{home}/bin/python3.14 -m venv .venv" in prepare
    # the very bytes the runtime was built as: extracted from the release, then sent
    sent = cache / rt.sha256 / rt.path.name
    assert sent.read_bytes() == rt.path.read_bytes()
    assert remote.puts[0].endswith(f"/{rt.path.name}")


def test_the_dry_run_names_the_release_that_carries_it(tmp_path, cache):
    rt = runtime(tmp_path)
    pkg = carrying(tmp_path / "release", rt)
    code, remote, out = push_carried(tmp_path, pkg, "--dry-run")
    assert code == 0 and remote.puts == []
    assert f"the runtime {pkg.name} carries" in out


def test_a_release_that_carries_none_is_refused_naming_both_ways(tmp_path, cache):
    pkg = carrying(tmp_path / "release", None)
    with pytest.raises(DeployError, match=r"needs python_runtime.*carries none.*--python-runtime"):
        push_carried(tmp_path, pkg)


def test_a_python_runtime_given_wins_over_the_carried_one(tmp_path, cache):
    carried = runtime(tmp_path / "a")
    given = runtime(tmp_path / "b", "cpython-3.14.8-linux-x86_64-gnu")
    pkg = carrying(tmp_path / "release", carried)
    probe = {" -I -c ": _done("UQF_PYTHON 3.14.8 cpython\n")}
    code, _, out = push_carried(tmp_path, pkg, "--python-runtime", str(given.path), state=probe)
    py = json.loads(out)["components"]["python"]
    assert code == 0 and py["sha256"] == given.sha256 and "carried_by" not in py
    assert not cache.exists(), "nothing is extracted when a runtime is given"


def test_a_pin_holds_the_carried_runtime_too(tmp_path, cache):
    pkg = carrying(tmp_path / "release", runtime(tmp_path))
    with pytest.raises(DeployError, match="python_runtime_sha256"):
        push_carried(tmp_path, pkg, "--python-runtime-sha256", "ab" * 32)


def test_an_altered_extracted_copy_is_extracted_again(tmp_path, cache):
    rt = runtime(tmp_path)
    pkg = carrying(tmp_path / "release", rt)
    push_carried(tmp_path, pkg, "--dry-run")
    copy = cache / rt.sha256 / rt.path.name
    copy.write_bytes(b"not the runtime")
    code, _, _ = push_carried(tmp_path, pkg, "--dry-run")
    assert code == 0 and copy.read_bytes() == rt.path.read_bytes()
