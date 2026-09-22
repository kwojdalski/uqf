"""Tests for runtime.py's process plumbing and schema_view's live reads.

runtime.py sat at 61% and schema_view.py at 60%. What was untested is where
this package meets something outside Python: the torq.sh subprocess, the kdb+
IPC in `query`, file export, and schema_view's translation of what a real
`meta` returns.

That last one is why a real q process is started here rather than kola being
mocked. The whole job of schema_view is turning `meta`'s encoding - case as
the vector/atom distinction, a space for a general column, bytes for chars -
into something a reader can use. A mock would encode MY understanding of
`meta`, and the tests would then pass against it however wrong that was.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from torq_orchestrator import listing, runtime, schema_view
from torq_orchestrator.paths import UqfStackError, UqfStackPaths

#: Tables whose `meta` exercises each translation schema_view makes.
SCHEMA_SCRIPT = """
quotes:([] sym:`g#`EURUSD`GBPUSD; bid_prices:(1.1 1.09;1.25 1.24); venue:("EBS";"REUTERS"));
trades:([] time:2#2026.09.17D09:00:00.000000000; sym:`s#`EURUSD`GBPUSD; px:1.1 1.2);
mixed:([] payload:(1;`a));
"""


def _paths(root: Path = Path("/repo")) -> UqfStackPaths:
    """A real path bundle, so these tests type-check against the signatures
    they exercise. Nothing here touches the paths - the subprocess is patched."""
    return UqfStackPaths(
        repo_root=root,
        torqhome=root / "lib" / "torq",
        torqapphome=root / "lib" / "torq-finance-starter-pack",
        torqdata=root / "scripts" / "output" / "uqf-stack",
        scripts_dir=root / "scripts",
        orchestrator_dir=root / "python" / "torq_orchestrator",
    )


@pytest.fixture(scope="module")
def q_port(start_q, tmp_path_factory) -> Any:
    script = tmp_path_factory.mktemp("q") / "schema.q"
    script.write_text(SCHEMA_SCRIPT)
    with start_q(str(script)) as port:
        yield port


# --------------------------------------------------------------- run_torq_sh


def test_run_torq_sh_extends_the_environment_rather_than_replacing_it(monkeypatch):
    """subprocess.run's env= REPLACES the environment. Passing only the
    generated variables would lose PATH, and envsubst, rlwrap and q would
    stop resolving though they are on PATH in the calling shell."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(runtime, "bootstrap", lambda paths, base_port: {"KDBBASEPORT": "7000"})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, **kw) or "RESULT")
    assert runtime.run_torq_sh(_paths(), ["summary"], base_port=7000, capture=True) == "RESULT"
    assert seen["cmd"] == ["/repo/lib/torq/torq.sh", "summary"]
    assert seen["env"]["KDBBASEPORT"] == "7000"
    assert seen["env"]["PATH"] == os.environ["PATH"], "the inherited PATH survives"
    assert seen["capture_output"] is True
    assert seen["check"] is False, "a non-zero torq.sh is a result to report, not an exception"


@pytest.mark.parametrize(
    ("fn", "extra", "argv"),
    [
        (runtime.start, {"procs": "rdb1"}, ["start", "rdb1"]),
        (runtime.stop, {"procs": "all"}, ["stop", "all"]),
        (runtime.restart, {"procs": "hdb1"}, ["restart", "hdb1"]),
        (runtime.summary, {}, ["summary"]),
        (runtime.print_procs, {"procs": "rdb1"}, ["print", "rdb1"]),
    ],
)
def test_each_verb_passes_torq_sh_its_own_arguments(monkeypatch, fn, extra, argv):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        runtime, "run_torq_sh", lambda paths, args, **kw: seen.update(args=args, **kw)
    )
    fn(_paths(), base_port=6100, **extra)
    assert seen["args"] == argv
    assert seen["base_port"] == 6100


def test_summary_and_print_capture_by_default_and_lifecycle_verbs_stream(monkeypatch):
    """summary and print are parsed or shown by the caller, so their output
    is captured; start/stop/restart stream live to the terminal."""
    seen: dict[str, bool] = {}
    monkeypatch.setattr(
        runtime, "run_torq_sh", lambda paths, args, **kw: seen.__setitem__(args[0], kw["capture"])
    )
    runtime.summary(_paths())
    runtime.print_procs(_paths())
    runtime.start(_paths())
    assert seen == {"summary": True, "print": True, "start": False}


# --------------------------------------------------------------------- query


def test_query_runs_an_expression_on_a_live_process(q_port):
    assert runtime.query("1+1", q_port, user="", passwd="") == 2


def test_query_returns_a_table_as_a_dataframe(q_port):
    out = runtime.query("select from trades", q_port, user="", passwd="")
    assert isinstance(out, pl.DataFrame)
    assert out.height == 2


def test_query_disconnects_even_when_the_expression_fails(q_port):
    """A q error must not leak a connection: the community licence caps
    concurrent connections, so leaked handles eventually refuse everything."""
    for _ in range(20):
        with pytest.raises(Exception, match="nope"):
            runtime.query("'nope", q_port, user="", passwd="")
    assert runtime.query("1", q_port, user="", passwd="") == 1


# -------------------------------------------------------------- export_table


def test_export_writes_csv_from_a_list_of_rows(tmp_path):
    target = tmp_path / "out.csv"
    runtime.export_table([{"procname": "rdb1", "port": "6052"}], target)
    assert target.read_text().splitlines() == ["procname,port", "rdb1,6052"]


def test_export_writes_parquet_from_a_dataframe(tmp_path):
    target = tmp_path / "out.parquet"
    runtime.export_table(pl.DataFrame({"sym": ["EURUSD"], "px": [1.1]}), target)
    assert pl.read_parquet(target).to_dicts() == [{"sym": "EURUSD", "px": 1.1}]


def test_export_matches_the_extension_case_insensitively(tmp_path):
    target = tmp_path / "OUT.CSV"
    runtime.export_table([{"a": 1}], target)
    assert target.exists()


def test_a_scalar_result_is_refused_rather_than_wrapped(tmp_path):
    """`count trades` is a number, not rows. Wrapping it in a one-cell table
    would write a file that looks like data and is not."""
    with pytest.raises(UqfStackError, match="tabular"):
        runtime.export_table(2, tmp_path / "out.csv")


def test_an_unknown_extension_is_refused(tmp_path):
    with pytest.raises(UqfStackError, match=r"\.csv or \.parquet"):
        runtime.export_table([{"a": 1}], tmp_path / "out.xlsx")


# ---------------------------------------------------- schema_view, live meta


def test_table_names_are_read_from_the_process(q_port):
    assert set(schema_view.table_names(q_port)) >= {"quotes", "trades", "mixed"}


def test_the_overview_counts_rows_and_columns(q_port):
    rows = {r["table"]: r for r in schema_view.overview(q_port)}
    assert rows["quotes"] == {"table": "quotes", "rows": 2, "columns": 3}
    assert rows["mixed"]["columns"] == 1


def test_columns_translate_a_real_meta(q_port):
    """The translation this module exists for, against q's own `meta` rather
    than a guess at it."""
    cols = {c["column"]: c for c in schema_view.columns("quotes", q_port)}
    assert cols["sym"]["type"] == "symbol"
    assert cols["sym"]["attribute"] == "grouped", "an attribute is spelled out"
    assert cols["bid_prices"]["type"] == "float vector", "case carries vector-ness"
    assert cols["bid_prices"]["q"] == "F"
    assert cols["venue"]["type"] == "char vector"


def test_a_sorted_attribute_and_a_timestamp_are_named(q_port):
    cols = {c["column"]: c for c in schema_view.columns("trades", q_port)}
    assert cols["sym"]["attribute"] == "sorted"
    assert cols["time"]["type"] == "timestamp"
    assert cols["px"]["attribute"] == "", "no attribute is an empty cell, not a guess"


def test_a_general_column_is_named_general_not_blank(q_port):
    """q reports a general column as a SPACE - invisible in any rendering."""
    (col,) = schema_view.columns("mixed", q_port)
    assert col["type"] == "general"
    assert col["q"] == " "


def test_an_unknown_table_is_refused_naming_what_exists(q_port):
    """The name is validated against the live list before it reaches an
    expression - the one-escape-path discipline for a symbol that cannot be
    parameterised."""
    with pytest.raises(UqfStackError, match="quotes") as exc:
        schema_view.columns("quotes; exit 0", q_port)
    assert "not a table" in str(exc.value)


def test_a_pattern_is_matched_against_live_names(q_port):
    assert schema_view.match_tables("*s", q_port) == [
        n for n in schema_view.table_names(q_port) if n.endswith("s")
    ]


# ------------------------------------------------------- schema_view helpers


def test_a_non_table_result_is_refused():
    with pytest.raises(UqfStackError, match="expected a table"):
        schema_view._rows(42)


def test_a_list_result_passes_through():
    assert schema_view._rows([{"a": 1}]) == [{"a": 1}]


@pytest.mark.parametrize(("raw", "text"), [(b"EBS", "EBS"), ("EBS", "EBS"), (None, "")])
def test_a_meta_cell_decodes_bytes_and_none(raw, text):
    """kola hands back bytes for q char columns and str for symbols, which
    are the same thing to a reader."""
    assert schema_view._decode(raw) == text


# ------------------------------------------------ heartbeat states (listing)


def test_an_unreachable_monitor_is_none_not_an_empty_all_clear(monkeypatch):
    """None means "monitoring could not be reached"; {} would mean "heard
    from nobody". Rendering one as the other turns a monitoring gap into an
    all-clear, or an all-clear into a panic."""
    monkeypatch.setattr(listing, "_monitor_port", lambda paths, base_port: 1)

    def unreachable(expr, port):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr(listing, "query", unreachable)
    assert listing.heartbeat_states(_paths()) is None


def test_heartbeat_states_reads_monitor1_s_table(monkeypatch):
    monkeypatch.setattr(listing, "_monitor_port", lambda paths, base_port: 6059)
    seen: dict[str, Any] = {}

    def answer(expr, port, timeout=0):
        seen["port"] = port
        seen["timeout"] = timeout
        return pl.DataFrame({"procname": ["rdb1"], "warning": [False], "error": [False]})

    monkeypatch.setattr(listing, "query", answer)
    assert listing.heartbeat_states(_paths(), base_port=6050) == {"rdb1": "ok"}
    assert seen["port"] == 6059, "the monitor's port comes from the registry"


def test_the_heartbeat_query_gets_the_timeout_it_is_given(monkeypatch):
    """The reason it exists: monitor1 at its licence connection cap accepts
    the TCP connection and then does not answer, so a heartbeat lookup with
    no timeout hangs `summary` indefinitely."""
    monkeypatch.setattr(listing, "_monitor_port", lambda paths, base_port: 6059)
    seen: dict[str, Any] = {}

    def answer(expr, port, timeout=0):
        seen["timeout"] = timeout
        return pl.DataFrame({"procname": ["rdb1"], "warning": [False], "error": [False]})

    monkeypatch.setattr(listing, "query", answer)
    listing.heartbeat_states(_paths(), base_port=6050, timeout=7)
    assert seen["timeout"] == 7


def test_the_monitor_port_is_refused_when_monitor1_is_not_declared(monkeypatch):
    monkeypatch.setattr(
        listing, "_list_processes", lambda paths, base_port: [{"procname": "rdb1", "port": "6052"}]
    )
    with pytest.raises(UqfStackError, match="not a declared process"):
        listing._monitor_port(_paths(), 6050)


def test_the_monitor_port_is_resolved_from_the_registry(monkeypatch):
    monkeypatch.setattr(
        listing,
        "_list_processes",
        lambda paths, base_port: [{"procname": listing.MONITOR_PROCNAME, "port": "6061"}],
    )
    assert listing._monitor_port(_paths(), 6050) == 6061


def test_error_outranks_warning_and_nameless_rows_are_skipped():
    """A process past the error tolerance is past the warning one too;
    reporting the lesser would understate it."""
    rows = [
        {"procname": "rdb1", "warning": True, "error": True},
        {"procname": "hdb1", "warning": True, "error": False},
        {"procname": "gw1", "warning": False, "error": False},
        {"procname": "", "warning": True, "error": True},
    ]
    assert listing._heartbeat_by_procname(rows) == {"rdb1": "error", "hdb1": "warning", "gw1": "ok"}


def test_no_rows_is_an_empty_mapping():
    assert listing._heartbeat_by_procname(None) == {}
