"""Kafka in a release (#1515): opt-in packaging of both consumers, checked
at build and before any process starts, with no broker.

The native libraries are tiny files carrying only an ELF header, and ldd is
a fake, so packaging is tested on any builder; one test loads the real kfk
in a real q when this machine has both.
"""

from __future__ import annotations

import io
import json
import struct
import subprocess
import tarfile
from pathlib import Path

import pytest
from test_build_release import _built, _tools, _tree
from test_deploy import _HEALTHY, ARGS, FakeRemote, _done, _verified, parse_args

from uqs.deploy import artifact, driver, kafka
from uqs.deploy import build as release_build
from uqs.deploy.artifact import ReleaseError
from uqs.external import kafka_feed
from uqs.paths import UqsError
from uqs.stack import kafka_extra

LOCAL_KFK = Path.home() / ".local" / "kfk"


def _elf(path: Path, machine: int = 62) -> Path:
    """A file that reads as a 64-bit little-endian ELF object for `machine`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ident = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    path.write_bytes(ident + struct.pack("<HH", 3, machine) + bytes(44))
    return path


def _kafka_home(tmp_path: Path, *, machine: int = 62, notice: bool = True) -> Path:
    home = tmp_path / "kfk"
    home.mkdir()
    (home / "kfk.q").write_text("\\d .kfk\n")
    _elf(home / "libkfk.so", machine)
    if notice:
        (home / "LICENSE").write_text("Apache-2.0\n")
        (home / "librdkafka").mkdir()
        (home / "librdkafka" / "LICENSES.txt").write_text("BSD-2-Clause\n")
    return home


def _ldd(deps: Path, machine: int = 62, extra: str = "") -> str:
    """What ldd prints for libkfk.so: librdkafka beside it, and the C library."""
    rdk = _elf(deps / "librdkafka.so.1.2.3", machine)
    return (
        "\tlinux-vdso.so.1 (0x00007ffc)\n"
        f"\tlibrdkafka.so.1 => {rdk} (0x00007f01)\n"
        "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f02)\n"
        "\tlibpthread.so.0 => /lib/x86_64-linux-gnu/libpthread.so.0 (0x00007f03)\n"
        "\t/lib64/ld-linux-x86-64.so.2 (0x00007f04)\n" + extra
    )


def _with_ldd(runner, ldd_out: str, rc: int = 0):
    calls: list[list[str]] = []

    def run(argv, **kw):
        if argv[0] == "ldd":
            calls.append(list(argv))
            return _done(ldd_out, rc=rc, stderr="not a dynamic executable" if rc else "")
        return runner(argv, **kw)

    return run, calls


@pytest.fixture(autouse=True)
def _linux_builder(monkeypatch):
    monkeypatch.setattr(kafka, "BUILDER_PLATFORM", "linux")


def _built_with(argv: list[str], **kwargs):
    """_built, passing the Kafka options the command parsed."""
    from test_build_release import _CLI

    p = _CLI.commands["build"].make_context("build", list(argv)).params
    return _built(
        argv,
        kafka_home=p["kafka_home"],
        kafka_connector=p["with_kafka_connector"],
        **kwargs,
    )


def _build_kafka(tmp_path: Path, *argv: str, ldd_out: str | None = None):
    root = tmp_path / "tree"
    tools, calls = _tools(_tree(root))
    runner, ldd_calls = _with_ldd(tools, ldd_out or _ldd(tmp_path / "deps"))
    art = _built_with(["--output", str(tmp_path / "dist"), *argv], root=root, runner=runner)
    return art, calls, ldd_calls


def _members(art) -> list[str]:
    with tarfile.open(art.path) as tar:
        return tar.getnames()


# --- a default release ----------------------------------------------------------


def test_a_default_release_carries_no_kafka_dependency(tmp_path):
    art, calls, ldd_calls = _build_kafka(tmp_path)
    assert not [m for m in _members(art) if "kafka" in m or "kfk" in m]
    assert "kafka" not in art.manifest and "extras" not in art.manifest["python"]
    export = next(c for c in calls if "export" in c)
    assert "--extra" not in export
    assert ldd_calls == []


# --- the native client ----------------------------------------------------------


def test_kafka_home_packages_kfk_its_libraries_and_notices_but_never_glibc(tmp_path):
    home = _kafka_home(tmp_path)
    art, _, ldd_calls = _build_kafka(tmp_path, "--kafka-home", str(home))
    names = _members(art)
    d = kafka.KAFKA_DIR
    for want in ("kfk.q", "libkfk.so", "lib/librdkafka.so.1", "notices/LICENSE",
                 "notices/librdkafka/LICENSES.txt"):  # fmt: skip
        assert f"{d}/{want}" in names
    assert not [n for n in names if "libc.so" in n or "ld-linux" in n or "pthread" in n]
    assert art.manifest["kafka"] == {
        "home": d,
        "arch": "x86_64",
        "libraries": ["librdkafka.so.1"],
        "notices": ["notices/LICENSE", "notices/librdkafka/LICENSES.txt"],
    }
    assert ldd_calls == [["ldd", str(home.resolve() / "libkfk.so")]]
    # every packaged file is hashed like any other member, so push checks it
    assert f"{d}/lib/librdkafka.so.1" in art.manifest["files"]
    assert artifact.read_artifact(art.path).manifest["kafka"]["home"] == d


def test_a_library_for_another_architecture_is_refused(tmp_path):
    home = _kafka_home(tmp_path, machine=183)  # aarch64
    with pytest.raises(ReleaseError, match=r"libkfk.so is built for aarch64.*linux/x86_64"):
        _build_kafka(tmp_path, "--kafka-home", str(home))
    assert not (tmp_path / "dist").exists()


def test_a_dependency_for_another_architecture_is_refused(tmp_path):
    home = _kafka_home(tmp_path)
    ldd_out = _ldd(tmp_path / "deps", machine=183)
    with pytest.raises(ReleaseError, match=r"librdkafka.so.1.2.3 is built for aarch64"):
        _build_kafka(tmp_path, "--kafka-home", str(home), ldd_out=ldd_out)


def test_a_macos_library_is_refused_for_a_linux_release(tmp_path):
    home = _kafka_home(tmp_path)
    (home / "libkfk.so").write_bytes(b"\xcf\xfa\xed\xfe" + bytes(60))
    with pytest.raises(ReleaseError, match=r"macOS \(Mach-O\) library - a release runs on Linux"):
        _build_kafka(tmp_path, "--kafka-home", str(home))


def test_the_aarch64_target_takes_an_aarch64_library(tmp_path):
    home = _kafka_home(tmp_path, machine=183)
    art, _, _ = _build_kafka(
        tmp_path, "--arch", "aarch64", "--kafka-home", str(home),
        ldd_out=_ldd(tmp_path / "deps", machine=183),
    )  # fmt: skip
    assert art.manifest["kafka"]["arch"] == "aarch64"


@pytest.mark.parametrize("gone", ["kfk.q", "libkfk.so"])
def test_a_kafka_home_missing_kfk_is_refused(tmp_path, gone):
    home = _kafka_home(tmp_path)
    (home / gone).unlink()
    with pytest.raises(ReleaseError, match=rf"lacks {gone} - it must hold kfk.q and libkfk.so"):
        _build_kafka(tmp_path, "--kafka-home", str(home))


def test_a_kafka_home_with_no_licence_is_refused(tmp_path):
    home = _kafka_home(tmp_path, notice=False)
    with pytest.raises(ReleaseError, match="holds no LICENSE, NOTICE or COPYING file"):
        _build_kafka(tmp_path, "--kafka-home", str(home))


def test_a_dependency_ldd_cannot_find_is_refused(tmp_path):
    home = _kafka_home(tmp_path)
    ldd_out = _ldd(tmp_path / "deps", extra="\tlibzstd.so.1 => not found\n")
    with pytest.raises(ReleaseError, match=r"needs libzstd.so.1, which ldd does not find"):
        _build_kafka(tmp_path, "--kafka-home", str(home), ldd_out=ldd_out)


def test_a_library_ldd_cannot_read_is_refused(tmp_path):
    home = _kafka_home(tmp_path)
    root = tmp_path / "tree"
    tools, _ = _tools(_tree(root))
    runner, _ = _with_ldd(tools, "", rc=1)
    with pytest.raises(ReleaseError, match="ldd could not list libkfk.so's libraries"):
        _built_with(
            ["--output", str(tmp_path / "dist"), "--kafka-home", str(home)],
            root=root,
            runner=runner,
        )


def test_a_macos_builder_lists_libraries_with_otool_leaving_the_system_out(tmp_path):
    lib = tmp_path / "libkfk.so"
    rdk = tmp_path / "librdkafka.1.dylib"
    out = (
        f"{lib}:\n\tlibkfk.so (compatibility version 0.0.0)\n"
        f"\t{rdk} (compatibility version 0.0.0)\n"
        "\t/usr/lib/libz.1.dylib (compatibility version 1.0.0)\n"
        "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
    )
    seen = []

    def runner(argv, **_):
        seen.append(argv)
        return _done(out)

    assert kafka.dependencies(lib, runner, platform="darwin") == {rdk.name: rdk}
    assert seen == [["otool", "-L", str(lib)]]


# --- the Python connector's wheels ------------------------------------------------


def test_with_kafka_connector_exports_the_kafka_extra_into_the_wheels(tmp_path):
    art, calls, ldd_calls = _build_kafka(tmp_path, "--with-kafka-connector")
    export = next(c for c in calls if "export" in c)
    assert export[export.index("--extra") + 1] == "kafka"
    assert art.manifest["python"]["extras"] == ["kafka"]
    assert "kafka" not in art.manifest and ldd_calls == []


def test_the_extra_is_declared_in_uqs_and_locked():
    import tomllib

    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "python/uqs/pyproject.toml").read_text())["project"]
    assert any(d.startswith("confluent-kafka") for d in project["optional-dependencies"]["kafka"])
    assert all(not d.startswith("confluent-kafka") for d in project["dependencies"])
    assert 'name = "confluent-kafka"' in (root / "uv.lock").read_text()


# --- the server -----------------------------------------------------------------


def _kafka_artifact(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    files = ["pyproject.toml", "src/init.q", "scripts/deploy_smoke.q"]
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    py = tmp_path / "py"
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("rich==15.0.0 --hash=sha256:00\n")
    (py / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
    staged = tmp_path / "staged-kafka"
    record = kafka.package(
        _kafka_home(tmp_path),
        "x86_64",
        staged,
        _with_ldd(lambda *a, **k: _done(), _ldd(tmp_path / "deps"))[0],
    )
    art = release_build.build_artifact(
        root,
        tmp_path / "dist",
        rid="20261007T000000Z-0123456789ab",
        rev="0123456789abcdef",
        dirty=False,
        files=files,
        target=artifact.Target(os="linux", arch="x86_64", python="3.14"),
        python_dir=py,
        kafka_sel=kafka.Selected(staged=staged, record=record),
    )
    return art.path


def _push(path: Path, rules: dict, *extra: str):
    remote = FakeRemote({**_HEALTHY, "uqs deploy verify --profile": _verified(True), **rules})
    out = io.StringIO()
    code = driver.deploy(parse_args([str(path), *ARGS, *extra]), remote, out=out)
    return code, remote, (json.loads(out.getvalue()) if out.getvalue().startswith("{") else None)


LOADED = {kafka.LOAD_MARKER: _done(f"{kafka.LOAD_MARKER} librdkafka 34603263i\n")}


def test_the_release_environment_points_every_process_at_its_own_kfk(tmp_path):
    code, remote, _ = _push(_kafka_artifact(tmp_path), LOADED)
    assert code == 0
    prepare = remote.stage("prepare")[0]
    env = prepare.split("<<'DEPLOYENV'")[1].split("DEPLOYENV")[0]
    home = "/opt/uqf/releases/20261007T000000Z-0123456789ab/.release/kafka"
    assert f"export UQF_KFK_HOME={home}\n" in env
    assert f'export LD_LIBRARY_PATH={home}/lib"${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"' in env


def test_the_native_load_check_runs_before_anything_starts(tmp_path):
    code, remote, report = _push(_kafka_artifact(tmp_path), LOADED)
    assert code == 0 and report["status"] == "deployed"
    stages = [stage for stage, _ in remote.scripts]
    assert stages.index("prepare") < stages.index("kafka") < stages.index("start")
    check = remote.stage("kafka")[0]
    assert "source ./deploy.env" in check and 'cd "$UQF_KFK_HOME"' in check
    assert '"$QCMD"' in check and "l kfk.q" in check


def test_a_failed_load_check_stops_the_deployment_before_any_start(tmp_path):
    failed = {kafka.LOAD_MARKER: _done(rc=1, stderr="kfk did not load: librdkafka.so.1: cannot "
                                       "open shared object file")}  # fmt: skip
    code, remote, report = _push(_kafka_artifact(tmp_path), failed)
    assert code == 1 and report["stage"] == "kafka"
    assert "librdkafka.so.1" in report["error"]
    assert not remote.ran("uqs start") and not remote.ran(".current.new")


def test_a_release_without_kfk_runs_no_check_and_sets_nothing(tmp_path):
    from test_deploy import _artifact

    code, remote, report = _push(_artifact(tmp_path), {})
    assert code == 0 and report["status"] == "deployed"
    assert remote.stage("kafka") == []
    assert not remote.ran("UQF_KFK_HOME") and not remote.ran("LD_LIBRARY_PATH")


def test_the_dry_run_plan_names_the_kafka_check(tmp_path):
    remote = FakeRemote({"uv python find": _done("os=Linux\narch=x86_64\npython=3.14\n"
                         "qhome=/q\nqcmd=/q/q\ndata=present\n")})  # fmt: skip
    out = io.StringIO()
    cfg = parse_args([str(_kafka_artifact(tmp_path)), *ARGS, "--dry-run"])
    assert driver.deploy(cfg, remote, out=out) == 0
    assert "; UQF_KFK_HOME=/opt/uqf/releases/" in out.getvalue()
    assert "kfk.q loaded once in $QCMD (no broker)" in out.getvalue()


def _run_check(home: Path, qcmd: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    lines = kafka.load_check_lines(60)
    return subprocess.run(
        ["bash", "-c", "\n".join(lines)],
        env={**env, "QCMD": qcmd, "UQF_KFK_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.skipif(not (LOCAL_KFK / "kfk.q").is_file(), reason=f"no kfk build in {LOCAL_KFK}")
def test_the_load_check_loads_a_real_kfk_in_a_real_q(q_binary, tmp_path):
    """The deploy-time check, run as written against this machine's kfk -
    no broker - and against a copy whose libkfk.so is not a library."""
    q, env = q_binary
    ok = _run_check(LOCAL_KFK, q, env)
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.startswith(f"{kafka.LOAD_MARKER} librdkafka ")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "kfk.q").write_text((LOCAL_KFK / "kfk.q").read_text())
    (broken / "libkfk.so").write_bytes(b"not a library")
    bad = _run_check(broken, q, env)
    assert bad.returncode == 1
    assert f"kfk did not load from {broken}" in bad.stderr and "kfk.q did not load" in bad.stderr


# --- the Python connector's prerequisite ----------------------------------------


@pytest.fixture
def no_extra(monkeypatch):
    real = kafka_extra.importlib.util.find_spec
    monkeypatch.setattr(
        kafka_extra.importlib.util,
        "find_spec",
        lambda name, *a: None if name == kafka_extra.MODULE else real(name, *a),
    )


def test_the_connector_refuses_without_the_extra_naming_it(no_extra, tmp_path, monkeypatch):
    monkeypatch.setattr(kafka_feed, "get_process_config", lambda *a: pytest.fail("looked for stp1"))
    with pytest.raises(UqsError) as exc:
        kafka_feed.start_kafka_feed(None)  # ty: ignore[invalid-argument-type]
    msg = str(exc.value)
    assert "needs confluent-kafka, the uqs `kafka` extra" in msg
    assert "uv sync --extra kafka" in msg and "--with-kafka-connector" in msg


def test_uqs_connector_start_kafka_reports_the_missing_extra(no_extra, monkeypatch):
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli import shared

    errors: list[str] = []

    class Log:
        def error(self, fmt, *args):
            errors.append(fmt.format(*args))

    monkeypatch.setattr(shared, "log", Log())
    r = CliRunner().invoke(cli.app, ["connector", "start", "kafka"])
    assert r.exit_code == 1
    assert any("the uqs `kafka` extra" in m for m in errors), errors


def test_the_kafka_example_reports_the_missing_extra(no_extra, capsys):
    from uqs.external.kafka_example.__main__ import main

    assert main(["publish", "--dir", "/nonexistent"]) == 2
    assert "the uqs `kafka` extra" in capsys.readouterr().err


def test_the_connector_runs_in_the_interpreter_that_was_checked(tmp_path, monkeypatch):
    """Not `uv run`: on a server it would sync the release's venv to the lock."""
    monkeypatch.setattr(kafka_extra, "available", lambda: True)
    monkeypatch.setattr(kafka_feed, "get_process_config", lambda *a: {"port": 6550})
    started = {}

    class Proc:
        log_path = tmp_path / "kafka_feed.log"

        def start(self, cmd, cwd):
            started["cmd"] = cmd
            return 42

    monkeypatch.setattr(kafka_feed, "_process", lambda paths: Proc())

    class Paths:
        repo_root = tmp_path

    assert kafka_feed.start_kafka_feed(Paths()) == 42  # ty: ignore[invalid-argument-type]
    import sys

    assert started["cmd"][0] == sys.executable and "uv" not in started["cmd"][:2]
