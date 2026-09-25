"""Tests for the `uqs` command surface (cli/entry.py).

WHY THIS FILE EXISTS. `scripts/test.py coverage` reported cli/entry.py at 0% of
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

Every test monkeypatches `core`, so nothing here starts a process,
opens a socket or touches output/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from typer.testing import CliRunner

# The CLI is a package of command families now (see cli/entry.py): the `cli`
# package itself still exposes the assembled `app` and `main`, and each
# command's own helpers live in the family module that registers it.
from uqs import cli
from uqs import paths as stack_paths
from uqs.checks import schema_view
from uqs.cli import config, create, inspect, lifecycle, shared, summary, summary_graph
from uqs.external import crypto
from uqs.external.crypto import CRYPTO_FILLS_RECORDER_TABLE, CRYPTO_REAL_FILLS_RECORDER_TABLE
from uqs.model.pipeline_edges import LICENCE_CONNECTION_LIMIT
from uqs.paths import UqsError, UqsPaths
from uqs.stack import alive, listing, probe, runtime
from uqs.stack import logs as stack_logs
from uqs.stack import procs as stack_procs
from uqs.stack.listing import LISTABLE_KINDS, SUMMARY_COLUMNS, SUMMARY_GRAPH_COLUMNS

runner = CliRunner()

#: Grabbed at import so `_known_procs` can restore it after the autouse
#: fixture stubs the name out.
_real_assert_known_procnames = stack_procs.assert_known_procnames


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
    """Stands in for UqsPaths. No test here cares what it points at, but
    it has to have the attributes the commands read - a bare sentinel string
    made `summary`'s debug line an AttributeError in tests only."""

    torqdata: str = "TORQDATA"


@pytest.fixture(autouse=True)
def _no_real_paths(monkeypatch):
    """`_paths()` reads the filesystem and the vendored tree. Every command
    calls it, and no test here cares what it returns.

    The lifecycle guard is stubbed out for the same reason. It reads the real
    process table, which `_Paths` cannot stand in for, and most tests here
    drive commands with invented names (`p0`, `p1`, ... for the cap
    arithmetic) that no registry would know. Tests that are ABOUT the guard
    take the `_known_procs` fixture, which puts the real one back against a
    small stub registry.
    """
    monkeypatch.setattr(stack_paths, "default_paths", _Paths)
    monkeypatch.setattr(stack_procs, "assert_known_procnames", lambda _paths, _procs: None)


def _patch(monkeypatch, module: ModuleType, name: str, **kw) -> Recorder:
    """Replace `module.name` with a recorder. Patched on the module that
    DEFINES it, which is where every caller looks it up."""
    rec = Recorder(**kw)
    monkeypatch.setattr(module, name, rec)
    return rec


# ------------------------------------------------- the streaming commands


@pytest.mark.parametrize(
    ("command", "fn"), [("start", "start"), ("stop", "stop"), ("restart", "restart")]
)
def test_a_lifecycle_command_calls_its_core_function(monkeypatch, command, fn):
    rec = _patch(monkeypatch, runtime, fn, result=Completed())
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 0
    assert rec.args[1] == "all", "the default process selector is 'all'"


@pytest.fixture
def _known_procs(monkeypatch):
    """Put the real guard back, against a two-row stub registry.

    Undoes the autouse stub above. `_Paths` cannot satisfy the real process
    table reader, so `list_process_names` is what gets faked - the guard's
    decision is what is under test, not the table's contents.
    """
    monkeypatch.setattr(stack_procs, "assert_known_procnames", _real_assert_known_procnames)
    monkeypatch.setattr(stack_procs, "list_process_names", lambda _paths: ["rdb1", "stp1"])


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print", "up"])
def test_an_unknown_process_is_refused_before_anything_runs(monkeypatch, _known_procs, command):
    """A typo must not reach torq.sh.

    It used to: `uqs start xyz` printed a licence-cap warning whose count
    included the nonexistent process, then the vendored script's own
    `hostname: illegal option` noise, then `xyz failed - unavailable
    processname` - and exited **0**. The exit code is the part that mattered,
    because it made a typo indistinguishable from a successful start to
    anything scripting this.

    The exit code is asserted here; the wording of the refusal is asserted in
    test_the_refusal_names_the_typo_and_the_alternatives, against the error
    itself. `_die` reports through loguru, whose handler holds the stderr it
    was built with, so neither capsys nor capfd sees it through CliRunner's
    stream swap - asserting on the raise is both simpler and more direct.
    """
    for fn in ("start", "stop", "restart", "print_procs"):
        _patch(monkeypatch, runtime, fn, result=Completed())
    result = runner.invoke(cli.app, [command, "definitely_not_a_process"])
    assert result.exit_code == 1, "a typo must be a failure, not a silent success"


def test_the_refusal_names_the_typo_and_the_alternatives(_known_procs):
    """The message has to be actionable: what was wrong, and what was valid."""
    with pytest.raises(UqsError) as excinfo:
        # cast: `_known_procs` stubs the only thing that reads `paths`, so the
        # stub never has to satisfy UqsPaths at runtime.
        stack_procs.assert_known_procnames(cast("UqsPaths", _Paths()), "rdb1 xyz")
    message = str(excinfo.value)
    assert "xyz" in message
    assert "rdb1" in message, "the known names are what makes the error useful"
    assert "Nothing was started or stopped" in message


@pytest.mark.parametrize("selector", ["all", " all ", "rdb1", "rdb1 stp1"])
def test_valid_selectors_raise_nothing(_known_procs, selector):
    """`all` is torq.sh's own word for the startwithall rows, not a process,
    so it must pass the guard without being looked up."""
    stack_procs.assert_known_procnames(cast("UqsPaths", _Paths()), selector)


@pytest.mark.parametrize("command", ["start", "stop", "restart"])
def test_the_guard_does_not_reach_the_stack(monkeypatch, _known_procs, command):
    """Refusal happens before the vendored script is invoked at all."""
    rec = _patch(monkeypatch, runtime, command, result=Completed())
    runner.invoke(cli.app, [command, "definitely_not_a_process"])
    assert rec.calls == [], "nothing should have been handed to torq.sh"


@pytest.mark.parametrize("selector", ["all", "rdb1"])
def test_known_selectors_still_pass_through(monkeypatch, _known_procs, selector):
    """`all` is torq.sh's own word and must reach it unexpanded."""
    rec = _patch(monkeypatch, runtime, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", selector])
    assert result.exit_code == 0
    assert rec.args[1] == selector


def _up(monkeypatch, args: list[str], already: set[str] | Exception, rows=()):
    """Run `uqs up` with the stack faked: the follow starts, then returns as a
    Ctrl-C would. Returns the start and stop recorders and the follow call."""
    start = _patch(monkeypatch, runtime, "start", result=Completed())
    stop = _patch(monkeypatch, runtime, "stop", result=Completed())
    monkeypatch.setattr(stack_logs, "resolve_procnames", lambda paths, names: names.split())
    monkeypatch.setattr(listing, "list_items", lambda *a, **kw: list(rows))

    def running(port):
        if isinstance(already, Exception):
            raise already
        return already

    monkeypatch.setattr(lifecycle, "_running", running)
    follow: dict[str, Any] = {}

    def fake_follow(paths, procnames, start_fn, min_level=None):
        follow.update(procnames=procnames, min_level=min_level)
        start_fn()

    monkeypatch.setattr(stack_logs, "follow_during", fake_follow)
    result = runner.invoke(cli.app, ["up", *args])
    return result, start, stop, follow


def test_up_starts_follows_and_on_ctrl_c_stops_only_what_it_started(monkeypatch):
    result, start, stop, follow = _up(monkeypatch, ["rdb1", "fxpositions1"], already={"rdb1"})
    assert result.exit_code == 0, result.output
    assert start.args[1] == "rdb1 fxpositions1"
    assert follow["procnames"] == ["rdb1", "fxpositions1"], "follows everything it starts"
    assert stop.args[1] == "fxpositions1", "rdb1 was already running, so it is left up"


def test_up_stops_everything_it_was_asked_for_when_it_cannot_tell_what_ran(monkeypatch):
    result, _, stop, _ = _up(monkeypatch, ["rdb1", "fxpositions1"], already=UqsError("no stack"))
    assert result.exit_code == 0, result.output
    assert stop.args[1] == "rdb1 fxpositions1"


def test_up_leaves_a_fleet_that_was_already_running_alone(monkeypatch):
    result, _, stop, _ = _up(monkeypatch, ["rdb1"], already={"rdb1"})
    assert result.exit_code == 0, result.output
    assert stop.calls == []


def test_up_all_follows_the_startwithall_processes(monkeypatch):
    rows = [
        {"procname": "rdb1", "startwithall": "1"},
        {"procname": "tap1", "startwithall": "0"},
        {"procname": "fxfeed1", "startwithall": "1"},
    ]
    result, start, stop, follow = _up(monkeypatch, [], already=set(), rows=rows)
    assert result.exit_code == 0, result.output
    assert start.args[1] == "all"
    assert follow["procnames"] == ["rdb1", "fxfeed1"]
    assert stop.args[1] == "rdb1 fxfeed1", (
        "never a bare `stop all`: that stops what it did not start"
    )


def test_a_failed_start_still_stops_what_it_started_and_keeps_its_exit_code(monkeypatch):
    """_run_streaming always exits the command, so `up` must not start through
    it - the first version did, and never streamed or stopped anything."""
    start = _patch(monkeypatch, runtime, "start", result=Completed(returncode=3))
    stop = _patch(monkeypatch, runtime, "stop", result=Completed())
    monkeypatch.setattr(stack_logs, "resolve_procnames", lambda paths, names: names.split())
    monkeypatch.setattr(lifecycle, "_running", lambda port: set())
    monkeypatch.setattr(
        stack_logs, "follow_during", lambda paths, procnames, start_fn, min_level=None: start_fn()
    )
    result = runner.invoke(cli.app, ["up", "rdb1"])
    assert start.calls, "it started"
    assert result.exit_code == 3, "the start's own failure is what the command reports"
    assert stop.args[1] == "rdb1"


def test_up_passes_the_level_filter_to_the_stream(monkeypatch):
    result, _, _, follow = _up(monkeypatch, ["rdb1", "--level", "WARNING"], already=set())
    assert result.exit_code == 0, result.output
    assert follow["min_level"] == "WARNING"


@pytest.mark.parametrize("command", ["start", "stop", "restart"])
def test_a_lifecycle_command_passes_the_process_names_through(monkeypatch, command):
    rec = _patch(monkeypatch, runtime, command, result=Completed())
    runner.invoke(cli.app, [command, "rdb1 hdb1"])
    assert rec.args[1] == "rdb1 hdb1"


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print"])
def test_the_port_option_reaches_core(monkeypatch, command):
    """A dropped --port silently drives the DEFAULT stack.

    That is the worst shape of wiring bug available here: every command
    succeeds, against the wrong fleet.
    """
    fn = "print_procs" if command == "print" else command
    rec = _patch(monkeypatch, runtime, fn, result=Completed())
    runner.invoke(cli.app, [command, "--port", "7000"])
    assert rec.kwargs["base_port"] == 7000


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print"])
def test_a_failing_subprocess_exit_code_survives(monkeypatch, command):
    """The property CI depends on. A non-zero torq.sh must not become a
    zero `uqs`."""
    fn = "print_procs" if command == "print" else command
    _patch(monkeypatch, runtime, fn, result=Completed(returncode=3))
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 3


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print"])
def test_a_refusal_exits_one_rather_than_raising(monkeypatch, command):
    fn = "print_procs" if command == "print" else command
    _patch(monkeypatch, runtime, fn, raises=UqsError("no such process"))
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 1
    assert not isinstance(result.exception, UqsError), "the error is handled, not raised"


# ---------------------------------------------------------------- summary


def _summary_ok(monkeypatch, *, rows=None, returncode=0):
    _patch(monkeypatch, runtime, "summary", result=Completed(returncode=returncode, stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    _patch(monkeypatch, listing, "heartbeat_states", result={})
    # The Responds probe opens a real socket to every up row's port.
    _patch(monkeypatch, probe, "probe_all", result={})
    rows = rows if rows is not None else []
    _patch(monkeypatch, listing, "summary_rows", result=rows)


def test_summary_renders_and_propagates_the_exit_code(monkeypatch):
    _summary_ok(monkeypatch, returncode=2)
    result = runner.invoke(cli.app, ["summary"])
    assert result.exit_code == 2


def test_summary_survives_a_port_map_it_cannot_build(monkeypatch):
    """Documented behaviour: a summary that still prints beats one that dies
    because the port map failed - the reported ports are unaffected."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", raises=UqsError("no process.csv"))
    _patch(monkeypatch, listing, "heartbeat_states", result={})
    rec = _patch(monkeypatch, listing, "summary_rows", result=[])
    result = runner.invoke(cli.app, ["summary"])
    assert result.exit_code == 0
    assert rec.args[1] == {}, "an unbuildable port map becomes empty, not fatal"


def test_summary_distinguishes_unreachable_monitoring_from_a_healthy_fleet(monkeypatch):
    """`None` heartbeats means monitor1 could not be reached - a gap in
    MONITORING, not a verdict on the fleet. The distinction is the whole
    point of the message, so it is pinned."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    _patch(monkeypatch, listing, "heartbeat_states", raises=UqsError("monitor1 down"))
    rec = _patch(monkeypatch, listing, "summary_rows", result=[])
    result = runner.invoke(cli.app, ["summary"])
    assert rec.args[2] is None, "unreachable monitoring is None, not an empty dict"
    assert "could not be reached" in result.stdout


def test_an_unreachable_but_running_monitor_is_not_called_stopped(monkeypatch):
    """The message used to assert "monitor1 is not running", which is one
    cause and not the common one. A monitor at its connection cap is running
    perfectly and still collecting heartbeats - it just has no slot left to
    answer on. Telling the reader to restart it sends them to fix a process
    with nothing wrong with it."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    _patch(monkeypatch, listing, "heartbeat_states", result=None)
    _patch(monkeypatch, listing, "summary_rows", result=[_row(Process="monitor1", Status="up")])
    result = runner.invoke(cli.app, ["summary"])
    assert "connection cap" in result.stdout
    assert "is not running" not in result.stdout
    assert "uqs start monitor1" not in result.stdout, "it is already running"


def test_a_genuinely_stopped_monitor_still_says_to_start_it(monkeypatch):
    """The other half: when monitor1 really is down, the advice that was
    always given is the right advice, and must not be lost to the new one."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    _patch(monkeypatch, listing, "heartbeat_states", result=None)
    _patch(monkeypatch, listing, "summary_rows", result=[_row(Process="monitor1", Status="down")])
    result = runner.invoke(cli.app, ["summary"])
    # Rich hard-wraps the panel text, so a phrase can straddle a newline.
    flat = " ".join(result.stdout.split())
    assert "is not running" in flat
    assert "uqs start monitor1" in flat


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
    for column in SUMMARY_GRAPH_COLUMNS:
        assert column in flat


def test_a_process_that_does_not_answer_is_shown_and_named(monkeypatch):
    """Up by PID but silent when asked - the case Status cannot show."""
    monkeypatch.setenv("COLUMNS", "220")
    _summary_ok(monkeypatch, rows=[_row(), _row(Process="hdb1", Port="6053")])
    _patch(
        monkeypatch,
        probe,
        "probe_all",
        result={"rdb1": probe.ProbeResult("ok", 4.0), "hdb1": probe.ProbeResult("timeout")},
    )
    result = runner.invoke(cli.app, ["summary", "--columns", "status"])
    assert result.exit_code == 0, result.output
    assert "Responds" in result.output
    assert "4ms" in result.output
    assert "did not answer within 0.5s" in result.output and "hdb1" in result.output


def test_probe_timeout_zero_skips_the_probe(monkeypatch):
    _summary_ok(monkeypatch, rows=[_row()])
    rec = _patch(monkeypatch, probe, "probe_all", result={})
    result = runner.invoke(cli.app, ["summary", "--columns", "status", "--probe-timeout", "0"])
    assert result.exit_code == 0, result.output
    assert rec.calls == []


def test_columns_status_gives_back_the_narrow_table(monkeypatch):
    """The escape hatch for an 80-column terminal, and the reason showing the
    graph by default is safe."""
    narrow = [*SUMMARY_COLUMNS, "Responds"]
    assert summary._resolve_columns("status") == narrow
    assert summary._resolve_columns("STATUS") == narrow


def test_columns_all_adds_the_graph(monkeypatch):
    # Nine columns do not fit the 80-column default the runner reports, and
    # Rich elides the headers rather than the data - so the width is set here
    # to assert on the columns rather than on Rich's truncation of them.
    monkeypatch.setenv("COLUMNS", "220")
    _summary_ok(monkeypatch, rows=[_row()])
    result = runner.invoke(cli.app, ["summary", "--columns", "all"])
    flat = " ".join(result.stdout.split())
    for column in SUMMARY_GRAPH_COLUMNS:
        assert column in flat


def test_columns_are_matched_case_insensitively_and_kept_in_order(monkeypatch):
    """The names have a space and a capital in them ("Depends on"), so an
    exact-match-only option would be unusable from a shell."""
    assert summary._resolve_columns("outputs,process") == ["Outputs", "Process"]
    assert summary._resolve_columns("PROCESS") == ["Process"]


def test_a_repeated_column_is_not_rendered_twice(monkeypatch):
    assert summary._resolve_columns("Process,process,Process") == ["Process"]


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
    cell = summary_graph.graph_cell(["a_table", "b_table", "c_table"])
    lines = cell.split("\n")
    assert lines == ["a_table, b_table,", "c_table"]
    assert all("_" not in line[-1:] for line in lines), "no name split mid-word"


def test_a_short_graph_cell_does_not_wrap(monkeypatch):
    assert "\n" not in summary_graph.graph_cell(["one", "two"])


def test_an_empty_graph_cell_is_a_dash_not_a_blank(monkeypatch):
    """ "declares no inputs" and "this column has nothing to say" look
    identical as a blank, and the first is a real fact about a feed."""
    assert "-" in summary_graph.graph_cell([])


def test_the_graph_columns_come_from_the_declared_pipelines(monkeypatch):
    """Derived from the same Pipeline declarations verify_pipeline_edges
    checks, so a row here cannot claim an edge the build would reject."""
    rows = [_row(Process="posbook1")]
    summary_graph.attach_graph_columns(rows)
    assert "executions" in rows[0]["Inputs"]
    assert "position" in rows[0]["Outputs"]
    assert "executions1" in rows[0]["Depends on"]


def test_a_process_with_no_declared_edges_gets_dashes(monkeypatch):
    """A vendored TorQ process has no Pipeline entry and so no declared
    edges. It must render, not raise."""
    rows = [_row(Process="hdb1")]
    summary_graph.attach_graph_columns(rows)
    assert all(rows[0][c] == "[dim]-[/]" for c in SUMMARY_GRAPH_COLUMNS)


# -------------------------------------------------------- the timeout


def test_summary_passes_a_default_timeout_to_both_blocking_calls(monkeypatch):
    """`summary` is the command you run when something is already wrong, which
    makes it the worst thing to hang. Both of its blocking steps could: the
    torq.sh subprocess had no timeout at all, and the heartbeat query talks to
    a process that at its connection cap accepts and then goes quiet."""
    rec_summary = _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    rec_hb = _patch(monkeypatch, listing, "heartbeat_states", result={})
    _patch(monkeypatch, listing, "summary_rows", result=[])
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert rec_summary.kwargs["timeout"] is not None
    assert rec_summary.kwargs["timeout"] <= summary.SUMMARY_TIMEOUT_SECONDS
    assert 0 < rec_hb.kwargs["timeout"] <= summary.SUMMARY_TIMEOUT_SECONDS


def test_the_timeout_is_one_budget_not_one_per_call(monkeypatch):
    """Two calls given ten seconds each is a twenty-second hang, which is not
    what anyone means by a ten-second timeout. The second call gets what the
    first left behind."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    rec_hb = _patch(monkeypatch, listing, "heartbeat_states", result={})
    _patch(monkeypatch, listing, "summary_rows", result=[])
    # A monotonic clock that jumps 4s per reading, so the budget visibly
    # drains between the two calls without the test sleeping.
    ticks = iter([0.0, 4.0, 8.0, 12.0, 16.0, 20.0])
    monkeypatch.setattr(summary.time, "monotonic", lambda: next(ticks))
    runner.invoke(cli.app, ["summary", "--timeout", "10"])
    assert rec_hb.kwargs["timeout"] < 10, "the heartbeat query gets the remainder"


def test_a_zero_timeout_waits_forever(monkeypatch):
    """The documented escape hatch, and it has to reach BOTH calls as their
    own 'no limit' spelling - None for subprocess, 0 for kola."""
    rec_summary = _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    rec_hb = _patch(monkeypatch, listing, "heartbeat_states", result={})
    _patch(monkeypatch, listing, "summary_rows", result=[])
    runner.invoke(cli.app, ["summary", "--timeout", "0"])
    assert rec_summary.kwargs["timeout"] is None
    assert rec_hb.kwargs["timeout"] == 0


def test_an_exhausted_budget_never_hands_out_zero(monkeypatch):
    """kola refuses a zero duration outright and subprocess reads <=0 as
    already-expired, so a spent budget would raise something less legible
    than the timeout it actually is."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    rec_hb = _patch(monkeypatch, listing, "heartbeat_states", result={})
    _patch(monkeypatch, listing, "summary_rows", result=[])
    ticks = iter([0.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(summary.time, "monotonic", lambda: next(ticks))
    runner.invoke(cli.app, ["summary", "--timeout", "10"])
    assert rec_hb.kwargs["timeout"] >= 1


def test_a_timed_out_summary_exits_one_rather_than_hanging(monkeypatch):
    _patch(monkeypatch, runtime, "summary", raises=UqsError("did not finish within 10s"))
    assert runner.invoke(cli.app, ["summary"]).exit_code == 1


# --------------------------------------------------- the connection cap


def test_a_start_past_the_licence_cap_warns(monkeypatch):
    """Past the cap the licence, not the configuration, decides what runs -
    and it does so silently: the extra handle is reset, the process wedges in
    its retry loop, and `summary` still reports it `up` because that is a PID
    check."""
    over = {f"p{i}" for i in range(LICENCE_CONNECTION_LIMIT + 1)}
    _patch(monkeypatch, alive, "running", result=over)
    _patch(monkeypatch, runtime, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "rdb1"])
    assert result.exit_code == 0
    assert "past the" in result.stdout
    assert str(LICENCE_CONNECTION_LIMIT) in result.stdout


def test_a_start_inside_the_cap_is_silent(monkeypatch):
    """A warning on every start would be noise, and noise is how a real one
    gets missed."""
    _patch(monkeypatch, alive, "running", result={"rdb1"})
    _patch(monkeypatch, runtime, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "rdb1"])
    assert "concurrent connections" not in result.stdout


def test_the_cap_warning_never_blocks_a_start(monkeypatch):
    """Advisory only. A warning that cannot be produced - the fleet is
    unreachable, the registry cannot be read - must not stop a start."""
    _patch(monkeypatch, alive, "running", raises=RuntimeError("fleet unreachable"))
    _patch(monkeypatch, runtime, "start", result=Completed())
    assert runner.invoke(cli.app, ["start", "rdb1"]).exit_code == 0


def test_summary_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, runtime, "summary", raises=UqsError("no stack"))
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
    _patch(monkeypatch, listing, "list_items", result=[{"procname": "rdb1"}])
    rec = _patch(monkeypatch, runtime, "export_table")
    target = tmp_path / "out.csv"
    result = runner.invoke(cli.app, ["list", "processes", "--export", str(target)])
    assert result.exit_code == 0
    assert rec.args[1] == target
    assert "exported to" in result.stdout


def test_nothing_is_exported_without_the_option(monkeypatch):
    _patch(monkeypatch, listing, "list_items", result=[{"procname": "rdb1"}])
    rec = _patch(monkeypatch, runtime, "export_table")
    runner.invoke(cli.app, ["list", "processes"])
    assert rec.calls == []


def test_a_failed_export_reports_rather_than_losing_the_output(monkeypatch, tmp_path):
    """The rows were already printed by the time the export runs. An
    unwritable path must say so, not take the whole command down with a
    traceback."""
    _patch(monkeypatch, listing, "list_items", result=[{"procname": "rdb1"}])
    _patch(monkeypatch, runtime, "export_table", raises=UqsError("unsupported suffix"))
    result = runner.invoke(cli.app, ["list", "processes", "--export", str(tmp_path / "x.txt")])
    assert result.exit_code == 1


# ------------------------------------------------------------------ query


def test_query_passes_the_expression_and_connection_through(monkeypatch):
    rec = _patch(monkeypatch, runtime, "query", result="RESULT")
    result = runner.invoke(
        cli.app, ["query", "select from t", "--port", "6052", "--user", "u", "--passwd", "p"]
    )
    assert result.exit_code == 0
    assert rec.args[:2] == ("select from t", 6052)
    assert rec.kwargs["user"] == "u"
    assert rec.kwargs["passwd"] == "p"


def test_query_turns_a_driver_error_into_an_exit_code(monkeypatch):
    """kola raises its own exception types, which are NOT UqsError. The
    command catches Exception for that reason, and this pins it: a connect
    failure must be an exit code, not a traceback in the user's face."""
    _patch(monkeypatch, runtime, "query", raises=RuntimeError("connection refused"))
    result = runner.invoke(cli.app, ["query", "1+1", "--port", "6052"])
    assert result.exit_code == 1


# ----------------------------------------------------------------- schema


def test_schema_resolves_a_named_process_to_a_port(monkeypatch):
    resolve = _patch(monkeypatch, schema_view, "resolve_port", result=6052)
    overview = _patch(monkeypatch, schema_view, "overview", result=[])
    runner.invoke(cli.app, ["schema", "--proc", "hdb1"])
    assert resolve.args[1] == "hdb1"
    assert overview.args[0] == 6052, "the resolved port is what gets queried"


def test_an_explicit_port_skips_resolution_entirely(monkeypatch):
    """--port is documented as reading that port DIRECTLY instead of
    resolving --proc, so resolution must not happen at all."""
    resolve = _patch(monkeypatch, schema_view, "resolve_port", result=9999)
    overview = _patch(monkeypatch, schema_view, "overview", result=[])
    runner.invoke(cli.app, ["schema", "--port", "1234"])
    assert resolve.calls == []
    assert overview.args[0] == 1234


def test_schema_with_a_table_describes_its_columns(monkeypatch):
    _patch(monkeypatch, schema_view, "resolve_port", result=6052)
    _patch(monkeypatch, schema_view, "match_tables", result=["quotes"])
    cols = _patch(
        monkeypatch,
        schema_view,
        "columns",
        result=[{"column": "sym", "type": "symbol", "q": "s", "attribute": "grouped"}],
    )
    result = runner.invoke(cli.app, ["schema", "quotes"])
    assert result.exit_code == 0
    assert cols.args[0] == "quotes"
    assert "sym" in result.stdout


def test_a_pattern_matching_nothing_exits_one_and_names_what_exists(monkeypatch):
    _patch(monkeypatch, schema_view, "resolve_port", result=6052)
    _patch(monkeypatch, schema_view, "match_tables", result=[])
    _patch(monkeypatch, schema_view, "table_names", result=["quotes", "trades"])
    result = runner.invoke(cli.app, ["schema", "nope*"])
    assert result.exit_code == 1


def test_an_unknown_process_exits_one(monkeypatch):
    _patch(monkeypatch, schema_view, "resolve_port", raises=UqsError("rbd1 is not declared"))
    assert runner.invoke(cli.app, ["schema", "--proc", "rbd1"]).exit_code == 1


def test_schema_turns_a_driver_error_into_an_exit_code(monkeypatch):
    _patch(monkeypatch, schema_view, "resolve_port", result=6052)
    _patch(monkeypatch, schema_view, "overview", raises=RuntimeError("connection refused"))
    assert runner.invoke(cli.app, ["schema"]).exit_code == 1


# ---------------------------------------------------------------- config


def test_config_get_prints_one_field_when_asked(monkeypatch):
    _patch(
        monkeypatch, stack_procs, "get_process_config", result={"procname": "rdb1", "port": "6052"}
    )
    result = runner.invoke(cli.app, ["config-get", "rdb1", "port"])
    assert result.exit_code == 0
    assert "6052" in result.stdout


def test_config_get_resolves_placeholders_unless_raw_is_given(monkeypatch):
    """--raw is the difference between "will listen on 6052" and
    "{KDBBASEPORT}+2". Wiring it backwards would show the wrong one with no
    other symptom."""
    rec = _patch(monkeypatch, stack_procs, "get_process_config", result={})
    runner.invoke(cli.app, ["config-get", "rdb1"])
    assert rec.kwargs["resolve"] is True
    runner.invoke(cli.app, ["config-get", "rdb1", "--raw"])
    assert rec.kwargs["resolve"] is False


def test_config_set_reports_what_it_wrote(monkeypatch):
    rec = _patch(monkeypatch, stack_procs, "set_process_config")
    result = runner.invoke(cli.app, ["config-set", "rdb1", "startwithall", "1"])
    assert result.exit_code == 0
    assert rec.args[1:] == ("rdb1", "startwithall", "1")


def test_config_set_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, stack_procs, "set_process_config", raises=UqsError("unknown field"))
    assert runner.invoke(cli.app, ["config-set", "rdb1", "nope", "1"]).exit_code == 1


def test_list_with_no_kind_shows_the_kinds_rather_than_failing(monkeypatch):
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    for kind in LISTABLE_KINDS:
        assert kind in result.stdout


def test_list_renders_the_items_of_a_kind(monkeypatch):
    _patch(monkeypatch, listing, "list_items", result=[{"procname": "rdb1", "port": "6052"}])
    result = runner.invoke(cli.app, ["list", "processes"])
    assert result.exit_code == 0
    assert "rdb1" in result.stdout


def test_an_unknown_kind_exits_one(monkeypatch):
    _patch(monkeypatch, listing, "list_items", raises=UqsError("unknown kind"))
    assert runner.invoke(cli.app, ["list", "bogus"]).exit_code == 1


# -------------------------------------------------------- list --sort


def _procs() -> list[dict[str, str]]:
    return [
        {"procname": "sortworker2", "port": "6067"},
        {"procname": "stp1", "port": "659"},
        {"procname": "Arbitrage1", "port": "6100"},
    ]


def test_a_numeric_column_sorts_numerically_not_lexicographically():
    """`port` is a string like "6051". Sorted as text, "6100" comes before
    "659" - which looks like the sort silently did nothing on the one column
    most worth sorting."""
    order = [i["port"] for i in config._sorted_items(_procs(), "port", reverse=False)]
    assert order == ["659", "6067", "6100"]


def test_a_text_column_sorts_case_insensitively():
    """ "Arbitrage1" must not sort before every lowercase name just for its
    capital - the reader is looking up a name, not an ordinal."""
    order = [i["procname"] for i in config._sorted_items(_procs(), "procname", reverse=False)]
    assert order == ["Arbitrage1", "sortworker2", "stp1"]


def test_reverse_flips_the_order():
    order = [i["port"] for i in config._sorted_items(_procs(), "port", reverse=True)]
    assert order == ["6100", "6067", "659"]


def test_the_column_name_is_matched_case_insensitively():
    assert config._sorted_items(_procs(), "PORT", reverse=False)[0]["port"] == "659"


def test_empty_cells_group_at_one_end_rather_than_sorting_as_empty_string():
    """A process with no override set is not "before aaa", it is absent -
    and a blank interleaved among real values reads as data."""
    items = [{"v": "b"}, {"v": ""}, {"v": "a"}]
    assert [i["v"] for i in config._sorted_items(items, "v", reverse=False)] == ["a", "b", ""]


def test_sorting_is_a_no_op_without_the_option():
    items = _procs()
    assert config._sorted_items(items, None, reverse=False) == items


def test_sorting_an_empty_listing_does_not_look_up_columns():
    """There is no first row to read column names from, and a kind with no
    items is a legitimate result - `overrides` is empty until something is
    set."""
    assert config._sorted_items([], "anything", reverse=False) == []


def test_an_unsortable_column_names_the_real_ones(monkeypatch):
    """The columns differ per kind, so there is no fixed set to check
    against - a typo has to be answered with the columns this listing
    actually produced."""
    _patch(monkeypatch, listing, "list_items", result=_procs())
    result = runner.invoke(cli.app, ["list", "processes", "--sort", "bogus"])
    assert result.exit_code == 1


def test_the_sorted_order_reaches_the_export(monkeypatch):
    """An exported CSV that disagreed with what was on screen would be the
    worst of both."""
    _patch(monkeypatch, listing, "list_items", result=_procs())
    rec = _patch(monkeypatch, runtime, "export_table", result=None)
    result = runner.invoke(
        cli.app, ["list", "processes", "--sort", "port", "--export", "/tmp/x.csv"]
    )
    assert result.exit_code == 0
    assert [i["port"] for i in rec.args[0]] == ["659", "6067", "6100"]


# ------------------------------------------------------------------- logs


def test_logs_defaults_to_a_bounded_tail_not_a_follow(monkeypatch):
    """`follow` blocks until Ctrl-C. Defaulting to it would hang any script
    that ran `uqs logs`."""
    recent = _patch(monkeypatch, stack_logs, "print_recent_logs")
    follow = _patch(monkeypatch, stack_logs, "follow_logs")
    result = runner.invoke(cli.app, ["logs"])
    assert result.exit_code == 0
    assert follow.calls == []
    assert recent.kwargs["lines"] == 20


def test_follow_selects_the_streaming_path(monkeypatch):
    recent = _patch(monkeypatch, stack_logs, "print_recent_logs")
    follow = _patch(monkeypatch, stack_logs, "follow_logs")
    runner.invoke(cli.app, ["logs", "--follow"])
    assert recent.calls == []
    assert len(follow.calls) == 1


def test_the_level_filter_reaches_core(monkeypatch):
    rec = _patch(monkeypatch, stack_logs, "print_recent_logs")
    runner.invoke(cli.app, ["logs", "--level", "WARNING", "--lines", "5"])
    assert rec.kwargs["min_level"] == "WARNING"
    assert rec.kwargs["lines"] == 5


# -------------------------------------------------------------- multitail


def test_multitail_passes_its_options_to_core_and_execs(monkeypatch):
    build = _patch(monkeypatch, stack_logs, "multitail_command", result=["multitail", "x"])
    run = _patch(monkeypatch, stack_logs, "run_multitail")
    result = runner.invoke(
        cli.app, ["multitail", "rdb1 stp1", "--stream", "err", "-c", "2", "-n", "7"]
    )
    assert result.exit_code == 0
    assert build.args[1] == "rdb1 stp1"
    assert build.kwargs == {"stream": "err", "columns": 2, "lines": 7}
    assert run.args == (["multitail", "x"],)


def test_multitail_print_shows_the_command_and_runs_nothing(monkeypatch):
    _patch(monkeypatch, stack_logs, "multitail_command", result=["multitail", "-t", "a b"])
    run = _patch(monkeypatch, stack_logs, "run_multitail")
    result = runner.invoke(cli.app, ["multitail", "--print"])
    assert result.exit_code == 0
    assert "multitail -t 'a b'" in result.stdout
    assert run.calls == []


def test_a_multitail_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, stack_logs, "multitail_command", raises=UqsError("no such process"))
    assert runner.invoke(cli.app, ["multitail", "nope"]).exit_code == 1


# -------------------------------------------------------------------- raw


def test_raw_passes_every_extra_argument_through_verbatim(monkeypatch):
    """`raw` exists to reach torq.sh verbs this CLI does not model. Dropping
    or reordering the arguments would make it useless in a way no other
    command's failure resembles."""
    rec = _patch(monkeypatch, runtime, "run_torq_sh", result=Completed())
    runner.invoke(cli.app, ["raw", "--", "qcon", "gateway1", "admin:admin"])
    assert rec.args[1] == ["qcon", "gateway1", "admin:admin"]


def test_raw_propagates_the_exit_code(monkeypatch):
    _patch(monkeypatch, runtime, "run_torq_sh", result=Completed(returncode=7))
    assert runner.invoke(cli.app, ["raw", "--", "debug", "rdb1"]).exit_code == 7


# ------------------------------------------------------------------ clean


def test_clean_delegates(monkeypatch):
    rec = _patch(monkeypatch, stack_paths, "clean", result=[])
    assert runner.invoke(cli.app, ["clean"]).exit_code == 0
    assert len(rec.calls) == 1


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], {"match": None, "dry_run": False}),
        (["--dry-run"], {"match": None, "dry_run": True}),
        (["-n"], {"match": None, "dry_run": True}),
        (["--match", "^logs$"], {"match": "^logs$", "dry_run": False}),
        (["--match", "^logs$", "-n"], {"match": "^logs$", "dry_run": True}),
    ],
)
def test_clean_passes_its_flags_through(monkeypatch, argv, expected):
    """The flags have to arrive as given - a dropped --dry-run deletes."""
    rec = _patch(monkeypatch, stack_paths, "clean", result=[])
    assert runner.invoke(cli.app, ["clean", *argv]).exit_code == 0
    assert rec.calls[-1][1] == expected


def test_clean_reports_what_it_removed(monkeypatch):
    rec_result = [(Path("/data/logs"), 800), (Path("/data/tplogs"), 200)]
    _patch(monkeypatch, stack_paths, "clean", result=rec_result)
    result = runner.invoke(cli.app, ["clean", "--dry-run"])
    assert result.exit_code == 0
    assert "would remove 2 entries" in result.output
    assert "logs" in result.output


# ----------------------------------------------------------------- crypto


def test_crypto_start_splits_the_comma_separated_lists(monkeypatch):
    """The CLI takes comma-separated strings and core takes tuples, so the
    split happens here - the one piece of real logic in this file."""
    rec = _patch(monkeypatch, crypto, "start_crypto_recorder", result=4242)
    result = runner.invoke(cli.app, ["crypto", "start", "--venues", "a, b ,c", "--symbols", "X,Y"])
    assert result.exit_code == 0
    assert rec.kwargs["venues"] == ("a", "b", "c"), "whitespace around a name is trimmed"
    assert rec.kwargs["symbols"] == ("X", "Y")
    assert "4242" in result.stdout


def test_crypto_start_drops_empty_entries_rather_than_passing_blanks(monkeypatch):
    """A trailing comma is an ordinary typo, and a blank venue name reaches
    the recorder as a connection attempt to nothing."""
    rec = _patch(monkeypatch, crypto, "start_crypto_recorder", result=1)
    runner.invoke(cli.app, ["crypto", "start", "--venues", "a,,b,"])
    assert rec.kwargs["venues"] == ("a", "b")


def test_crypto_stop_and_status_render(monkeypatch):
    _patch(monkeypatch, crypto, "stop_crypto_recorder")
    assert runner.invoke(cli.app, ["crypto", "stop"]).exit_code == 0
    _patch(monkeypatch, crypto, "crypto_recorder_status", result={"running": "yes", "pid": "42"})
    result = runner.invoke(cli.app, ["crypto", "status"])
    assert result.exit_code == 0
    assert "42" in result.stdout


def test_a_crypto_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, crypto, "start_crypto_recorder", raises=UqsError("no checkout"))
    assert runner.invoke(cli.app, ["crypto", "start"]).exit_code == 1


def test_crypto_fills_start_passes_the_oms_socket_and_poll_interval(monkeypatch):
    rec = _patch(monkeypatch, crypto, "start_crypto_fills_recorder", result=99)
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
    _patch(monkeypatch, crypto, "start_crypto_fills_recorder", result=1)
    result = runner.invoke(cli.app, ["crypto", "fills-start"])
    assert CRYPTO_FILLS_RECORDER_TABLE in result.stdout
    assert CRYPTO_REAL_FILLS_RECORDER_TABLE in result.stdout
    assert "SIMULATED" in result.stdout


def test_crypto_fills_stop_and_status_render(monkeypatch):
    _patch(monkeypatch, crypto, "stop_crypto_fills_recorder")
    assert runner.invoke(cli.app, ["crypto", "fills-stop"]).exit_code == 0
    _patch(monkeypatch, crypto, "crypto_fills_recorder_status", result={"running": "no"})
    result = runner.invoke(cli.app, ["crypto", "fills-status"])
    assert result.exit_code == 0
    assert "running" in result.stdout


def test_a_crypto_fills_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, crypto, "start_crypto_fills_recorder", raises=UqsError("no socket"))
    assert runner.invoke(cli.app, ["crypto", "fills-start"]).exit_code == 1
    _patch(monkeypatch, crypto, "stop_crypto_fills_recorder", raises=UqsError("not running"))
    assert runner.invoke(cli.app, ["crypto", "fills-stop"]).exit_code == 1


def test_a_crypto_stop_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, crypto, "stop_crypto_recorder", raises=UqsError("not running"))
    assert runner.invoke(cli.app, ["crypto", "stop"]).exit_code == 1


def test_a_logs_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, stack_logs, "print_recent_logs", raises=UqsError("no such process"))
    assert runner.invoke(cli.app, ["logs", "nope"]).exit_code == 1


def test_a_config_get_refusal_exits_one(monkeypatch):
    _patch(monkeypatch, stack_procs, "get_process_config", raises=UqsError("no such process"))
    assert runner.invoke(cli.app, ["config-get", "nope"]).exit_code == 1


# ------------------------------------------------------------------- app


def test_every_command_is_reachable_and_documented():
    """A command with no help text is one nobody can discover. `--help` also
    exercises every command's signature, so a malformed Annotated default
    fails here rather than the first time someone runs it."""
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "start",
        "stop",
        "restart",
        "summary",
        "query",
        "schema",
        "logs",
        "multitail",
        "raw",
    ):
        assert command in result.stdout


def test_main_configures_logging_before_running(monkeypatch):
    """Ordering, not decoration: a command that logged before logging was
    configured would write through a default handler nobody sees."""
    order: list[str] = []
    monkeypatch.setattr(cli.entry, "configure_logging", lambda **kw: order.append("configure"))
    monkeypatch.setattr(cli.entry, "app", lambda: order.append("app"))
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
    assert shared._env_log_level() == expected


def test_an_unset_log_level_is_the_default(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert shared._env_log_level() == shared.DEFAULT_LOG_LEVEL


def test_main_passes_the_environment_level_to_the_logger(monkeypatch):
    """The wiring this whole knob depends on: main() used to call
    configure_logging with a hardcoded INFO, so LOG_LEVEL=DEBUG changed
    nothing and the absence of debug output read as 'nothing to see'."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli.entry, "configure_logging", lambda **kw: seen.update(kw))
    monkeypatch.setattr(cli.entry, "app", lambda: None)
    cli.main()
    assert seen["level"] == "DEBUG"


def test_the_debug_flag_wins_over_the_environment(monkeypatch):
    """`--debug` is the more deliberate of the two, so it has to override an
    environment that says otherwise - otherwise a LOG_LEVEL exported once in
    a shell profile silently disables the flag."""
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    levels: list[Any] = []
    monkeypatch.setattr(shared, "configure_logging", lambda **kw: levels.append(kw.get("level")))
    _summary_ok(monkeypatch)
    assert runner.invoke(cli.app, ["--debug", "summary"]).exit_code == 0
    assert levels == ["DEBUG"], "the callback reconfigures to DEBUG"


def test_no_debug_flag_leaves_the_level_alone(monkeypatch):
    """Without the flag the callback must not reconfigure: doing so would
    overwrite whatever main() already set from LOG_LEVEL."""
    levels: list[Any] = []
    monkeypatch.setattr(shared, "configure_logging", lambda **kw: levels.append(kw.get("level")))
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


def _debug_log(monkeypatch, module=summary) -> _DebugLog:
    """Capture the debug lines `module` emits.

    The module matters: each command module holds its own `log`, so patching
    summary's would leave a lifecycle warning writing to the real logger
    and the assertion looking at an empty list.
    """
    captured = _DebugLog()
    monkeypatch.setattr(module, "log", captured)
    return captured


def test_summary_says_at_debug_why_the_port_map_is_missing(monkeypatch):
    """The failure is swallowed so the table still prints - which means the
    only symptom is every `down` row losing its port, with nothing on screen
    saying why. The reason has to survive somewhere, and DEBUG is where."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", raises=UqsError("no process.csv"))
    _patch(monkeypatch, listing, "heartbeat_states", result={})
    _patch(monkeypatch, listing, "summary_rows", result=[])
    captured = _debug_log(monkeypatch)
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert any("no process.csv" in m for m in captured.messages)


def test_summary_says_at_debug_why_heartbeats_are_missing(monkeypatch):
    """Two different faults print the same sentence at INFO - monitor1 absent
    from the registry, and monitor1 declared but unreachable. Only the debug
    line distinguishes them, which is the difference between restarting a
    process and chasing a connection."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    _patch(monkeypatch, listing, "heartbeat_states", raises=UqsError("monitor1 is not declared"))
    _patch(monkeypatch, listing, "summary_rows", result=[])
    captured = _debug_log(monkeypatch)
    assert runner.invoke(cli.app, ["summary"]).exit_code == 0
    assert any("monitor1 is not declared" in m for m in captured.messages)


def test_summary_distinguishes_unreachable_from_undeclared_at_debug(monkeypatch):
    """A None return is monitor1 declared but not answering - a different
    fault from the raise above, and it must not be reported as that one."""
    _patch(monkeypatch, runtime, "summary", result=Completed(stdout="raw"))
    _patch(monkeypatch, listing, "configured_ports", result={})
    _patch(monkeypatch, listing, "heartbeat_states", result=None)
    _patch(monkeypatch, listing, "summary_rows", result=[])
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
    _patch(monkeypatch, alive, "running", raises=RuntimeError("fleet unreachable"))
    _patch(monkeypatch, runtime, "start", result=Completed())
    captured = _debug_log(monkeypatch, lifecycle)
    assert runner.invoke(cli.app, ["start", "rdb1"]).exit_code == 0
    assert any("fleet unreachable" in m for m in captured.messages)
    assert not any("%s" in m for m in captured.messages)


# ------------------------------------------- new-job: a dataset already filled


def _worker_tree(tmp_path, body: str):
    workers = tmp_path / "src" / "etl" / "workers"
    workers.mkdir(parents=True)
    (workers / "w.q").write_text(body)
    return tmp_path


def test_a_dataset_an_unpartitioned_worker_fills_is_found(tmp_path):
    """.qbw.define refuses two workers on one dataset and partition, and a
    scaffolded worker declares no partition - so new-job must refuse first,
    rather than write a tree that no longer loads."""
    root = _worker_tree(
        tmp_path,
        "/ .qbw.define[`commented;`source`dataset!(`s;`fx)];\n"
        ".qbw.define[`w;\n    `source`dataset`width`transform!\n"
        "    (`s;`fx;1D;`s_passthrough)];\n",
    )
    assert create._unpartitioned_workers_filling(root, "fx") == ["w"]
    assert create._unpartitioned_workers_filling(root, "other") == []


def test_a_partitioned_worker_leaves_room_for_another(tmp_path):
    root = _worker_tree(
        tmp_path,
        ".qbw.define[`w;`source`dataset`width`transform`partition!"
        "(`s;`fx;1D;`s_passthrough;`EURUSD)];\n",
    )
    assert create._unpartitioned_workers_filling(root, "fx") == []


def test_every_real_worker_is_read():
    """Against the tree itself: each worker file's dataset is found, so the
    parser has not silently stopped matching the shape the files use."""
    root = Path(__file__).resolve().parents[3]
    for dataset, worker in {
        "demo_deals": "demo_deals_backfill",
        "event_tape": "demo_events_backfill",
        "imported_trades": "upstream_trades_backfill",
        "databento_book": "databento_book_backfill",
    }.items():
        assert create._unpartitioned_workers_filling(root, dataset) == [worker]


# ------------------------------------------- query with no expression (qcon)


def test_qcon_takes_one_colon_joined_target_not_four_arguments():
    """The mistake this encodes: `qcon host port user pass` does not fail as a
    usage error. qcon reads the first argument as the whole target and the
    rest as files, so the symptom is a connection refusal that reads exactly
    like the process being down."""
    assert runtime.qcon_command("localhost", 6053, "admin", "admin", rlwrap=False) == [
        "qcon",
        "localhost:6053:admin:admin",
    ]


def test_rlwrap_wraps_qcon_when_it_is_available():
    """Line editing and history, and optional - qcon runs without it, which is
    why the caller decides rather than this failing when rlwrap is absent."""
    assert runtime.qcon_command("h", 1, "u", "p", rlwrap=True) == ["rlwrap", "qcon", "h:1:u:p"]


class _ErrorLog:
    """The messages `_die` decided to emit.

    `_die` logs through loguru rather than writing to stdout, so CliRunner's
    `result.output` is empty for every refusal in this file - asserting on it
    passes vacuously in one direction and fails confusingly in the other. Same
    reasoning as _DebugLog above, for the error level.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args: Any) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


def _error_log(monkeypatch) -> _ErrorLog:
    """`_die` lives in `shared`, so that is whose `log` has to be replaced -
    patching inspect's would leave the message going to the real sink."""
    captured = _ErrorLog()
    monkeypatch.setattr(shared, "log", captured)
    return captured


def test_query_with_no_expression_execs_qcon_on_the_same_connection(monkeypatch):
    """No expression means a session: the same four options pointed at a
    different transport, so it must land on the process a query would hit."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(inspect.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inspect.os, "execvp", lambda f, a: seen.update(file=f, argv=a))
    result = runner.invoke(
        cli.app,
        ["query", "--port", "6099", "--host", "h2", "--user", "u", "--passwd", "p"],
    )
    assert result.exit_code == 0, result.output
    assert seen["argv"] == ["rlwrap", "qcon", "h2:6099:u:p"]


def test_a_session_without_qcon_installed_says_what_still_works(monkeypatch):
    """qcon ships with kdb+, not with this repository, so its absence is an
    ordinary state rather than a broken install - and the message has to leave
    the reader with a way to run their query."""
    errors = _error_log(monkeypatch)
    monkeypatch.setattr(inspect.shutil, "which", lambda name: None)
    assert runner.invoke(cli.app, ["query", "--port", "6099"]).exit_code == 1
    assert any("not on PATH" in m and "still works over IPC" in m for m in errors.messages), (
        errors.messages
    )


def test_an_expression_runs_it_and_never_opens_a_session(monkeypatch):
    rec = _patch(monkeypatch, runtime, "query", result="RESULT")
    monkeypatch.setattr(inspect.os, "execvp", lambda f, a: pytest.fail("should not exec"))
    assert runner.invoke(cli.app, ["query", "--port", "6099", "select 1"]).exit_code == 0
    assert rec.args[0] == "select 1"


def test_export_without_an_expression_is_refused(monkeypatch):
    """A session has no single result to write, so --export would be silently
    ignored - refused instead, before qcon takes the terminal."""
    errors = _error_log(monkeypatch)
    monkeypatch.setattr(inspect.os, "execvp", lambda f, a: pytest.fail("should not exec"))
    result = runner.invoke(cli.app, ["query", "--port", "6099", "--export", "out.csv"])
    assert result.exit_code == 1
    assert any("--export needs an expression" in m for m in errors.messages), errors.messages


# ------------------------------------------------------- conn (qcon by name)


def _conn(monkeypatch, *, running=("rdb1",), ports=None):
    """Patch what conn reads - the port map and the up/down check - and
    capture the exec instead of replacing the test process."""
    seen: dict[str, Any] = {}
    ports = ports if ports is not None else {"rdb1": "6052", "hdb1": "6053"}
    monkeypatch.setattr(
        inspect.listing,
        "configured_ports",
        lambda paths, base_port: seen.update(base=base_port) or ports,
    )
    if isinstance(running, Exception):

        def refuse(paths, base_port):
            raise running

        monkeypatch.setattr(inspect.alive, "running", refuse)
    else:
        monkeypatch.setattr(inspect.alive, "running", lambda paths, base_port: set(running))
    monkeypatch.setattr(inspect.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inspect.os, "execvp", lambda f, a: seen.update(argv=a))
    return seen


def test_conn_opens_qcon_on_the_named_process_port(monkeypatch):
    seen = _conn(monkeypatch)
    result = runner.invoke(cli.app, ["conn", "rdb1"])
    assert result.exit_code == 0, result.output
    assert seen["argv"] == ["rlwrap", "qcon", "localhost:6052:admin:admin"]


def test_conn_resolves_the_port_at_the_stacks_base_port(monkeypatch):
    seen = _conn(monkeypatch)
    runner.invoke(cli.app, ["conn", "rdb1", "--port", "7000", "--user", "u", "--passwd", "p"])
    assert seen["base"] == 7000
    assert seen["argv"][-1] == "localhost:6052:u:p"


def test_conn_refuses_an_unknown_process_by_name(monkeypatch):
    errors = _error_log(monkeypatch)
    seen = _conn(monkeypatch)
    assert runner.invoke(cli.app, ["conn", "nope1"]).exit_code == 1
    assert "argv" not in seen
    assert any("not a declared process" in m for m in errors.messages), errors.messages


def test_conn_refuses_a_stopped_process_and_says_how_to_start_it(monkeypatch):
    """qcon's own refusal reads the same as a wrong port; this names the cause."""
    errors = _error_log(monkeypatch)
    seen = _conn(monkeypatch, running=())
    assert runner.invoke(cli.app, ["conn", "hdb1"]).exit_code == 1
    assert "argv" not in seen
    assert any("uqs start hdb1" in m for m in errors.messages), errors.messages


def test_conn_still_connects_when_it_cannot_tell_what_is_running(monkeypatch):
    """The up/down check is advisory; qcon says for itself if nothing listens."""
    seen = _conn(monkeypatch, running=RuntimeError("ps failed"))
    assert runner.invoke(cli.app, ["conn", "rdb1"]).exit_code == 0
    assert seen["argv"][-1] == "localhost:6052:admin:admin"


def test_conn_without_qcon_installed_says_what_still_works(monkeypatch):
    errors = _error_log(monkeypatch)
    _conn(monkeypatch)
    monkeypatch.setattr(inspect.shutil, "which", lambda name: None)
    assert runner.invoke(cli.app, ["conn", "rdb1"]).exit_code == 1
    assert any("not on PATH" in m for m in errors.messages), errors.messages


def test_the_port_option_is_still_required():
    """Giving --port a default was the tempting way to satisfy Python's
    ordering rule when `expr` gained one. It would have turned "you forgot to
    say which process" into "silently queried the tickerplant"."""
    result = runner.invoke(cli.app, ["query", "select 1"])
    assert result.exit_code != 0
    assert "port" in result.output.lower()


# ------------------------------------------------------------ start --profile


def test_a_profile_expands_to_its_resolved_process_list(monkeypatch):
    """torq.sh is handed process NAMES, so a profile needs no process.csv
    change and no startwithall edit - it resolves in the CLI."""
    from uqs.model import profiles

    rec = _patch(monkeypatch, runtime, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "--profile", "arbitrage"])
    assert result.exit_code == 0
    passed = rec.args[1].split()
    assert set(passed) == set(profiles.resolve(["arbitrage"]))
    assert "stp1" in passed, "the plant must be started with the jobs"
    assert "crossarb1" in passed and "superbook1" in passed, "the chain is resolved"


def test_a_profile_over_the_cap_is_refused_rather_than_warned(monkeypatch):
    """The asymmetry with a positional start: a profile is a set this tree
    named, so one that cannot run is reported here rather than discovered when
    the plant resets a handle and the process wedges while reporting `up`."""
    rec = _patch(monkeypatch, runtime, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "--profile", "fx,arbitrage"])
    assert result.exit_code != 0
    assert not rec.calls, "nothing may be started when the set cannot run"


def test_an_unknown_profile_starts_nothing(monkeypatch):
    """That the refusal NAMES the known profiles is asserted where the message
    is built, in test_profiles.py - `_die` logs through loguru, which the CLI
    runner does not capture."""
    rec = _patch(monkeypatch, runtime, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "--profile", "nope"])
    assert result.exit_code != 0
    assert not rec.calls


def test_a_profile_and_positional_names_together_are_refused(monkeypatch):
    rec = _patch(monkeypatch, runtime, "start", result=Completed())
    result = runner.invoke(cli.app, ["start", "posbook1", "--profile", "fx"])
    assert result.exit_code != 0
    assert not rec.calls


def test_a_positional_start_over_the_cap_still_only_warns(monkeypatch):
    """Unchanged on purpose: an operator naming processes is making their own
    call, and several orderings that exceed the cap briefly are legitimate."""
    rec = _patch(monkeypatch, runtime, "start", result=Completed())
    names = " ".join(f"p{i}" for i in range(LICENCE_CONNECTION_LIMIT + 3))
    result = runner.invoke(cli.app, ["start", names])
    assert result.exit_code == 0, "a positional start is never blocked"
    assert rec.calls, "it was started"


def test_list_profiles_shows_the_slot_count(monkeypatch):
    result = runner.invoke(cli.app, ["list", "profiles"])
    assert result.exit_code == 0
    assert "arbitrage" in result.output
    assert "/14" in result.output, "the budget is the column that matters"
