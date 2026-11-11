"""`uqs data compress` (#2147, #2148): the plan, the estimate and the disk it
needs, then - with --apply - one partition at a time, audited, stopping at the
first named error.

Everything q does - .cmp's plan, the scope, the sample, the run - is
housekeeping1's, tested in tests/q/test_torq_compress_survey.q; here the
query to it is a stand-in that answers as `.j.j` would, and what is under test
is what uqs asks, the arithmetic it adds, and the run's control: the order, the
refusals, the stop, and the activity history's row. The end-to-end run against
a running stack's HDB is test_data_compress_stack.py.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs.cli import compress as compress_cmd
from uqs.cli import compress_plan as data_compress
from uqs.cli.shared import app
from uqs.cli.target_confirm import changes_the_server, may_change_the_server
from uqs.paths import UqsError, UqsUsageError
from uqs.paths import default_paths as real_paths
from uqs.stack import compress as stack_compress
from uqs.stack import procs as stack_procs

CONFIG = [
    {
        "table": "default",
        "minage": 7,
        "column": "default",
        "calgo": 2,
        "cblocksize": 16,
        "clevel": 6,
    },
    {
        "table": "fx_markout",
        "minage": 14,
        "column": "invert",
        "calgo": 2,
        "cblocksize": 16,
        "clevel": 9,
    },
    {
        "table": "reference_data",
        "minage": 7,
        "column": "default",
        "calgo": 0,
        "cblocksize": 0,
        "clevel": 0,
    },
]


def _part(table: str, day: str, d: Path, **kw: Any) -> dict[str, Any]:
    base = {
        "table": table,
        "partition": day,
        "age": 10,
        "status": "act",
        "dir": str(d),
        "compress": ["px", "qty"],
        "decompress": [],
        "skip": [],
        "compressed": [],
        "files": 2,
        "bytes_now": 1000,
        "bytes_after": 300,
        "bytes_written": 300,
    }
    return {**base, **kw}


@pytest.fixture
def answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """housekeeping1, answering: what each call was asked, and the survey it gives."""
    asked: list[str] = []
    doc = {
        "config": CONFIG,
        "held": [{"dataset": "fx_markout", "date": "2026-10-30"}],
        "partitions": [
            _part("fx_markout", "2026-10-20", tmp_path),
            _part("fx_markout", "2026-10-30", tmp_path, status="held"),
            _part(
                "market_data",
                "2026-10-21",
                tmp_path,
                bytes_now=4000,
                bytes_after=1000,
                bytes_written=1000,
            ),
            _part("market_data", "2026-10-20", tmp_path, bytes_written=200),
            _part(
                "market_data",
                "2026-10-19",
                tmp_path,
                compress=[],
                skip=["px"],
                compressed=["px"],
                bytes_after=1000,
                bytes_written=0,
            ),
        ],
        "sample": [{"table": "market_data", "partition": "2026-10-21", "rows": 100000}],
        "ratios": [],
    }

    def query(expr: str, port: int, timeout: int = 0) -> str:
        asked.append(expr)
        if "hk.reload" in expr:
            return json.dumps({"registered": 1, "opened": 1, "reloaded": 1})
        if "apply_partition" in expr:
            day = expr.rsplit('"', 2)[-2]
            rows = [
                {
                    "fullpath": f":/h/{day}/t/px",
                    "table": "market_data",
                    "partition": day,
                    "column": "px",
                    "action": "compress",
                    "bytes_before": 100,
                    "bytes_after": 40,
                }
            ]
            return json.dumps(rows)
        return json.dumps(doc)

    monkeypatch.setattr(stack_compress, "engine_port", lambda paths, base_port=None: 5011)
    return asked, doc, query


# ------------------------------------------------------------------ scope


def test_older_than_is_whole_days():
    assert stack_compress.older_days(None) == 0
    assert stack_compress.older_days(7 * 86_400) == 7
    with pytest.raises(UqsUsageError, match="whole number of days, e.g. 7d"):
        stack_compress.older_days(36 * 3600)


def test_a_table_is_a_q_name_before_it_reaches_q():
    assert stack_compress.scope(["a", "b", "a"], None).tables == ("a", "b")
    with pytest.raises(UqsUsageError, match="--table x`y: not a table name"):
        stack_compress.scope(["x`y"], None)


# ----------------------------------------------------------------- survey


def test_the_survey_asks_housekeeping1_with_the_scope(answers):
    asked, _, query = answers
    paths = real_paths()
    doc = stack_compress.survey(paths, stack_compress.Scope(("market_data",), 7), query=query)
    (expr,) = asked
    assert expr.startswith(".j.j .qtorq.compress.survey[(hsym `$")
    assert f'"{paths.hdb_dir}"' in expr
    assert expr.endswith(f";.cmp.inputcsv;`market_data;7;();()!();{stack_compress.SAMPLE_ROWS}]")
    # held partitions are shown, never counted; a partition with nothing to change is not one
    assert doc["totals"] == {
        "partitions": 2,
        "files": 6,
        "bytes_now": 6000,
        "bytes_after": 1600,
        "bytes_saved": 4400,
    }
    assert stack_compress.partitions_to_run(doc) == ["2026-10-20", "2026-10-21"]


def test_each_table_says_which_policy_it_takes(answers):
    _, _, query = answers
    doc = stack_compress.survey(real_paths(), stack_compress.Scope(), query=query)
    by = {p["table"]: p for p in doc["policies"]}
    assert by["market_data"]["policy"] == "stack default"
    assert (by["market_data"]["algo"], by["market_data"]["minage"]) == ("gzip", 7)
    assert by["fx_markout"]["policy"] == "declared"
    assert by["fx_markout"]["columns"] == {
        "invert": {"algo": "gzip", "blocksize": 16, "level": 9, "minage": 14}
    }
    assert stack_compress.policies(CONFIG, ["reference_data"])[0]["algo"] == "none"


def test_the_disk_needed_is_the_largest_partition_per_filesystem(tmp_path, monkeypatch):
    parts = [
        _part("a", "2026-10-20", tmp_path, bytes_written=300),
        _part("b", "2026-10-20", tmp_path, bytes_written=400),
        _part("a", "2026-10-21", tmp_path, bytes_written=500),
    ]

    class Usage:
        free = 650

    monkeypatch.setattr(stack_compress.shutil, "disk_usage", lambda d: Usage())
    (disk,) = stack_compress.headroom(parts)
    assert (disk["needed_bytes"], disk["free_bytes"], disk["ok"]) == (700, 650, False)
    with pytest.raises(UqsError, match="has 650 bytes free and one partition needs 700"):
        stack_compress.refuse_short_disk([disk])


def test_a_partition_run_names_its_partition_and_scope(answers):
    asked, _, query = answers
    rows = stack_compress.run_partition(
        real_paths(), stack_compress.Scope(), "2026-10-20", query=query
    )
    assert asked[-1].endswith(';.cmp.inputcsv;`symbol$();0;();()!();"2026-10-20"]')
    assert stack_compress.summary("2026-10-20", rows) == {
        "partition": "2026-10-20",
        "tables": ["market_data"],
        "files": 1,
        "bytes_before": 100,
        "bytes_after": 40,
    }


def test_a_process_that_is_down_is_named():
    def down(*a, **k):
        raise ConnectionError("Connection refused (os error 61)")

    with pytest.raises(
        UqsError, match="housekeeping1 .* is not answering: `uqs start housekeeping1`"
    ):
        stack_compress._ask(real_paths(), stack_compress.Scope(), "1", 1, down)


@pytest.mark.parametrize("missing", [".qtorq.compress.survey", ".qtorq.hk.reload"])
def test_a_process_started_before_the_wrapper_is_told_to_restart(missing):
    def old(*a, **k):
        raise RuntimeError(missing)

    with pytest.raises(UqsError, match=f"has no {missing}: .* restart housekeeping1"):
        stack_compress._ask(real_paths(), stack_compress.Scope(), "1", 1, old)


# -------------------------------------------------------------- the command


@pytest.fixture
def cli(answers, monkeypatch, tmp_path):
    """The command with housekeeping1 answering, no HDB lock to take, and the
    activity history in a file of its own."""
    asked, doc, query = answers
    monkeypatch.setattr(stack_compress, "_ask", _ask_with(query))
    monkeypatch.setattr(
        data_compress.hdb_alter, "write_lock", lambda paths: contextlib.nullcontext()
    )
    history = tmp_path / "activity.sqlite3"
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_DB", str(history))

    class Usage:
        free = 10**12

    monkeypatch.setattr(stack_compress.shutil, "disk_usage", lambda d: Usage())

    def run(*args: str):
        return CliRunner().invoke(app, ["data", "compress", *args])

    return run, asked, doc, history


def _ask_with(query):
    real = stack_compress._ask

    def ask(paths, sc, expr, timeout, q=None):
        return real(paths, sc, expr, timeout, query)

    return ask


def _audit(history: Path) -> list[sqlite3.Row]:
    db = sqlite3.connect(history)
    db.row_factory = sqlite3.Row
    return db.execute("SELECT * FROM activity WHERE action = 'hdb.compress'").fetchall()


def test_without_apply_nothing_runs_and_the_plan_says_so(cli):
    run, asked, _, history = cli
    done = run()
    assert done.exit_code == 0, done.output
    assert not any("apply_partition" in a for a in asked)
    assert "stack default" in done.output and "dry run: nothing was changed" in done.output
    assert "held       fx_markout 2026-10-30: still being written" in done.output
    assert not history.exists()


def test_apply_runs_each_partition_oldest_first_and_audits_each(cli):
    run, asked, _, history = cli
    done = run("--apply", "--yes")
    assert done.exit_code == 0, done.output
    runs = [a for a in asked if "apply_partition" in a]
    assert [r.rsplit('"', 2)[-2] for r in runs] == ["2026-10-20", "2026-10-21"]
    (row,) = _audit(history)
    assert (row["source"], row["kind"], row["outcome"], row["method"]) == (
        "server",
        "request",
        "succeeded",
        "CLI",
    )
    assert json.loads(row["items"]) == [
        {"subject": "2026-10-20", "outcome": "succeeded"},
        {"subject": "2026-10-21", "outcome": "succeeded"},
    ]
    # this stack's own HDB: target `local`, its directory the subject (#2234)
    assert row["target"] == "local" and row["subject"]
    assert json.loads(row["detail"])["process"] == "housekeeping1"


def test_apply_stops_at_the_first_named_error(cli, monkeypatch):
    run, asked, _, history = cli
    calls: list[str] = []

    def fails(paths, sc, part, **kw):
        calls.append(part)
        raise UqsError(
            "housekeeping1: qtorq.compress: 1 file(s) were not brought to their target state"
        )

    monkeypatch.setattr(stack_compress, "run_partition", fails)
    done = run("--apply", "--yes", "--json")
    assert done.exit_code == 1
    doc = json.loads(done.stdout)
    assert doc["ok"] is False
    assert doc["result"]["failed"]["partition"] == "2026-10-20"
    assert "not brought to their target state" in doc["result"]["failed"]["error"]
    assert calls == ["2026-10-20"], "nothing after the failure is tried"
    (row,) = _audit(history)
    assert row["outcome"] == "failed"
    assert json.loads(row["items"]) == [{"subject": "2026-10-20", "outcome": "failed"}]


def test_apply_is_refused_before_anything_when_the_disk_is_short(cli, monkeypatch):
    run, asked, _, history = cli

    class Usage:
        free = 10

    monkeypatch.setattr(stack_compress.shutil, "disk_usage", lambda d: Usage())
    done = run("--apply", "--yes")
    assert done.exit_code != 0
    assert not any("apply_partition" in a for a in asked)
    assert not history.exists()


def test_json_cannot_ask(cli, terminal, monkeypatch):
    """#2229: a refusal (exit 1), as every --json command without --yes is."""
    _, asked, _, _ = cli
    argv = ["data", "compress", "--apply", "--json"]
    monkeypatch.setattr("sys.argv", ["uqs", *argv])  # json_requested reads it
    done = CliRunner().invoke(app, argv, input="y\n")
    assert done.exit_code == 1, done.output
    doc = json.loads(done.stdout)
    assert doc["ok"] is False
    assert "--json cannot ask for confirmation: add --yes to compress" in doc["error"]
    assert not any("apply_partition" in a for a in asked)


def test_the_json_document_carries_the_plan(cli):
    run, *_ = cli
    doc = json.loads(run("--json", "--table", "market_data", "--older-than", "7d").stdout)
    assert doc["ok"] is True and doc["result"] is None
    assert doc["scope"] == {
        "tables": ["market_data"],
        "older_than_days": 7,
        "days": None,
        "change": {},
    }
    assert {"policies", "held", "partitions", "sample", "disk", "totals"} <= set(doc)


# ----------------------------------------------------- target and inspection


def test_a_target_asks_on_the_server_and_inspection_refuses_apply():
    """A dry run never changes the server; --apply asks there, and is what an
    inspection-only target refuses (cli/target_confirm.py, #2032)."""
    assert not changes_the_server(["data", "compress"])
    assert not may_change_the_server(["data", "compress", "--table", "t"])
    assert may_change_the_server(["data", "compress", "--apply"])


# ---------------------------------------------------------------- wiring


def test_housekeeping1_points_cmp_at_the_policy_before_and_loads_the_wrapper_after():
    real = real_paths()
    vendored = (real.torqapphome / "appconfig" / "process.csv").read_text()
    upstream = {r["procname"]: r["load"] for r in csv.DictReader(io.StringIO(vendored))}
    composed = {r["procname"]: r for r in stack_procs.effective_process_rows(real)}
    load = composed["housekeeping1"]["load"].split()
    config = load.index("${UQF_SCRIPTS}/processes/uqs_compression_config.q")
    assert config < load.index(upstream["housekeeping1"]), ".cmp.inputcsv is set before"
    order = [
        load.index(f"${{UQF_SCRIPTS}}/processes/{f}")
        for f in (
            "torq_pipeline.q",
            "torq_compress.q",
            "torq_compress_survey.q",
        )
    ]
    assert order == sorted(order)
    assert "${UQF_ROOT}/src/etl/core/materialisation.q" in load
    for f in load:
        path = f.replace("${UQF_SCRIPTS}", str(real.scripts_dir)).replace(
            "${UQF_ROOT}", str(real.repo_root)
        )
        if "$" not in path:
            assert Path(path).is_file(), path


# ------------------------------------------------------------- named days


def test_the_days_and_a_one_off_change_reach_q_as_its_literals():
    sc = stack_compress.scope([], None, None, (date(2026, 11, 2), None), "qipc", 17)
    assert stack_compress._q_scope(sc) == "`symbol$();0;(2026.11.02;0Nd);`calgo`cblocksize!(1;17)"
    one = stack_compress.scope([], None, level=9)
    assert stack_compress._q_change(one.change) == "(enlist `clevel)!enlist 9"


def _scope_of(cli, monkeypatch, *args):
    run, *_ = cli
    seen = {}
    real = data_compress.plan_and_apply

    def spy(sc, **kw):
        seen["sc"] = sc
        return real(sc, **kw)

    monkeypatch.setattr(data_compress, "plan_and_apply", spy)
    done = run(*args)
    assert done.exit_code == 0, done.output
    return seen["sc"]


def test_a_date_names_that_one_partition(cli, monkeypatch):
    assert _scope_of(cli, monkeypatch, "--date", "2026-11-02").days == (
        date(2026, 11, 2),
        date(2026, 11, 2),
    )


def test_from_to_is_half_open(cli, monkeypatch):
    sc = _scope_of(cli, monkeypatch, "--from", "2026-10-20", "--to", "2026-10-22")
    assert sc.days == (date(2026, 10, 20), date(2026, 10, 21)), "--to's day is not in"


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (("--from", "2026-10-20T06:00", "--to", "2026-10-22"), "--from 2026-10-20T06:00:00 is not"),
        (
            ("--from", "2026-10-20", "--to", "2026-10-22T06:00"),
            "give --to 2026-10-22 or 2026-10-23",
        ),
        (("--last", "12h"), "--last 12h is not a whole number of days"),
    ],
)
def test_a_range_off_whole_days_is_refused_before_the_plan(cli, monkeypatch, args, said):
    """#2245: a partial day was rounded - the last one taken whole past --to,
    and --last N put today's partition in the scope."""
    run, *_ = cli
    monkeypatch.setattr(data_compress, "plan_and_apply", lambda *a, **k: pytest.fail("planned"))
    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(compress_cmd, "_die", record)
    run(*args)
    assert len(refused) == 1
    assert refused[0].startswith("`data compress` compresses whole partitions: ")
    assert said in refused[0]


def test_last_is_the_whole_days_before_today(cli, monkeypatch):
    sc = _scope_of(cli, monkeypatch, "--last", "2d")
    today = datetime.now(UTC).date()
    assert sc.days == (today - timedelta(days=2), today - timedelta(days=1)), "today is not in"


def test_without_a_range_the_policy_chooses(cli, monkeypatch):
    assert _scope_of(cli, monkeypatch).days is None


def test_the_dry_run_says_a_named_day_is_younger_than_the_policy(cli):
    run, _, doc, _ = cli
    doc["partitions"][2].update(early=True, policy_minage=7, age=1)
    out = run("--date", "2026-10-21").output
    assert "compressing it although the policy's minage is 7" in out


def test_apply_reloads_the_hdbs_once(cli):
    run, asked, _, _ = cli
    assert run("--apply", "--yes").exit_code == 0
    assert sum("hk.reload" in a for a in asked) == 1


def test_the_report_takes_no_plan_option_and_the_plan_no_hdb(cli):
    run, asked, *_ = cli
    done = run("--report", "--apply")
    assert done.exit_code == 2 and not asked
    done = run("--hdb", "/tmp")
    assert done.exit_code == 2 and not asked


def test_a_one_off_algorithm_reaches_the_plan(cli, monkeypatch):
    sc = _scope_of(cli, monkeypatch, "--algo", "qipc", "--gzip-level", "0")
    assert dict(sc.change) == {"calgo": 1, "clevel": 0}
