"""scripts/install_release.py: an unpacked release installs itself offline (#1646).

The install runs for real: a release directory is made with a manifest, a
lock and two tiny wheels built here, and the installer creates its .venv with
`python -m venv` and pip, `--no-index`, hashes required. No network is needed
or reached. The refusals are checked to come before anything is written.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "install_release.py"
_spec = importlib.util.spec_from_file_location("install_release", SCRIPT)
assert _spec and _spec.loader
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)

PY = f"{sys.version_info.major}.{sys.version_info.minor}"


def _wheel(directory: Path, name: str, version: str, module: str, body: str) -> Path:
    """A minimal pure-Python wheel, valid for pip."""
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{module}.py": body,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n",
    }
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        record.append(f"{path},sha256={digest.decode()},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = "\n".join(record) + "\n"
    wheel = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
    return wheel


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def release(tmp_path: Path) -> Path:
    """An unpacked release: a tree file, the lock, the wheels and a manifest."""
    rel = tmp_path / "releases" / "r1"
    wheels = rel / ".release" / "wheels"
    wheels.mkdir(parents=True)
    dep = _wheel(wheels, "uqfdep", "1.0", "uqfdep", "VALUE = 41\n")
    _wheel(wheels, "uqs", "0.0.1", "uqs_probe", "import uqfdep\nANSWER = uqfdep.VALUE + 1\n")
    (rel / ".release" / "requirements.txt").write_text(f"uqfdep==1.0 --hash=sha256:{_sha(dep)}\n")
    (rel / "src").mkdir()
    (rel / "src" / "init.q").write_text("/ the tree\n")
    files = {p.relative_to(rel).as_posix(): _sha(p) for p in sorted(rel.rglob("*")) if p.is_file()}
    manifest = {
        "release": "r1",
        "target": {
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "python": PY,
            "platforms": [],
        },
        "python": {
            "requirements": ".release/requirements.txt",
            "wheel_dir": ".release/wheels",
            "app_wheel": ".release/wheels/uqs-0.0.1-py3-none-any.whl",
        },
        "files": files,
    }
    (rel / installer.MANIFEST).write_text(json.dumps(manifest))
    return rel


def _install(release: Path, *extra: str) -> int:
    return installer.main(["--release", str(release), "--python", sys.executable, *extra])


def test_a_release_installs_offline_into_its_own_venv(release, tmp_path):
    env_file = tmp_path / "site.env"
    env_file.write_text("export SITE_Q=/opt/q\n")
    assert _install(release, "--env-file", str(env_file)) == 0
    python = release / installer.VENV / "bin" / "python"
    out = subprocess.run(
        [str(python), "-c", "import uqs_probe; print(uqs_probe.ANSWER)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "42"
    deploy_env = (release / installer.ENV_FILE).read_text()
    assert str(env_file) in deploy_env and "SITE_Q" not in deploy_env  # loaded, not copied
    recorded = json.loads((release / installer.INSTALLED).read_text())
    assert recorded["release"] == "r1" and recorded["python"] == PY


def test_check_writes_nothing(release):
    assert _install(release, "--check") == 0
    assert not (release / installer.VENV).exists()
    assert not (release / installer.INSTALLED).exists()


def test_an_altered_file_is_refused_before_anything_is_written(release, capsys):
    (release / "src" / "init.q").write_text("/ tampered\n")
    assert _install(release) == 1
    assert "src/init.q: altered" in capsys.readouterr().err
    assert not (release / installer.VENV).exists()


def test_a_wheel_the_lock_does_not_match_is_refused_and_leaves_no_venv(release, capsys):
    """pip with --require-hashes refuses it; nothing is fetched instead."""
    requirements = release / ".release" / "requirements.txt"
    requirements.write_text("uqfdep==1.0 --hash=sha256:" + "0" * 64 + "\n")
    manifest = json.loads((release / installer.MANIFEST).read_text())
    manifest["files"][".release/requirements.txt"] = _sha(requirements)
    (release / installer.MANIFEST).write_text(json.dumps(manifest))
    assert _install(release) == 1
    assert "installing the locked dependencies" in capsys.readouterr().err
    assert not (release / installer.VENV).exists()


def test_an_installed_release_is_not_installed_over(release, capsys):
    (release / installer.VENV).mkdir()
    assert _install(release) == 1
    assert "exists already" in capsys.readouterr().err


def test_another_python_version_is_refused(release, capsys):
    manifest = json.loads((release / installer.MANIFEST).read_text())
    manifest["target"]["python"] = "3.9"
    (release / installer.MANIFEST).write_text(json.dumps(manifest))
    assert _install(release) == 1
    assert "the release's wheels are for Python 3.9" in capsys.readouterr().err


def test_a_relative_env_file_is_refused(release, capsys):
    assert _install(release, "--env-file", "site.env") == 1
    assert "must be an absolute path" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("system", "machine", "libc", "says"),
    [
        ("Darwin", "x86_64", ("", ""), "built for linux"),
        ("Linux", "aarch64", ("glibc", "2.35"), "built for x86_64"),
        ("Linux", "x86_64", ("glibc", "2.17"), "need glibc 2.28 or later; this host has 2.17"),
        ("Linux", "x86_64", ("", ""), "could not be identified as glibc"),
    ],
)
def test_a_host_the_wheels_were_not_built_for_is_refused(system, machine, libc, says):
    target = {"os": "linux", "arch": "x86_64", "platforms": ["manylinux_2_28_x86_64"]}
    with pytest.raises(installer.Refused, match=says):
        installer.check_platform(target, system, machine, libc)


def test_a_host_at_or_above_the_glibc_floor_passes():
    target = {"os": "linux", "arch": "x86_64", "platforms": ["manylinux_2_28_x86_64"]}
    installer.check_platform(target, "Linux", "x86_64", ("glibc", "2.28"))
    installer.check_platform(target, "Linux", "x86_64", ("glibc", "2.39"))


def test_the_installer_ships_in_every_release():
    """It must be inside the artifact: the server has no uqs to install with."""
    from uqs.deploy import payload

    assert not payload.is_excluded("scripts/install_release.py")
    assert any(
        "scripts/install_release.py".startswith(prefix + "/") for prefix in payload.ALLOWLIST
    )
