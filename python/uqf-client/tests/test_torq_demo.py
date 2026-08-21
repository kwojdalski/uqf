from pathlib import Path

import pytest

from uqf_client import torq_demo as demo


@pytest.fixture
def fake_paths(tmp_path: Path) -> demo.TorqDemoPaths:
    """A minimal stand-in for lib/torq + lib/torq-finance-starter-pack, so
    bootstrap()'s process.csv/env generation can be tested without touching
    the real vendored trees or needing envsubst/rlwrap on PATH.
    """
    torqhome = tmp_path / "lib" / "torq"
    torqapphome = tmp_path / "lib" / "torq-finance-starter-pack"
    (torqhome).mkdir(parents=True)
    (torqhome / "torq.q").touch()
    (torqapphome / "appconfig").mkdir(parents=True)
    (torqapphome / "database.q").touch()
    (torqapphome / "appconfig" / "process.csv").write_text(
        "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd\n"
        "localhost,{KDBBASEPORT},discovery,discovery1,,1,0,,,${KDBCODE}/processes/discovery.q,1,,q\n"
    )
    (torqapphome / "hdb").mkdir()
    (torqapphome / "dqe").mkdir()

    return demo.TorqDemoPaths(
        repo_root=tmp_path,
        torqhome=torqhome,
        torqapphome=torqapphome,
        torqdata=tmp_path / "scripts" / "output" / "torq-demo",
        scripts_dir=tmp_path / "scripts",
    )


def test_bootstrap_appends_fxfeed1_without_touching_vendored_csv(
    fake_paths: demo.TorqDemoPaths, monkeypatch
):
    monkeypatch.setattr(demo.shutil, "which", lambda _tool: "/usr/bin/true")

    env = demo.bootstrap(fake_paths, base_port=7000)

    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "fxfeed1" not in vendored

    generated = fake_paths.generated_procs.read_text()
    assert "discovery1" in generated
    assert f"localhost,{{KDBBASEPORT}}+{demo.FXFEED_PORT_OFFSET},feed,fxfeed1" in generated

    assert env["KDBBASEPORT"] == "7000"
    assert env["TORQPROCESSES"] == str(fake_paths.generated_procs)
    assert env["SETENV"] == str(fake_paths.generated_setenv)
    assert fake_paths.generated_setenv.is_file()
    assert (fake_paths.torqdata / "hdb").is_dir()
    assert (fake_paths.torqdata / "logs").is_dir()


def test_bootstrap_is_idempotent(fake_paths: demo.TorqDemoPaths, monkeypatch):
    monkeypatch.setattr(demo.shutil, "which", lambda _tool: "/usr/bin/true")

    demo.bootstrap(fake_paths, base_port=7000)
    demo.bootstrap(fake_paths, base_port=7000)  # must not raise (e.g. copytree onto itself)

    generated = fake_paths.generated_procs.read_text()
    assert generated.count("fxfeed1") == 1


def test_clean_removes_generated_data_dir(fake_paths: demo.TorqDemoPaths, monkeypatch):
    monkeypatch.setattr(demo.shutil, "which", lambda _tool: "/usr/bin/true")

    demo.bootstrap(fake_paths, base_port=7000)
    assert fake_paths.torqdata.exists()

    demo.clean(fake_paths)
    assert not fake_paths.torqdata.exists()
