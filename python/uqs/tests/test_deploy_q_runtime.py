"""A bundled q (#1557, step 6): the runtime artifact `uqs deploy component build q`
makes of the pinned PeachQ - its one binary, content-addressed, checked whole -
and a push with `q_mode = "bundled"`: its refusals, KX's above all, its pins,
what it sends the server and what it records. No licence file ever enters an
archive. The server side - the write-once install, the probe, prune and
rollback - is test_deploy_q_runtime_server.py's.

The binary here is a real one, if tiny: a shell script that answers the
probe as PeachQ would (or as KDB-X would)."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
from test_deploy import _HEALTHY, FakeRemote, _done, _verified, parse_args

from uqs.deploy import artifact, driver, q_runtime, targets, torq_runtime
from uqs.deploy import build as release_build
from uqs.deploy.artifact import ReleaseError
from uqs.deploy.config import DeployError, make_config

BASE: dict[str, Any] = {"artifact": "x", "host": "h", "dest": "/srv/uqf", "profile": "capture"}
COMMIT = "a872e258f59ae218c8c516485d7d49607e6386ec"


def peachq_entry(root: Path, says: str = "peachq", platform: str = "Linux-x86_64") -> Path:
    """A PeachQ cache entry as scripts/peachq.py publishes one - `q` and its
    manifest.json - with a KX licence left beside them, which must not ship."""
    root.mkdir(parents=True, exist_ok=True)
    binary = root / "q"
    binary.write_text(f'#!/bin/sh\necho "UQF_Q {says} 5"\n')
    binary.chmod(0o755)
    manifest = {
        "repository": "https://github.com/peachq-org/peachq",
        "commit": COMMIT,
        "platform": platform,
        "make_target": "q",
        "options": {"RAY_MARCH": "x86-64-v2"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "k4.lic").write_text("a KX licence\n")
    return root


def runtime(tmp_path: Path, **entry: str) -> q_runtime.QRuntime:
    return q_runtime.build_runtime(peachq_entry(tmp_path / "peachq", **entry), tmp_path / "dist")


# --- the build: the pinned PeachQ, its binary and nothing else -----------------------


def test_the_runtime_is_the_binary_and_nothing_else(tmp_path):
    rt = runtime(tmp_path)
    with tarfile.open(rt.path, "r:gz") as tar:
        members = {m.name: m for m in tar}
        assert set(members) == {"q", q_runtime.MANIFEST}, "no licence, no manifest.json"
        f = tar.extractfile(members["q"])
        assert f is not None and f.read() == (tmp_path / "peachq" / "q").read_bytes()
        assert members["q"].mode == 0o755
    m = rt.manifest
    assert (m["implementation"], m["commit"], m["version"]) == ("peachq", COMMIT, "5")
    assert (m["os"], m["arch"], m["options"]) == ("linux", "x86_64", {"RAY_MARCH": "x86-64-v2"})
    assert rt.path.name == f"q-runtime-peachq-{COMMIT[:12]}-linux-x86_64.tar.gz"
    assert Path(f"{rt.path}.sha256").read_text().split()[0] == rt.sha256 and rt.pinned


def test_the_same_binary_makes_the_same_bytes(tmp_path):
    entry = peachq_entry(tmp_path / "peachq")
    a = q_runtime.build_runtime(entry, tmp_path / "a")
    b = q_runtime.build_runtime(entry, tmp_path / "b")
    assert a.sha256 == b.sha256


def test_an_arm_mac_build_says_so(tmp_path):
    assert runtime(tmp_path, platform="Darwin-arm64").platform == ("darwin", "aarch64")


def test_a_commit_override_is_recorded(tmp_path):
    entry = peachq_entry(tmp_path / "peachq")
    built = json.loads((entry / "manifest.json").read_text())
    unpinned = {"pinned_commit": "b" * 40, "reason": "testing a fix"}
    (entry / "manifest.json").write_text(json.dumps({**built, "unpinned": unpinned}))
    rt = q_runtime.build_runtime(entry, tmp_path / "dist")
    assert rt.manifest["unpinned"] == unpinned
    assert rt.record("/opt/uqf", "installed")["unpinned"] == unpinned


# --- KX's q is never bundled --------------------------------------------------------


def test_a_binary_that_is_kx_is_never_bundled(tmp_path):
    with pytest.raises(ReleaseError, match="says it is kdbx, not PeachQ - KX's q is never bundled"):
        runtime(tmp_path, says="kdbx")
    assert not (tmp_path / "dist").exists()


def test_a_runtime_of_another_implementation_is_refused(tmp_path):
    rt = runtime(tmp_path)
    manifest = {**rt.manifest, "implementation": "kdbx"}
    path = _archive(tmp_path / "kx.tar.gz", [("q", b"#!/bin/sh\n")], manifest)
    with pytest.raises(ReleaseError, match="bundles 'kdbx', not PeachQ: KX's q is never bundled"):
        q_runtime.read_runtime(path)


def test_the_cli_refuses_kx_by_name(capfd):
    from typer.testing import CliRunner

    from uqs import cli

    r = CliRunner().invoke(cli.app, ["deploy", "component", "build", "kdbx"])
    assert r.exit_code == 1
    assert "KX's q is never bundled" in r.output + capfd.readouterr().err


def test_uqf_peachq_is_never_bundled(tmp_path):
    """A binary of no recorded provenance: a runtime says what it was built from."""
    with pytest.raises(ReleaseError, match="UQF_PEACHQ names a binary of no recorded provenance"):
        q_runtime.pinned_peachq(tmp_path, {"UQF_PEACHQ": "/opt/q", "UQF_PEACHQ_REASON": "x"})


def test_the_pinned_peachq_comes_from_scripts_peachq(tmp_path):
    entry = peachq_entry(tmp_path / "cache" / "entry")
    (tmp_path / "peachq.py").write_text(
        "class PeachQError(Exception):\n    pass\n"
        f"def resolve(env):\n    return __import__('pathlib').Path({str(entry / 'q')!r})\n"
    )
    assert q_runtime.pinned_peachq(tmp_path, {}) == entry


def test_the_cli_builds_a_q_runtime(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli import deploy_component

    entry = peachq_entry(tmp_path / "peachq")
    monkeypatch.setattr(deploy_component, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(q_runtime, "pinned_peachq", lambda scripts: entry)
    r = CliRunner().invoke(cli.app, ["deploy", "component", "build", "q"])
    assert r.exit_code == 0, r.output
    (built,) = list((tmp_path / "dist").glob("q-runtime-peachq-*.tar.gz"))
    assert str(built) in r.output


# --- no licence file ever enters an archive -------------------------------------------


def _archive(path: Path, files: list[tuple[str, bytes]], manifest: dict) -> Path:
    """`files`, each listed in `manifest` with its true sha256."""
    listed = {n: hashlib.sha256(d).hexdigest() for n, d in files}
    body = json.dumps({**manifest, "files": listed}).encode()
    with tarfile.open(path, "w:gz") as tar:
        for member, data in [*files, (q_runtime.MANIFEST, body)]:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.parametrize("licence", ["k4.lic", "kc.lic", "qhome/KX.LIC"])
@pytest.mark.parametrize(
    "read",
    [
        pytest.param(q_runtime.read_runtime, id="q-runtime"),
        pytest.param(torq_runtime.read_runtime, id="torq-runtime"),
        pytest.param(artifact.read_artifact, id="release"),
        pytest.param(lambda p: artifact.checked_archive(p, q_runtime.MANIFEST), id="any-archive"),
    ],
)
def test_a_licence_file_can_never_enter_an_archive(tmp_path, licence, read):
    """Listed in its manifest with the right sha256 or not, a licence is refused
    by the one reader every artifact goes through."""
    rt = runtime(tmp_path)
    files = [("q", b"#!/bin/sh\n"), (licence, b"a KX licence\n")]
    path = _archive(tmp_path / "lic.tar.gz", files, rt.manifest)
    with pytest.raises(ReleaseError, match=f"member '{licence}' is a licence file"):
        read(path)


def test_a_release_build_never_ships_a_licence(tmp_path):
    assert release_build.is_excluded("k4.lic") and release_build.is_excluded("config/kc.lic")


def test_a_runtime_with_more_than_the_binary_is_refused(tmp_path):
    rt = runtime(tmp_path)
    path = _archive(tmp_path / "x.tar.gz", [("q", b"x"), ("qlib/x.q", b"y")], rt.manifest)
    with pytest.raises(ReleaseError, match=r"holds \['q', 'qlib/x.q'\], not one q binary"):
        q_runtime.read_runtime(path)


def test_a_torq_runtime_is_not_a_q_runtime(tmp_path):
    rt = runtime(tmp_path)
    path = _archive(tmp_path / "t.tar.gz", [("q", b"x")], {**rt.manifest, "kind": "torq-runtime"})
    with pytest.raises(ReleaseError, match="is not a q runtime of format 1"):
        q_runtime.read_runtime(path)


# --- the push's settings --------------------------------------------------------------


def test_bundled_q_needs_a_runtime():
    with pytest.raises(DeployError, match="q_mode is bundled, and needs q_runtime"):
        make_config(**BASE, q_mode="bundled")


@pytest.mark.parametrize("field", ["qcmd", "qhome"])
def test_an_existing_q_field_beside_bundled_is_refused(field):
    with pytest.raises(DeployError, match=f"q_mode is bundled, but {field} - existing mode's"):
        make_config(**BASE, q_mode="bundled", q_runtime="q.tar.gz", **{field: "/opt/kx"})


def test_a_q_runtime_without_bundled_is_refused():
    with pytest.raises(DeployError, match="q_mode is existing, but q_runtime - bundled"):
        make_config(**BASE, q_runtime="q.tar.gz")


LAYERED = """
[defaults]
profile = "capture"
qcmd = "/opt/kx/bin/q"
qhome = "/opt/kx"

[environments.uat]
q_mode = "bundled"
q_runtime = "dist/q-runtime.tar.gz"
q_runtime_sha256 = "{pin}"

[targets.a]
host = "h"
dest = "/srv/uqf"
environment = "uat"

[targets.kx]
host = "h"
dest = "/srv/uqf"
environment = "uat"
q_mode = "existing"
qcmd = "/opt/kx5/bin/q"
"""


def test_a_layer_switching_q_replaces_its_whole_table(tmp_path, monkeypatch):
    """[environments.uat] switches q to bundled: [defaults]' qcmd and qhome
    go; [targets.kx] switches back: uat's runtime and its pin go, and only
    what kx itself sets of existing mode is there."""
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    (tmp_path / targets.DECLARATION).write_text(LAYERED.format(pin="ab" * 32))
    (a,) = targets.configs("a", "x", {}, False, tmp_path)
    assert (a.q_mode, a.qcmd, a.qhome) == ("bundled", None, None)
    assert (a.q_runtime, a.q_runtime_sha256) == ("dist/q-runtime.tar.gz", "ab" * 32)
    assert a.sources["q_runtime"] == "environment uat"
    (kx,) = targets.configs("kx", "x", {}, False, tmp_path)
    assert (kx.q_mode, kx.qcmd, kx.qhome, kx.q_runtime, kx.q_runtime_sha256) == (
        "existing", "/opt/kx5/bin/q", None, None, None)  # fmt: skip


# --- a bundled push ---------------------------------------------------------------------

#: The server: Linux on x86_64, the runtime directory absent, then installed;
#: the installed q answers as PeachQ.
SERVER = "os=Linux\narch=x86_64\npython=3.14\n"
ABSENT = {
    "the runtime arrived as": _done("runtime=ok\n"),
    "echo runtime=absent": _done("runtime=absent\n"),
    'probe.q" -q': _done("UQF_Q peachq 5\n"),
}


def release(tmp_path: Path, runtime_name: str = "peachq") -> Path:
    """A small real release of `runtime_name`, as test_deploy._artifact's;
    built once per `tmp_path`."""
    done = list((tmp_path / "dist").glob("uqf-*.tar.gz"))
    if done:
        return done[0]
    root = tmp_path / "tree"
    files = ["pyproject.toml", "src/init.q", "scripts/deploy_smoke.q"]
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    py = tmp_path / "py"
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("rich==15.0.0 --hash=sha256:00\n")
    (py / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
    (py / "wheels" / "rich-15.0.0-py3-none-any.whl").write_bytes(b"rich")
    built = release_build.build_artifact(
        root, tmp_path / "dist", rid="20261007T000000Z-0123456789ab", rev="0123456789abcdef",
        dirty=False, files=files, target=artifact.Target(os="linux", arch="x86_64", python="3.14"),
        python_dir=py, runtime=runtime_name,
    )  # fmt: skip
    return built.path


def push(tmp_path: Path, rt: q_runtime.QRuntime, *extra: str, state: dict | None = None,
         runtime_name: str = "peachq"):  # fmt: skip
    rules = {**(state or ABSENT), **_HEALTHY, "uqs deploy verify --profile": _verified(True)}
    rules["uv python find"] = _done(SERVER + "data=present\n")
    remote, out = FakeRemote(rules), io.StringIO()
    pkg = release(tmp_path / "release", runtime_name)
    argv = [str(pkg), "--host", "uqf-server", "--dest", "/opt/uqf", "--profile", "capture",
            "--torq-home", "/opt/torq", "--q-mode", "bundled",
            "--q-runtime", str(rt.path)]  # fmt: skip
    code = driver.deploy(parse_args([*argv, *extra]), remote, out=out)
    return code, remote, out.getvalue()


def env_of(remote: FakeRemote) -> list[str]:
    (prepare,) = remote.stage("prepare")
    return prepare.split("<<'DEPLOYENV'\n")[1].split("\nDEPLOYENV")[0].splitlines()


def test_deploy_env_names_the_bundled_q(tmp_path):
    rt = runtime(tmp_path)
    code, remote, _ = push(tmp_path, rt)
    assert code == 0
    home = f"/opt/uqf/shared/runtimes/q/{rt.sha256}"
    env = env_of(remote)
    assert env[1:6] == [
        f"export QCMD={home}/q",
        f"export QHOME={home}",
        "export UQF_Q_IMPL=peachq",
        f"export UQF_PEACHQ={home}/q",
        f"export UQF_PEACHQ_REASON='the bundled q runtime {rt.sha256}, PeachQ {COMMIT}'",
    ]


def test_preflight_looks_for_no_q_on_the_server(tmp_path):
    rt = runtime(tmp_path)
    _, remote, _ = push(tmp_path, rt)
    (preflight,) = [s for s in remote.stage("preflight") if "uv python find" in s]
    assert "QHOME is not set" not in preflight and "DEPLOY_Q_OK" not in preflight
    assert "envsubst rlwrap" in preflight and 'if [ -n "$odbc_home" ]' in preflight


def test_the_q_is_installed_checked_and_run_before_the_release_arrives(tmp_path):
    rt = runtime(tmp_path)
    _, remote, _ = push(tmp_path, rt)
    stages = [s for s, _ in remote.scripts]
    assert stages.index("runtime") < stages.index("transfer")
    assert remote.puts[0].endswith(rt.path.name), "the runtime goes up first"
    (install,) = [s for s in remote.stage("runtime") if "tar -xzf" in s]
    assert f'if [ "$got" != {rt.sha256} ]' in install, "its bytes are checked on the server"
    assert remote.ran(f"QHOME=/opt/uqf/shared/runtimes/q/{rt.sha256} timeout"), "and it is run"


def test_the_report_records_the_bundled_q(tmp_path):
    rt = runtime(tmp_path)
    _, _, out = push(tmp_path, rt)
    report = json.loads(out)
    q = report["components"]["q"]
    assert (q["mode"], q["sha256"], q["version"], q["install"]) == (
        "bundled", rt.sha256, "5", "installed")  # fmt: skip
    assert (q["implementation"], q["commit"]) == ("peachq", COMMIT)
    assert q["path"] == f"/opt/uqf/shared/runtimes/q/{rt.sha256}/q" and report["qversion"] == "5"
    assert not any("licence in QHOME" in r for r in q["requires"])


def test_an_installed_q_that_says_it_is_kx_is_refused(tmp_path):
    rt = runtime(tmp_path)
    kx = {**ABSENT, 'probe.q" -q': _done("UQF_Q kdbx 4.1\n")}
    code, _, out = push(tmp_path, rt, state=kx)
    report = json.loads(out)
    assert code == 1 and report["stage"] == "runtime"
    assert "says it is kdbx - KX's q is never bundled" in report["error"]


def test_a_release_of_a_kdbx_runtime_is_refused_a_bundled_q(tmp_path):
    rt = runtime(tmp_path)
    with pytest.raises(DeployError, match="runs the uqf runtime, declared on KDB-X") as err:
        push(tmp_path, rt, runtime_name="uqf")
    assert "peachq, peachq-etl" in str(err.value) and err.value.stage == "artifact"


def test_a_q_runtime_for_another_platform_is_refused(tmp_path):
    rt = runtime(tmp_path, platform="Darwin-arm64")
    with pytest.raises(DeployError, match="is PeachQ for darwin aarch64, and .* linux x86_64"):
        push(tmp_path, rt)


def test_the_dry_run_shows_the_bundled_q_and_installs_nothing(tmp_path):
    rt = runtime(tmp_path)
    code, remote, out = push(tmp_path, rt, "--dry-run")
    assert code == 0 and remote.puts == []
    assert f"q      bundled (--q-mode): /opt/uqf/shared/runtimes/q/{rt.sha256}/q, version 5" in out
    assert f"runtime sha256 {rt.sha256}: not on the server yet, installed write-once" in out
    assert "(bundled) - installed under the lock, and run once there" in out


def test_q_version_holds_the_bundled_q_too(tmp_path):
    rt = runtime(tmp_path)
    with pytest.raises(DeployError, match="the server's q is 5, and q_version requires >=6"):
        push(tmp_path, rt, "--q-version", ">=6")


# --- the pins ------------------------------------------------------------------------------


def test_an_unpinned_q_runtime_is_refused_without_allow_unpinned(tmp_path):
    rt = runtime(tmp_path)
    Path(f"{rt.path}.sha256").unlink()
    with pytest.raises(
        DeployError, match="no q_runtime_sha256 pins it - pass --override allow-unpinned"
    ):
        push(tmp_path, rt)
    code, _, out = push(
        tmp_path, rt, "--override", "allow-unpinned", "--reason", "a local PeachQ build"
    )
    exceptions = json.loads(out)["exceptions"]
    assert code == 0 and exceptions[-1]["requirement"] == "a .sha256 beside the q runtime"


def test_a_q_runtime_that_is_not_the_pinned_one_needs_accept_digest(tmp_path):
    rt = runtime(tmp_path)
    pin = ("--q-runtime-sha256", "ab" * 32)
    with pytest.raises(DeployError, match="not the q_runtime_sha256 abab"):
        push(tmp_path, rt, *pin)
    code, remote, out = push(
        tmp_path, rt, *pin, "--override", "accept-digest", "--reason", "rebuilt PeachQ"
    )
    (e,) = json.loads(out)["exceptions"]
    assert code == 0 and e["observed"] == f"the q runtime's sha256 {rt.sha256}"
    assert remote.ran(f'if [ "$got" != {rt.sha256} ]'), "the actual bytes are still verified"


def test_allow_unpinned_may_relax_the_q_runtime_when_the_release_is_pinned():
    cfg = make_config(**BASE, q_mode="bundled", q_runtime="q.tar.gz",
                      expected_sha256="ab" * 32, allow_unpinned="dev PeachQ")  # fmt: skip
    assert cfg.allow_unpinned == "dev PeachQ"
    with pytest.raises(DeployError, match="nothing for it to relax"):
        make_config(**BASE, q_mode="bundled", q_runtime="q.tar.gz", expected_sha256="ab" * 32,
                    q_runtime_sha256="cd" * 32, allow_unpinned="dev PeachQ")  # fmt: skip
