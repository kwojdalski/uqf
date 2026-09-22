"""Tests for the `uqf-stack` command surface (cli.py).

WHY THIS FILE EXISTS. `scripts/test.py coverage` reported cli.py at 0% of
275 statements - the entire user-facing CLI, executed by nothing. The one
test that named it, test_module_split.py, reads it as TEXT to scan for
facade references. (Naming that pattern literally here would make this
file fail that scan - it cannot tell an example from a call, which is why
it excludes itself.) So every command's wiring was unverified: a
dropped `--port`, a swallowed exit code, an argument passed in the wrong
order would all have shipped green.

WHAT IS AND IS NOT TESTED HERE. `core` is separately and thoroughly tested,
so re-testing its behaviour through the CLI would only make those tests
slower and more brittle. What is untested without this file is the WIRING:

  * does each command call the core function it claims to,
  * with the options the user typed actually reaching it,
  * does a failure become a non-zero exit rather than a traceback,
  * and does a subprocess's exit code survive to the caller.

That last one is the reason to care. `start`, `stop`, `restart`, `print` and
`summary` all end in `raise typer.Exit(code=result.returncode)`. If any of
them returned 0 while torq.sh failed, CI would go green on a stack that
never came up, and nothing else in this repository would notice.

Every test monkeypatches `cli.core`, so nothing here starts a process,
opens a socket or touches scripts/output/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from torq_orchestrator import cli, core

runner = CliRunner()


@dataclass
class Completed:
    """Stands in for subprocess.CompletedProcess, which is all the streaming
    commands use of it."""

    returncode: int = 0
    stdout: str = ""


@dataclass
class Recorder:
    """Records one call so a test can assert on the arguments, not just that
    something was called."""

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    result: Any = None
    raises: Exception | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result

    @property
    def kwargs(self) -> dict[str, Any]:
        return self.calls[-1][1]

    @property
    def args(self) -> tuple[Any, ...]:
        return self.calls[-1][0]


@dataclass
class _Paths:
    """Stands in for UqfStackPaths. No test here cares what it points at, but
    it has to have the attributes the commands read - a bare sentinel string
    made `summary`'s debug line an AttributeError in tests only."""

    torqdata: str = "TORQDATA"


@pytest.fixture(autouse=True)
def _no_real_paths(monkeypatch):
    """`_paths()` reads the filesystem and the vendored tree. Every command
    calls it, and no test here cares what it returns."""
    monkeypatch.setattr(cli.core, "default_paths", _Paths)


def _patch(monkeypatch, name: str, **kw) -> Recorder:
    rec = Recorder(**kw)
    monkeypatch.setattr(cli.core, name, rec)
    return rec


# ------------------------------------------------- the streaming commands


@pytest.mark.parametrize(
    ("command", "fn"), [("start", "start"), ("stop", "stop"), ("restart", "restart")]
)
def test_a_lifecycle_command_calls_its_core_function(monkeypatch, command, fn):
    rec = _patch(monkeypatch, fn, result=Completed())
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 0
    assert rec.args[1] == "all", "the default process selector is 'all'"


@pytest.mark.parametrize("command", ["start", "stop", "restart"])
def test_a_lifecycle_command_passes_the_process_names_through(monkeypatch, command):
    rec = _patch(monkeypatch, command, result=Completed())
    runner.invoke(cli.app, [command, "rdb1 hdb1"])
    assert rec.args[1] == "rdb1 hdb1"


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print"])
def test_the_port_option_reaches_core(monkeypatch, command):
    """A dropped --port silently drives the DEFAULT stack.

    That is the worst shape of wiring bug available here: every command
    succeeds, against the wrong fleet.
    """
    fn = "print_procs" if command == "print" else command
    rec = _patch(monkeypatch, fn, result=Completed())
    runner.invoke(cli.app, [command, "--port", "7000"])
    assert rec.kwargs["base_port"] == 7000


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print"])
def test_a_failing_subprocess_exit_code_survives(monkeypatch, command):
    """The property CI depends on. A non-zero torq.sh must not become a
    zero `uqf-stack`."""
    fn = "print_procs" if command == "print" else command
    _patch(monkeypatch, fn, result=Completed(returncode=3))
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 3


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print"])
def test_a_refusal_exits_one_rather_than_raising(monkeypatch, command):
    fn = "print_procs" if command == "print" else command
    _patch(monkeypatch, fn, raises=core.UqfStackError("no such process"))
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 1
    assert not isinstance(result.exception, core.UqfStackError), "the error is handled, not raised"


# ---------------------------------------------------------------- summary


def _summary_ok(monkeypatch, *, rows=None, returncode=0):
    _patch(monkeypatch, "summary", result=Completed(returncode=returncode, stdout="raw"))
    _patch(monkeypatch, "configured_ports", result={})
    _patch(monkeypatch, "heartbeat_states", result={})
    rows = rows if rows is not None else []
    _patch(monkeypatch, "summary_rows", result=rows)


def test_summary_renders_and_propagates_the_exit_code(monkeypatch):
    _summary_ok(monkeypatch, returncode=2)
    result = runner.invoke(cli.app, ["summary"])
    assert result.exit_code == 2


def test_summary_survives_a_port_map_it_cannot_build(monkeypatch):
    """Documented behaviour: a summary that still prints beats one that dies
    because the port map failed - the reported ports are unaffected."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "configured_ports", raises=core.UqfStackError("no process.csv"))
    _patch(monkeypatch, "heartbeat_states", result={})
    rec = _patch(monkeypatch, "summary_rows", result=[])
    result = runner.invoke(cli.app, ["summary"])
    assert result.exit_code == 0
    assert rec.args[1] == {}, "an unbuildable port map becomes empty, not fatal"


def test_summary_distinguishes_unreachable_monitoring_from_a_healthy_fleet(monkeypatch):
    """`None` heartbeats means monitor1 could not be reached - a gap in
    MONITORING, not a verdict on the fleet. The distinction is the whole
    point of the message, so it is pinned."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "configured_ports", result={})
    _patch(monkeypatch, "heartbeat_states", raises=core.UqfStackError("monitor1 down"))
    rec = _patch(monkeypatch, "summary_rows", result=[])
    result = runner.invoke(cli.app, ["summary"])
    assert rec.args[2] is None, "unreachable monitoring is None, not an empty dict"
    assert "could not be reached" in result.stdout


def test_an_unreachable_but_running_monitor_is_not_called_stopped(monkeypatch):
    """The message used to assert "monitor1 is not running", which is one
    cause and not the common one. A monitor at its connection cap is running
    perfectly and still collecting heartbeats - it just has no slot left to
    answer on. Telling the reader to restart it sends them to fix a process
    with nothing wrong with it."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "configured_ports", result={})
    _patch(monkeypatch, "heartbeat_states", result=None)
    _patch(monkeypatch, "summary_rows", result=[_row(Process="monitor1", Status="up")])
    result = runner.invoke(cli.app, ["summary"])
    assert "connection cap" in result.stdout
    assert "is not running" not in result.stdout
    assert "uqf-stack start monitor1" not in result.stdout, "it is already running"


def test_a_genuinely_stopped_monitor_still_says_to_start_it(monkeypatch):
    """The other half: when monitor1 really is down, the advice that was
    always given is the right advice, and must not be lost to the new one."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "configured_ports", result={})
    _patch(monkeypatch, "heartbeat_states", result=None)
    _patch(monkeypatch, "summary_rows", result=[_row(Process="monitor1", Status="down")])
    result = runner.invoke(cli.app, ["summary"])
    # Rich hard-wraps the panel text, so a phrase can straddle a newline.
    flat = " ".join(result.stdout.split())
    assert "is not running" in flat
    assert "uqf-stack start monitor1" in flat


# ------------------------------------------------- the graph columns


def test_summary_shows_the_graph_by_default(monkeypatch):
    """These were opt-in first, because nine columns do not fit an
    eighty-column terminal. That was the wrong trade: a column nobody knows
    about answers nothing, and a reader on a narrow terminal can say
    `--columns status` while one who never learns they exist cannot."""
    monkeypatch.setenv("COLUMNS", "220")
    _summary_ok(monkeypatch, rows=[_row()])
    result = runner.invoke(cli.app, ["summary"])
    flat = " ".join(result.stdout.split())
    for column in core.SUMMARY_GRAPH_COLUMNS:
        assert column in flat


def test_columns_status_gives_back_the_narrow_table(monkeypatch):
    """The escape hatch for an 80-column terminal, and the reason showing the
    graph by default is safe."""
    assert cli._resolve_columns("status") == list(core.SUMMARY_COLUMNS)
    assert cli._resolve_columns("STATUS") == list(core.SUMMARY_COLUMNS)


def test_columns_all_adds_the_graph(monkeypatch):
    # Nine columns do not fit the 80-column default the runner reports, and
    # Rich elides the headers rather than the data - so the width is set here
    # to assert on the columns rather than on Rich's truncation of them.
    monkeypatch.setenv("COLUMNS", "220")
    _summary_ok(monkeypatch, rows=[_row()])
    result = runner.invoke(cli.app, ["summary", "--columns", "all"])
    flat = " ".join(result.stdout.split())
    for column in core.SUMMARY_GRAPH_COLUMNS:
        assert column in flat


def test_columns_are_matched_case_insensitively_and_kept_in_order(monkeypatch):
    """The names have a space and a capital in them ("Depends on"), so an
    exact-match-only option would be unusable from a shell."""
    assert cli._resolve_columns("outputs,process") == ["Outputs", "Process"]
    assert cli._resolve_columns("PROCESS") == ["Process"]


def test_a_repeated_column_is_not_rendered_twice(monkeypatch):
    assert cli._resolve_columns("Process,process,Process") == ["Process"]


def test_an_unknown_column_is_refused_with_the_available_ones(monkeypatch):
    """A typo in a column name must not silently render a narrower table -
    the reader would conclude the data is missing, not the column name wrong."""
    _summary_ok(monkeypatch, rows=[_row()])
    result = runner.invoke(cli.app, ["summary", "--columns", "Process,Bogus"])
    assert result.exit_code == 1


def test_a_graph_cell_breaks_between_entries_not_inside_a_name(monkeypatch):
    """Rich would wrap these itself, on whitespace and at whatever width is
    left over - which splits `fx_limit_breach` across two lines. A reader
    scans these cells by counting entries, so the break belongs at the
    commas."""
    cell = cli._graph_cell(["a_table", "b_table", "c_table"])
    lines = cell.split("\n")
    assert lines == ["a_table, b_table,", "c_table"]
    assert all("_" not in line[-1:] for line in lines), "no name split mid-word"


def test_a_short_graph_cell_does_not_wrap(monkeypatch):
    assert "\n" not in cli._graph_cell(["one", "two"])


def test_an_empty_graph_cell_is_a_dash_not_a_blank(monkeypatch):
    """ "declares no inputs" and "this column has nothing to say" look
    identical as a blank, and the first is a real fact about a feed."""
    assert "-" in cli._graph_cell([])


def test_the_graph_columns_come_from_the_declared_pipelines(monkeypatch):
    """Derived from the same Pipeline declarations verify_pipeline_edges
    checks, so a row here cannot claim an edge the build would reject."""
    rows = [_row(Process="posbook1")]
    cli._attach_graph_columns(rows)
    assert "executions" in rows[0]["Inputs"]
    assert "position" in rows[0]["Outputs"]
    assert "executions1" in rows[0]["Depends on"]


def test_a_process_with_no_declared_edges_gets_dashes(monkeypatch):
    """A vendored TorQ process has no Pipeline entry and so no declared
    edges. It must render, not raise."""
    rows = [_row(Process="hdb1")]
    cli._attach_graph_columns(rows)
    assert all(rows[0][c] == "[dim]-[/]" for c in core.SUMMARY_GRAPH_COLUMNS)


# --------------------------------------------------- the connection cap


def test_a_start_past_the_licence_cap_warns(monkeypatch):
    """Past the cap the licence, not the configuration, decides what runs -
    and it does so silently: the extra handle is reset, the process wedges in
    its retry loop, and `summary` still reports it `up` because that is a PID
    check."""
    over = [_row(Process=f"p{i}") for i in range(core.PLANT_CONNECTION_BUDGET + 1)]
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "summary_rows", result=over)
    _patch(monkeypatch, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "rdb1"])
    assert result.exit_code == 0
    assert "past the" in result.stdout
    assert str(core.PLANT_CONNECTION_BUDGET) in result.stdout


def test_a_start_inside_the_cap_is_silent(monkeypatch):
    """A warning on every start would be noise, and noise is how a real one
    gets missed."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "summary_rows", result=[_row(Process="rdb1")])
    _patch(monkeypatch, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "rdb1"])
    assert "concurrent connections" not in result.stdout


def test_the_cap_warning_never_blocks_a_start(monkeypatch):
    """Advisory only. A warning that cannot be produced - the fleet is
    unreachable, the registry cannot be read - must not stop a start."""
    _patch(monkeypatch, "summary", raises=RuntimeError("fleet unreachable"))
    _patch(monkeypatch, "start", result=Completed())
    assert runner.invoke(cli.app, ["start", "rdb1"]).exit_code == 0


def test_summary_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, "summary", raises=core.UqfStackError("no stack"))
    assert runner.invoke(cli.app, ["summary"]).exit_code == 1


def _row(**kw):
    base = {
        "Time": "10:00",
        "Process": "rdb1",
        "Status": "up",
        "PID": "42",
        "Port": "6052",
        "PortSource": "reported",
        "Heartbeat": "ok",
    }
    return {**base, **kw}


def test_a_configured_port_is_marked_as_a_different_claim(monkeypatch):
    """ "will listen here" is not "is listening here". The dim markup is the
    only thing carrying that distinction to the reader, and a table that
    printed both identically would be quietly misleading."""
    _summary_ok(monkeypatch, rows=[_row(Status="down", PID="-", PortSource="configured")])
    result = runner.invoke(cli.app, ["summary"])
    assert "come from process.csv" in result.stdout


def test_a_running_process_with_no_heartbeat_is_explained_not_left_blank(monkeypatch):
    """A "-" on an `up` process is neither an all-clear nor a fault: on the
    community edition monitor1's connection count is licence-capped, so it
    simply never subscribed. Saying so beats a dash the reader has to guess
    at."""
    _summary_ok(monkeypatch, rows=[_row(Heartbeat="-"), _row(Process="hdb1", Heartbeat="ok")])
    result = runner.invoke(cli.app, ["summary"])
    assert "No heartbeat collected for 1 running" in result.stdout
    assert "rdb1" in result.stdout


def test_a_down_process_with_no_heartbeat_is_not_reported_as_unheard(monkeypatch):
    """The message is about processes that ARE running and unheard. Counting
    a stopped one would send the reader looking for a monitoring fault that
    is really just a stopped process."""
    _summary_ok(monkeypatch, rows=[_row(Status="down", Heartbeat="-")])
    result = runner.invoke(cli.app, ["summary"])
    assert "No heartbeat collected" not in result.stdout


@pytest.mark.parametrize("state", ["ok", "warning", "error", "not collected"])
def test_every_heartbeat_state_renders(monkeypatch, state):
    _summary_ok(monkeypatch, rows=[_row(Heartbeat=state)])
    result = runner.invoke(cli.app, ["summary"])
    assert result.exit_code == 0


# ----------------------------------------------------------------- export


def test_export_writes_when_a_path_is_given(monkeypatch, tmp_path):
    _patch(monkeypatch, "list_items", result=[{"procname": "rdb1"}])
    rec = _patch(monkeypatch, "export_table")
    target = tmp_path / "out.csv"
    result = runner.invoke(cli.app, ["list", "processes", "--export", str(target)])
    assert result.exit_code == 0
    assert rec.args[1] == target
    assert "exported to" in result.stdout


def test_nothing_is_exported_without_the_option(monkeypatch):
    _patch(monkeypatch, "list_items", result=[{"procname": "rdb1"}])
    rec = _patch(monkeypatch, "export_table")
    runner.invoke(cli.app, ["list", "processes"])
    assert rec.calls == []


def test_a_failed_export_reports_rather_than_losing_the_output(monkeypatch, tmp_path):
    """The rows were already printed by the time the export runs. An
    unwritable path must say so, not take the whole command down with a
    traceback."""
    _patch(monkeypatch, "list_items", result=[{"procname": "rdb1"}])
    _patch(monkeypatch, "export_table", raises=core.UqfStackError("unsupported suffix"))
    result = runner.invoke(cli.app, ["list", "processes", "--export", str(tmp_path / "x.txt")])
    assert result.exit_code == 1


# ------------------------------------------------------------------ query


def test_query_passes_the_expression_and_connection_through(monkeypatch):
    rec = _patch(monkeypatch, "query", result="RESULT")
    result = runner.invoke(
        cli.app, ["query", "select from t", "--port", "6052", "--user", "u", "--passwd", "p"]
    )
    assert result.exit_code == 0
    assert rec.args[:2] == ("select from t", 6052)
    assert rec.kwargs["user"] == "u"
    assert rec.kwargs["passwd"] == "p"


def test_query_turns_a_driver_error_into_an_exit_code(monkeypatch):
    """kola raises its own exception types, which are NOT UqfStackError. The
    command catches Exception for that reason, and this pins it: a connect
    failure must be an exit code, not a traceback in the user's face."""
    _patch(monkeypatch, "query", raises=RuntimeError("connection refused"))
    result = runner.invoke(cli.app, ["query", "1+1", "--port", "6052"])
    assert result.exit_code == 1


# ----------------------------------------------------------------- schema


def test_schema_resolves_a_named_process_to_a_port(monkeypatch):
    resolve = _patch(monkeypatch, "resolve_port", result=6052)
    overview = _patch(monkeypatch, "schema_overview", result=[])
    runner.invoke(cli.app, ["schema", "--proc", "hdb1"])
    assert resolve.args[1] == "hdb1"
    assert overview.args[0] == 6052, "the resolved port is what gets queried"


def test_an_explicit_port_skips_resolution_entirely(monkeypatch):
    """--port is documented as reading that port DIRECTLY instead of
    resolving --proc, so resolution must not happen at all."""
    resolve = _patch(monkeypatch, "resolve_port", result=9999)
    overview = _patch(monkeypatch, "schema_overview", result=[])
    runner.invoke(cli.app, ["schema", "--port", "1234"])
    assert resolve.calls == []
    assert overview.args[0] == 1234


def test_schema_with_a_table_describes_its_columns(monkeypatch):
    _patch(monkeypatch, "resolve_port", result=6052)
    _patch(monkeypatch, "match_tables", result=["quotes"])
    cols = _patch(
        monkeypatch,
        "schema_columns",
        result=[{"column": "sym", "type": "symbol", "q": "s", "attribute": "grouped"}],
    )
    result = runner.invoke(cli.app, ["schema", "quotes"])
    assert result.exit_code == 0
    assert cols.args[0] == "quotes"
    assert "sym" in result.stdout


def test_a_pattern_matching_nothing_exits_one_and_names_what_exists(monkeypatch):
    _patch(monkeypatch, "resolve_port", result=6052)
    _patch(monkeypatch, "match_tables", result=[])
    _patch(monkeypatch, "schema_table_names", result=["quotes", "trades"])
    result = runner.invoke(cli.app, ["schema", "nope*"])
    assert result.exit_code == 1


def test_an_unknown_process_exits_one(monkeypatch):
    _patch(monkeypatch, "resolve_port", raises=core.UqfStackError("rbd1 is not declared"))
    assert runner.invoke(cli.app, ["schema", "--proc", "rbd1"]).exit_code == 1


def test_schema_turns_a_driver_error_into_an_exit_code(monkeypatch):
    _patch(monkeypatch, "resolve_port", result=6052)
    _patch(monkeypatch, "schema_overview", raises=RuntimeError("connection refused"))
    assert runner.invoke(cli.app, ["schema"]).exit_code == 1


# ---------------------------------------------------------------- config


def test_config_get_prints_one_field_when_asked(monkeypatch):
    _patch(monkeypatch, "get_process_config", result={"procname": "rdb1", "port": "6052"})
    result = runner.invoke(cli.app, ["config-get", "rdb1", "port"])
    assert result.exit_code == 0
    assert "6052" in result.stdout


def test_config_get_resolves_placeholders_unless_raw_is_given(monkeypatch):
    """--raw is the difference between "will listen on 6052" and
    "{KDBBASEPORT}+2". Wiring it backwards would show the wrong one with no
    other symptom."""
    rec = _patch(monkeypatch, "get_process_config", result={})
    runner.invoke(cli.app, ["config-get", "rdb1"])
    assert rec.kwargs["resolve"] is True
    runner.invoke(cli.app, ["config-get", "rdb1", "--raw"])
    assert rec.kwargs["resolve"] is False


def test_config_set_reports_what_it_wrote(monkeypatch):
    rec = _patch(monkeypatch, "set_process_config")
    result = runner.invoke(cli.app, ["config-set", "rdb1", "startwithall", "1"])
    assert result.exit_code == 0
    assert rec.args[1:] == ("rdb1", "startwithall", "1")


def test_config_set_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, "set_process_config", raises=core.UqfStackError("unknown field"))
    assert runner.invoke(cli.app, ["config-set", "rdb1", "nope", "1"]).exit_code == 1


def test_list_with_no_kind_shows_the_kinds_rather_than_failing(monkeypatch):
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    for kind in core.LISTABLE_KINDS:
        assert kind in result.stdout


def test_list_renders_the_items_of_a_kind(monkeypatch):
    _patch(monkeypatch, "list_items", result=[{"procname": "rdb1", "port": "6052"}])
    result = runner.invoke(cli.app, ["list", "processes"])
    assert result.exit_code == 0
    assert "rdb1" in result.stdout


def test_an_unknown_kind_exits_one(monkeypatch):
    _patch(monkeypatch, "list_items", raises=core.UqfStackError("unknown kind"))
    assert runner.invoke(cli.app, ["list", "bogus"]).exit_code == 1


# ------------------------------------------------------------------- logs


def test_logs_defaults_to_a_bounded_tail_not_a_follow(monkeypatch):
    """`follow` blocks until Ctrl-C. Defaulting to it would hang any script
    that ran `uqf-stack logs`."""
    recent = _patch(monkeypatch, "print_recent_logs")
    follow = _patch(monkeypatch, "follow_logs")
    result = runner.invoke(cli.app, ["logs"])
    assert result.exit_code == 0
    assert follow.calls == []
    assert recent.kwargs["lines"] == 20


def test_follow_selects_the_streaming_path(monkeypatch):
    recent = _patch(monkeypatch, "print_recent_logs")
    follow = _patch(monkeypatch, "follow_logs")
    runner.invoke(cli.app, ["logs", "--follow"])
    assert recent.calls == []
    assert len(follow.calls) == 1


def test_the_level_filter_reaches_core(monkeypatch):
    rec = _patch(monkeypatch, "print_recent_logs")
    runner.invoke(cli.app, ["logs", "--level", "WARNING", "--lines", "5"])
    assert rec.kwargs["min_level"] == "WARNING"
    assert rec.kwargs["lines"] == 5


# -------------------------------------------------------------------- raw


def test_raw_passes_every_extra_argument_through_verbatim(monkeypatch):
    """`raw` exists to reach torq.sh verbs this CLI does not model. Dropping
    or reordering the arguments would make it useless in a way no other
    command's failure resembles."""
    rec = _patch(monkeypatch, "run_torq_sh", result=Completed())
    runner.invoke(cli.app, ["raw", "--", "qcon", "gateway1", "admin:admin"])
    assert rec.args[1] == ["qcon", "gateway1", "admin:admin"]


def test_raw_propagates_the_exit_code(monkeypatch):
    _patch(monkeypatch, "run_torq_sh", result=Completed(returncode=7))
    assert runner.invoke(cli.app, ["raw", "--", "debug", "rdb1"]).exit_code == 7


# ------------------------------------------------------------------ clean


def test_clean_delegates(monkeypatch):
    rec = _patch(monkeypatch, "clean")
    assert runner.invoke(cli.app, ["clean"]).exit_code == 0
    assert len(rec.calls) == 1


# ----------------------------------------------------------- new-process


def test_new_process_runs_the_wizard(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(cli.wizard, "run", rec)
    result = runner.invoke(cli.app, ["new-process", "--port", "7000"])
    assert result.exit_code == 0
    assert rec.kwargs["base_port"] == 7000


def test_a_wizard_refusal_exits_one(monkeypatch):
    monkeypatch.setattr(
        cli.wizard, "run", Recorder(raises=core.UqfStackError("no recipe")).__call__
    )
    assert runner.invoke(cli.app, ["new-process"]).exit_code == 1


# ----------------------------------------------------------------- crypto


def test_crypto_start_splits_the_comma_separated_lists(monkeypatch):
    """The CLI takes comma-separated strings and core takes tuples, so the
    split happens here - the one piece of real logic in this file."""
    rec = _patch(monkeypatch, "start_crypto_recorder", result=4242)
    result = runner.invoke(cli.app, ["crypto", "start", "--venues", "a, b ,c", "--symbols", "X,Y"])
    assert result.exit_code == 0
    assert rec.kwargs["venues"] == ("a", "b", "c"), "whitespace around a name is trimmed"
    assert rec.kwargs["symbols"] == ("X", "Y")
    assert "4242" in result.stdout


def test_crypto_start_drops_empty_entries_rather_than_passing_blanks(monkeypatch):
    """A trailing comma is an ordinary typo, and a blank venue name reaches
    the recorder as a connection attempt to nothing."""
    rec = _patch(monkeypatch, "start_crypto_recorder", result=1)
    runner.invoke(cli.app, ["crypto", "start", "--venues", "a,,b,"])
    assert rec.kwargs["venues"] == ("a", "b")


def test_crypto_stop_and_status_render(monkeypatch):
    _patch(monkeypatch, "stop_crypto_recorder")
    assert runner.invoke(cli.app, ["crypto", "stop"]).exit_code == 0
    _patch(monkeypatch, "crypto_recorder_status", result={"running": "yes", "pid": "42"})
    result = runner.invoke(cli.app, ["crypto", "status"])
    assert result.exit_code == 0
    assert "42" in result.stdout


def test_a_crypto_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, "start_crypto_recorder", raises=core.UqfStackError("no checkout"))
    assert runner.invoke(cli.app, ["crypto", "start"]).exit_code == 1


def test_crypto_fills_start_passes_the_oms_socket_and_poll_interval(monkeypatch):
    rec = _patch(monkeypatch, "start_crypto_fills_recorder", result=99)
    result = runner.invoke(
        cli.app,
        ["crypto", "fills-start", "--oms-socket-path", "/tmp/x.sock", "--poll-interval-ms", "250"],
    )
    assert result.exit_code == 0
    assert rec.kwargs["oms_socket_path"] == "/tmp/x.sock"
    assert rec.kwargs["poll_interval_ms"] == 250


def test_crypto_fills_start_names_which_table_is_simulated(monkeypatch):
    """The two tables this recorder writes mean different things - one is
    paper, one is confirmed exchange executions - and confusing them is a
    trading-decision error, not a cosmetic one. The message says which is
    which, so it is asserted."""
    _patch(monkeypatch, "start_crypto_fills_recorder", result=1)
    result = runner.invoke(cli.app, ["crypto", "fills-start"])
    assert core.CRYPTO_FILLS_RECORDER_TABLE in result.stdout
    assert core.CRYPTO_REAL_FILLS_RECORDER_TABLE in result.stdout
    assert "SIMULATED" in result.stdout


def test_crypto_fills_stop_and_status_render(monkeypatch):
    _patch(monkeypatch, "stop_crypto_fills_recorder")
    assert runner.invoke(cli.app, ["crypto", "fills-stop"]).exit_code == 0
    _patch(monkeypatch, "crypto_fills_recorder_status", result={"running": "no"})
    result = runner.invoke(cli.app, ["crypto", "fills-status"])
    assert result.exit_code == 0
    assert "running" in result.stdout


def test_a_crypto_fills_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, "start_crypto_fills_recorder", raises=core.UqfStackError("no socket"))
    assert runner.invoke(cli.app, ["crypto", "fills-start"]).exit_code == 1
    _patch(monkeypatch, "stop_crypto_fills_recorder", raises=core.UqfStackError("not running"))
    assert runner.invoke(cli.app, ["crypto", "fills-stop"]).exit_code == 1


def test_a_crypto_stop_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, "stop_crypto_recorder", raises=core.UqfStackError("not running"))
    assert runner.invoke(cli.app, ["crypto", "stop"]).exit_code == 1


def test_a_logs_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, "print_recent_logs", raises=core.UqfStackError("no such process"))
    assert runner.invoke(cli.app, ["logs", "nope"]).exit_code == 1


def test_a_config_get_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, "get_process_config", raises=core.UqfStackError("no such process"))
    assert runner.invoke(cli.app, ["config-get", "nope"]).exit_code == 1


# ------------------------------------------------------------------- app


def test_every_command_is_reachable_and_documented():
    """A command with no help text is one nobody can discover. `--help` also
    exercises every command's signature, so a malformed Annotated default
    fails here rather than the first time someone runs it."""
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("start", "stop", "restart", "summary", "query", "schema", "logs", "raw"):
        assert command in result.stdout


def test_main_configures_logging_before_running(monkeypatch):
    """Ordering, not decoration: a command that logged before logging was
    configured would write through a default handler nobody sees."""
    order: list[str] = []
    monkeypatch.setattr(cli, "configure_logging", lambda **kw: order.append("configure"))
    monkeypatch.setattr(cli, "app", lambda: order.append("app"))
    cli.main()
    assert order == ["configure", "app"]


# ------------------------------------------------------------ debug mode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("DEBUG", "DEBUG"),
        ("debug", "DEBUG"),
        ("  Warning  ", "WARNING"),
        ("", "INFO"),
        ("bananas", "INFO"),
        ("11", "INFO"),
    ],
)
def test_log_level_comes_from_the_environment(monkeypatch, value, expected):
    """LOG_LEVEL is listed in .env.example as a developer knob and in
    docs/reference/environment.md as a variable this package reads. It has
    to actually move the level, and an unrecognised value has to fall back
    rather than abort - a typo in a log level must never stop someone
    inspecting the fleet."""
    monkeypatch.setenv("LOG_LEVEL", value)
    assert cli._env_log_level() == expected


def test_an_unset_log_level_is_the_default(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert cli._env_log_level() == cli.DEFAULT_LOG_LEVEL


def test_main_passes_the_environment_level_to_the_logger(monkeypatch):
    """The wiring this whole knob depends on: main() used to call
    configure_logging with a hardcoded INFO, so LOG_LEVEL=DEBUG changed
    nothing and the absence of debug output read as 'nothing to see'."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "configure_logging", lambda **kw: seen.update(kw))
    monkeypatch.setattr(cli, "app", lambda: None)
    cli.main()
    assert seen["level"] == "DEBUG"


def test_the_debug_flag_wins_over_the_environment(monkeypatch):
    """`--debug` is the more deliberate of the two, so it has to override an
    environment that says otherwise - otherwise a LOG_LEVEL exported once in
    a shell profile silently disables the flag."""
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    levels: list[Any] = []
    monkeypatch.setattr(cli, "configure_logging", lambda **kw: levels.append(kw.get("level")))
    _summary_ok(monkeypatch)
    assert runner.invoke(cli.app, ["--debug", "summary"]).exit_code == 0
    assert levels == ["DEBUG"], "the callback reconfigures to DEBUG"


def test_no_debug_flag_leaves_the_level_alone(monkeypatch):
    """Without the flag the callback must not reconfigure: doing so would
    overwrite whatever main() already set from LOG_LEVEL."""
    levels: list[Any] = []
    monkeypatch.setattr(cli, "configure_logging", lambda **kw: levels.append(kw.get("level")))
    _summary_ok(monkeypatch)
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert levels == []


class _DebugLog:
    """Captures the debug lines a command emits, without going through a
    loguru sink - the assertion is about what the code decided to say."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str, *args: Any) -> None:
        self.messages.append(message.format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


def _debug_log(monkeypatch) -> _DebugLog:
    captured = _DebugLog()
    monkeypatch.setattr(cli, "log", captured)
    return captured


def test_summary_says_at_debug_why_the_port_map_is_missing(monkeypatch):
    """The failure is swallowed so the table still prints - which means the
    only symptom is every `down` row losing its port, with nothing on screen
    saying why. The reason has to survive somewhere, and DEBUG is where."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "configured_ports", raises=core.UqfStackError("no process.csv"))
    _patch(monkeypatch, "heartbeat_states", result={})
    _patch(monkeypatch, "summary_rows", result=[])
    captured = _debug_log(monkeypatch)
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert any("no process.csv" in m for m in captured.messages)


def test_summary_says_at_debug_why_heartbeats_are_missing(monkeypatch):
    """Two different faults print the same sentence at INFO - monitor1 absent
    from the registry, and monitor1 declared but unreachable. Only the debug
    line distinguishes them, which is the difference between restarting a
    process and chasing a connection."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "configured_ports", result={})
    _patch(monkeypatch, "heartbeat_states", raises=core.UqfStackError("monitor1 is not declared"))
    _patch(monkeypatch, "summary_rows", result=[])
    captured = _debug_log(monkeypatch)
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert any("monitor1 is not declared" in m for m in captured.messages)


def test_summary_distinguishes_unreachable_from_undeclared_at_debug(monkeypatch):
    """A None return is monitor1 declared but not answering - a different
    fault from the raise above, and it must not be reported as that one."""
    _patch(monkeypatch, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, "configured_ports", result={})
    _patch(monkeypatch, "heartbeat_states", result=None)
    _patch(monkeypatch, "summary_rows", result=[])
    captured = _debug_log(monkeypatch)
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert any("monitor1 not reached" in m for m in captured.messages)


def test_summary_counts_the_rows_it_parsed_at_debug(monkeypatch):
    """`torq.sh summary` emitting rows the parser then drops is silent
    otherwise: the table just looks short."""
    _summary_ok(monkeypatch, rows=[_row(), _row(Process="hdb1", Status="down")])
    captured = _debug_log(monkeypatch)
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert any("1 up, 1 down" in m for m in captured.messages)


def test_a_skipped_dependency_warning_keeps_its_reason(monkeypatch):
    """loguru formats with str.format, so the `%s` this line used to carry
    printed literally and dropped the exception - the one line explaining
    why the warning was skipped explained nothing."""
    _patch(monkeypatch, "summary", raises=RuntimeError("fleet unreachable"))
    _patch(monkeypatch, "start", result=Completed())
    captured = _debug_log(monkeypatch)
    assert runner.invoke(cli.app, ["start", "rdb1"]).exit_code == 0
    assert any("fleet unreachable" in m for m in captured.messages)
    assert not any("%s" in m for m in captured.messages)
