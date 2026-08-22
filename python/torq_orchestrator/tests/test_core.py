import os
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
    (torqapphome / "database.q").write_text(
        'quote:([]time:`timestamp$(); sym:`g#`symbol$(); bid:`float$())\n'
    )
    (torqapphome / "appconfig" / "process.csv").write_text(
        "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd\n"
        "localhost,{KDBBASEPORT},discovery,discovery1,,1,0,,,${KDBCODE}/processes/discovery.q,1,,q\n"
        "localhost,{KDBBASEPORT}+1,segmentedtickerplant,stp1,,1,0,,,"
        "${KDBCODE}/processes/segmentedtickerplant.q,1,"
        "-schemafile ${TORQAPPHOME}/database.q -tplogdir ${KDBTPLOG},q\n"
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


def test_bootstrap_generates_schema_with_quotes_table(
    fake_paths: core.TorqDemoPaths, monkeypatch
):
    monkeypatch.setattr(core.shutil, "which", lambda _tool: "/usr/bin/true")

    core.bootstrap(fake_paths, base_port=7000)

    vendored = (fake_paths.torqapphome / "database.q").read_text()
    assert "quotes:" not in vendored  # vendored file itself is never touched

    generated = fake_paths.generated_schema.read_text()
    assert "quote:" in generated  # vendored table still present
    assert core.QUOTES_TABLE_SCHEMA in generated


def test_bootstrap_repoints_stp1_schemafile_at_generated_copy(
    fake_paths: core.TorqDemoPaths, monkeypatch
):
    monkeypatch.setattr(core.shutil, "which", lambda _tool: "/usr/bin/true")

    core.bootstrap(fake_paths, base_port=7000)

    generated_procs = fake_paths.generated_procs.read_text()
    assert "${TORQDATA}/database.q" in generated_procs
    assert "${TORQAPPHOME}/database.q" not in generated_procs


def test_next_free_port_offset_skips_taken_offsets(fake_paths: core.TorqDemoPaths):
    # fixture's vendored csv: discovery1 (bare {KDBBASEPORT}), stp1 (+1);
    # _base_process_rows also appends fxfeed1(+19)/quotesfeed1(+24)/cross1(+25)/
    # widefeed1(+26)/vectorize1(+27)
    assert core.next_free_port_offset(fake_paths) == core.VECTORIZE_ETL_PORT_OFFSET + 1


def test_add_extra_process_appears_in_base_rows(fake_paths: core.TorqDemoPaths):
    offset = core.next_free_port_offset(fake_paths)
    core.add_extra_process(
        fake_paths,
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{offset}",
            "proctype": "feed",
            "procname": "wizardfeed1",
            "U": "",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/wizardfeed1.q",
            "startwithall": "1",
            "extras": "",
            "qcmd": "q",
        },
    )

    assert "wizardfeed1" in core.list_process_names(fake_paths)
    row = core.get_process_config(fake_paths, "wizardfeed1", base_port=7000)
    assert row["port"] == str(7000 + offset)

    # extra_processes.csv is the only file touched - vendored csv untouched
    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "wizardfeed1" not in vendored


def test_add_extra_process_rejects_duplicate_procname(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.add_extra_process(fake_paths, {"procname": "stp1", "proctype": "x"})


def test_add_extra_table_schema_appears_in_generated_schema(fake_paths: core.TorqDemoPaths):
    core.add_extra_table_schema(fake_paths, "mytable:([]time:`timestamp$(); sym:`g#`symbol$())")

    generated = core._generated_schema_content(fake_paths)
    assert "mytable:" in generated
    assert "quotes:" in generated  # existing extension point untouched


def test_list_items_unknown_kind_raises(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.list_items(fake_paths, "not_a_kind")


def test_list_processes_includes_vendored_and_fxfeed1_resolved(fake_paths: core.TorqDemoPaths):
    items = core.list_items(fake_paths, "processes", base_port=7000)
    by_name = {item["procname"]: item for item in items}
    assert set(by_name) == {
        "discovery1",
        "stp1",
        "fxfeed1",
        "quotesfeed1",
        "cross1",
        "widefeed1",
        "vectorize1",
    }
    assert by_name["discovery1"]["port"] == "7000"
    assert by_name["fxfeed1"]["port"] == str(7000 + core.FXFEED_PORT_OFFSET)
    assert by_name["quotesfeed1"]["port"] == str(7000 + core.QUOTES_FEED_PORT_OFFSET)
    assert by_name["cross1"]["port"] == str(7000 + core.CROSS_ETL_PORT_OFFSET)
    assert by_name["widefeed1"]["port"] == str(7000 + core.WIDE_BOOK_FEED_PORT_OFFSET)
    assert by_name["vectorize1"]["port"] == str(7000 + core.VECTORIZE_ETL_PORT_OFFSET)


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


def test_parse_log_line_splits_seven_fields():
    line = (
        "2026.08.22D14:21:10.644413000|mac.lan|segmentedtickerplant|stp1|"
        "INF|fileload|loading /some/path with | a pipe in it"
    )
    rec = core.parse_log_line(line)
    assert rec == {
        "time": "2026.08.22D14:21:10.644413000",
        "host": "mac.lan",
        "proctype": "segmentedtickerplant",
        "procname": "stp1",
        "loglevel": "INF",
        "id": "fileload",
        "message": "loading /some/path with | a pipe in it",
    }


def test_parse_log_line_returns_none_for_non_matching_line():
    assert core.parse_log_line("some banner line with no pipes") is None
    assert core.parse_log_line("a|b|c") is None


def test_resolve_procnames_all_returns_every_process(fake_paths: core.TorqDemoPaths):
    assert set(core.resolve_procnames(fake_paths, "all")) == {
        "discovery1",
        "stp1",
        "fxfeed1",
        "quotesfeed1",
        "cross1",
        "widefeed1",
        "vectorize1",
    }


def test_resolve_procnames_specific_splits_on_space(fake_paths: core.TorqDemoPaths):
    assert core.resolve_procnames(fake_paths, "stp1 rdb1") == ["stp1", "rdb1"]


def test_print_recent_logs_raises_when_no_log_files(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.print_recent_logs(fake_paths, "discovery1")


def test_print_recent_logs_emits_sorted_by_time(fake_paths: core.TorqDemoPaths, capsys):
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|INF|x|second\n"
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|first\n"
    )

    core.print_recent_logs(fake_paths, "discovery1")

    # print_recent_logs reconfigures loguru's sink onto sys.stdout at call
    # time (see _configure_kdb_log_sink), i.e. after capsys has already
    # patched sys.stdout - unlike loguru's own un-configured default sink
    # (bound once, at import time), this one is reliably captured here.
    out = capsys.readouterr().out
    assert out.index("first") < out.index("second")


def test_print_recent_logs_filters_by_min_level(fake_paths: core.TorqDemoPaths, capsys):
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|quiet info\n"
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|ERR|x|loud error\n"
    )

    core.print_recent_logs(fake_paths, "discovery1", min_level="ERROR")

    out = capsys.readouterr().out
    assert "loud error" in out
    assert "quiet info" not in out


def test_list_env_includes_kdbbaseport(fake_paths: core.TorqDemoPaths):
    items = core.list_items(fake_paths, "env", base_port=7000)
    by_name = {item["name"]: item["value"] for item in items}
    assert by_name["KDBBASEPORT"] == "7000"
    assert by_name["KDBHDB"] == str(fake_paths.torqdata / "hdb")


def test_cryptorust_root_defaults_to_sibling_dir(fake_paths: core.TorqDemoPaths, monkeypatch):
    monkeypatch.delenv(core.CRYPTORUST_ROOT_ENV, raising=False)
    assert core.cryptorust_root(fake_paths) == fake_paths.repo_root.parent / "cryptorust"


def test_cryptorust_root_respects_env_override(
    fake_paths: core.TorqDemoPaths, monkeypatch, tmp_path
):
    override = tmp_path / "elsewhere"
    monkeypatch.setenv(core.CRYPTORUST_ROOT_ENV, str(override))
    assert core.cryptorust_root(fake_paths) == override


def test_crypto_recorder_config_yaml_contains_overrides():
    content = core._crypto_recorder_config_yaml(
        stp1_port=7000,
        venues=["binance_spot"],
        symbols=["BTC-USDT", "ETH-USDT"],
        credential="feed:pass",
        table="crypto_book",
        top_n_levels=5,
        interval_ms=1000,
    )
    assert "port: 7000" in content
    assert "- binance_spot" in content
    assert "- BTC-USDT" in content
    assert '"feed:pass"' in content
    assert "table: crypto_book" in content


def test_start_crypto_recorder_rejects_non_cryptorust_dir(
    fake_paths: core.TorqDemoPaths, monkeypatch, tmp_path
):
    monkeypatch.setenv(core.CRYPTORUST_ROOT_ENV, str(tmp_path / "not-a-checkout"))
    with pytest.raises(core.TorqDemoError):
        core.start_crypto_recorder(fake_paths)


def test_crypto_recorder_status_when_never_started(fake_paths: core.TorqDemoPaths):
    status = core.crypto_recorder_status(fake_paths)
    assert status["running"] == "False"
    assert status["pid"] == ""


def test_is_crypto_recorder_running_reflects_live_pid(fake_paths: core.TorqDemoPaths):
    fake_paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    fake_paths.crypto_recorder_pid_path.write_text(str(os.getpid()))
    assert core.is_crypto_recorder_running(fake_paths) is True


def test_is_crypto_recorder_running_false_for_dead_pid(fake_paths: core.TorqDemoPaths):
    fake_paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    # a pid essentially guaranteed not to be a running process
    fake_paths.crypto_recorder_pid_path.write_text("999999")
    assert core.is_crypto_recorder_running(fake_paths) is False


def test_stop_crypto_recorder_raises_without_pidfile(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.stop_crypto_recorder(fake_paths)
