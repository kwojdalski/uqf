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
    row = core.get_process_config(fake_paths, "discovery1", resolve=False)
    assert row["proctype"] == "discovery"
    assert row["port"] == "{KDBBASEPORT}"


def test_get_process_config_unknown_process_raises(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.get_process_config(fake_paths, "nope1")


def test_get_process_config_resolves_brace_arith_placeholder(fake_paths: core.TorqDemoPaths):
    row = core.get_process_config(fake_paths, "fxfeed1", base_port=7000)
    assert row["port"] == str(7000 + core.FXFEED_PORT_OFFSET)


def test_get_process_config_resolves_dollar_brace_placeholder(fake_paths: core.TorqDemoPaths):
    row = core.get_process_config(fake_paths, "discovery1")
    assert row["load"] == str(fake_paths.torqhome / "code" / "processes" / "discovery.q")


def test_get_process_config_resolve_false_leaves_placeholders_literal(
    fake_paths: core.TorqDemoPaths,
):
    row = core.get_process_config(fake_paths, "discovery1", resolve=False)
    assert row["load"] == "${KDBCODE}/processes/discovery.q"
    assert row["port"] == "{KDBBASEPORT}"


def test_resolve_process_config_leaves_unknown_var_literal():
    row = {"port": "{NOT_A_REAL_VAR}", "load": "${ALSO_NOT_REAL}/x.q"}
    resolved = core.resolve_process_config(row, {"KDBBASEPORT": "6010"})
    assert resolved["port"] == "{NOT_A_REAL_VAR}"
    assert resolved["load"] == "${ALSO_NOT_REAL}/x.q"


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


def test_list_items_unknown_kind_raises(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.list_items(fake_paths, "not_a_kind")


def test_list_processes_includes_vendored_and_fxfeed1_resolved(fake_paths: core.TorqDemoPaths):
    items = core.list_items(fake_paths, "processes", base_port=7000)
    by_name = {item["procname"]: item for item in items}
    assert set(by_name) == {"discovery1", "fxfeed1"}
    assert by_name["discovery1"]["port"] == "7000"
    assert by_name["fxfeed1"]["port"] == str(7000 + core.FXFEED_PORT_OFFSET)


def test_list_processes_reflects_overrides(fake_paths: core.TorqDemoPaths):
    core.set_process_config(fake_paths, "fxfeed1", "startwithall", "0")
    items = core.list_items(fake_paths, "processes")
    by_name = {item["procname"]: item for item in items}
    assert by_name["fxfeed1"]["startwithall"] == "0"


def test_list_fields_matches_process_csv_fields(fake_paths: core.TorqDemoPaths):
    items = core.list_items(fake_paths, "fields")
    assert [item["field"] for item in items] == list(core.PROCESS_CSV_FIELDS)


def test_list_overrides_empty_then_populated(fake_paths: core.TorqDemoPaths):
    assert core.list_items(fake_paths, "overrides") == []

    core.set_process_config(fake_paths, "discovery1", "port", "9999")
    items = core.list_items(fake_paths, "overrides")
    assert items == [{"procname": "discovery1", "field": "port", "value": "9999"}]


def test_list_env_includes_kdbbaseport(fake_paths: core.TorqDemoPaths):
    items = core.list_items(fake_paths, "env", base_port=7000)
    by_name = {item["name"]: item["value"] for item in items}
    assert by_name["KDBBASEPORT"] == "7000"
    assert by_name["KDBHDB"] == str(fake_paths.torqdata / "hdb")
