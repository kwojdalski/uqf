import os
from pathlib import Path

import pytest

from torq_orchestrator import core, pipelines


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
        "quote:([]time:`timestamp$(); sym:`g#`symbol$(); bid:`float$())\n"
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


def test_bootstrap_generates_schema_with_quotes_table(fake_paths: core.TorqDemoPaths, monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda _tool: "/usr/bin/true")

    core.bootstrap(fake_paths, base_port=7000)

    vendored = (fake_paths.torqapphome / "database.q").read_text()
    assert "quotes:" not in vendored  # vendored file itself is never touched

    generated = fake_paths.generated_schema.read_text()
    assert "quote:" in generated  # vendored table still present
    assert core.QUOTES_TABLE_SCHEMA in generated
    assert core.TRADES_TABLE_SCHEMA in generated
    assert core.POSITION_TABLE_SCHEMA in generated
    assert core.EXECUTION_QUALITY_TABLE_SCHEMA in generated


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
    # widefeed1(+26)/vectorize1(+27)/tap1(+28)/fxtradesfeed1(+29)/posbook1(+30)/
    # markout1(+31)
    assert core.next_free_port_offset(fake_paths) == core.MARKOUT_PORT_OFFSET + 1


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
        "tap1",
        "fxtradesfeed1",
        "posbook1",
        "markout1",
    }
    assert by_name["discovery1"]["port"] == "7000"
    assert by_name["fxfeed1"]["port"] == str(7000 + core.FXFEED_PORT_OFFSET)
    assert by_name["quotesfeed1"]["port"] == str(7000 + core.QUOTES_FEED_PORT_OFFSET)
    assert by_name["cross1"]["port"] == str(7000 + core.CROSS_ETL_PORT_OFFSET)
    assert by_name["widefeed1"]["port"] == str(7000 + core.WIDE_BOOK_FEED_PORT_OFFSET)
    assert by_name["vectorize1"]["port"] == str(7000 + core.VECTORIZE_ETL_PORT_OFFSET)
    assert by_name["tap1"]["port"] == str(7000 + core.TAP_PORT_OFFSET)
    assert by_name["tap1"]["startwithall"] == "0"
    assert by_name["fxtradesfeed1"]["port"] == str(7000 + core.FX_TRADES_FEED_PORT_OFFSET)
    assert by_name["posbook1"]["port"] == str(7000 + core.POSBOOK_PORT_OFFSET)
    assert by_name["markout1"]["port"] == str(7000 + core.MARKOUT_PORT_OFFSET)


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
        "tap1",
        "fxtradesfeed1",
        "posbook1",
        "markout1",
    }


def test_resolve_procnames_specific_splits_on_space(fake_paths: core.TorqDemoPaths):
    assert core.resolve_procnames(fake_paths, "stp1 fxfeed1") == ["stp1", "fxfeed1"]


def test_resolve_procnames_refuses_a_name_no_process_has(fake_paths: core.TorqDemoPaths):
    """H-03: an unknown name used to be returned as given.

    This test previously asserted exactly that, using `"stp1 rdb1"` - and
    `rdb1` is not a process in this fixture. So the test passed a name
    nothing had and got it straight back, which is the defect rather than the
    contract: `logs posbook1 typo1` returned one process's log as though one
    had been asked for, and a reader diagnosing a quiet process saw an empty
    section and concluded it was idle.
    """
    with pytest.raises(core.TorqDemoError) as excinfo:
        core.resolve_procnames(fake_paths, "stp1 rdb1")
    message = str(excinfo.value)
    assert "rdb1" in message, "the refusal must name the offending process"
    assert "stp1" not in message.split(" - ")[0], "only the unknown name is the problem"


def test_resolve_procnames_still_allows_a_process_with_no_log_file(
    fake_paths: core.TorqDemoPaths,
):
    """The distinction that makes H-03 fixable rather than a trade-off.

    A name absent from process.csv is a typo. A name present in process.csv
    with no log file yet is legitimate - a process that has never started has
    no log - and must still resolve, with the skipping left to `_log_files`
    downstream. Conflating the two is what the old docstring did by calling
    both "just skipped".
    """
    assert core.resolve_procnames(fake_paths, "discovery1") == ["discovery1"]


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


def test_get_recent_logs_returns_sorted_field_dicts(fake_paths: core.TorqDemoPaths):
    # the data source torq_demo_mcp.py's torq_demo_logs tool returns
    # directly - print_recent_logs just formats/prints this same data.
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|INF|x|second\n"
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|first\n"
    )

    records = core.get_recent_logs(fake_paths, "discovery1")

    assert [r["message"] for r in records] == ["first", "second"]


def test_get_recent_logs_filters_by_min_level(fake_paths: core.TorqDemoPaths):
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|quiet info\n"
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|ERR|x|loud error\n"
    )

    records = core.get_recent_logs(fake_paths, "discovery1", min_level="ERROR")

    assert [r["message"] for r in records] == ["loud error"]


def test_get_recent_logs_raises_when_no_log_files(fake_paths: core.TorqDemoPaths):
    with pytest.raises(core.TorqDemoError):
        core.get_recent_logs(fake_paths, "discovery1")


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


# --- the PIPELINES registry ------------------------------------------------
#
# Ports, process.csv rows and database.q definitions are all derived from one
# Pipeline() entry each, so these tests guard the derivation rather than the
# nine literal dicts they replaced.


def test_pipeline_offsets_are_stable():
    """Offsets are allocated from PIPELINE_BLOCK_START in list order, so
    reordering PIPELINES would renumber ports and move a running demo's
    processes. Pin every one: an accidental reorder fails here instead of
    silently breaking someone's running stack.
    """
    assert core.PIPELINE_OFFSETS == {
        "fxfeed1": 19,
        "quotesfeed1": 24,
        "cross1": 25,
        "widefeed1": 26,
        "vectorize1": 27,
        "tap1": 28,
        "fxtradesfeed1": 29,
        "posbook1": 30,
        "markout1": 31,
    }


def test_pipeline_offsets_are_unique():
    offsets = list(core.PIPELINE_OFFSETS.values())
    assert len(offsets) == len(set(offsets))


def test_feed_and_etl_kinds_derive_proctype_and_credentials():
    """`kind` drives the two fields that always move together: a feed only
    publishes and needs no credentials, an ETL subscribes and so needs
    .servers.startup[]'s access-listed handle to stp1.
    """
    for pipeline in core.PIPELINES:
        if pipeline.kind == "feed":
            assert pipeline.proctype == "feed"
            assert pipeline.access_list == ""
        else:
            assert pipeline.kind == "etl"
            assert pipeline.proctype == "metrics"
            assert pipeline.access_list.endswith("accesslist.txt")


def test_qpipe_library_loads_before_the_pipeline_that_needs_it():
    """scripts/torq_pipeline.q must come FIRST in the load column: the
    pipeline script calls .qpipe.load_uqf[] at top level, and TorQ's
    .proc.reloadf each loads -load's files in the order given.
    """
    markout = core.PIPELINE_BY_NAME["markout1"]
    assert markout.uses_qpipe
    loaded = markout.load_column().split()
    assert loaded[0].endswith(core.PIPELINE_LIB_SCRIPT)
    assert loaded[1].endswith("torq_markout_etl.q")


def test_pipelines_not_using_qpipe_load_only_their_own_script():
    for pipeline in core.PIPELINES:
        if not pipeline.uses_qpipe:
            assert pipeline.load_column() == f"${{UQFSCRIPTS}}/{pipeline.script}"
            assert core.PIPELINE_LIB_SCRIPT not in pipeline.load_column()


def test_every_pipeline_script_exists_on_disk():
    """Catches a typo in a Pipeline(script=...) at test time rather than as a
    process that silently fails to start.
    """
    scripts_dir = core.default_paths().scripts_dir
    for pipeline in core.PIPELINES:
        assert (scripts_dir / pipeline.script).is_file(), pipeline.script
        if pipeline.uses_qpipe:
            assert (scripts_dir / core.PIPELINE_LIB_SCRIPT).is_file()


def test_table_and_schema_are_declared_together():
    """A pipeline that names a published table must carry that table's
    definition, and vice versa - otherwise it publishes into a table the
    tickerplant has no schema for.
    """
    for pipeline in core.PIPELINES:
        assert (pipeline.table is None) == (pipeline.schema is None), pipeline.procname
        if pipeline.table:
            # Restated rather than inferred from the assert above: the
            # equivalence there means a table implies a schema, but nothing
            # in the type says so, and a reader (or a checker) should not
            # have to derive it two lines later.
            assert pipeline.schema is not None, pipeline.procname
            assert pipeline.schema.startswith(f"{pipeline.table}:(["), pipeline.procname


def test_pipeline_rows_are_appended_to_the_base_rows(fake_paths: core.TorqDemoPaths):
    rows = {r["procname"]: r for r in core._base_process_rows(fake_paths)}
    for pipeline in core.PIPELINES:
        row = rows[pipeline.procname]
        assert row["port"] == f"{{KDBBASEPORT}}+{core.PIPELINE_OFFSETS[pipeline.procname]}"
        assert row["proctype"] == pipeline.proctype
        assert row["U"] == pipeline.access_list
        assert row["localtime"] == pipeline.localtime
        assert row["startwithall"] == pipeline.startwithall
        assert row["load"] == pipeline.load_column()
        assert row["host"] == "localhost"
        assert row["qcmd"] == "q"


def test_markout_runs_on_utc_and_tap_does_not_autostart():
    """The two rows that deviate from the defaults, kept honest: markout1 is
    the only process comparing .proc.cp[] against tickerplant-stamped data
    timestamps (localtime:1 would skew its cutoff by the local UTC offset),
    and tap1 is a diagnostic subscriber started on demand.
    """
    assert core.PIPELINE_BY_NAME["markout1"].localtime == "0"
    assert all(p.localtime == "1" for p in core.PIPELINES if p.procname != "markout1")
    assert core.PIPELINE_BY_NAME["tap1"].startwithall == "0"
    assert all(p.startwithall == "1" for p in core.PIPELINES if p.procname != "tap1")


def test_generated_schema_covers_every_published_table(fake_paths: core.TorqDemoPaths):
    generated = core._generated_schema_content(fake_paths)
    for pipeline in core.PIPELINES:
        if pipeline.schema:
            assert pipeline.schema in generated, pipeline.procname
    # the crypto tables are written by the external cryptorust recorders, not
    # by any pipeline, so they are listed explicitly and must survive too
    assert core.CRYPTO_BOOK_TABLE_SCHEMA in generated
    assert core.CRYPTO_SIM_FILLS_TABLE_SCHEMA in generated
    assert core.CRYPTO_TRADES_TABLE_SCHEMA in generated


def test_declared_dataflow_edges_match_the_q_scripts():
    """Every pipeline's `subscribes`/`publishes` declaration agrees with the
    `.sub.subscribe` / `.qpipe.subscribe_etl` / `.u.upd` calls in its own
    script.

    `verify_pipeline_edges` existed and passed - when someone ran it by hand.
    Nothing exercised it in the suite, so a declaration could drift from the
    script it describes and the diagrams derived from it would go stale with
    no signal. That is the same dormant-guard shape as `.qcov.require_schema`
    before it was wired into a worker's init: a check that cannot fire
    protects nothing, and its existence reads as protection to anyone
    auditing the code.
    """
    problems = core.verify_pipeline_edges(core.default_paths().scripts_dir)
    assert not problems, "\n".join(problems)


def test_the_edge_verifier_detects_a_drifted_declaration(tmp_path):
    """A verifier nobody has seen fail might match nothing.

    Write a script whose subscription disagrees with what the registry
    declares for it, point the verifier at that directory, and require that
    the mismatch is reported by pipeline name. Only the pipelines with a
    static, non-.qpipe subscription can be checked this way, so this picks
    the first such one rather than hard-coding a name that a later registry
    edit would silently invalidate.
    """
    target = next(
        p for p in core.PIPELINES if p.subscribes and not p.subscribes_dynamic and not p.uses_qpipe
    )
    real = core.default_paths().scripts_dir
    for p in core.PIPELINES:
        (tmp_path / p.script).write_text((real / p.script).read_text())
    (tmp_path / core.PIPELINE_LIB_SCRIPT).write_text((real / core.PIPELINE_LIB_SCRIPT).read_text())
    # Flip the subscription to a table the registry does not declare - at the
    # actual `.sub.subscribe[` call, not the first bare backtick-name in the
    # file. The first draft replaced the first occurrence anywhere, which
    # landed in a comment, left the real call intact, and the "negative"
    # test passed the unmodified script as if drift had been detected.
    original = (tmp_path / target.script).read_text()
    call = f".sub.subscribe[`{target.subscribes[0]}"
    assert call in original, f"expected {call!r} in {target.script}"
    drifted = original.replace(call, ".sub.subscribe[`not_a_declared_table", 1)
    (tmp_path / target.script).write_text(drifted)

    problems = core.verify_pipeline_edges(tmp_path)
    assert any(target.procname in problem for problem in problems), problems


def test_pipeline_procnames_are_unique():
    """H-09: a procname identifies a process, so two entries cannot share one.

    Nothing enforced this. `PIPELINE_BY_NAME` and `PIPELINE_OFFSETS` are both
    dict comprehensions over `PIPELINES`, so a repeated name does not raise -
    it drops one pipeline from the registry and hands the survivor the
    other's port offset. `add_extra_process` already refuses a duplicate that
    an operator adds at runtime, which made the unguarded literal the wrong
    way round: the trusted source of truth was the one with no check.
    """
    names = [pipeline.procname for pipeline in core.PIPELINES]
    assert len(names) == len(set(names)), f"duplicate procname in PIPELINES: {names}"


def test_the_edge_verifier_detects_a_duplicate_procname(monkeypatch, tmp_path):
    """And the guard is seen to fire, not merely to exist.

    Duplicating the first pipeline is enough: the verifier should name the
    procname and both offending positions, because "a duplicate exists"
    without saying where leaves the reader diffing a nine-entry literal.
    """
    duplicated = (*core.PIPELINES, core.PIPELINES[0])
    monkeypatch.setattr(pipelines, "PIPELINES", duplicated)

    real = core.default_paths().scripts_dir
    for pipeline in duplicated:
        (tmp_path / pipeline.script).write_text((real / pipeline.script).read_text())
    (tmp_path / core.PIPELINE_LIB_SCRIPT).write_text((real / core.PIPELINE_LIB_SCRIPT).read_text())

    problems = pipelines.verify_pipeline_edges(tmp_path)
    duplicate_reports = [p for p in problems if "declared twice" in p]
    assert duplicate_reports, problems
    assert core.PIPELINES[0].procname in duplicate_reports[0]


def test_the_three_process_csv_layers_compose_in_a_stated_order(fake_paths: core.TorqDemoPaths):
    """H-01: what is the precedence between the vendored `process.csv`,
    `extra_processes.csv` and `process_overrides.csv`?

    Answered by the code, asserted here so it stays answered. The order is:

        vendored process.csv  ->  PIPELINES  ->  extra_processes.csv  (appended)
        then process_overrides.csv applied LAST, per procname, field by field

    So an override wins over every other source, and the vendored file is
    never edited. The failure mode of an unstated precedence is "works on my
    machine"; this test sets the same field in two layers and checks which
    one wins, which is the only way an order is observable.
    """
    offset = core.next_free_port_offset(fake_paths)
    core.add_extra_process(
        fake_paths,
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{offset}",
            "proctype": "feed",
            "procname": "layered1",
            "U": "",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/layered1.q",
            "startwithall": "1",
            "extras": "from-extra",
            "qcmd": "q",
        },
    )
    # extra_processes.csv supplied extras="from-extra"; an override says otherwise
    core.set_process_config(fake_paths, "layered1", "extras", "from-override")

    row = core.get_process_config(fake_paths, "layered1", base_port=7000)
    assert row["extras"] == "from-override", (
        "process_overrides.csv must outrank extra_processes.csv for the same field"
    )

    # ...and an override on a VENDORED process outranks the vendored file too,
    # without the vendored file being touched.
    core.set_process_config(fake_paths, "stp1", "extras", "vendored-overridden")
    assert core.get_process_config(fake_paths, "stp1", base_port=7000)["extras"] == (
        "vendored-overridden"
    )
    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "vendored-overridden" not in vendored
