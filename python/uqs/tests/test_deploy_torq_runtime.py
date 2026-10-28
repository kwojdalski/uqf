"""A bundled TorQ (#1557, step 5): the runtime artifact `uqs deploy component build
torq` makes of lib/ - byte-identical, content-addressed, checked whole - and a
push with `torq_mode = "bundled"`: its refusals, its pins, what it sends the
server, and what it records. The server side - the write-once install, prune
and rollback - is test_deploy_torq_runtime_server.py's."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
from test_deploy import _HEALTHY, SERVER, FakeRemote, _args, _artifact, _done, _verified, parse_args

from uqs.deploy import driver, targets, torq_runtime
from uqs.deploy.artifact import ReleaseError
from uqs.deploy.config import DeployError, make_config

ROOT = Path(__file__).resolve().parents[3]
BASE: dict[str, Any] = {"artifact": "x", "host": "h", "dest": "/srv/uqf", "profile": "essential"}

#: A small vendored TorQ: the two trees, with the files preflight and the
#: version reader look at, and an executable torq.sh.
VENDORED = {
    "lib/torq/torq.q": "/ TorQ core\n",
    "lib/torq/torq.sh": "#!/bin/bash\necho torq\n",
    "lib/torq/config/dependency.csv": "app,version,dependency\nTorQ,5.2.12,\n",
    "lib/torq-finance-starter-pack/database.q": "/ schema\n",
    "lib/torq-finance-starter-pack/appconfig/process.csv": "host,port,proctype\n",
    "lib/torq-finance-starter-pack/appconfig/dependency.csv": (
        "app,version,dependency\nTorQ Finance Starter Pack,1.11.0,TorQ 4.0.0\n"
    ),
}


def _git(root: Path, *argv: str, **env: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *argv], capture_output=True, text=True, check=True,
        env={**os.environ, **env},
    ).stdout.strip()  # fmt: skip


def vendored(root: Path, files: dict[str, str] | None = None) -> Path:
    """A git checkout holding `files` (VENDORED), committed with plumbing -
    commit-tree runs no hook."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, text in (files or VENDORED).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
        if rel.endswith(".sh"):
            (root / rel).chmod(0o755)
    _git(root, "add", "-A")
    who = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}  # fmt: skip
    commit = _git(root, "commit-tree", _git(root, "write-tree"), "-m", "vendor TorQ", **who)
    _git(root, "update-ref", "HEAD", commit)
    return root


def runtime(tmp_path: Path, files: dict[str, str] | None = None) -> torq_runtime.Runtime:
    return torq_runtime.build_runtime(vendored(tmp_path / "repo", files), tmp_path / "dist")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- the build: lib/ exactly as vendored ------------------------------------------


def test_the_runtime_is_lib_byte_for_byte(tmp_path):
    """The real vendored trees: every tracked file of lib/torq and the starter
    pack is a member, with the same bytes and the same executable bit, and
    nothing else is - read from the tree, which is left as it was."""
    trees = ["lib/torq", "lib/torq-finance-starter-pack"]
    changed = _git(ROOT, "status", "--porcelain", "--untracked-files=all", "--", *trees)
    if changed:
        pytest.skip(f"lib/ differs from what is committed here: {changed.splitlines()[0]}")
    built = torq_runtime.build_runtime(ROOT, tmp_path)
    # lib/torq/x is torq/x in the runtime, and the starter pack's likewise
    tracked = _git(ROOT, "ls-files", "--", *trees).splitlines()
    expected = {p.removeprefix("lib/"): ROOT / p for p in tracked}
    with tarfile.open(built.path, "r:gz") as tar:
        members = {m.name: m for m in tar if m.name != torq_runtime.MANIFEST}
        assert set(members) == set(expected)
        for name, member in members.items():
            f = tar.extractfile(member)
            assert f is not None
            assert _sha(f.read()) == _sha(expected[name].read_bytes()), name
            assert bool(member.mode & 0o100) == os.access(expected[name], os.X_OK), name
    assert _git(ROOT, "status", "--porcelain", "--", *trees) == ""
    t = built.manifest["trees"]
    assert t["torq"]["version"] == "5.2.12" and t["torq-finance-starter-pack"]["version"]
    assert t["torq"]["tree"] == _git(ROOT, "rev-parse", "HEAD:lib/torq")
    assert t["torq"]["commit"] == _git(ROOT, "log", "-1", "--format=%H", "--", "lib/torq")


def test_the_same_trees_make_the_same_bytes(tmp_path):
    """Content-addressed: built twice, the archive is identical, so every
    release built against this TorQ shares one runtime on the server."""
    repo = vendored(tmp_path / "repo")
    a = torq_runtime.build_runtime(repo, tmp_path / "a")
    b = torq_runtime.build_runtime(repo, tmp_path / "b")
    assert a.sha256 == b.sha256 and a.path.name == b.path.name
    assert a.path.name.startswith("torq-runtime-5.2.12-")
    assert Path(f"{a.path}.sha256").read_text().split()[0] == a.sha256
    assert a.pinned and a.version == "5.2.12"


def test_a_changed_lib_is_refused_and_left_alone(tmp_path):
    repo = vendored(tmp_path / "repo")
    (repo / "lib/torq/torq.q").write_text("/ patched\n")
    with pytest.raises(ReleaseError, match="exactly as vendored"):
        torq_runtime.build_runtime(repo, tmp_path / "dist")
    assert (repo / "lib/torq/torq.q").read_text() == "/ patched\n"
    assert not (tmp_path / "dist").exists()


def test_an_untracked_file_in_lib_is_refused(tmp_path):
    repo = vendored(tmp_path / "repo")
    (repo / "lib/torq/extra.q").write_text("x\n")
    with pytest.raises(ReleaseError, match="exactly as vendored"):
        torq_runtime.build_runtime(repo, tmp_path / "dist")


def test_a_tracked_symlink_is_refused(tmp_path):
    repo = vendored(tmp_path / "repo")
    (repo / "lib/torq/link.q").symlink_to("torq.q")
    _git(repo, "add", "-A")
    commit = _git(repo, "commit-tree", _git(repo, "write-tree"), "-p", "HEAD", "-m", "link",
                  GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                  GIT_COMMITTER_EMAIL="t@t")  # fmt: skip
    _git(repo, "update-ref", "HEAD", commit)
    with pytest.raises(ReleaseError, match="link.q is not a regular file"):
        torq_runtime.build_runtime(repo, tmp_path / "dist")


def test_a_tree_with_no_version_file_records_none(tmp_path):
    files = {k: v for k, v in VENDORED.items() if not k.endswith("config/dependency.csv")}
    rt = runtime(tmp_path, files)
    assert rt.version is None and rt.path.name.startswith("torq-runtime-unknown-")


# --- reading one back: every member checked ----------------------------------------


def _archive(path: Path, members: list[tuple[tarfile.TarInfo, bytes]], manifest: dict) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data))
        body = json.dumps(manifest).encode()
        info = tarfile.TarInfo(torq_runtime.MANIFEST)
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return path


def _file(name: str, data: bytes = b"x") -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def _manifest(files: dict[str, bytes]) -> dict:
    return {"format": 1, "kind": "torq-runtime", "trees": {},
            "files": {n: _sha(d) for n, d in files.items()}}  # fmt: skip


@pytest.mark.parametrize(
    ("member", "said"),
    [
        (_file("../torq/escape.q"), "is not a relative path"),
        (_file("/etc/torq.q"), "is not a relative path"),
    ],
)
def test_a_member_outside_the_runtime_is_refused(tmp_path, member, said):
    path = _archive(tmp_path / "rt.tar.gz", [member], _manifest({member[0].name: member[1]}))
    with pytest.raises(ReleaseError, match=said):
        torq_runtime.read_runtime(path)


def test_a_link_member_is_refused(tmp_path):
    link = tarfile.TarInfo("torq/torq.q")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    path = _archive(tmp_path / "rt.tar.gz", [(link, b"")], _manifest({}))
    with pytest.raises(ReleaseError, match="is not a regular file"):
        torq_runtime.read_runtime(path)


def test_a_member_the_manifest_does_not_list_is_refused(tmp_path):
    path = _archive(tmp_path / "rt.tar.gz", [_file("torq/torq.q")], _manifest({}))
    with pytest.raises(ReleaseError, match="in the archive but not the manifest"):
        torq_runtime.read_runtime(path)


def test_a_tree_that_is_not_torq_is_refused(tmp_path):
    path = _archive(tmp_path / "rt.tar.gz", [_file("bin/q")], _manifest({"bin/q": b"x"}))
    with pytest.raises(ReleaseError, match=r"holds \['bin'\] - not TorQ"):
        torq_runtime.read_runtime(path)


def test_a_release_artifact_is_not_a_runtime(tmp_path):
    with pytest.raises(ReleaseError, match="carries no RUNTIME_MANIFEST.json"):
        torq_runtime.read_runtime(_artifact(tmp_path))


def test_a_runtime_that_does_not_match_its_sha256_is_refused(tmp_path):
    rt = runtime(tmp_path)
    Path(f"{rt.path}.sha256").write_text("0" * 64 + f"  {rt.path.name}\n")
    with pytest.raises(ReleaseError, match="does not match"):
        torq_runtime.read_runtime(rt.path)


# --- the push's settings -------------------------------------------------------------


def test_bundled_needs_a_runtime():
    with pytest.raises(DeployError, match="torq_mode is bundled, and needs torq_runtime"):
        make_config(**BASE, torq_mode="bundled")


@pytest.mark.parametrize("field", ["torq_home", "torq_app_home", "torq_launcher"])
def test_an_existing_field_beside_bundled_is_refused(field):
    with pytest.raises(DeployError, match=f"torq_mode is bundled, but {field} - existing mode's"):
        make_config(**BASE, torq_mode="bundled", torq_runtime="rt.tar.gz", **{field: "/opt/t"})


def test_a_runtime_without_bundled_is_refused():
    with pytest.raises(DeployError, match="torq_mode is existing, but torq_runtime - bundled"):
        make_config(**BASE, torq_runtime="rt.tar.gz")


def test_a_target_switching_to_bundled_drops_the_torq_home_below_it(tmp_path, monkeypatch):
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    (tmp_path / targets.DECLARATION).write_text(
        '[defaults]\nprofile = "essential"\ntorq_home = "/opt/torq"\n'
        '[targets.b]\nhost = "h"\ndest = "/srv/uqf"\ntorq_mode = "bundled"\n'
        'torq_runtime = "dist/rt.tar.gz"\n'
    )
    (cfg,) = targets.configs("b", "x", {}, False, tmp_path)
    assert (cfg.torq_mode, cfg.torq_home, cfg.torq_runtime) == ("bundled", None, "dist/rt.tar.gz")
    # and a flag switching back drops the target's runtime
    (cfg,) = targets.configs("b", "x", {"torq_mode": "existing"}, False, tmp_path)
    assert (cfg.torq_mode, cfg.torq_runtime) == ("existing", None)


# --- a bundled push ----------------------------------------------------------------------

#: The server's answers: the runtime directory is absent, then the install
#: checks out - unless a test says otherwise.
ABSENT = {"the runtime arrived as": _done("runtime=ok\n"), "echo runtime=absent": _done(
    "runtime=absent\n")}  # fmt: skip


def _push(tmp_path: Path, rt: torq_runtime.Runtime, *extra: str, state: dict | None = None):
    rules = {**(state or ABSENT), **_HEALTHY, "uqs deploy verify --profile": _verified(True)}
    rules["uv python find"] = _done(SERVER + "qversion=5.0\ndata=present\n")
    remote, out = FakeRemote(rules), io.StringIO()
    built = list((tmp_path / "release" / "dist").glob("uqf-*.tar.gz"))
    release = built[0] if built else _artifact(tmp_path / "release")
    argv = _args(release, "--torq-mode", "bundled", "--torq-runtime", str(rt.path))
    code = driver.deploy(parse_args([*argv, *extra]), remote, out=out)
    return code, remote, out.getvalue()


def _env(remote: FakeRemote) -> list[str]:
    (prepare,) = remote.stage("prepare")
    return prepare.split("<<'DEPLOYENV'\n")[1].split("\nDEPLOYENV")[0].splitlines()


def test_deploy_env_names_the_runtime(tmp_path):
    rt = runtime(tmp_path)
    code, remote, _ = _push(tmp_path, rt)
    assert code == 0
    home = f"/opt/uqf/shared/runtimes/torq/{rt.sha256}"
    env = _env(remote)
    assert env[:2] == [f"export TORQHOME={home}/torq",
                       f"export TORQAPPHOME={home}/torq-finance-starter-pack"]  # fmt: skip
    assert not any("UQS_TORQ_LAUNCHER" in line for line in env), "torq.sh is $TORQHOME's"


def test_the_runtime_is_installed_before_the_release_arrives(tmp_path):
    rt = runtime(tmp_path)
    _, remote, _ = _push(tmp_path, rt)
    stages = [s for s, _ in remote.scripts]
    assert "runtime" in stages and stages.index("runtime") < stages.index("transfer")
    assert remote.puts[0].endswith(rt.path.name), "the runtime goes up first"
    (install,) = [s for s in remote.stage("runtime") if "tar -xzf" in s]
    assert f'if [ "$got" != {rt.sha256} ]' in install, "its bytes are checked on the server"


def test_the_report_records_the_bundled_torq(tmp_path):
    rt = runtime(tmp_path)
    _, _, out = _push(tmp_path, rt)
    torq = json.loads(out)["components"]["torq"]
    assert (torq["mode"], torq["sha256"], torq["version"]) == ("bundled", rt.sha256, "5.2.12")
    assert torq["runtime"] == f"/opt/uqf/shared/runtimes/torq/{rt.sha256}"
    assert torq["path"] == f"{torq['runtime']}/torq" and torq["install"] == "installed"


def test_a_runtime_already_there_is_reused_and_never_uploaded(tmp_path):
    rt = runtime(tmp_path)
    code, remote, out = _push(tmp_path, rt, state={"echo runtime=absent": _done("runtime=ok\n")})
    assert code == 0 and json.loads(out)["components"]["torq"]["install"] == "reused"
    assert not any(p.endswith(rt.path.name) for p in remote.puts)
    assert not any("tar -xzf" in s for s in remote.stage("runtime")), "nothing is written"


def test_a_tampered_runtime_directory_is_refused_before_anything_changes(tmp_path):
    rt = runtime(tmp_path)
    tampered = {"echo runtime=absent": _done("runtime=refused its marker names no runtime\n")}
    with pytest.raises(DeployError, match="is never overwritten") as err:
        _push(tmp_path, rt, state=tampered)
    assert err.value.stage == "preflight"


def test_the_dry_run_shows_the_runtime_and_installs_nothing(tmp_path):
    rt = runtime(tmp_path)
    code, remote, out = _push(tmp_path, rt, "--dry-run")
    assert code == 0 and remote.puts == []
    assert "torq   bundled (--torq-mode): /opt/uqf/shared/runtimes/torq/" in out
    assert f"runtime sha256 {rt.sha256}: not on the server yet, installed write-once" in out


# --- the pins: step 1's overrides, applied to the runtime ----------------------------------


def test_an_unpinned_runtime_is_refused_without_allow_unpinned(tmp_path):
    rt = runtime(tmp_path)
    Path(f"{rt.path}.sha256").unlink()
    with pytest.raises(
        DeployError, match="no torq_runtime_sha256 pins it - pass --override allow-unpinned"
    ):
        _push(tmp_path, rt)
    code, _, out = _push(
        tmp_path, rt, "--override", "allow-unpinned", "--reason", "a local TorQ build"
    )
    release, runtime_ = json.loads(out)["exceptions"]
    assert code == 0 and not release["applied"], "the release has its .sha256"
    assert runtime_["applied"] and runtime_["requirement"] == "a .sha256 beside the torq runtime"


def test_a_runtime_that_is_not_the_pinned_one_is_refused_without_accept_digest(tmp_path):
    rt = runtime(tmp_path)
    pin = ("--torq-runtime-sha256", "ab" * 32)
    with pytest.raises(DeployError, match="not the torq_runtime_sha256 abab"):
        _push(tmp_path, rt, *pin)
    code, remote, out = _push(
        tmp_path, rt, *pin, "--override", "accept-digest", "--reason", "rebuilt TorQ"
    )
    (e,) = json.loads(out)["exceptions"]
    assert code == 0 and e["observed"] == f"the torq runtime's sha256 {rt.sha256}"
    assert remote.ran(f'if [ "$got" != {rt.sha256} ]'), "the actual bytes are still verified"


def test_allow_unpinned_may_relax_the_runtime_when_the_release_is_pinned():
    cfg = make_config(**BASE, torq_mode="bundled", torq_runtime="rt.tar.gz",
                      expected_sha256="ab" * 32, allow_unpinned="dev TorQ")  # fmt: skip
    assert cfg.allow_unpinned == "dev TorQ"
    with pytest.raises(DeployError, match="nothing for it to relax"):
        make_config(**BASE, torq_mode="bundled", torq_runtime="rt.tar.gz",
                    expected_sha256="ab" * 32, torq_runtime_sha256="cd" * 32,
                    allow_unpinned="dev TorQ")  # fmt: skip


def test_the_cli_builds_a_runtime(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli import deploy_component

    repo = vendored(tmp_path / "repo")
    monkeypatch.setattr(deploy_component, "repo_root", lambda: repo)
    r = CliRunner().invoke(cli.app, ["deploy", "component", "build", "torq"])
    assert r.exit_code == 0, r.output
    (built,) = list((repo / "dist").glob("torq-runtime-*.tar.gz"))
    assert str(built) in r.output
    r = CliRunner().invoke(cli.app, ["deploy", "component", "build", "python"])
    assert r.exit_code == 1


def test_the_old_build_runtime_name_still_builds_and_names_the_new_one(tmp_path, monkeypatch):
    """`uqs deploy build-runtime`, renamed `deploy component build` by #1740,
    works for one release, saying what to type instead - and is hidden."""
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli import deploy_component

    repo = vendored(tmp_path / "repo")
    monkeypatch.setattr(deploy_component, "repo_root", lambda: repo)
    r = CliRunner().invoke(cli.app, ["deploy", "build-runtime", "torq"])
    assert r.exit_code == 0, r.output
    assert "uqs deploy component build torq" in r.output
    assert list((repo / "dist").glob("torq-runtime-*.tar.gz"))
    assert "build-runtime" not in CliRunner().invoke(cli.app, ["deploy", "--help"]).output
