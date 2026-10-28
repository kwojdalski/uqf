"""`uqs deploy build` (#778, #835): the artifact `uqs deploy push` puts on servers.

uv, pip and git are replaced by fakes, so what is packaged, what the manifest
promises and what read_artifact refuses are tested without a network.
"""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from typer.main import get_group

from uqs.cli.deploy import deploy_app
from uqs.deploy import artifact, payload
from uqs.deploy import build as release_build

ROOT = Path(__file__).resolve().parents[3]


_CLI = get_group(deploy_app)


def _built(argv: list[str], **kwargs):
    """`uqs deploy build` on `argv`: parsed by the command's own options."""
    p = _CLI.commands["build"].make_context("build", list(argv)).params
    return release_build.build(
        p["output"],
        arch=p["arch"],
        python=p["python"],
        allow_dirty=p["allow_dirty"],
        bundles=p["bundle"] or (),
        **kwargs,
    )


REV = "0123456789abcdef0123456789abcdef01234567"
#: What the faked `uqs.stack.hdb_shape declared` step prints.
SHAPE = {"quote": ["time", "sym"]}


def _done(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


def _tools(files: list[str], dirty: str = "", fail: str | None = None):
    """git, uv and pip, answering as a healthy checkout would."""
    calls: list[list[str]] = []

    def runner(argv, **_):
        calls.append(list(argv))
        if fail and fail in argv:
            return _done(rc=2, stderr=f"{fail} broke")
        if "uqs.stack.hdb_shape" in argv:
            return _done(json.dumps(SHAPE))
        if "ls-files" in argv:
            return _done("\0".join(files) + "\0")
        if "rev-parse" in argv:
            return _done(REV + "\n")
        if "status" in argv:
            return _done(dirty)
        if "export" in argv:
            Path(argv[argv.index("-o") + 1]).write_text("rich==15.0.0 --hash=sha256:00\n")
            return _done()
        if "build" in argv:
            out = Path(argv[argv.index("--out-dir") + 1])
            (out / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
            return _done()
        if "download" in argv:
            out = Path(argv[argv.index("--dest") + 1])
            (out / "rich-15.0.0-py3-none-any.whl").write_bytes(b"rich")
            return _done()
        raise AssertionError(argv)

    return runner, calls


def _tree(root: Path) -> list[str]:
    files = ["pyproject.toml", "src/init.q", "python/uqs/pyproject.toml"]
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    (root / "python/uqs/pyproject.toml").write_text('requires-python = ">=3.14"\n')
    return files


def _build(tmp_path: Path, *extra: str, dirty: str = "", fail: str | None = None):
    root = tmp_path / "tree"
    files = _tree(root)
    tools, calls = _tools(files, dirty=dirty, fail=fail)
    art = _built(["--output", str(tmp_path / "dist"), *extra], root=root, runner=tools)
    return art, calls


# --- what is packaged ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".envrc",
        "python/uqs/.env",
        "python/uqs/.venv/bin/python",
        "lib/torq/torq.q",
        "lib/ml/ml/ml.q",
        "lib/kx-dbmaint-extras/other.q",
        "output/uqs/hdb/sym",
        "scripts/torqconfig/permissions/gateway_users.csv",
        "python/uqs/tests/test_x.py",
        "src/x.log",
        "keys/id_ed25519",
        "k4.lic",
        "kc.lic",
        "python/uqs/dist/uqs-0.1.0.tar.gz",
        ".release/wheels/x.whl",
    ],
)
def test_secrets_licences_data_and_external_trees_are_never_packaged(path):
    assert payload.is_excluded(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/init.q",
        "uv.lock",
        "scripts/deploy_smoke.q",
        "python/uqs/pyproject.toml",
        ".env.example",
    ],
)
def test_source_and_configuration_are_packaged(path):
    assert not payload.is_excluded(path)


@pytest.mark.parametrize(
    "path",
    [
        "lib/kx-dbmaint/dbmaint.q",
        "lib/kx-dbmaint/dbmaint.md",
        "lib/kx-dbmaint/LICENSE-kx-kdb",
        "lib/kx-dbmaint/NOTICE.md",
    ],
)
def test_the_vendored_dbmaint_and_its_licence_are_packaged_though_lib_is_not(path):
    """Apache 2.0 asks that the licence travel with the copy (#1423)."""
    assert not payload.is_excluded(path)


def test_the_artifact_carries_dbmaint_with_its_licence_and_notice(tmp_path):
    kept = [
        "lib/kx-dbmaint/dbmaint.q",
        "lib/kx-dbmaint/LICENSE-kx-kdb",
        "lib/kx-dbmaint/NOTICE.md",
    ]
    root = tmp_path / "tree"
    files = _tree(root)
    for f in [*kept, "lib/torq/torq.q"]:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    tools, _ = _tools([*files, *kept, "lib/torq/torq.q"])
    art = _built(["--output", str(tmp_path / "dist")], root=root, runner=tools)
    with tarfile.open(art.path) as tar:
        names = set(tar.getnames())
    assert set(kept) <= names
    assert not any(n.startswith("lib/torq") for n in names), "the rest of lib/ still stays out"


def test_the_real_checkout_ships_its_vendored_dbmaint():
    """Against git itself: what a release of THIS checkout lists."""
    files = payload.tracked_files(ROOT)
    for f in ("dbmaint.q", "dbmaint.md", "LICENSE-kx-kdb", "NOTICE.md"):
        assert f"lib/kx-dbmaint/{f}" in files
    assert "scripts/gates/fill_hdb_partitions.q" in files
    assert "scripts/gates/hdb_alter.q" in files, "`--target` runs hdb-alter from the release"
    assert not any(f.startswith(("lib/torq", "lib/ml")) for f in files)


def test_only_tracked_files_under_the_allowlist_are_listed():
    files = payload.tracked_files(ROOT, _tools(["src/init.q", ".envrc", "lib/torq/torq.q"])[0])
    assert files == ["src/init.q"]


def test_uncommitted_changes_are_refused_unless_allowed(tmp_path):
    with pytest.raises(artifact.ReleaseError, match="uncommitted"):
        _build(tmp_path, dirty=" M src/init.q")
    art, _ = _build(tmp_path, "--allow-dirty", dirty=" M src/init.q")
    assert art.manifest["dirty"] is True


# --- the artifact ------------------------------------------------------------


def test_the_artifact_keeps_the_repository_layout_and_ships_its_wheels(tmp_path):
    art, _ = _build(tmp_path)
    with tarfile.open(art.path) as tar:
        names = set(tar.getnames())
    assert {"src/init.q", "pyproject.toml", "python/uqs/pyproject.toml"} <= names
    assert {
        ".release/requirements.txt",
        ".release/wheels/uqs-0.1.0-py3-none-any.whl",
        ".release/wheels/rich-15.0.0-py3-none-any.whl",
        artifact.MANIFEST,
    } <= names


def test_the_manifest_records_revision_target_and_every_checksum(tmp_path):
    art, _ = _build(tmp_path, "--arch", "aarch64")
    m = art.manifest
    assert m["revision"] == REV and m["release"].endswith("-" + REV[:12])
    assert m["target"]["os"] == "linux" and m["target"]["arch"] == "aarch64"
    assert m["target"]["python"] == "3.14" and "manylinux_2_28_aarch64" in m["target"]["platforms"]
    assert m["python"]["app_wheel"] == ".release/wheels/uqs-0.1.0-py3-none-any.whl"
    assert all(len(h) == 64 for h in m["files"].values()) and len(m["files"]) == 6
    assert m["hdb_shape"] == SHAPE
    # #1646: what it is, and what installs it on a server without uqs deploy push
    assert m["packaging"] == {"mode": "offline", "installer": "scripts/install_release.py"}
    beside = json.loads((tmp_path / "dist" / f"uqf-{m['release']}.manifest.json").read_text())
    assert beside == m


def test_the_checksum_file_is_sha256sum_format(tmp_path):
    art, _ = _build(tmp_path)
    digest, name = Path(f"{art.path}.sha256").read_text().split()
    assert digest == art.sha256 and name == art.path.name


def test_dependency_wheels_are_binary_only_for_the_target_from_the_frozen_lock(tmp_path):
    _, calls = _build(tmp_path, "--python", "3.14")
    export = next(c for c in calls if "export" in c)
    download = next(c for c in calls if "download" in c)
    assert "--frozen" in export and "--no-dev" in export and "--no-emit-workspace" in export
    assert "--only-binary=:all:" in download and "--python-version" in download
    assert download[download.index("--python-version") + 1] == "3.14"
    assert "manylinux_2_28_x86_64" in download


def test_a_failed_wheel_download_fails_the_build_naming_the_step(tmp_path):
    with pytest.raises(artifact.ReleaseError, match="fetching the dependency wheels") as err:
        _build(tmp_path, fail="download")
    assert err.value.stage == "python"
    assert not (tmp_path / "dist").exists() or not list((tmp_path / "dist").glob("*.tar.gz"))


#: An export as uv writes it: every platform's markers kept, hashes continued.
MARKED_EXPORT = """\
# This file was autogenerated by uv
jeepney==0.9.0 ; sys_platform == 'linux' \\
    --hash=sha256:aa \\
    --hash=sha256:bb
    # via secretstorage
colorama==0.4.6 ; sys_platform == 'win32' \\
    --hash=sha256:cc
cffi==2.1.1 ; platform_python_implementation != 'PyPy' \\
    --hash=sha256:dd
numpy==2.4.0 ; platform_machine == 'aarch64' \\
    --hash=sha256:ee
rich==15.0.0 \\
    --hash=sha256:ff
"""


def _marked_tools(wheels: tuple[str, ...]):
    """git, uv and pip, the export carrying markers; download writes `wheels`."""
    runner, calls = _tools(["pyproject.toml", "src/init.q", "python/uqs/pyproject.toml"])

    def marked(argv, **kw):
        if "export" in argv:
            calls.append(list(argv))
            Path(argv[argv.index("-o") + 1]).write_text(MARKED_EXPORT)
            return _done()
        if "download" in argv:
            calls.append(list(argv))
            calls.append(["requirements:", Path(argv[argv.index("--requirement") + 1]).read_text()])
            out = Path(argv[argv.index("--dest") + 1])
            for w in wheels:
                (out / w).write_bytes(b"w")
            return _done()
        return runner(argv, **kw)

    return marked, calls


ALL_WHEELS = (
    "jeepney-0.9.0-py3-none-any.whl",
    "cffi-2.1.1-cp314-cp314-manylinux_2_17_x86_64.whl",
    "rich-15.0.0-py3-none-any.whl",
)


def _marked_build(tmp_path: Path, wheels: tuple[str, ...] = ALL_WHEELS, *extra: str):
    root = tmp_path / "tree"
    _tree(root)
    tools, calls = _marked_tools(wheels)
    art = _built(["--output", str(tmp_path / "dist"), *extra], root=root, runner=tools)
    return art, calls


def test_markers_are_evaluated_for_the_target_not_the_build_host(tmp_path, monkeypatch):
    """#1713: on a Mac, pip skipped `jeepney ; sys_platform == 'linux'` while
    requirements.txt still pinned it, and the Linux install failed. The pins
    are now chosen for the target, so the host's platform cannot matter."""
    monkeypatch.setattr("sys.platform", "darwin")
    art, calls = _marked_build(tmp_path)
    download = next(c for c in calls if "download" in c)
    sent = next(c[1] for c in calls if c[0] == "requirements:")
    assert "--no-deps" in download
    # the Linux-only pin is fetched, marker stripped so pip cannot skip it
    assert "jeepney==0.9.0 \\\n    --hash=sha256:aa \\\n    --hash=sha256:bb" in sent
    assert "cffi==2.1.1" in sent and "rich==15.0.0" in sent
    # pins that do not hold on linux/x86_64 are not shipped
    assert "colorama" not in sent and "numpy" not in sent and ";" not in sent
    with tarfile.open(art.path) as tar:
        member = tar.extractfile(".release/requirements.txt")
        assert member is not None
        shipped = member.read().decode()
    assert shipped == sent


def test_the_target_architecture_selects_its_own_pins(tmp_path):
    _, calls = _marked_build(
        tmp_path, (*ALL_WHEELS, "numpy-2.4.0-cp314-cp314-manylinux_2_28_aarch64.whl"),
        "--arch", "aarch64",
    )  # fmt: skip
    sent = next(c[1] for c in calls if c[0] == "requirements:")
    assert "numpy==2.4.0" in sent and "jeepney==0.9.0" in sent


def test_a_pin_the_target_has_no_wheel_for_refuses_the_build(tmp_path):
    """A source-only (or skipped) dependency is refused at build time, by name,
    never left for the server's offline install to find."""
    wheels = tuple(w for w in ALL_WHEELS if not w.startswith("jeepney"))
    with pytest.raises(artifact.ReleaseError, match=r"jeepney==0\.9\.0") as err:
        _marked_build(tmp_path, wheels)
    assert err.value.stage == "python"
    assert "source distribution" in str(err.value)
    assert not list((tmp_path / "dist").glob("*.tar.gz"))


def test_a_wheel_of_another_version_does_not_count(tmp_path):
    wheels = (*[w for w in ALL_WHEELS if not w.startswith("rich")], "rich-14.0.0-py3-none-any.whl")
    with pytest.raises(artifact.ReleaseError, match=r"rich==15\.0\.0"):
        _marked_build(tmp_path, wheels)


def test_the_target_environment_is_linux_cpython_of_the_target():
    env = payload.target_environment(artifact.Target(os="linux", arch="aarch64", python="3.14"))
    assert env["sys_platform"] == "linux" and env["platform_system"] == "Linux"
    assert env["platform_machine"] == "aarch64" and env["python_version"] == "3.14"
    assert env["implementation_name"] == "cpython"


def test_a_malformed_python_version_is_refused():
    with pytest.raises(artifact.ReleaseError, match="major.minor"):
        _built(["--output", "dist", "--python", "3"])


def test_the_builder_needs_no_ssh(tmp_path):
    _, calls = _build(tmp_path)
    assert not any(c[0] in ("ssh", "scp") for c in calls)


# --- reading it back ------------------------------------------------------------


def test_a_built_artifact_reads_back_whole(tmp_path):
    art, _ = _build(tmp_path)
    back = artifact.read_artifact(art.path)
    assert back.sha256 == art.sha256 and back.manifest == art.manifest


def test_an_archive_that_does_not_match_its_checksum_file_is_refused(tmp_path):
    art, _ = _build(tmp_path)
    with art.path.open("ab") as f:
        f.write(b"x")
    with pytest.raises(artifact.ReleaseError, match="does not match"):
        artifact.read_artifact(art.path)


def _rewrite(
    art,
    *,
    drop: str | None = None,
    add: tuple[str, bytes] | None = None,
    change: tuple[str, bytes] | None = None,
    symlink: str | None = None,
) -> Path:
    """The artifact repacked with one thing wrong, and no checksum file."""
    out = art.path.with_name("bad.tar.gz")
    with tarfile.open(art.path) as src, tarfile.open(out, "w:gz") as dst:
        for m in src:
            if m.name == drop:
                continue
            f = src.extractfile(m)
            data = f.read() if f else b""
            if change and m.name == change[0]:
                data = change[1]
            info = tarfile.TarInfo(m.name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
        for name, data in [add] if add else []:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            dst.addfile(info)
    return out


@pytest.mark.parametrize(
    ("kwargs", "said"),
    [
        ({"drop": "src/init.q"}, "in the manifest but not the archive"),
        ({"add": ("src/extra.q", b"x")}, "in the archive but not the manifest"),
        ({"change": ("src/init.q", b"tampered")}, "does not match its manifest checksum"),
        ({"add": ("../escape", b"x")}, "not a relative path"),
        ({"symlink": "src/link"}, "not a regular file"),
        ({"drop": artifact.MANIFEST}, "carries no"),
    ],
)
def test_an_artifact_that_differs_from_its_manifest_is_refused(tmp_path, kwargs, said):
    art, _ = _build(tmp_path)
    with pytest.raises(artifact.ReleaseError, match=said):
        artifact.read_artifact(_rewrite(art, **kwargs))


def test_an_expected_checksum_is_enforced(tmp_path):
    art, _ = _build(tmp_path)
    with pytest.raises(artifact.ReleaseError, match="not 00"):
        artifact.read_artifact(art.path, expected_sha256="00")


def test_the_artifact_goes_to_dist_at_the_repository_root_by_default(tmp_path):
    """dist/ is gitignored at the root and excluded from every release, so a
    build never packages an earlier build's output."""
    root = tmp_path / "tree"
    tools, _ = _tools(_tree(root))
    art = _built([], root=root, runner=tools)
    assert art.path.parent == root / "dist"
    assert payload.is_excluded("dist/" + art.path.name)
