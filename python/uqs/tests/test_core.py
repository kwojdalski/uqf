import csv
import io
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs.external import crypto
from uqs.external.crypto import CRYPTORUST_ROOT_ENV
from uqs.model import dependencies, pipeline_edges, plant_schema, schemas
from uqs.model.pipeline import (
    FROM_DECLARATION,
    PIPELINE_LIB_SCRIPT,
    STREAM_RUNNER_SCRIPT,
    PipelineKind,
)
from uqs.model.pipelines import PIPELINE_OFFSETS, PROCESS_CSV_FIELDS
from uqs.model.registry import PIPELINES
from uqs.paths import UqsError, UqsPaths, check_data_dir_was_migrated
from uqs.stack import listing, runtime
from uqs.stack import logs as stack_logs
from uqs.stack import procs as stack_procs
from uqs.stack.procs import VENDORED_STARTWITHALL_OVERLAY

#: Every pipeline by procname, for the tests that look one up.
BY_NAME = {p.procname: p for p in PIPELINES}


@pytest.fixture
def fake_paths(tmp_path: Path) -> UqsPaths:
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

    return UqsPaths(
        repo_root=tmp_path,
        torqhome=torqhome,
        torqapphome=torqapphome,
        torqdata=tmp_path / "output" / "uqs",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "python" / "uqs",
    )


#: The vendored rows the fake_paths fixture writes. Every other process is a
#: declared pipeline, so the full set is these plus PIPELINES - derived, so an
#: appended pipeline is not an edit to three hand-kept sets below.
_FIXTURE_VENDORED = {"discovery1", "stp1"}


def test_bootstrap_appends_fxfeed1_without_touching_vendored_csv(fake_paths: UqsPaths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _tool: "/usr/bin/true")

    env = runtime.bootstrap(fake_paths, base_port=7000)

    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "fxfeed1" not in vendored

    generated = fake_paths.generated_procs.read_text()
    assert "discovery1" in generated
    assert f"localhost,{{KDBBASEPORT}}+{PIPELINE_OFFSETS['fxfeed1']},feed,fxfeed1" in generated

    assert env["KDBBASEPORT"] == "7000"
    assert env["TORQPROCESSES"] == str(fake_paths.generated_procs)
    assert env["SETENV"] == str(fake_paths.generated_setenv)
    assert fake_paths.generated_setenv.is_file()
    assert (fake_paths.torqdata / "hdb").is_dir()
    assert (fake_paths.torqdata / "logs").is_dir()


def test_bootstrap_is_idempotent(fake_paths: UqsPaths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _tool: "/usr/bin/true")

    runtime.bootstrap(fake_paths, base_port=7000)
    runtime.bootstrap(fake_paths, base_port=7000)  # must not raise (e.g. copytree onto itself)

    generated = fake_paths.generated_procs.read_text()
    assert generated.count("fxfeed1") == 1


def test_clean_removes_generated_data_dir(fake_paths: UqsPaths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _tool: "/usr/bin/true")

    runtime.bootstrap(fake_paths, base_port=7000)
    assert fake_paths.torqdata.exists()

    stack_paths.clean(fake_paths)
    assert not fake_paths.torqdata.exists()


def _tree(root: Path) -> None:
    """A data directory with the shapes `clean --match` has to tell apart."""
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "tplogs").mkdir(parents=True, exist_ok=True)
    (root / "hdb" / "2026.09.01").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "out_rdb1.log").write_bytes(b"x" * 300)
    (root / "logs" / "out_stp1.log").write_bytes(b"x" * 500)
    (root / "tplogs" / "tp1.log").write_bytes(b"x" * 200)
    (root / "hdb" / "2026.09.01" / "trade").write_bytes(b"x" * 900)


def test_a_dry_run_removes_nothing(fake_paths: UqsPaths):
    """The whole point of the flag: it reports and does not act."""
    _tree(fake_paths.torqdata)
    before = sorted(p.name for p in fake_paths.torqdata.rglob("*"))

    targets = stack_paths.clean(fake_paths, dry_run=True)

    assert targets, "a dry run still reports what it would remove"
    assert sorted(p.name for p in fake_paths.torqdata.rglob("*")) == before


def test_a_dry_run_reports_what_the_real_run_removes(fake_paths: UqsPaths):
    """Listing and removal come from one walk, so they cannot disagree."""
    _tree(fake_paths.torqdata)
    planned = [entry for entry, _size in stack_paths.clean(fake_paths, r"^logs", dry_run=True)]
    removed = [entry for entry, _size in stack_paths.clean(fake_paths, r"^logs")]
    assert planned == removed


def test_a_matching_directory_goes_whole(fake_paths: UqsPaths):
    """`^logs$` means the logs, not a list of the files under them."""
    _tree(fake_paths.torqdata)
    targets = stack_paths.clean(fake_paths, r"^logs$")
    assert [e.name for e, _s in targets] == ["logs"]
    assert not (fake_paths.torqdata / "logs").exists()
    assert (fake_paths.torqdata / "tplogs" / "tp1.log").exists(), "only logs was asked for"


def test_a_non_matching_directory_is_descended(fake_paths: UqsPaths):
    """`out_rdb1` finds the file although its parent does not match."""
    _tree(fake_paths.torqdata)
    targets = stack_paths.clean(fake_paths, "out_rdb1")
    assert [e.name for e, _s in targets] == ["out_rdb1.log"]
    assert (fake_paths.torqdata / "logs" / "out_stp1.log").exists()
    assert (fake_paths.torqdata / "logs").exists(), "the directory itself was not asked for"


def test_sizes_are_the_bytes_under_each_entry(fake_paths: UqsPaths):
    _tree(fake_paths.torqdata)
    sizes = dict(
        (entry.name, size) for entry, size in stack_paths.clean_targets(fake_paths, r"^logs$")
    )
    assert sizes == {"logs": 800}, "300 + 500, the two files under logs/"


def test_a_match_selecting_nothing_removes_nothing(fake_paths: UqsPaths):
    _tree(fake_paths.torqdata)
    assert stack_paths.clean(fake_paths, "no_such_thing") == []
    assert (fake_paths.torqdata / "logs" / "out_rdb1.log").exists()


def test_an_invalid_regex_is_refused_before_anything_is_removed(fake_paths: UqsPaths):
    """A bad pattern must not fall back to removing everything."""
    _tree(fake_paths.torqdata)
    with pytest.raises(UqsError, match="not a valid regular expression"):
        stack_paths.clean(fake_paths, "[unclosed")
    assert (fake_paths.torqdata / "logs" / "out_rdb1.log").exists()


def test_no_match_still_removes_the_whole_directory(fake_paths: UqsPaths):
    """Unchanged behaviour: `uqs clean` with no flags is the old wipe."""
    _tree(fake_paths.torqdata)
    stack_paths.clean(fake_paths)
    assert not fake_paths.torqdata.exists()


def test_get_process_config_returns_vendored_row(fake_paths: UqsPaths):
    row = stack_procs.get_process_config(fake_paths, "discovery1", resolve=False)
    assert row["proctype"] == "discovery"
    assert row["port"] == "{KDBBASEPORT}"


def test_get_process_config_unknown_process_raises(fake_paths: UqsPaths):
    with pytest.raises(UqsError):
        stack_procs.get_process_config(fake_paths, "nope1")


def test_get_process_config_resolves_brace_arith_placeholder(fake_paths: UqsPaths):
    row = stack_procs.get_process_config(fake_paths, "fxfeed1", base_port=7000)
    assert row["port"] == str(7000 + PIPELINE_OFFSETS["fxfeed1"])


def test_get_process_config_resolves_dollar_brace_placeholder(fake_paths: UqsPaths):
    row = stack_procs.get_process_config(fake_paths, "discovery1")
    assert row["load"] == str(fake_paths.torqhome / "code" / "processes" / "discovery.q")


def test_get_process_config_resolve_false_leaves_placeholders_literal(
    fake_paths: UqsPaths,
):
    row = stack_procs.get_process_config(fake_paths, "discovery1", resolve=False)
    assert row["load"] == "${KDBCODE}/processes/discovery.q"
    assert row["port"] == "{KDBBASEPORT}"


def test_resolve_process_config_leaves_unknown_var_literal():
    row = {"port": "{NOT_A_REAL_VAR}", "load": "${ALSO_NOT_REAL}/x.q"}
    resolved = stack_procs.resolve_process_config(row, {"KDBBASEPORT": "6010"})
    assert resolved["port"] == "{NOT_A_REAL_VAR}"
    assert resolved["load"] == "${ALSO_NOT_REAL}/x.q"


def test_set_process_config_unknown_field_raises(fake_paths: UqsPaths):
    with pytest.raises(UqsError):
        stack_procs.set_process_config(fake_paths, "discovery1", "not_a_field", "x")


def test_set_process_config_persists_and_is_read_back(fake_paths: UqsPaths):
    stack_procs.set_process_config(fake_paths, "discovery1", "port", "9999")

    assert fake_paths.overrides_path.is_file()
    row = stack_procs.get_process_config(fake_paths, "discovery1")
    assert row["port"] == "9999"
    # vendored file itself is never touched
    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "9999" not in vendored


def test_set_process_config_survives_bootstrap_and_flows_into_generated_csv(
    fake_paths: UqsPaths, monkeypatch
):
    import csv

    monkeypatch.setattr(shutil, "which", lambda _tool: "/usr/bin/true")

    base_rows = {r["procname"]: r for r in stack_procs._base_process_rows(fake_paths)}
    assert base_rows["fxfeed1"]["startwithall"] == "1"  # unaffected by the override below

    stack_procs.set_process_config(fake_paths, "fxfeed1", "startwithall", "0")
    runtime.bootstrap(fake_paths, base_port=7000)

    with fake_paths.generated_procs.open(newline="") as f:
        generated_rows = {r["procname"]: r for r in csv.DictReader(f)}
    assert generated_rows["fxfeed1"]["startwithall"] == "0"


def test_bootstrap_generates_schema_with_quotes_table(fake_paths: UqsPaths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _tool: "/usr/bin/true")

    runtime.bootstrap(fake_paths, base_port=7000)

    vendored = (fake_paths.torqapphome / "database.q").read_text()
    assert "quotes:" not in vendored  # vendored file itself is never touched

    generated = fake_paths.generated_schema.read_text()
    assert "quote:" in generated  # vendored table still present
    assert schemas.definition("quotes") in generated
    assert schemas.definition("trades") in generated
    assert schemas.definition("position") in generated
    assert schemas.definition("execution_quality") in generated


def test_bootstrap_repoints_stp1_schemafile_at_generated_copy(fake_paths: UqsPaths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _tool: "/usr/bin/true")

    runtime.bootstrap(fake_paths, base_port=7000)

    generated_procs = fake_paths.generated_procs.read_text()
    assert "${TORQDATA}/database.q" in generated_procs
    assert "${TORQAPPHOME}/database.q" not in generated_procs


def test_list_items_unknown_kind_raises(fake_paths: UqsPaths):
    with pytest.raises(UqsError):
        listing.list_items(fake_paths, "not_a_kind")


def test_list_processes_includes_vendored_and_fxfeed1_resolved(fake_paths: UqsPaths):
    items = listing.list_items(fake_paths, "processes", base_port=7000)
    by_name = {item["procname"]: item for item in items}
    assert set(by_name) == _FIXTURE_VENDORED | {p.procname for p in PIPELINES}
    assert by_name["discovery1"]["port"] == "7000"
    assert by_name["fxfeed1"]["port"] == str(7000 + PIPELINE_OFFSETS["fxfeed1"])
    assert by_name["quotesfeed1"]["port"] == str(7000 + PIPELINE_OFFSETS["quotesfeed1"])
    assert by_name["cross1"]["port"] == str(7000 + PIPELINE_OFFSETS["cross1"])
    assert by_name["widefeed1"]["port"] == str(7000 + PIPELINE_OFFSETS["widefeed1"])
    assert by_name["vectorize1"]["port"] == str(7000 + PIPELINE_OFFSETS["vectorize1"])
    assert by_name["tap1"]["port"] == str(7000 + PIPELINE_OFFSETS["tap1"])
    assert by_name["tap1"]["startwithall"] == "0"
    assert by_name["fxtradesfeed1"]["port"] == str(7000 + PIPELINE_OFFSETS["fxtradesfeed1"])
    assert by_name["posbook1"]["port"] == str(7000 + PIPELINE_OFFSETS["posbook1"])
    assert by_name["markout1"]["port"] == str(7000 + PIPELINE_OFFSETS["markout1"])


def test_list_processes_shows_what_each_process_reads_and_writes(fake_paths: UqsPaths):
    by_name = {i["procname"]: i for i in listing.list_items(fake_paths, "processes")}
    assert by_name["fxpositions1"]["inputs"] == "orders"
    assert set(by_name["fxpositions1"]["outputs"].split(", ")) == {"fx_position", "fx_limit_breach"}
    assert by_name["fxfeed1"]["inputs"] == "", "a feed reads nothing"
    assert by_name["discovery1"]["outputs"] == "", "a vendored process declares no edges"


def test_list_processes_edges_are_the_ones_summary_draws(fake_paths: UqsPaths):
    """One source for both views, so `list` and `summary` cannot disagree."""
    inputs, outputs = dependencies.inputs_by_process(), dependencies.outputs_by_process()
    for item in listing.list_items(fake_paths, "processes"):
        name = item["procname"]
        assert item["outputs"] == ", ".join(outputs.get(name, ())), name
        if item["inputs"] != listing.RUNTIME_INPUTS:
            assert item["inputs"] == ", ".join(inputs.get(name, ())), name


def test_list_processes_gives_a_bounded_worker_its_dataset_as_output(fake_paths: UqsPaths):
    """A worker writes through its IO manager, so it has no publish edge; the
    dataset it fills is still its output. Without a procname it runs as <name>1."""
    workers = fake_paths.repo_root / "src" / "etl" / "workers"
    workers.mkdir(parents=True)
    (workers / "w.q").write_text(
        "/ .qbw.define[`commented;`source`dataset!(`s;`nope)];\n"
        ".qbw.define[`demo_deals_backfill;`source`dataset`width`procname!(\n"
        "    `demo_deals;`demo_deals;1D;`deals_backfill1)];\n"
        ".qbw.define[`x_backfill;`source`dataset`width!(`x;`x_rows;1D)];\n"
    )
    assert listing._worker_datasets(fake_paths) == {
        "deals_backfill1": "demo_deals",
        "x_backfill1": "x_rows",
    }
    by_name = {i["procname"]: i for i in listing.list_items(fake_paths, "processes")}
    assert by_name["deals_backfill1"]["outputs"] == "demo_deals"


def test_list_processes_says_a_runtime_input_is_chosen_at_start(fake_paths: UqsPaths):
    """tap1 picks its tables with -tables; an empty cell would say it reads nothing."""
    by_name = {i["procname"]: i for i in listing.list_items(fake_paths, "processes")}
    dynamic = [p.procname for p in PIPELINES if p.subscribes_dynamic]
    assert dynamic, "the case this covers must still exist"
    assert all(by_name[name]["inputs"] == listing.RUNTIME_INPUTS for name in dynamic)


def test_list_processes_reflects_overrides(fake_paths: UqsPaths):
    stack_procs.set_process_config(fake_paths, "fxfeed1", "startwithall", "0")
    items = listing.list_items(fake_paths, "processes")
    by_name = {item["procname"]: item for item in items}
    assert by_name["fxfeed1"]["startwithall"] == "0"


def test_list_fields_matches_process_csv_fields(fake_paths: UqsPaths):
    items = listing.list_items(fake_paths, "fields")
    assert [item["field"] for item in items] == list(PROCESS_CSV_FIELDS)


def test_list_overrides_empty_then_populated(fake_paths: UqsPaths):
    assert listing.list_items(fake_paths, "overrides") == []

    stack_procs.set_process_config(fake_paths, "discovery1", "port", "9999")
    items = listing.list_items(fake_paths, "overrides")
    assert items == [{"procname": "discovery1", "field": "port", "value": "9999"}]


def test_parse_log_line_splits_seven_fields():
    line = (
        "2026.08.22D14:21:10.644413000|mac.lan|segmentedtickerplant|stp1|"
        "INF|fileload|loading /some/path with | a pipe in it"
    )
    rec = stack_logs.parse_log_line(line)
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
    assert stack_logs.parse_log_line("some banner line with no pipes") is None
    assert stack_logs.parse_log_line("a|b|c") is None


def test_resolve_procnames_all_returns_every_process(fake_paths: UqsPaths):
    assert set(stack_logs.resolve_procnames(fake_paths, "all")) == _FIXTURE_VENDORED | {
        p.procname for p in PIPELINES
    }


def test_resolve_procnames_specific_splits_on_space(fake_paths: UqsPaths):
    assert stack_logs.resolve_procnames(fake_paths, "stp1 fxfeed1") == ["stp1", "fxfeed1"]


def test_resolve_procnames_refuses_a_name_no_process_has(fake_paths: UqsPaths):
    """An unknown name used to be returned as given.

    This test previously asserted exactly that, using `"stp1 rdb1"` - and
    `rdb1` is not a process in this fixture. So the test passed a name
    nothing had and got it straight back, which is the defect rather than the
    contract: `logs posbook1 typo1` returned one process's log as though one
    had been asked for, and a reader diagnosing a quiet process saw an empty
    section and concluded it was idle.
    """
    with pytest.raises(UqsError) as excinfo:
        stack_logs.resolve_procnames(fake_paths, "stp1 rdb1")
    message = str(excinfo.value)
    assert "rdb1" in message, "the refusal must name the offending process"
    assert "stp1" not in message.split(" - ")[0], "only the unknown name is the problem"


def test_resolve_procnames_still_allows_a_process_with_no_log_file(
    fake_paths: UqsPaths,
):
    """The distinction that makes it fixable rather than a trade-off.

    A name absent from process.csv is a typo. A name present in process.csv
    with no log file yet is legitimate - a process that has never started has
    no log - and must still resolve, with the skipping left to `_log_files`
    downstream. Conflating the two is what the old docstring did by calling
    both "just skipped".
    """
    assert stack_logs.resolve_procnames(fake_paths, "discovery1") == ["discovery1"]


#: TorQ's own `summary` output shape. The load-bearing detail is that a
#: `down` row STOPS after its status field rather than emitting empty pid and
#: port cells, which is why the parser pads instead of requiring an exact
#: width - and why every down process used to show a blank port.
_SUMMARY_STDOUT = """TIME | PROCESS | STATUS | PID | PORT
2026.09.16 | markout1 | up | 4242 | 6081
2026.09.16 | tap1 | down
2026.09.16 | dqc1 | down
"""


def test_summary_fills_the_port_torq_omits_for_a_stopped_process():
    """The whole point: a `down` row has a known port and used to show none.

    "What port will this be on when I start it" is a question you ask about a
    process that is NOT running, so the blank was precisely where the answer
    was wanted.
    """
    rows = listing.summary_rows(_SUMMARY_STDOUT, {"tap1": "6078", "dqc1": "6070"})
    by_name = {r["Process"]: r for r in rows}
    assert by_name["tap1"]["Port"] == "6078"
    assert by_name["dqc1"]["Port"] == "6070"
    assert [r["Port"] for r in rows] == ["6081", "6078", "6070"], "no row is left portless"


def test_heartbeat_absent_is_distinguished_from_heartbeat_silent():
    """The distinction this whole change turns on.

    `None` means monitor1 could not be reached, so NOTHING is known about any
    process. An empty dict means the collector is up and has heard from
    nobody — a fleet-wide outage. Rendering both as a blank column would turn
    a monitoring gap into an all-clear, or an all-clear into a panic.
    """
    absent = listing.summary_rows(_SUMMARY_STDOUT, {}, None)
    silent = listing.summary_rows(_SUMMARY_STDOUT, {}, {})
    assert absent[0]["Heartbeat"] == "not collected"
    assert silent[0]["Heartbeat"] == "-"
    assert absent[0]["Heartbeat"] != silent[0]["Heartbeat"]


def test_heartbeat_state_is_reported_per_process():
    rows = listing.summary_rows(_SUMMARY_STDOUT, {}, {"markout1": "ok", "tap1": "error"})
    by_name = {r["Process"]: r for r in rows}
    assert by_name["markout1"]["Heartbeat"] == "ok"
    assert by_name["tap1"]["Heartbeat"] == "error"
    # dqc1 is in the stdout fixture but not in the heartbeat map: the
    # collector is up and simply has no row for it.
    assert by_name["dqc1"]["Heartbeat"] == "-"


def test_a_process_can_be_up_by_pid_and_failing_by_heartbeat():
    """The case that motivates the column.

    torq.sh reports `up` from a PID lookup, and a hung process still has a
    PID. A row showing `up` beside `error` is exactly what this is for — and
    it must not be collapsed into one verdict.
    """
    rows = listing.summary_rows(_SUMMARY_STDOUT, {}, {"markout1": "error"})
    markout = next(r for r in rows if r["Process"] == "markout1")
    assert markout["Status"] == "up"
    assert markout["Heartbeat"] == "error"


def test_error_wins_over_warning():
    """A process past the error tolerance is also past the warning one, so
    reporting the lesser would understate it."""
    from uqs.stack import listing

    got = listing._heartbeat_by_procname([{"procname": "a", "warning": True, "error": True}])
    assert got == {"a": "error"}


def test_a_heartbeat_row_without_a_procname_is_skipped():
    """A malformed row must not become a process called empty-string."""
    from uqs.stack import listing

    got = listing._heartbeat_by_procname(
        [{"procname": "", "error": True}, {"procname": "b", "error": False}]
    )
    assert got == {"b": "ok"}


def test_summary_marks_a_filled_port_as_configured_not_reported():
    """A filled port is a different claim, and the row has to say which.

    Collapsing "configured to listen here" into "listening here" would mean a
    reader could not tell a running process from a planned one by looking at
    the port - worse than the blank it replaced.
    """
    rows = listing.summary_rows(_SUMMARY_STDOUT, {"tap1": "6078", "dqc1": "6070"})
    by_name = {r["Process"]: r for r in rows}
    assert by_name["markout1"]["PortSource"] == "reported"
    assert by_name["tap1"]["PortSource"] == "configured"


def test_summary_never_overwrites_a_reported_port():
    """If the running stack and process.csv disagree, the running truth wins.

    Someone restarting with a different base port is exactly when a summary
    must not tidy the disagreement away.
    """
    rows = listing.summary_rows(_SUMMARY_STDOUT, {"markout1": "9999", "tap1": "6078"})
    by_name = {r["Process"]: r for r in rows}
    assert by_name["markout1"]["Port"] == "6081"
    assert by_name["markout1"]["PortSource"] == "reported"


def test_summary_leaves_a_port_blank_when_nothing_declares_one():
    """The one case where the answer genuinely is not known.

    A process TorQ reports but process.csv does not contain gets a blank and
    `unknown`, rather than an invented number.
    """
    rows = listing.summary_rows("2026.09.16 | mystery1 | down\n", {"tap1": "6078"})
    assert rows[0]["Port"] == ""
    assert rows[0]["PortSource"] == "unknown"


def test_summary_skips_the_header_and_blank_lines():
    rows = listing.summary_rows(_SUMMARY_STDOUT, {})
    assert [r["Process"] for r in rows] == ["markout1", "tap1", "dqc1"]


def test_every_process_has_a_configured_port(fake_paths: UqsPaths):
    """The fill can only work if every process declares a port.

    process.csv carries `{KDBBASEPORT}+N` and `resolve_process_config`
    evaluates it, so this should hold for every row - and if a future process
    is added without a port, this fails here rather than showing one blank
    cell in a table nobody is diffing.
    """
    ports = listing.configured_ports(fake_paths, base_port=6050)
    names = stack_procs.list_process_names(fake_paths)
    missing = [n for n in names if not ports.get(n)]
    assert not missing, f"no configured port for {missing}"


def test_process_choices_cover_every_process_and_apply_overrides(fake_paths: UqsPaths):
    """The list a picker offers is the list `start all` acts on, overrides
    included: a startwithall set through config-set must be the value
    reported, or the picker's "started by all" hint lies about exactly the
    processes someone deliberately changed."""
    stack_procs.set_process_config(fake_paths, "discovery1", "startwithall", "0")
    choices = {row["procname"]: row for row in stack_procs.list_process_choices(fake_paths)}
    assert set(choices) == set(stack_procs.list_process_names(fake_paths))
    assert choices["discovery1"] == {
        "procname": "discovery1",
        "proctype": "discovery",
        "startwithall": "0",
    }
    assert all(row["startwithall"] in ("0", "1") for row in choices.values())


def _touch_logs(paths: UqsPaths, *names: str) -> Path:
    log_dir = paths.torqdata / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (log_dir / name).touch()
    return log_dir


def test_multitail_opens_one_titled_following_pane_per_log_file(fake_paths: UqsPaths):
    log_dir = _touch_logs(fake_paths, "out_stp1.log", "err_stp1.log")
    argv = stack_logs.multitail_command(fake_paths, "stp1", lines=5)
    assert argv[0] == "multitail"
    assert "-s" not in argv, "multitail refuses -s 1; stacked panes are its default"
    assert argv[1:] == [
        "-n", "5", "-f", "-t", "out_stp1.log", str(log_dir / "out_stp1.log"),
        "-n", "5", "-f", "-t", "err_stp1.log", str(log_dir / "err_stp1.log"),
    ]  # fmt: skip


def test_multitail_stream_and_columns_shape_the_panes(fake_paths: UqsPaths):
    _touch_logs(fake_paths, "out_stp1.log", "err_stp1.log", "err_discovery1.log")
    argv = stack_logs.multitail_command(fake_paths, "stp1 discovery1", stream="err", columns=2)
    assert argv[1:3] == ["-s", "2"]
    titles = [argv[i + 1] for i, a in enumerate(argv) if a == "-t"]
    assert titles == ["err_stp1.log", "err_discovery1.log"], "only err files, in the order asked"


def test_multitail_skips_a_process_with_no_log_yet(fake_paths: UqsPaths):
    """resolve_procnames' rule: never started is not a typo."""
    _touch_logs(fake_paths, "out_stp1.log")
    argv = stack_logs.multitail_command(fake_paths, "stp1 discovery1", stream="out")
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "-t"] == ["out_stp1.log"]


def test_multitail_refuses_an_unknown_process(fake_paths: UqsPaths):
    _touch_logs(fake_paths, "out_stp1.log")
    with pytest.raises(UqsError, match="typo1"):
        stack_logs.multitail_command(fake_paths, "stp1 typo1")


def test_multitail_refuses_when_there_is_nothing_to_show(fake_paths: UqsPaths):
    with pytest.raises(UqsError, match="no both log files found"):
        stack_logs.multitail_command(fake_paths, "stp1")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"stream": "stdout"}, "--stream"), ({"columns": 0}, "--columns"), ({"lines": -1}, "--lines")],
)
def test_multitail_refuses_bad_options(fake_paths: UqsPaths, kwargs, message):
    _touch_logs(fake_paths, "out_stp1.log")
    with pytest.raises(UqsError, match=message):
        stack_logs.multitail_command(fake_paths, "stp1", **kwargs)


def test_run_multitail_without_the_binary_names_the_install_and_the_fallback(monkeypatch):
    monkeypatch.setattr(stack_logs.shutil, "which", lambda _: None)
    with pytest.raises(UqsError) as excinfo:
        stack_logs.run_multitail(["multitail", "-f", "x.log"])
    assert "brew install multitail" in str(excinfo.value)
    assert "uqs logs -f" in str(excinfo.value)


def test_run_multitail_execs_the_binary_with_the_argv(monkeypatch):
    """exec, not a child: multitail needs the terminal to itself."""
    seen = []
    monkeypatch.setattr(stack_logs.shutil, "which", lambda _: "/usr/bin/multitail")
    monkeypatch.setattr(stack_logs.os, "execv", lambda binary, argv: seen.append((binary, argv)))
    stack_logs.run_multitail(["multitail", "-f", "x.log"])
    assert seen == [("/usr/bin/multitail", ["multitail", "-f", "x.log"])]


def test_print_recent_logs_raises_when_no_log_files(fake_paths: UqsPaths):
    with pytest.raises(UqsError):
        stack_logs.print_recent_logs(fake_paths, "discovery1")


def test_print_recent_logs_emits_sorted_by_time(fake_paths: UqsPaths, capsys):
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|INF|x|second\n"
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|first\n"
    )

    stack_logs.print_recent_logs(fake_paths, "discovery1")

    # print_recent_logs reconfigures loguru's sink onto sys.stdout at call
    # time (see _configure_kdb_log_sink), i.e. after capsys has already
    # patched sys.stdout - unlike loguru's own un-configured default sink
    # (bound once, at import time), this one is reliably captured here.
    out = capsys.readouterr().out
    assert out.index("first") < out.index("second")


def test_print_recent_logs_filters_by_min_level(fake_paths: UqsPaths, capsys):
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|quiet info\n"
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|ERR|x|loud error\n"
    )

    stack_logs.print_recent_logs(fake_paths, "discovery1", min_level="ERROR")

    out = capsys.readouterr().out
    assert "loud error" in out
    assert "quiet info" not in out


def test_get_recent_logs_returns_sorted_field_dicts(fake_paths: UqsPaths):
    # the data source uqs_mcp.py's uqs_logs tool returns
    # directly - print_recent_logs just formats/prints this same data.
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|INF|x|second\n"
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|first\n"
    )

    records = stack_logs.get_recent_logs(fake_paths, "discovery1")

    assert [r["message"] for r in records] == ["first", "second"]


def test_get_recent_logs_filters_by_min_level(fake_paths: UqsPaths):
    log_dir = fake_paths.torqdata / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "out_discovery1.log").write_text(
        "2026.08.22D14:21:10.000000000|h|discovery|discovery1|INF|x|quiet info\n"
        "2026.08.22D14:21:11.000000000|h|discovery|discovery1|ERR|x|loud error\n"
    )

    records = stack_logs.get_recent_logs(fake_paths, "discovery1", min_level="ERROR")

    assert [r["message"] for r in records] == ["loud error"]


def test_get_recent_logs_raises_when_no_log_files(fake_paths: UqsPaths):
    with pytest.raises(UqsError):
        stack_logs.get_recent_logs(fake_paths, "discovery1")


def test_list_env_includes_kdbbaseport(fake_paths: UqsPaths):
    items = listing.list_items(fake_paths, "env", base_port=7000)
    by_name = {item["name"]: item["value"] for item in items}
    assert by_name["KDBBASEPORT"] == "7000"
    assert by_name["KDBHDB"] == str(fake_paths.torqdata / "hdb")


def test_cryptorust_root_defaults_to_sibling_dir(fake_paths: UqsPaths, monkeypatch):
    monkeypatch.delenv(CRYPTORUST_ROOT_ENV, raising=False)
    assert crypto.cryptorust_root(fake_paths) == fake_paths.repo_root.parent / "cryptorust"


def test_cryptorust_root_respects_env_override(fake_paths: UqsPaths, monkeypatch, tmp_path):
    override = tmp_path / "elsewhere"
    monkeypatch.setenv(CRYPTORUST_ROOT_ENV, str(override))
    assert crypto.cryptorust_root(fake_paths) == override


def test_crypto_recorder_config_yaml_contains_overrides():
    content = crypto._crypto_recorder_config_yaml(
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
    fake_paths: UqsPaths, monkeypatch, tmp_path
):
    monkeypatch.setenv(CRYPTORUST_ROOT_ENV, str(tmp_path / "not-a-checkout"))
    with pytest.raises(UqsError):
        crypto.start_crypto_recorder(fake_paths)


def test_crypto_recorder_status_when_never_started(fake_paths: UqsPaths):
    status = crypto.crypto_recorder_status(fake_paths)
    assert status["running"] == "False"
    assert status["pid"] == ""


def test_is_crypto_recorder_running_reflects_live_pid(fake_paths: UqsPaths):
    fake_paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    fake_paths.crypto_recorder_pid_path.write_text(str(os.getpid()))
    assert crypto.is_crypto_recorder_running(fake_paths) is True


def test_is_crypto_recorder_running_false_for_dead_pid(fake_paths: UqsPaths):
    fake_paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    # a pid essentially guaranteed not to be a running process
    fake_paths.crypto_recorder_pid_path.write_text("999999")
    assert crypto.is_crypto_recorder_running(fake_paths) is False


def test_stop_crypto_recorder_raises_without_pidfile(fake_paths: UqsPaths):
    with pytest.raises(UqsError):
        crypto.stop_crypto_recorder(fake_paths)


# --- the PIPELINES registry ------------------------------------------------
#
# Ports, process.csv rows and database.q definitions are all derived from one
# Pipeline() entry each, so these tests guard the derivation rather than the
# nine literal dicts they replaced.


def test_pipeline_offsets_are_stable():
    """Offsets come from scripts/processes/process_ports.csv, the generated
    port lock, and a running demo's processes move if one changes. Pin every
    one that exists today: an edited or regenerated lock that moved a port
    fails here instead of silently breaking someone's running stack.

    A pin, not an inventory. A pipeline APPENDED after these moves no pinned
    offset, so it passes without an edit here - it only has to land above
    every pinned one, which is what "appended, never inserted" means.
    """
    pinned = {
        "fxfeed1": 19,
        "quotesfeed1": 24,
        "cross1": 25,
        "widefeed1": 26,
        "vectorize1": 27,
        "tap1": 28,
        "fxtradesfeed1": 29,
        "posbook1": 30,
        "markout1": 31,
        "deals_backfill1": 32,
        "events_backfill1": 33,
        # Appended last so the eleven above keep their ports; a new
        # pipeline inserted mid-list renumbers everything after it.
        "databento1": 34,
        "cryptomock1": 35,
        "executions1": 36,
        "marks1": 37,
        "fxordersfeed1": 38,
        "fxpositions1": 39,
        "databento_backfill1": 40,
        "upstream_backfill1": 41,
        "marketdata1": 42,
        "superbook1": 43,
        "arbitrage1": 44,
        "crossarb1": 45,
    }
    actual = PIPELINE_OFFSETS
    moved = {n: (o, actual.get(n)) for n, o in pinned.items() if actual.get(n) != o}
    assert not moved, f"pinned pipelines moved or vanished (pinned, now): {moved}"
    ceiling = max(pinned.values())
    inserted = {n: o for n, o in actual.items() if n not in pinned and o <= ceiling}
    assert not inserted, f"new pipelines must be appended after offset {ceiling}: {inserted}"


def test_pipeline_offsets_are_unique():
    offsets = list(PIPELINE_OFFSETS.values())
    assert len(offsets) == len(set(offsets))


def test_feed_and_etl_kinds_derive_proctype_and_credentials():
    """`kind` drives the two fields that always move together: a feed only
    publishes and needs no credentials, an ETL subscribes and so needs
    .servers.startup[]'s access-listed handle to stp1.
    """
    for pipeline in PIPELINES:
        if pipeline.kind is PipelineKind.FEED:
            assert pipeline.proctype == "feed"
            assert pipeline.access_list == ""
        elif pipeline.kind is PipelineKind.BACKFILL:
            # Its OWN proctype, because proctype is what discovery indexes by:
            # gethandlebytype on `backfill` must find backfill workers and not
            # the metrics pipelines they share a code path with.
            assert pipeline.proctype == "backfill"
            # It reads from the fleet like an etl, so it carries the same
            # access list - only a pure feed needs none.
            assert pipeline.access_list.endswith("accesslist.txt")
        else:
            # A normalizer is an etl of one shape - it subscribes and
            # republishes - so discovery sees the two alike.
            assert pipeline.kind in (PipelineKind.ETL, PipelineKind.NORMALIZER)
            assert pipeline.proctype == "metrics"
            assert pipeline.access_list.endswith("accesslist.txt")


def test_qpipe_library_loads_before_the_pipeline_that_needs_it():
    """scripts/processes/torq_pipeline.q must come FIRST in the load column: the
    pipeline script calls .qpipe.load_uqf[] at top level, and TorQ's
    .proc.reloadf each loads -load's files in the order given.
    """
    markout = BY_NAME["markout1"]
    assert markout.loads_qpipe
    loaded = markout.load_column().split()
    assert loaded[0].endswith(PIPELINE_LIB_SCRIPT)
    # The streaming jobs all run under one generic runner now; which job a
    # process runs is decided in q, from its own procname.
    assert loaded[1].endswith(STREAM_RUNNER_SCRIPT)


def test_pipelines_not_loading_qpipe_load_only_their_own_script():
    for pipeline in PIPELINES:
        if not pipeline.loads_qpipe:
            assert pipeline.load_column() == f"${{UQFSCRIPTS}}/{pipeline.script}"
            assert PIPELINE_LIB_SCRIPT not in pipeline.load_column()


def test_every_pipeline_script_exists_on_disk():
    """Catches a typo in a Pipeline(script=...) at test time rather than as a
    process that silently fails to start.
    """
    scripts_dir = stack_paths.default_paths().scripts_dir
    for pipeline in PIPELINES:
        assert (scripts_dir / pipeline.script).is_file(), pipeline.script
        if pipeline.loads_qpipe:
            assert (scripts_dir / PIPELINE_LIB_SCRIPT).is_file()


def test_table_and_schema_are_declared_together():
    """A pipeline that names a published table must carry that table's
    definition, and vice versa - otherwise it publishes into a table the
    tickerplant has no schema for.
    """
    for pipeline in PIPELINES:
        assert (pipeline.table is None) == (pipeline.schema is None), pipeline.procname
        if pipeline.table:
            # Restated rather than inferred from the assert above: the
            # equivalence there means a table implies a schema, but nothing
            # in the type says so, and a reader (or a checker) should not
            # have to derive it two lines later.
            assert pipeline.schema is not None, pipeline.procname
            assert pipeline.schema.startswith(f"{pipeline.table}:(["), pipeline.procname


def test_pipeline_rows_are_appended_to_the_base_rows(fake_paths: UqsPaths):
    rows = {r["procname"]: r for r in stack_procs._base_process_rows(fake_paths)}
    for pipeline in PIPELINES:
        row = rows[pipeline.procname]
        assert row["port"] == f"{{KDBBASEPORT}}+{PIPELINE_OFFSETS[pipeline.procname]}"
        assert row["proctype"] == pipeline.proctype
        assert row["U"] == pipeline.access_list
        assert row["localtime"] == pipeline.localtime
        assert row["startwithall"] == pipeline.startwithall
        assert row["load"] == pipeline.load_column()
        assert row["host"] == "localhost"
        assert row["qcmd"] == "q"


def test_no_pipeline_overrides_the_process_clock():
    """Every process reads the same clock, because the CODE names its own.

    This used to assert the opposite: five processes carried `localtime="0"`
    so that `.proc.cp[]` returned UTC, because `.u.upd` stamps data in UTC
    and comparing a local-time clock against it skews every comparison by the
    machine's offset. It was found live - markout scoring trades an hour
    before their horizon had elapsed on a UTC+1 machine.

    Starting one process on a different clock fixed that arithmetic and left
    a worse problem. The override spread by association rather than by need:
    of the five that carried it, `superbook` and `cross_arbitrage` read `.z.p`
    directly and never needed it, `marketdata` and `arbitrage` read no clock
    at all, and `cross1` - which compares `.proc.cp[]` against UTC data
    exactly as markout did - never got it and carried the bug.

    So the clock is named in the code now (`now:{[] .z.p}`), the override is
    gone, and the fleet reads one clock. A new `localtime="0"` means someone
    is fixing a timestamp bug in process configuration again, which is the
    thing that produced a permanent false `error` in monitor1's heartbeat
    table: markout1 stamped its heartbeat in UTC while the monitor compared
    against local time.
    """
    assert all(p.localtime == "1" for p in PIPELINES), [
        p.procname for p in PIPELINES if p.localtime != "1"
    ]


def test_tap_and_the_on_demand_chain_do_not_autostart():
    """The rows that deviate from the startwithall default, kept honest."""
    # Every backfill is a bounded job triggered with a range, so it never
    # belongs in `uqs start` - held as a RULE over the kind, so a new
    # worker is covered the moment it is declared.
    backfills = [p for p in PIPELINES if p.kind is PipelineKind.BACKFILL]
    assert backfills, "no backfill pipelines found - the rule below would hold vacuously"
    assert all(p.startwithall == "0" for p in backfills), [
        p.procname for p in backfills if p.startwithall != "0"
    ]
    # tap1 is a diagnostic subscriber, cryptomock1 a mock. cross1, widefeed1,
    # vectorize1, databento1 and the direct-arbitrage chain are on demand for
    # a different reason: the licence allows a q process sixteen inbound
    # connections and the default start would want more (#285). Each is a
    # leaf or a closed pair, so shedding it strands nothing - which is held
    # by test_every_on_demand_plant_client_still_has_a_producer_to_start_with.
    on_demand = {
        "tap1",
        "cryptomock1",
        "cross1",
        "widefeed1",
        "vectorize1",
        "databento1",
        "marketdata1",
        "superbook1",
        "arbitrage1",
        "crossarb1",
    }
    # And the ones `uqs start` does bring up. Pinned by name rather than
    # as "everything else", because a scaffolded job is written with
    # startwithall="0" - joining the default start is a connection-budget
    # decision someone makes on purpose, and this is where it gets recorded.
    always_on = {
        "fxfeed1",
        "quotesfeed1",
        "fxtradesfeed1",
        "posbook1",
        "markout1",
        "executions1",
        "marks1",
        "fxordersfeed1",
        "fxpositions1",
    }
    assert all(BY_NAME[n].startwithall == "0" for n in on_demand)
    assert all(BY_NAME[n].startwithall == "1" for n in always_on)
    unpinned = {
        p.procname for p in PIPELINES if p.startwithall == "1" and p.procname not in always_on
    }
    assert not unpinned, f"these start with the stack but are not in always_on: {unpinned}"


def test_generated_schema_covers_every_published_table(fake_paths: UqsPaths):
    """Both directions, because for a long time this ran only one.

    The body used to be `if pipeline.schema: assert pipeline.schema in
    generated` - every declared schema reaches the generated file. That is
    not what the name says, and the gap was not hypothetical: fxpositions1
    declares `fx_position` and `fx_limit_breach`, carries no `schema`, and
    so contributed nothing to check. Both tables were defined in
    uqs_tables.q all along, `database.q` never mentioned them, and the
    service published a correct book onto tables the plant had never heard
    of - silently, every five seconds, for as long as it had been running
    (#287).

    So the direction that matters is the other one: every table a pipeline
    sends rows to has a definition the tickerplant will load.
    """
    undefined = plant_schema.undefined_published_tables(fake_paths)
    assert not undefined, (
        "published with no definition in the generated database.q, so stp1 "
        f"would not know the table and the rows would go nowhere: {undefined}"
    )

    generated = plant_schema._generated_schema_content(fake_paths)
    # and the original direction, so a definition cannot quietly stop being
    # emitted either
    for pipeline in PIPELINES:
        if pipeline.schema:
            assert pipeline.schema in generated, pipeline.procname
    # and every table uqs_tables.q defines, including the ones only an
    # outside producer writes (crypto_sim_fills from cryptorust's recorder,
    # databento_mbp10 from the live feed handler) - no list names them
    for definition in schemas._definitions().values():
        assert definition in generated, definition.split(":")[0]


def test_declared_dataflow_edges_match_the_q_scripts():
    """Every pipeline's `subscribes`/`publishes` declaration agrees with the
    `.sub.subscribe` / `.qpipe.subscribe_etl` / `.u.upd` calls in its own
    script.

    `verify_pipeline_edges` existed and passed - when someone ran it by hand.
    Nothing exercised it in the suite, so a declaration could drift from the
    script it describes and the diagrams derived from it would go stale with
    no signal. That is the same dormant-guard shape as `.qmatz.require_schema`
    before it was wired into a worker's init: a check that cannot fire
    protects nothing, and its existence reads as protection to anyone
    auditing the code.
    """
    problems = pipeline_edges.verify_pipeline_edges(
        stack_paths.default_paths().scripts_dir, PIPELINES
    )
    assert not problems, "\n".join(problems)


def test_the_edge_verifier_detects_a_drifted_declaration(tmp_path):
    """A verifier nobody has seen fail might match nothing.

    Make a copy of the tree in which one pipeline subscribes to a table the
    registry does not declare for it, point the verifier at that copy, and
    require the mismatch to be reported by pipeline name.

    Where the drift is INTRODUCED depends on the pipeline: a streaming job
    declares its edges in its own file under src/etl/streaming/, because the
    process script it runs under is generic and names no table at all.
    Anything else spells the subscribe out in its own script. Both paths are
    exercised here rather than assumed, so a pipeline moving from one to the
    other fails this test rather than quietly skipping the check.
    """
    real = stack_paths.default_paths().scripts_dir
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    def _copy(name: str) -> None:
        # A script name carries its subdirectory since #241 foldered
        # scripts/, and that subdirectory is part of what lands in
        # process.csv - so the fake tree has to have it too.
        dest = scripts / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((real / name).read_text())

    for pipeline in PIPELINES:
        _copy(pipeline.script)
    _copy(PIPELINE_LIB_SCRIPT)
    real_jobs = real.parent / "src" / "etl" / "streaming"
    jobs = tmp_path / "src" / "etl" / "streaming"
    jobs.mkdir(parents=True)
    for path in real_jobs.glob("*.q"):
        (jobs / path.name).write_text(path.read_text())

    # A pipeline that DEFERS its edges cannot drift from them - there is one
    # declaration, not two - so the verifier deliberately skips it and there
    # is nothing here to detect. What this test still has a job to check is
    # the other case: an entry that spells its edges out. Since every
    # streaming entry now defers, one is copied and given the edges back, so
    # the drift path stays exercised rather than quietly covering nothing.
    deferred = next(
        p
        for p in PIPELINES
        # ...and SUBSCRIBES: feeds defer too now that every entry is read
        # from q, and a feed has no subscription to drift.
        if p.subscribes is FROM_DECLARATION
        and p.script == STREAM_RUNNER_SCRIPT
        and p.subscribed_tables
    )
    target = replace(deferred, subscribes=deferred.subscribed_tables)
    first = target.subscribed_tables[0]

    if target.script == STREAM_RUNNER_SCRIPT:
        # The job file that claims this process - found the same way the
        # runner finds it, by procname, rather than by guessing the filename.
        job_file = next(
            path for path in jobs.glob("*.q") if f"`{target.procname};" in path.read_text()
        )
        original = job_file.read_text()
        # Inside the register call, not the first backtick-name in the file:
        # a job's own schemas and comments mention its tables by name long
        # before it declares them, and drifting one of those would leave the
        # declaration intact and this "negative" test passing over an
        # unmodified file.
        head, _, tail = original.partition(".qstream.register[")
        assert tail, f"no .qstream.register call in {job_file.name}"
        drifted_tail = tail.replace(f"`{first}", "`not_a_declared_table", 1)
        assert drifted_tail != tail
        job_file.write_text(head + ".qstream.register[" + drifted_tail)
    else:
        original = (scripts / target.script).read_text()
        for prefix in (
            ".sub.subscribe[`",
            f".qpipe.subscribe_etl[`{target.procname[:-1]};`",
        ):
            call = f"{prefix}{first}"
            if call in original:
                break
        else:
            raise AssertionError(f"no readable subscribe call for {first!r} in {target.script}")
        (scripts / target.script).write_text(
            original.replace(call, f"{prefix}not_a_declared_table", 1)
        )

    # The spelled-out copy is passed in, not PIPELINES: the real entry defers
    # and would report nothing, which is the point of the copy.
    problems = pipeline_edges.verify_pipeline_edges(
        scripts, [target, *(p for p in PIPELINES if p.procname != target.procname)]
    )
    assert any(target.procname in problem for problem in problems), problems


def test_pipeline_procnames_are_unique():
    """A procname identifies a process, so two entries cannot share one.

    Nothing enforced this. `PIPELINE_OFFSETS` and every lookup by name are
    dict comprehensions over `PIPELINES`, so a repeated name does not raise -
    it drops one pipeline from the registry and hands the survivor the
    other's port offset.
    """
    names = [pipeline.procname for pipeline in PIPELINES]
    assert len(names) == len(set(names)), f"duplicate procname in PIPELINES: {names}"


def test_monitor1_starts_with_the_stack_so_heartbeats_are_actually_collected():
    """The vendored overlay, in both directions: monitor1 on, feed1 off.

    Every process publishes a heartbeat; only monitor1 collects them.

    TorQ ships monitor1 `startwithall=0`, which made `.hb.hb` empty on a
    fully healthy stack and `uqs summary`'s Heartbeat column read
    "not collected" unless an operator knew to start one more process by
    hand. VENDORED_STARTWITHALL_OVERLAY fixes that without editing the
    vendored file.

    Read against the REAL vendored csv rather than the fixture's two-row
    stand-in, because the whole point is what upstream ships.
    """
    real = stack_paths.default_paths()
    vendored = (real.torqapphome / "appconfig" / "process.csv").read_text()
    upstream = {
        row["procname"]: row["startwithall"] for row in csv.DictReader(io.StringIO(vendored))
    }

    # If upstream ever flips this itself, the overlay becomes a no-op that
    # still looks load-bearing - fail here so it gets deleted instead.
    assert upstream["monitor1"] == "0", (
        "the vendored csv no longer ships monitor1 off by default - "
        "VENDORED_STARTWITHALL_OVERLAY is now redundant and should be removed"
    )

    # feed1 is the other half of the overlay, in the other direction: the
    # starter pack's random demo feed, turned OFF because fxfeed1 already
    # publishes `quote` and because it held one of the sixteen plant
    # connections the licence allows (#285).
    assert upstream["feed1"] == "1", (
        "the vendored csv no longer ships feed1 on by default - the feed1 half "
        "of VENDORED_STARTWITHALL_OVERLAY is now redundant and should be removed"
    )

    composed = {row["procname"]: row for row in stack_procs._base_process_rows(real)}
    assert composed["monitor1"]["startwithall"] == "1"
    assert composed["feed1"]["startwithall"] == "0"

    # Nothing else moved: the overlay changes one field, on the processes it
    # names and no others.
    for procname, value in upstream.items():
        if procname in VENDORED_STARTWITHALL_OVERLAY:
            continue
        assert composed[procname]["startwithall"] == value, (
            f"{procname} changed, but the overlay only declares "
            f"{sorted(VENDORED_STARTWITHALL_OVERLAY)}"
        )


def test_an_operator_can_put_monitor1_back_to_the_upstream_default(
    fake_paths: UqsPaths,
):
    """The overlay is a default, not a decree.

    It sits at the vendored layer, so process_overrides.csv still outranks
    it - which is what makes it safe to change the shape of the running
    stack on everyone's behalf.
    """
    (fake_paths.torqapphome / "appconfig" / "process.csv").write_text(
        "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd\n"
        "localhost,{KDBBASEPORT}+9,monitor,monitor1,,1,0,,,"
        "${KDBCODE}/processes/monitor.q,0,,q\n"
    )
    assert (
        stack_procs.get_process_config(fake_paths, "monitor1", base_port=7000)["startwithall"]
        == "1"
    )

    stack_procs.set_process_config(fake_paths, "monitor1", "startwithall", "0")
    assert (
        stack_procs.get_process_config(fake_paths, "monitor1", base_port=7000)["startwithall"]
        == "0"
    )


def test_the_process_csv_layers_compose_in_a_stated_order(fake_paths: UqsPaths):
    """What is the precedence between the vendored `process.csv`, the
    pipelines and `process_overrides.csv`?

    Answered by the code, asserted here so it stays answered. The order is:

        vendored process.csv  ->  PIPELINES  (appended)
        then process_overrides.csv applied LAST, per procname, field by field

    So an override wins over every other source, and the vendored file is
    never edited. The failure mode of an unstated precedence is "works on my
    machine"; this test sets the same field in two layers and checks which
    one wins, which is the only way an order is observable.
    """
    # the pipeline row supplies extras=""; an override says otherwise
    stack_procs.set_process_config(fake_paths, "fxfeed1", "extras", "from-override")

    row = stack_procs.get_process_config(fake_paths, "fxfeed1", base_port=7000)
    assert row["extras"] == "from-override", (
        "process_overrides.csv must outrank a pipeline's own row for the same field"
    )

    # ...and an override on a VENDORED process outranks the vendored file too,
    # without the vendored file being touched.
    stack_procs.set_process_config(fake_paths, "stp1", "extras", "vendored-overridden")
    assert stack_procs.get_process_config(fake_paths, "stp1", base_port=7000)["extras"] == (
        "vendored-overridden"
    )
    vendored = (fake_paths.torqapphome / "appconfig" / "process.csv").read_text()
    assert "vendored-overridden" not in vendored


def test_the_default_start_fits_inside_the_licence_connection_budget():
    """The stack must fit on the licence it is actually run under.

    The community edition in `~/.kx/kc.lic` refuses a q process's
    seventeenth concurrent inbound connection, and every streaming job is
    its own process holding one handle to stp1. The plant does not report
    the refusal in any way an operator sees: it resets the handle, the
    process retries forever inside `torq_stream.q`'s init, and a PID check
    calls it `up`. So going over the cap does not fail — it silently makes
    *which* jobs run depend on which sixteen won the race to start, and that
    changes on every boot. `fxpositions1` and `executions1` lost it (#285).

    Declaring a job and running it are separate decisions here: a pipeline
    that need not start with the stack says so with `startwithall="0"` and a
    note. This holds the sum.
    """
    clients = {
        p.procname
        for p in PIPELINES
        if p.startwithall == "1" and p.kind is not PipelineKind.BACKFILL
    } | pipeline_edges.VENDORED_PLANT_CLIENTS
    allowance = pipeline_edges.LICENCE_CONNECTION_LIMIT - pipeline_edges.INBOUND_RESERVE
    assert len(clients) <= allowance, (
        f"the default start opens {len(clients)} tickerplant connections but only "
        f"{allowance} are available: {sorted(clients)}"
    )


def test_the_connection_budget_check_fires_when_the_default_start_grows(
    monkeypatch: pytest.MonkeyPatch,
):
    """The gate, not just the current sum.

    A check that only ever passes is indistinguishable from one that cannot
    fail, so this turns every shed pipeline back on and asserts the message
    names them and says how to get back under.
    """
    grown = tuple(
        replace(p, startwithall="1") if p.kind is not PipelineKind.BACKFILL else p
        for p in PIPELINES
    )

    problems = pipeline_edges.verify_pipeline_edges(stack_paths.default_paths().scripts_dir, grown)
    budget = [p for p in problems if "tickerplant connections" in p]
    assert len(budget) == 1, problems
    assert 'startwithall="0"' in budget[0]
    # the ones this tree sheds on purpose have to be in the list, or the
    # message sends the reader looking in the wrong place
    for shed in ("cross1", "widefeed1", "vectorize1", "databento1"):
        assert shed in budget[0]


def test_every_on_demand_plant_client_still_has_a_producer_to_start_with():
    """Shedding a process must leave it *startable*, not stranded.

    The point of `startwithall="0"` is that the job is still declared and
    one command away. That only holds if whatever publishes the tables it
    subscribes to is either in the default set or shed alongside it — a job
    whose producer was removed from the registry entirely would start and
    then consume nothing, which is the failure this guards.
    """
    producers: dict[str, set[str]] = {}
    for pipeline in PIPELINES:
        for table in pipeline.published_tables:
            producers.setdefault(table, set()).add(pipeline.procname)
    declared = {p.procname for p in PIPELINES}
    for pipeline in PIPELINES:
        if pipeline.startwithall == "1" or pipeline.subscribes_dynamic:
            continue
        for table in pipeline.subscribed_tables:
            # databento_mbp10 comes from an external Python feed handler and
            # from the backfill; no pipeline publishes it, which its own
            # note says.
            if table == "databento_mbp10":
                continue
            assert producers.get(table, set()) & declared, (
                f"{pipeline.procname} is on-demand and subscribes to {table!r}, "
                "which no declared pipeline publishes - starting it would consume "
                "nothing"
            )


def test_a_pipeline_publishing_an_undefined_table_is_reported(
    fake_paths: UqsPaths, monkeypatch: pytest.MonkeyPatch
):
    """The gate firing, which is the half #287 never had.

    The old check could not fail for the case it was named after: it
    iterated declared *schemas*, so a pipeline that declared a publish and
    no schema contributed nothing to assert. This adds one and expects to
    be told.
    """
    invented = replace(
        BY_NAME["fxpositions1"],
        procname="ghost1",
        # An invented process has no q file to defer to, so it states its
        # own edges - which is the resolver's strictness working.
        subscribes=(),
        publishes=("fx_position", "a_table_nothing_defines"),
    )
    monkeypatch.setattr(plant_schema, "PIPELINES", (*PIPELINES, invented))

    undefined = plant_schema.undefined_published_tables(fake_paths)
    assert len(undefined) == 1, undefined
    assert "a_table_nothing_defines" in undefined[0]
    # and it names who to go and ask
    assert "ghost1" in undefined[0]


def test_the_fx_positions_tables_reach_the_tickerplant(fake_paths: UqsPaths):
    """The regression itself, named so it cannot be quietly undone.

    fxpositions1 publishes two tables and owns neither `table` nor
    `schema`. Both were defined in uqs_tables.q and neither reached
    `database.q`, so the whole FX positions service published into nothing.
    """
    generated = plant_schema._generated_schema_content(fake_paths)
    assert schemas.definition("fx_position") in generated
    assert schemas.definition("fx_limit_breach") in generated
    assert plant_schema._published_tables([BY_NAME["fxpositions1"]]) == {
        "fx_position",
        "fx_limit_breach",
    }


# ----------------------------------------------------- the renamed data dir


def _data_dir_paths(tmp_path):
    """Only the two fields the migration guard looks at."""
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "lib" / "starter",
        torqdata=tmp_path / "output" / "uqs",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "python" / "uqs",
    )


def test_a_fresh_checkout_is_not_asked_to_migrate_anything(tmp_path):
    """Neither directory exists, which is every first run and every CI job.

    This is the assertion that stops the guard from being a permanent error
    for people who never had the old name.
    """
    check_data_dir_was_migrated(_data_dir_paths(tmp_path))


def test_the_new_data_dir_alone_is_fine(tmp_path):
    paths = _data_dir_paths(tmp_path)
    paths.torqdata.mkdir(parents=True)
    check_data_dir_was_migrated(paths)


def test_the_old_data_dir_alone_is_refused_with_the_command_that_fixes_it(tmp_path):
    """The failure this exists to prevent is silent: bootstrap regenerates
    process.csv and database.q on every command, so the stack would have
    started cleanly against an empty HDB and every historical query would
    have returned no rows rather than erroring.

    The message must carry the `mv`, because a reader who has just pulled has
    no reason to know the directory was renamed at all.
    """
    paths = _data_dir_paths(tmp_path)
    (tmp_path / "scripts" / "output" / "uqf-stack").mkdir(parents=True)
    with pytest.raises(UqsError) as exc:
        check_data_dir_was_migrated(paths)
    message = str(exc.value)
    assert "mv " in message
    assert "uqf-stack" in message and str(paths.torqdata) in message
    assert "empty HDB" in message


def test_the_data_dir_under_scripts_is_refused_with_the_command_that_moves_it(tmp_path):
    """The second move: out of scripts/ into output/. Same silent failure
    if it is skipped, so the same refusal - and the `mkdir -p`, because
    output/ does not exist on a checkout that has only ever used scripts/."""
    paths = _data_dir_paths(tmp_path)
    old = tmp_path / "scripts" / "output" / "uqs"
    old.mkdir(parents=True)
    with pytest.raises(UqsError) as exc:
        check_data_dir_was_migrated(paths)
    message = str(exc.value)
    assert f"mkdir -p {paths.torqdata.parent} && mv {old} {paths.torqdata}" in message


def test_the_newer_old_location_is_the_one_moved_when_both_remain(tmp_path):
    """scripts/output/uqs holds the data the stack last wrote; uqf-stack is
    what was left behind by a copy rather than a move the first time."""
    paths = _data_dir_paths(tmp_path)
    (tmp_path / "scripts" / "output" / "uqs").mkdir(parents=True)
    (tmp_path / "scripts" / "output" / "uqf-stack").mkdir(parents=True)
    with pytest.raises(UqsError) as exc:
        check_data_dir_was_migrated(paths)
    assert f"mv {tmp_path / 'scripts' / 'output' / 'uqs'} " in str(exc.value)


def test_the_refusal_does_not_tell_you_to_stop_the_stack_first(tmp_path):
    """The first version of this message said "Stop the stack, then: mv ...",
    which is an order this guard makes impossible.

    `stop` reaches the guard through `bootstrap`, so it is refused like
    everything else - and that is deliberate, because `process.csv` lives
    inside the data directory and a bootstrap allowed through would CREATE the
    new one, leaving both present, the guard permanently quiet and the old HDB
    orphaned in silence.

    Since the block cannot move, the instruction has to. The move needs no
    downtime: every location is inside the one checkout, so `mv` is a rename on
    one filesystem and every running process keeps its open files.
    """
    paths = _data_dir_paths(tmp_path)
    (tmp_path / "scripts" / "output" / "uqf-stack").mkdir(parents=True)
    with pytest.raises(UqsError) as exc:
        check_data_dir_was_migrated(paths)
    message = str(exc.value).lower()
    assert "stop the stack" not in message, (
        "the guard blocks `stop`, so telling the reader to stop first is a deadlock"
    )
    assert "safe with the stack up" in message


def test_both_present_is_allowed_rather_than_guessed_at(tmp_path):
    """A copy rather than a move is a legitimate thing to have done - keeping
    the old tree as a backup. Refusing would force a choice the tool is not
    entitled to make."""
    paths = _data_dir_paths(tmp_path)
    paths.torqdata.mkdir(parents=True)
    (tmp_path / "scripts" / "output" / "uqf-stack").mkdir(parents=True)
    check_data_dir_was_migrated(paths)


def test_gateway1_loads_the_desk_catalog_after_its_own_script():
    """The other vendored overlay: `.qcat` has to be on the gateway, because
    that is the process the front end's `Gateway.call` reaches.

    Read against the REAL vendored csv, for the reason the startwithall test
    gives: the point is what upstream ships. Order matters and is asserted -
    the vendored script must load FIRST, since appending to the `load` column
    is what makes this an overlay rather than a replacement.
    """
    real = stack_paths.default_paths()
    vendored = (real.torqapphome / "appconfig" / "process.csv").read_text()
    upstream = {row["procname"]: row["load"] for row in csv.DictReader(io.StringIO(vendored))}

    composed = {row["procname"]: row for row in stack_procs._base_process_rows(real)}
    loaded = composed["gateway1"]["load"].split()

    assert loaded[0] == upstream["gateway1"], (
        "the vendored gateway script must still load first - this overlay appends"
    )
    assert loaded[-1].endswith("processes/uqs_catalog.q")
    assert "${UQFSCRIPTS}" in loaded[-1], (
        "pathed through the env var the pipeline rows use, not a literal path"
    )


def test_the_load_overlay_touches_no_other_process():
    """A vendored row this tree does not mean to change must come through
    byte-identical - the failure mode of a field-level overlay is reaching
    one row too many."""
    real = stack_paths.default_paths()
    vendored = {
        row["procname"]: row["load"]
        for row in csv.DictReader(
            io.StringIO((real.torqapphome / "appconfig" / "process.csv").read_text())
        )
    }
    composed = {row["procname"]: row for row in stack_procs._base_process_rows(real)}
    for procname, original in vendored.items():
        if procname == "gateway1":
            continue
        assert composed[procname]["load"] == original, f"{procname}'s load column was altered"
