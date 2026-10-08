"""uqs.stack.odbc_home (#840): a deployment-owned, versioned ODBC setup -
installed only when approved, checked file by file, loaded through an overlay
that leaves the managed QHOME untouched, and rolled back by one rename.

The package here is a fake with the real layout: no driver is loaded, so
nothing needs unixODBC or KX's client.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from uqs.paths import UqsError
from uqs.stack import odbc_home

FILES = {
    "q/odbc.k": b"/ KX's client\n",
    "q/l64/odbc.so": b"\x7fELF client",
    "lib/libodbc.so.2": b"\x7fELF driver manager",
    "lib/libtestodbc.so": b"\x7fELF driver",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package(
    tmp_path: Path,
    version: str = "1.0",
    *,
    files: dict[str, bytes] | None = None,
    listed: dict[str, str] | None = None,
    platform: str | None = None,
    extra_member: str | None = None,
) -> Path:
    files = FILES if files is None else files
    manifest = {
        "name": "testodbc",
        "version": version,
        "platform": platform or odbc_home.host_platform(),
        "files": listed if listed is not None else {k: _sha(v) for k, v in files.items()},
        "drivers": {"Test ODBC Driver": "lib/libtestodbc.so"},
    }
    path = tmp_path / f"testodbc-{version}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in {**files, odbc_home.MANIFEST: json.dumps(manifest).encode()}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra_member:
            info = tarfile.TarInfo(extra_member)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    return path


def _approve(home: Path, *packages: Path, platform: str | None = None) -> None:
    home.mkdir(exist_ok=True)
    lines = ["name,version,platform,sha256"]
    for p in packages:
        version = p.name.removeprefix("testodbc-").removesuffix(".tar.gz")
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        lines.append(f"testodbc,{version},{platform or odbc_home.host_platform()},{sha}")
    (home / odbc_home.APPROVED).write_text("\n".join(lines) + "\n")


@pytest.fixture
def qhome(tmp_path) -> Path:
    """A managed QHOME: a licence, a q.k and the arch directory with q in it."""
    q = tmp_path / "managed_qhome"
    (q / "l64").mkdir(parents=True)
    (q / "q.k").write_text("k")
    (q / "kc.lic").write_text("licence")
    (q / "l64" / "q").write_text("binary")
    return q


def test_an_approved_package_installs_and_becomes_current(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path)
    _approve(home, pkg)
    version = odbc_home.install(home, pkg, qhome)
    assert version == home / "versions" / "testodbc-1.0"
    assert odbc_home.current(home) == version.resolve()
    ini = (version / "etc" / "odbcinst.ini").read_text()
    assert f"[Test ODBC Driver]\nDriver={version / 'lib/libtestodbc.so'}" in ini


def test_the_overlay_adds_the_client_and_links_everything_else(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path)
    _approve(home, pkg)
    overlay = odbc_home.install(home, pkg, qhome) / "qhome"
    assert (overlay / "kc.lic").resolve() == (qhome / "kc.lic").resolve()
    assert (overlay / "l64" / "q").resolve() == (qhome / "l64" / "q").resolve()
    assert (overlay / "odbc.k").read_bytes() == FILES["q/odbc.k"]
    assert (overlay / "l64" / "odbc.so").read_bytes() == FILES["q/l64/odbc.so"]
    assert sorted(p.name for p in qhome.rglob("*")) == ["kc.lic", "l64", "q", "q.k"], (
        "the managed QHOME is never written to"
    )


def test_env_sh_loads_this_version_and_keeps_the_callers_library_path(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path)
    _approve(home, pkg)
    version = odbc_home.install(home, pkg, qhome)
    text = (home / "current" / odbc_home.ENV_FILE).read_text()
    assert f'export QHOME="{version / "qhome"}"' in text
    assert f'export ODBCSYSINI="{version / "etc"}"' in text
    assert 'export ODBCINSTINI="odbcinst.ini"' in text
    assert f'LD_LIBRARY_PATH="{version / "lib"}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"' in text


def test_a_package_not_in_the_approved_list_is_refused_before_it_is_opened(tmp_path, qhome):
    home = tmp_path / "odbc"
    _approve(home, _package(tmp_path, "1.0"))
    other = _package(tmp_path, "2.0")
    with pytest.raises(UqsError, match="is not in .*approved_packages.csv"):
        odbc_home.install(home, other, qhome)
    assert not (home / "versions").exists() or not list((home / "versions").iterdir())


def test_no_approved_list_is_a_refusal_naming_it(tmp_path, qhome):
    with pytest.raises(UqsError, match="no approved_packages.csv"):
        odbc_home.install(tmp_path / "odbc", _package(tmp_path), qhome)


def test_a_file_that_does_not_match_its_manifest_is_refused(tmp_path, qhome):
    home = tmp_path / "odbc"
    listed = {k: _sha(v) for k, v in FILES.items()}
    listed["lib/libtestodbc.so"] = _sha(b"what was approved")
    pkg = _package(tmp_path, listed=listed)
    _approve(home, pkg)
    with pytest.raises(UqsError, match="libtestodbc.so does not match its manifest checksum"):
        odbc_home.install(home, pkg, qhome)
    assert odbc_home.current(home) is None
    assert [p.name for p in (home / "versions").iterdir()] == [], "the staging dir is gone"


def test_a_file_the_manifest_does_not_list_is_refused(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path, extra_member="lib/unlisted.so")
    _approve(home, pkg)
    with pytest.raises(UqsError, match="holds lib/unlisted.so, which its manifest does not list"):
        odbc_home.install(home, pkg, qhome)


def test_a_member_outside_the_package_is_refused(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path, extra_member="../escape")
    _approve(home, pkg)
    with pytest.raises(UqsError, match="is not a relative path"):
        odbc_home.install(home, pkg, qhome)
    assert not (home / "escape").exists()


def test_a_package_for_another_platform_is_refused(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path, platform="windows-x86_64")
    _approve(home, pkg, platform="windows-x86_64")
    with pytest.raises(UqsError, match="approved for windows-x86_64"):
        odbc_home.install(home, pkg, qhome)


def test_a_manifest_that_disagrees_with_its_approval_is_refused(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path, "1.0")
    _approve(home, pkg)
    rows = (home / odbc_home.APPROVED).read_text().replace("testodbc,1.0", "testodbc,9.9")
    (home / odbc_home.APPROVED).write_text(rows)
    with pytest.raises(UqsError, match="the approved list says"):
        odbc_home.install(home, pkg, qhome)


def test_an_upgrade_keeps_the_old_version_and_a_rollback_returns_to_it(tmp_path, qhome):
    home = tmp_path / "odbc"
    old, new = _package(tmp_path, "1.0"), _package(tmp_path, "2.0")
    _approve(home, old, new)
    odbc_home.install(home, old, qhome)
    odbc_home.install(home, new, qhome)
    assert odbc_home.status(home) == {
        "home": str(home),
        "current": "testodbc-2.0",
        "previous": "testodbc-1.0",
        "versions": ["testodbc-1.0", "testodbc-2.0"],
    }
    assert odbc_home.rollback(home).name == "testodbc-1.0"
    assert odbc_home.status(home)["current"] == "testodbc-1.0"
    assert odbc_home.status(home)["previous"] == "testodbc-2.0", "and rolling back again undoes it"


def test_reinstalling_a_version_is_refused(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path)
    _approve(home, pkg)
    odbc_home.install(home, pkg, qhome)
    with pytest.raises(UqsError, match="already installed"):
        odbc_home.install(home, pkg, qhome)


def test_a_rollback_with_nothing_before_is_refused(tmp_path, qhome):
    home = tmp_path / "odbc"
    pkg = _package(tmp_path)
    _approve(home, pkg)
    odbc_home.install(home, pkg, qhome)
    with pytest.raises(UqsError, match="no previous version"):
        odbc_home.rollback(home)
