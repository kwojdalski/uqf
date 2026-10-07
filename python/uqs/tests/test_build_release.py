"""scripts/build_release.py (#778): the artifact deploy.py puts on servers.

uv, pip and git are replaced by fakes, so what is packaged, what the manifest
promises and what read_artifact refuses are tested without a network.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

_spec = importlib.util.spec_from_file_location(
    "uqf_build_release_under_test", ROOT / "scripts" / "build_release.py"
)
assert _spec and _spec.loader
release = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release
_spec.loader.exec_module(release)

REV = "0123456789abcdef0123456789abcdef01234567"


def _done(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


def _tools(files: list[str], dirty: str = "", fail: str | None = None):
    """git, uv and pip, answering as a healthy checkout would."""
    calls: list[list[str]] = []

    def runner(argv, **_):
        calls.append(list(argv))
        if fail and fail in argv:
            return _done(rc=2, stderr=f"{fail} broke")
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
    art = release.build(
        ["--output", str(tmp_path / "dist"), *extra], root=root, runner=tools, out=io.StringIO()
    )
    return art, calls


def test_build_release_parses_on_the_oldest_python_it_runs_under():
    ast.parse((ROOT / "scripts" / "build_release.py").read_text(), feature_version=(3, 10))


# --- what is packaged ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".envrc",
        "python/uqs/.env",
        "python/uqs/.venv/bin/python",
        "lib/torq/torq.q",
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
    assert release.is_excluded(path)


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
    assert not release.is_excluded(path)


def test_only_tracked_files_under_the_allowlist_are_listed():
    files = release.tracked_files(ROOT, _tools(["src/init.q", ".envrc", "lib/torq/torq.q"])[0])
    assert files == ["src/init.q"]


def test_uncommitted_changes_are_refused_unless_allowed(tmp_path):
    with pytest.raises(release.ReleaseError, match="uncommitted"):
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
        release.MANIFEST,
    } <= names


def test_the_manifest_records_revision_target_and_every_checksum(tmp_path):
    art, _ = _build(tmp_path, "--arch", "aarch64")
    m = art.manifest
    assert m["revision"] == REV and m["release"].endswith("-" + REV[:12])
    assert m["target"]["os"] == "linux" and m["target"]["arch"] == "aarch64"
    assert m["target"]["python"] == "3.14" and "manylinux_2_28_aarch64" in m["target"]["platforms"]
    assert m["python"]["app_wheel"] == ".release/wheels/uqs-0.1.0-py3-none-any.whl"
    assert all(len(h) == 64 for h in m["files"].values()) and len(m["files"]) == 6
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
    with pytest.raises(release.ReleaseError, match="fetching the dependency wheels") as err:
        _build(tmp_path, fail="download")
    assert err.value.stage == "python"
    assert not (tmp_path / "dist").exists() or not list((tmp_path / "dist").glob("*.tar.gz"))


def test_a_malformed_python_version_is_refused():
    with pytest.raises(release.ReleaseError, match="major.minor"):
        release.parse_args(["--output", "dist", "--python", "3"])


def test_the_builder_needs_no_ssh(tmp_path):
    _, calls = _build(tmp_path)
    assert not any(c[0] in ("ssh", "scp") for c in calls)


# --- reading it back ------------------------------------------------------------


def test_a_built_artifact_reads_back_whole(tmp_path):
    art, _ = _build(tmp_path)
    back = release.read_artifact(art.path)
    assert back.sha256 == art.sha256 and back.manifest == art.manifest


def test_an_archive_that_does_not_match_its_checksum_file_is_refused(tmp_path):
    art, _ = _build(tmp_path)
    with art.path.open("ab") as f:
        f.write(b"x")
    with pytest.raises(release.ReleaseError, match="does not match"):
        release.read_artifact(art.path)


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
        ({"drop": release.MANIFEST}, "carries no"),
    ],
)
def test_an_artifact_that_differs_from_its_manifest_is_refused(tmp_path, kwargs, said):
    art, _ = _build(tmp_path)
    with pytest.raises(release.ReleaseError, match=said):
        release.read_artifact(_rewrite(art, **kwargs))


def test_an_expected_checksum_is_enforced(tmp_path):
    art, _ = _build(tmp_path)
    with pytest.raises(release.ReleaseError, match="not 00"):
        release.read_artifact(art.path, expected_sha256="00")
