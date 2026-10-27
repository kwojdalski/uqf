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
            "arch": installer.ARCH_ALIASES.get(platform.machine(), platform.machine()),
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
    target = {"os": "linux", "arch": "x86_64", "glibc": "2.28"}
    with pytest.raises(installer.Refused, match=says):
        installer.check_platform(target, system, machine, libc)


def test_a_host_at_or_above_the_glibc_floor_passes():
    target = {"os": "linux", "arch": "x86_64", "glibc": "2.28"}
    installer.check_platform(target, "Linux", "x86_64", ("glibc", "2.28"))
    installer.check_platform(target, "Linux", "x86_64", ("glibc", "2.39"))


def test_pure_python_wheels_set_no_glibc_floor():
    """The floor is what the build recorded from the wheels' manylinux tags,
    never guessed from the platforms pip was allowed: none recorded, none checked."""
    target = {
        "os": "linux",
        "arch": "x86_64",
        "glibc": None,
        "platforms": ["manylinux_2_28_x86_64"],
    }
    installer.check_platform(target, "Linux", "x86_64", ("musl", "1.2"))


def test_the_installer_names_architectures_as_the_push_does():
    from uqs.deploy.artifact import ARCH_ALIASES

    assert installer.ARCH_ALIASES == ARCH_ALIASES
    installer.check_platform({"os": "darwin", "arch": "aarch64"}, "Darwin", "arm64", ("", ""))


def test_the_installer_ships_in_every_release():
    """It must be inside the artifact: the server has no uqs to install with."""
    from uqs.deploy import payload

    assert not payload.is_excluded("scripts/install_release.py")
    assert any(
        "scripts/install_release.py".startswith(prefix + "/") for prefix in payload.ALLOWLIST
    )


# --- a release that carries its Python (#1557, step 7) -------------------------------


@pytest.fixture
def carrying(release: Path, tmp_path: Path) -> tuple[Path, dict]:
    """`release`, carrying a Python runtime as `uqs deploy build --python-runtime`
    puts one in: the archive at its path, recorded in the manifest."""
    from test_deploy_python_runtime import runtime

    from uqs.deploy import python_runtime

    rt = runtime(tmp_path / "rt")
    archive = release / python_runtime.CARRIED
    archive.write_bytes(rt.path.read_bytes())
    manifest = json.loads((release / installer.MANIFEST).read_text())
    manifest["files"][python_runtime.CARRIED] = rt.sha256
    manifest["python"]["runtime"] = rt.carried()
    (release / installer.MANIFEST).write_text(json.dumps(manifest))
    return release, rt.carried()


def test_check_names_the_carried_python_and_unpacks_nothing(carrying, capsys):
    release, record = carrying
    assert installer.main(["--release", str(release), "--check"]) == 0
    assert "carries Python 3.14.9, unpacked and run by the install" in capsys.readouterr().out
    assert not (release / installer.RUNTIME_DIR).exists()


def test_the_carried_python_is_unpacked_once_and_checked_file_by_file(carrying):
    release, record = carrying
    interpreter = installer.carried_python(release, record)
    home = release / installer.RUNTIME_DIR
    assert interpreter == str(home / "bin" / "python3.14")
    assert installer.carried_python(release, record) == interpreter, "a rerun reuses it"
    (home / "lib" / "python3.14" / "os.py").write_text("# changed\n")
    with pytest.raises(installer.Refused, match="is not the runtime it was unpacked from"):
        installer.carried_python(release, record)


def test_a_pyc_the_carried_python_wrote_for_its_own_source_is_a_cache(carrying):
    """Its first run writes a .pyc for a stdlib module the build shipped none
    for; CPython checks it against the verified source. Not a change."""
    release, record = carrying
    installer.carried_python(release, record)
    cache = release / installer.RUNTIME_DIR / "lib" / "python3.14" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "os.cpython-314.pyc").write_bytes(b"\x00pyc")
    installer.carried_python(release, record)
    (cache / "orphan.cpython-314.pyc").write_bytes(b"\x00pyc")
    with pytest.raises(installer.Refused, match="orphan.cpython-314.pyc"):
        installer.carried_python(release, record)


def test_a_carried_archive_that_is_not_the_recorded_one_is_refused(carrying):
    release, record = carrying
    with pytest.raises(installer.Refused, match="is not the Python runtime the manifest records"):
        installer.carried_python(release, {**record, "sha256": "ab" * 32})
    assert not (release / installer.RUNTIME_DIR).exists()


def test_a_release_built_with_frontend_installs_its_wheel_after_uqs(release):
    """`uqs deploy build --frontend` lists uqf-frontend's wheel as an extra."""
    wheel = _wheel(release / ".release" / "wheels", "uqffront", "0.1.0", "uqffront_probe",
                   "import uqs_probe\nREADY = uqs_probe.ANSWER\n")  # fmt: skip
    manifest = json.loads((release / installer.MANIFEST).read_text())
    rel = wheel.relative_to(release).as_posix()
    manifest["files"][rel] = _sha(wheel)
    manifest["python"]["extra_wheels"] = [rel]
    (release / installer.MANIFEST).write_text(json.dumps(manifest))
    assert _install(release) == 0
    out = subprocess.run(
        [str(release / installer.VENV / "bin" / "python"), "-c",
         "import uqffront_probe; print(uqffront_probe.READY)"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert out.stdout.strip() == "42"


# --- the host contract, and the environment it writes (#1646) ------------------------


def _manifest(release: Path, **extra) -> None:
    """`release`'s manifest with `extra` keys, which no file listing covers."""
    manifest = json.loads((release / installer.MANIFEST).read_text())
    manifest.update(extra)
    (release / installer.MANIFEST).write_text(json.dumps(manifest))


def test_a_host_lacking_a_contract_tool_is_refused_before_anything_is_written(release, capsys):
    _manifest(release, host={"tools": ["sh", "uqf-no-such-tool"]})
    assert _install(release) == 1
    err = capsys.readouterr().err
    assert "this host lacks uqf-no-such-tool" in err and "minimum host contract" in err
    assert not (release / installer.VENV).exists()
    assert _install(release, "--check") == 1, "--check refuses it too"


def test_a_host_with_every_contract_tool_installs(release, capsys):
    _manifest(release, host={"tools": ["sh"]})
    assert _install(release) == 0
    assert "the host has the 1 tools the release runs" in capsys.readouterr().out


def test_a_release_naming_no_host_tools_checks_none():
    assert installer.check_host({}) == []


def test_every_release_names_the_tools_preflight_checks(tmp_path):
    from uqs.deploy import artifact, preflight
    from uqs.deploy import build as release_build

    root, py = tmp_path / "tree", tmp_path / "py"
    (root / "src").mkdir(parents=True)
    (root / "src" / "init.q").write_text("/ the tree\n")
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("")
    pkg = release_build.build_artifact(
        root, tmp_path / "out", rid="20261007T000000Z-0123456789ab", rev="0123456789abcdef",
        dirty=False, files=["src/init.q"], python_dir=py,
        target=artifact.Target(os="linux", arch="x86_64", python="3.14"),
    )  # fmt: skip
    assert pkg.manifest["host"]["tools"] == list(artifact.HOST_TOOLS)
    loop = preflight.PREFLIGHT.split("for tool in ", 1)[1].split("; do", 1)[0]
    assert tuple(loop.replace("\\\n", " ").split()) == artifact.HOST_TOOLS


def test_no_env_file_and_no_kafka_write_no_deploy_env(release):
    assert _install(release) == 0
    assert not (release / installer.ENV_FILE).exists()


def test_a_release_carrying_kafka_names_its_own_copy_in_deploy_env(release, tmp_path):
    _manifest(release, kafka={"native": {"kfk": "1.6.6"}})
    env_file = tmp_path / "site.env"
    env_file.write_text("export LD_LIBRARY_PATH=/opt/site/lib\n")
    assert _install(release, "--env-file", str(env_file)) == 0
    home = release.resolve() / ".release" / "kafka"
    lines = (release / installer.ENV_FILE).read_text().splitlines()
    assert lines[1:] == [
        f"set -a; . {env_file}; set +a",
        f"export UQF_KFK_HOME={home}",
        f'export LD_LIBRARY_PATH={home}/lib"${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"',
    ], "the site file first, so the release's own copy comes ahead of the site's"
    out = subprocess.run(
        ["bash", "-c", f". {release / installer.ENV_FILE}; echo $UQF_KFK_HOME; "
         "echo $LD_LIBRARY_PATH"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert out == [str(home), f"{home}/lib:/opt/site/lib"]


def test_the_installer_puts_kafka_where_the_build_does():
    from uqs.deploy import kafka

    assert installer.KAFKA_DIR == kafka.KAFKA_DIR and installer.KAFKA_LIB == kafka.LIB_SUBDIR


def test_a_relative_env_file_is_refused_before_the_venv_is_made(release, capsys):
    assert _install(release, "--env-file", "site.env") == 1
    assert not (release / installer.VENV).exists()
