"""A bundled TorQ on the server (#1557, step 5): installed write-once under
shared/runtimes/torq/<sha256>/, reused and never rewritten, refused when what
is there is not what its name says; kept by prune while any release left
names it; and named by each release's own deploy.env, so a rollback runs the
release it returns to on the TorQ it was deployed with.

The remote here is no fake: each script runs with bash against a destination
under tmp_path, so the install, the check, the listing and the removals are
the server's own programs (as test_deploy_prune.py's are).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_deploy_torq_runtime import VENDORED, vendored

from uqs.deploy import prune, rollback, runtime_install, torq_runtime
from uqs.deploy.config import DeployError, make_config
from uqs.deploy.stages import Deployment
from uqs.deploy.verify import OK_MARKER

R1, R2, R3 = (f"2026100{d}T120000Z-0123456789a{d}" for d in range(1, 4))


class LocalRemote:
    """Runs each script with bash on this machine; `put` copies the file."""

    def __init__(self) -> None:
        self.scripts: list[tuple[str, str]] = []
        self.puts: list[str] = []

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        self.scripts.append((stage, script))
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=timeout, check=False
        )

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        self.puts.append(remote_path)
        shutil.copyfile(local, remote_path)


def _runtime(tmp_path: Path, name: str, torq_q: str = "/ TorQ core\n") -> torq_runtime.Runtime:
    """A runtime built from a vendored tree whose torq.q is `torq_q`."""
    repo = vendored(tmp_path / f"repo-{name}", {**VENDORED, "lib/torq/torq.q": torq_q})
    return torq_runtime.build_runtime(repo, tmp_path / f"dist-{name}")


def _dep(dest: Path, remote: LocalRemote | None = None) -> Deployment:
    cfg = make_config(artifact="", host="uqf-server", dest=str(dest), profile="essential")
    return Deployment(cfg, remote or LocalRemote())


def _install(dest: Path, rt: torq_runtime.Runtime, remote: LocalRemote | None = None) -> str:
    return runtime_install.install(_dep(dest, remote), rt, R1)


# --- write-once ------------------------------------------------------------------------


def test_a_runtime_is_installed_whole_read_only_and_as_shipped(tmp_path):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"
    assert runtime_install.state(_dep(dest), rt) == runtime_install.INSTALL
    assert _install(dest, rt) == "installed"
    home = Path(rt.directory(str(dest)))
    for rel, text in VENDORED.items():
        installed = home / rel.replace("lib/", "", 1)
        assert installed.read_text() == text, rel
        assert not os.access(installed, os.W_OK), f"{rel} is read-only"
    assert os.access(home / "torq" / "torq.sh", os.X_OK)
    assert (home / runtime_install.MARKER).read_text().strip() == rt.sha256
    assert not list(home.parent.glob(".*.partial")), "nothing is left half-installed"
    assert runtime_install.state(_dep(dest), rt) == runtime_install.REUSE


def test_a_second_push_reuses_it_and_writes_nothing(tmp_path):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"
    _install(dest, rt)
    home = Path(rt.directory(str(dest)))
    before = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in home.rglob("*")}
    remote = LocalRemote()
    assert _install(dest, rt, remote) == "reused"
    assert remote.puts == [], "the archive is not even uploaded"
    assert not any("tar -xzf" in s for _, s in remote.scripts)
    assert {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in home.rglob("*")} == before


def _tamper(path: Path, text: str) -> None:
    path.chmod(0o644)
    path.write_text(text)


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda h: _tamper(h / runtime_install.MARKER, "0" * 64 + "\n"), "its marker names 000"),
        (lambda h: _tamper(h / "torq" / "torq.q", "/ patched\n"),
         "torq/torq.q and 0 other file"),
        (lambda h: (h / "torq" / "extra.q").write_text("x\n"), "1 file(s) not in its manifest"),
        (lambda h: _tamper(h / torq_runtime.MANIFEST, "{}"), "is not the one this runtime shipped"),
    ],
)  # fmt: skip
def test_a_tampered_runtime_is_refused_and_never_overwritten(tmp_path, change, said):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"
    _install(dest, rt)
    home = Path(rt.directory(str(dest)))
    change(home)
    snapshot = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    remote = LocalRemote()
    with pytest.raises(DeployError, match="is never overwritten") as err:
        _install(dest, rt, remote)
    assert said in str(err.value)
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == snapshot
    assert remote.puts == []


def _with_python(tmp_path: Path) -> torq_runtime.Runtime:
    repo = vendored(tmp_path / "repo-py", {**VENDORED, "lib/torq/helper.py": "x = 1\n"})
    return torq_runtime.build_runtime(repo, tmp_path / "dist-py")


def _cache(home: Path, name: str) -> None:
    (home / "torq" / "__pycache__").mkdir(exist_ok=True)
    (home / "torq" / "__pycache__" / name).write_bytes(b"\x00pyc")


def test_a_pyc_python_wrote_for_a_shipped_source_is_a_cache_not_a_change(tmp_path):
    """python-build-standalone ships no .pyc for some of its stdlib, so the
    first run of a module writes one beside its verified source. CPython
    checks it against that source before use. It does not make the runtime
    another runtime."""
    rt, dest = _with_python(tmp_path), tmp_path / "uqf"
    _install(dest, rt)
    home = Path(rt.directory(str(dest)))
    _cache(home, "helper.cpython-314.pyc")
    _cache(home, "helper.cpython-314.opt-1.pyc")
    assert runtime_install.state(_dep(dest), rt) == runtime_install.REUSE


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("orphan.cpython-314.pyc", "no source the runtime shipped"),
        ("helper.cpython-314.txt", "not a .pyc"),
    ],
)
def test_anything_else_in_a_pycache_is_still_refused(tmp_path, name, why):
    rt, dest = _with_python(tmp_path), tmp_path / "uqf"
    _install(dest, rt)
    _cache(Path(rt.directory(str(dest))), name)
    with pytest.raises(DeployError, match="1 file\\(s\\) not in its manifest"):
        _install(dest, rt, LocalRemote())


def test_a_half_finished_install_is_replaced_never_used(tmp_path):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"
    partial = dest / torq_runtime.RUNTIMES_DIR / f".{rt.sha256}.partial"
    (partial / "torq").mkdir(parents=True)
    (partial / "torq" / "torq.q").write_text("/ half\n")
    assert _install(dest, rt) == "installed"
    assert not partial.exists()
    assert (Path(rt.directory(str(dest))) / "torq" / "torq.q").read_text() == "/ TorQ core\n"


def test_an_archive_that_arrives_changed_is_never_extracted(tmp_path):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"

    class Corrupting(LocalRemote):
        def put(self, local, remote_path, timeout, stage):
            super().put(local, remote_path, timeout, stage)
            with open(remote_path, "ab") as f:
                f.write(b"!")

    with pytest.raises(DeployError, match="checksum mismatch: the runtime arrived as"):
        _install(dest, rt, Corrupting())
    assert not Path(rt.directory(str(dest))).exists()


# --- each release names its own runtime: prune and rollback ------------------------------


def _release(dest: Path, rid: str, rt: torq_runtime.Runtime, log: Path, **report) -> None:
    """A release as a push leaves it: its deploy.env naming `rt`, its report,
    and a stand-in uqs that logs what it was asked and the TORQHOME it saw."""
    root = dest / "releases" / rid
    (root / ".venv" / "bin").mkdir(parents=True)
    dep = _dep(dest)
    dep.torq_runtime = rt
    (root / "deploy.env").write_text("\n".join(dep.env_lines()) + "\n")
    body = {"profile": "essential", "processes": [{"process": "rdb1"}], "status": "deployed"}
    (root / "deploy-report.json").write_text(json.dumps({**body, **report}))
    uqs = root / ".venv" / "bin" / "uqs"
    verified = json.dumps({"passed": True, "reason": "", "processes": []})
    uqs.write_text(
        "#!/bin/bash\n"
        '[ "$3" = --help ] && exit 0  # `deploy check --help`: it has the command\n'
        f'echo "{rid} $1 $TORQHOME" >> {log}\n'
        f"if [ \"$1\" = deploy ]; then echo '{verified}'; echo {OK_MARKER}; fi\n"
    )
    uqs.chmod(0o755)


@pytest.fixture
def server(tmp_path):
    """R3 is current and replaced R2, which replaced R1. R1 ran on runtime C,
    R2 on A, R3 on B; runtime D was installed and is named by none."""
    dest, log = tmp_path / "uqf", tmp_path / "uqs.log"
    rts = {n: _runtime(tmp_path, n, f"/ TorQ {n}\n") for n in "ABCD"}
    for rt in rts.values():
        _install(dest, rt)
    _release(dest, R1, rts["C"], log)
    _release(dest, R2, rts["A"], log, previous_release=R1)
    _release(dest, R3, rts["B"], log, previous_release=R2)
    (dest / "current").symlink_to(f"releases/{R3}")
    (dest / torq_runtime.RUNTIMES_DIR / "notes").mkdir()
    return dest, rts, log


def _id(rt: torq_runtime.Runtime) -> str:
    """A runtime as prune names it: its component, then its sha256."""
    return f"torq/{rt.sha256}"


def _runtimes(dest: Path) -> set[str]:
    return {p.name for p in (dest / torq_runtime.RUNTIMES_DIR).iterdir()}


@pytest.mark.parametrize("keep", [0, 1])
def test_prune_keeps_every_runtime_a_remaining_release_names(server, keep):
    """R1 goes, and with it C, which only it named, and D, which none did. A
    stays for R2, the rollback target; B for R3, current."""
    dest, rts, _ = server
    out = io.StringIO()
    assert prune.prune(_dep(dest).cfg, LocalRemote(), keep=keep, out=out) == 0
    result = json.loads(out.getvalue())
    assert result["removed"] == [R1]
    assert sorted(result["runtimes"]["removed"]) == sorted([_id(rts["C"]), _id(rts["D"])])
    kept = {k["runtime"]: k["why"] for k in result["runtimes"]["kept"]}
    assert kept[_id(rts["A"])] == f"named by {R2}" and kept[_id(rts["B"])] == f"named by {R3}"
    assert kept["torq/notes"] == "not named like a runtime, so never removed"
    assert _runtimes(dest) == {rts["A"].sha256, rts["B"].sha256, "notes"}


def test_a_prune_dry_run_removes_no_runtime(server):
    dest, rts, _ = server
    out = io.StringIO()
    prune.prune(_dep(dest).cfg, LocalRemote(), keep=0, dry_run=True, out=out)
    assert _id(rts["D"]) in json.loads(out.getvalue())["runtimes"]["removed"]
    assert _runtimes(dest) == {rt.sha256 for rt in rts.values()} | {"notes"}


def test_rollback_runs_the_target_on_its_own_runtime(server):
    """current (R3, on B) stops on B; R2 starts and verifies on A - each from
    its own deploy.env, whatever runtime the other names."""
    dest, rts, log = server
    out = io.StringIO()
    assert rollback.rollback(_dep(dest).cfg, LocalRemote(), out=out) == 0
    a, b = (f"{rts[n].directory(str(dest))}/torq" for n in "AB")
    assert log.read_text().splitlines() == [f"{R3} stop {b}", f"{R2} start {a}", f"{R2} deploy {a}"]
    assert os.readlink(dest / "current") == f"releases/{R2}"
    assert _runtimes(dest) >= {rts["A"].sha256, rts["B"].sha256}, "nothing is removed"
