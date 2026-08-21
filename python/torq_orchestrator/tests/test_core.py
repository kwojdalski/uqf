from pathlib import Path

import pytest

from torq_orchestrator import core


@pytest.fixture
def fake_paths(tmp_path: Path) -> core.TorqDemoPaths:
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

    return core.TorqDemoPaths(
        repo_root=tmp_path,
        torqhome=torqhome,
        torqapphome=torqapphome,
        torqdata=tmp_path / "scripts" / "output" / "torq-demo",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "python" / "torq_orchestrator",
    )


def test_bootstrap_appends_fxfeed1_without_touching_vendored_csv(
    fake_paths: core.TorqDemoPaths, monkeypatch
):
    monkeypatch.setattr(core.shutil, "which", lambda _tool: "/usr/bin/true")

    env = core.bootstrap(fake_paths, base_port=7000)

    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "fxfeed1" not in vendored

    generated = fake_paths.generated_procs.read_text()
    assert "discovery1" in generated
    assert f"localhost,{{KDBBASEPORT}}+{core.FXFEED_PORT_OFFSET},feed,fxfeed1" in generated

    assert env["KDBBASEPORT"] == "7000"
    assert env["TORQPROCESSES"] == str(fake_paths.generated_procs)
    assert env["SETENV"] == str(fake_paths.generated_setenv)
    assert fake_paths.generated_setenv.is_file()
    assert (fake_paths.torqdata / "hdb").is_dir()
    assert (fake_paths.torqdata / "logs").is_dir()


def test_bootstrap_is_idempotent(fake_paths: core.TorqDemoPaths, monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda _tool: "/usr/bin/true")

    core.bootstrap(fake_paths, base_port=7000)
    core.bootstrap(fake_paths, base_port=7000)  # must not raise (e.g. copytree onto itself)

    generated = fake_paths.generated_procs.read_text()
    assert generated.count("fxfeed1") == 1


def test_clean_removes_generated_data_dir(fake_paths: core.TorqDemoPaths, monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda _tool: "/usr/bin/true")

    core.bootstrap(fake_paths, base_port=7000)
    assert fake_paths.torqdata.exists()

    core.clean(fake_paths)
    assert not fake_paths.torqdata.exists()


def test_get_process_config_returns_vendored_row(fake_paths: core.TorqDemoPaths):
    row = core.get_process_config(fake_paths, "discovery1")
    assert row["proctype"] == "discovery"
    assert row["port"] == "{KDBBASEPORT}"


def test_get_process_config_unknown_process_raises(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.get_process_config(fake_paths, "nope1")


def test_set_process_config_unknown_field_raises(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.set_process_config(fake_paths, "discovery1", "not_a_field", "x")


def test_set_process_config_persists_and_is_read_back(fake_paths: core.TorqDemoPaths):
    core.set_process_config(fake_paths, "discovery1", "port", "9999")

    assert fake_paths.overrides_path.is_file()
    row = core.get_process_config(fake_paths, "discovery1")
    assert row["port"] == "9999"
    # vendored file itself is never touched
    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "9999" not in vendored


def test_set_process_config_survives_bootstrap_and_flows_into_generated_csv(
    fake_paths: core.TorqDemoPaths, monkeypatch
):
    import csv

    monkeypatch.setattr(core.shutil, "which", lambda _tool: "/usr/bin/true")

    base_rows = {r["procname"]: r for r in core._base_process_rows(fake_paths)}
    assert base_rows["fxfeed1"]["startwithall"] == "1"  # unaffected by the override below

    core.set_process_config(fake_paths, "fxfeed1", "startwithall", "0")
    core.bootstrap(fake_paths, base_port=7000)

    with fake_paths.generated_procs.open(newline="") as f:
        generated_rows = {r["procname"]: r for r in csv.DictReader(f)}
    assert generated_rows["fxfeed1"]["startwithall"] == "0"
