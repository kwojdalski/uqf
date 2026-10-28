"""What each reporting command's `--json` document holds (#1731).

test_json_stdout.py holds that stdout is exactly one document, refusals
included, by running each command for real; these hold the document's SHAPE on
the path that answers, with the stack's reads replaced: the records a script
reads must carry what the table shows, under snake_case keys.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import polars as pl
from typer.testing import CliRunner

from uqs import cli
from uqs.checks import schema_view
from uqs.cli import external, json_out, summary_table
from uqs.cli import graph as graph_cli
from uqs.deploy import control, history, verify
from uqs.model.process_graph import Node
from uqs.stack import attached_logs, runtime, stream_preview
from uqs.stack import logs as stack_logs
from uqs.stack import procs as stack_procs
from uqs.stack import runs as stack_runs
from uqs.stack import uptime as stack_uptime

runner = CliRunner()


def _doc(argv: list[str]) -> dict:
    result = runner.invoke(cli.app, [*argv, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


# ------------------------------------------------------------------ the pieces


def test_snake_turns_column_titles_into_keys():
    assert [json_out.snake(k) for k in ("PortSource", "Depends on", "PID", "run_id")] == [
        "port_source",
        "depends_on",
        "pid",
        "run_id",
    ]


def test_plain_makes_a_kola_table_records_and_a_nan_null():
    frame = pl.DataFrame({"sym": ["a"], "px": [float("nan")]})
    assert json_out.plain({"t": frame}) == {"t": [{"sym": "a", "px": None}]}


def test_a_summary_row_loses_its_markup_and_lists_its_graph():
    row = {
        "Process": "rdb1",
        "PID": "",
        "Port": "[dim]6002[/]",
        "Inputs": "quote, trade,\nfx_book",
        "Depends on": "[dim]-[/]",
        "Responds": "-",
    }
    assert summary_table.record(row) == {
        "process": "rdb1",
        "pid": None,
        "port": 6002,
        "inputs": ["quote", "trade", "fx_book"],
        "depends_on": [],
        "responds": None,
    }


def test_the_graph_is_nested_records():
    trees = [Node("stp1", (), False, [Node("rdb1", ("quote",), False, [])])]
    assert graph_cli.tree_records(trees, {"stp1": "up"}) == [
        {
            "process": "stp1",
            "status": "up",
            "tables": [],
            "repeat": False,
            "children": [
                {
                    "process": "rdb1",
                    "status": None,
                    "tables": ["quote"],
                    "repeat": False,
                    "children": [],
                }  # fmt: skip
            ],
        }
    ]


# ---------------------------------------------------------------- the commands


def test_job_runs_list_prints_runs_with_whole_counts(monkeypatch):
    monkeypatch.setattr(
        stack_runs, "history", lambda *_a, **_k: [{"run_id": "r1", "rows_published": 4.0}]
    )
    (run,) = _doc(["job", "runs", "list"])["runs"]
    assert run["run_id"] == "r1" and run["rows_published"] == 4
    assert run["status"] is None  # every column, each run


def test_job_runs_audit_prints_facts(monkeypatch):
    monkeypatch.setattr(stack_runs, "audit", lambda *_a: [{"run_id": "r1", "label": "rows"}])
    doc = _doc(["job", "runs", "audit", "d", "--from", "2024-01-01", "--to", "2024-01-02"])
    assert doc["facts"][0]["label"] == "rows"


def test_job_gaps_prints_the_gaps_and_their_refills(monkeypatch):
    hole = {"range_from": "2024-01-01T00:00:00.000000000"}
    holes = [hole | {"range_to": "2024-01-01T01:00:00.000000000"}]
    monkeypatch.setattr(stack_uptime, "gaps", lambda *_a: (holes, {"w_backfill": None}, 2))
    doc = _doc(["job", "gaps", "j", "--from", "2024-01-01", "--to", "2024-01-02"])
    assert doc["gaps"] == holes and doc["sessions"] == 2 and doc["refillable"]
    (refill,) = doc["refills"]
    assert refill["worker"] == "w_backfill"
    assert refill["command"].startswith("uqs job backfill w_backfill --from ")


def test_job_preview_prints_the_previews_own_result(monkeypatch):
    found = {"job": "f", "state": "previewed", "fetched": 3, "trace": [("INFO", "f", "q")]}
    monkeypatch.setattr(stream_preview, "preview", lambda *_a, **_k: found)
    doc = _doc(["job", "preview", "f"])
    assert doc["fetched"] == 3
    assert doc["trace"] == [{"level": "INFO", "source": "f", "message": "q"}]


def test_connector_status_prints_running_as_a_bool(monkeypatch):
    feed = external.FEEDS["kafka"]
    fields = {"running": "True", "pid": "42", "deduplicated by": "x", "log": "/l"}
    monkeypatch.setitem(
        external.FEEDS, "kafka", dataclasses.replace(feed, status=lambda _paths: fields)
    )
    (status,) = _doc(["connector", "status", "kafka"])["connectors"]
    assert status == {"name": "kafka", "running": True, "pid": 42, "deduplicated_by": "x",
                      "log": "/l"}  # fmt: skip


def test_config_get_prints_fields_or_one_field(monkeypatch):
    row = {"procname": "rdb1", "port": "6002"}
    monkeypatch.setattr(stack_procs, "get_process_config", lambda *_a, **_k: row)
    doc = _doc(["config", "get", "rdb1"])
    assert doc["fields"] == [{"field": "procname", "value": "rdb1"},
                             {"field": "port", "value": "6002"}]  # fmt: skip
    assert _doc(["config", "get", "rdb1", "port"])["value"] == "6002"


def test_logs_prints_the_lines(monkeypatch):
    line = {"time": "t", "procname": "rdb1", "loglevel": "INFO", "message": "m"}
    monkeypatch.setattr(stack_logs, "get_recent_logs", lambda *_a: [line])
    (got,) = _doc(["logs", "rdb1"])["lines"]
    assert got["message"] == "m" and got["host"] is None


def test_logs_attach_prints_the_runs_lines(monkeypatch):
    monkeypatch.setattr(attached_logs, "recent", lambda *_a, **_k: [{"message": "RESULT"}])
    assert _doc(["logs", "--attach", "a"])["lines"][0]["message"] == "RESULT"


def test_schema_prints_tables_or_named_columns(monkeypatch):
    monkeypatch.setattr(schema_view, "resolve_port", lambda *_a: 6002)
    monkeypatch.setattr(
        schema_view, "overview", lambda *_a, **_k: [{"table": "q", "rows": 0, "columns": 2}]
    )
    assert _doc(["schema"])["tables"] == [{"table": "q", "rows": 0, "columns": 2}]
    monkeypatch.setattr(schema_view, "match_tables", lambda *_a, **_k: ["q"])
    monkeypatch.setattr(
        schema_view, "columns", lambda *_a, **_k: [{"column": "sym", "type": "symbol"}]
    )
    assert _doc(["schema", "q"])["columns"] == [{"table": "q", "column": "sym", "type": "symbol"}]


def test_query_prints_the_result_as_data(monkeypatch):
    monkeypatch.setattr(
        runtime, "query", lambda *_a, **_k: pl.DataFrame({"sym": ["a", "b"], "n": [1, 2]})
    )
    doc = _doc(["query", "--port", "6002", "select from t"])
    assert doc == {"expression": "select from t", "result": [{"sym": "a", "n": 1},
                                                              {"sym": "b", "n": 2}]}  # fmt: skip


def test_deploy_history_prints_deployments_newest_first(monkeypatch):
    entries = [{"at": "1", "host": "h", "status": "ok"}, {"at": "2", "host": "h", "status": "ok"}]
    monkeypatch.setattr(history, "read", lambda _root: entries)
    monkeypatch.setattr(history, "defaults", lambda _root: {1})
    doc = _doc(["deploy", "history"])
    assert [(d["at"], d["default"]) for d in doc["deployments"]] == [("2", True), ("1", False)]


def test_target_check_prints_the_release(monkeypatch):
    remote = control.Remote("t", "h", "/srv/uqf")
    monkeypatch.setattr(control, "remote_for", lambda *_a: remote)
    monkeypatch.setattr(control, "current_release", lambda _r: "rel-1")
    doc = _doc(["target", "check", "t"])
    assert doc == {"ok": True, "target": "t", "host": "h", "dest": "/srv/uqf",
                   "remote_user": None, "release": "rel-1"}  # fmt: skip


def test_deploy_verify_json_leaves_out_the_marker(monkeypatch, capsys):
    monkeypatch.setattr(verify, "_expected", lambda *_a: ({"rdb1": 6002}, set()))
    monkeypatch.setattr(verify, "busy_ports", lambda expected, _c: {})
    assert verify.run("p", ports_free=True, marker=False) == 0
    assert json.loads(capsys.readouterr().out) == {"profile": "p", "busy": {}}
    verify.run("p", ports_free=True)
    assert capsys.readouterr().out.splitlines()[-1] == verify.OK_MARKER


def test_summary_json_carries_the_lines_under_the_table(monkeypatch):
    from uqs.cli import summary as summary_cli

    rows = [{"Process": "rdb1", "Status": "up", "Port": "6002"}]
    gathered = SimpleNamespace(
        result=SimpleNamespace(returncode=0), rows=rows, heartbeats=None, silent=["rdb1"],
        failing=[],
    )  # fmt: skip
    monkeypatch.setattr(summary_cli, "gather", lambda *_a: gathered)
    doc = _doc(["summary"])
    assert doc["processes"] == [{"process": "rdb1", "status": "up", "port": 6002}]
    assert doc["silent"] == ["rdb1"] and doc["heartbeats_collected"] is False
