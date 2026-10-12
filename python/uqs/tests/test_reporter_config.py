"""reporter1's generated checks (stack/reporter.py, #1209): selected before
reporter.q loads, validated against the deployment's tables, thresholds
explicit, and the site's own reporter.csv never read or written."""

from __future__ import annotations

import csv
import dataclasses
import io
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from uqs.model import runtime_members
from uqs.paths import UqsError, UqsPaths
from uqs.paths import default_paths as real_paths
from uqs.runtimes import RUNTIMES
from uqs.stack import procs as stack_procs
from uqs.stack import reporter

OVERLAY = "${UQF_SCRIPTS}/processes/uqs_reporter_config.q"
PROCS = ("rdb1", "hdb1")


def _paths(tmp_path: Path, runtime: str) -> UqsPaths:
    (tmp_path / "app" / "appconfig").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "uqs",
        runtime=runtime,
    )


def _rows(paths: UqsPaths) -> list[dict[str, str]]:
    with paths.generated_reporter_config.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="|"))


def _activity(name: str, table: str, **kw: Any) -> reporter.ReporterCheck:
    base = reporter.ReporterCheck(
        name=name, kind="activity", proctype="rdb", procname="rdb1", tables=(table,),
        window="0D00:15", min_rows=3, start="08:00", end="17:30", period="00:15",
        days=reporter.WEEKDAYS,
    )  # fmt: skip
    return dataclasses.replace(base, **kw)


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch):
    """Fake installed sidecar bundles over real processes, as
    test_runtime_bundles does."""
    entries: dict[str, dict] = {}

    def install(name: str, procname: str, table: str, runtimes: list[str]) -> None:
        entries[name] = {
            "jobs": [{"kind": "streaming", "name": procname[:-1], "procname": procname}],
            "tables": [table],
            "runtimes": runtimes,
        }

    monkeypatch.setattr(runtime_members, "_ledger", lambda: entries)
    return install


# ------------------------------------------------------------- selection


def test_reporter1_loads_the_overlay_before_reporter_q():
    """reporter.q reads `.rp.inputcsv` once, as it loads - the file that sets
    it must come first."""
    real = real_paths()
    vendored = (real.torqapphome / "appconfig" / "process.csv").read_text()
    upstream = {r["procname"]: r["load"] for r in csv.DictReader(io.StringIO(vendored))}
    composed = {r["procname"]: r for r in stack_procs.effective_process_rows(real)}
    assert composed["reporter1"]["load"].split() == [OVERLAY, upstream["reporter1"]]


def test_the_overlay_sets_inputcsv_to_the_generated_file(q_binary, repo_root, tmp_path):
    """Loaded as reporter1 loads it, after a site setting that names its own
    file: the value reporter.q's `@[value;`.rp.inputcsv;...]` then reads is the
    generated reporter.csv in TORQDATA."""
    q, env = q_binary
    script = tmp_path / "selection.q"
    script.write_text(
        ".lg.o:{[x;y]};.lg.w:{[x;y]};.proc.cp:{.z.p};\n"
        ".rp.inputcsv:`:/site/appconfig/reporter.csv;\n"
        f"\\l {repo_root}/scripts/processes/uqs_reporter_config.q\n"
        "-1 string @[value;`.rp.inputcsv;`unset];\n"
        "exit 0\n"
    )
    out = subprocess.run(
        [q, str(script)], capture_output=True, text=True, timeout=60,
        env={**env, "TORQDATA": str(tmp_path / "data")}, cwd=repo_root,
    )  # fmt: skip
    lines = [x for x in out.stdout.splitlines() if x.strip()]
    assert lines[-1:] == [f":{tmp_path / 'data'}/reporter.csv"], out.stdout + out.stderr


def test_the_site_reporter_csv_is_neither_read_nor_written(tmp_path):
    paths = _paths(tmp_path, "uqf")
    site = paths.torqapphome / "appconfig" / "reporter.csv"
    text = (
        "name|query|resulthandler|gateway|joinfunction|proctype|procname|start|end|period|"
        "timeoutinterval|daysofweek\n"
        "rdbcountcheck|.checks.tablecount[`site_only;0D00:01;.proc.cp[];`time;1]|x|||rdb|rdb1|"
        "00:00|23:59|00:01|00:00:20|0 1 2 3 4 5 6\n"
    )
    site.write_text(text)
    reporter.write_reporter_config(paths, PROCS)
    assert site.read_text() == text
    assert "site_only" not in paths.generated_reporter_config.read_text()
    assert [r["name"] for r in _rows(paths)] == [c.name for c in reporter.UQF_REPORTER_CHECKS]


def test_rewriting_keeps_the_same_selection(tmp_path):
    """Every start bootstraps again: a restarted reporter1 reads the same file."""
    paths = _paths(tmp_path, "uqf")
    reporter.write_reporter_config(paths, PROCS)
    first = paths.generated_reporter_config.read_text()
    reporter.write_reporter_config(paths, PROCS)
    assert paths.generated_reporter_config.read_text() == first


# ------------------------------------------------- against the deployment


def test_only_the_deployments_tables_are_checked(tmp_path):
    """The crypto runtime publishes crypto_book and no fx_orderbook: the FX
    checks are left out, the memory check kept."""
    paths = _paths(tmp_path, "crypto")
    reporter.write_reporter_config(paths, PROCS)
    names = [r["name"] for r in _rows(paths)]
    assert names == ["rdbmemorycheck", "crypto_book_activity"]


def test_a_selected_sidecars_output_is_checked_and_an_unselected_one_is_not(ledger):
    """`pb` is installed for fx only: fx checks its table, crypto leaves the
    check out and says which table and why."""
    ledger("pb", "arbitrage1", "arbitrage", ["fx"])
    check = _activity("arbitrage_activity", "arbitrage")
    kept, left_out = reporter.reporter_plan(RUNTIMES["fx"], PROCS, [check])
    assert kept == [check] and left_out == []
    kept, left_out = reporter.reporter_plan(RUNTIMES["crypto"], PROCS, [check])
    assert kept == []
    assert left_out == [
        "arbitrage_activity: arbitrage - not published by any process or sidecar bundle "
        "of the crypto deployment"
    ]


def test_a_table_no_definition_has_is_refused_at_generation():
    check = _activity("typo_activity", "fx_orderbok")
    with pytest.raises(UqsError, match=r"typo_activity.*fx_orderbok.*UQF_REPORTER_CHECKS"):
        reporter.reporter_plan(RUNTIMES["uqf"], PROCS, [check])


def test_a_time_column_the_table_lacks_is_refused():
    check = _activity("bad_timecol", "fx_orderbook", timecol="exch_time")
    with pytest.raises(UqsError, match="exch_time from fx_orderbook, which has no such column"):
        reporter.reporter_plan(RUNTIMES["uqf"], PROCS, [check])


def test_a_process_the_stack_lacks_leaves_its_check_out():
    check = _activity("elsewhere", "fx_orderbook", procname="rdb9")
    kept, left_out = reporter.reporter_plan(RUNTIMES["uqf"], PROCS, [check])
    assert kept == []
    assert left_out == ["elsewhere: queries rdb9, which is not a process of the uqf stack"]


def test_every_uqf_check_is_valid_for_every_runtime_with_overlays():
    for name, runtime in RUNTIMES.items():
        if runtime.overlays:
            kept, _ = reporter.reporter_plan(runtime, PROCS)
            assert "rdbmemorycheck" in [c.name for c in kept], name


# ------------------------------------------------------------ thresholds


def test_explicit_thresholds_are_carried_into_the_row():
    row = _activity("fx_slow", "fx_orderbook").row()
    assert row["query"] == ".checks.tablecount[(),`fx_orderbook;0D00:15;.proc.cp[];`time;3]"
    assert (row["start"], row["end"], row["period"]) == ("08:00", "17:30", "00:15")
    assert row["daysofweek"] == "2 3 4 5 6"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"window": ""}, "window is a q timespan"),
        ({"min_rows": 0}, "requires a positive min_rows"),
        ({"tables": ()}, "names the tables it watches"),
        ({"period": "5m"}, "period is HH:MM"),
        ({"days": (7,)}, "days are .proc.cd"),
    ],
)
def test_an_activity_check_without_explicit_thresholds_is_refused(override, message):
    with pytest.raises(UqsError, match=message):
        _activity("fx_bad", "fx_orderbook", **override)


# ------------------------------------------------------------- in q


def test_the_generated_file_loads_and_alerts_as_reporter_q_runs_it(q_binary, repo_root, tmp_path):
    """Read the file with csvloader's own format and checks, run the
    fx_orderbook activity row's query against a table whose last row is ten
    minutes old, and hand the result to the row's result handler: one warning,
    then none while alert_every has not passed."""
    paths = _paths(tmp_path, "uqf")
    reporter.write_reporter_config(paths, PROCS)
    q, env = q_binary
    script = tmp_path / "run.q"
    script.write_text(
        f"""
.proc.cp:{{.z.p}};.proc.cd:{{.z.d}};.proc.localtime:0b;
.lg.o:{{[x;y]}};
.lg.w:{{[x;y] -1 "WARN|",y}};
\\l {repo_root}/lib/torq/code/common/monitoringchecks.q
\\l {repo_root}/scripts/processes/uqs_reporter_config.q
t:("s**s*Ssuuun*";enlist "|") 0: .rp.inputcsv;
t:update daysofweek:value each daysofweek, query:raze each query,
  proctype:raze each `$" " vs' string proctype from t;
if[any (not count each raze value exec query,resulthandler from t),
  null raze/[value flip delete query,resulthandler,joinfunction,gateway,procname from t];
  -1 "NULLS";exit 1];
r:first select from t where name=`fx_orderbook_activity;
fx_orderbook:([] time:enlist .z.p-0D00:10; sym:enlist`EURUSD);
data:`queryid`name`procname`result!(1;r`name;`rdb1;value r`query);
(value r`resulthandler) @ data;
(value r`resulthandler) @ data;
-1 "days:"," " sv string r`daysofweek;
exit 0
"""
    )
    out = subprocess.run(
        [q, str(script)], capture_output=True, text=True, timeout=60,
        env={**env, "TORQDATA": str(paths.torqdata)}, cwd=repo_root,
    )  # fmt: skip
    lines = [x for x in out.stdout.splitlines() if x.strip()]
    warns = [x for x in lines if x.startswith("WARN|")]
    assert len(warns) == 1, out.stdout + out.stderr
    assert "fx_orderbook_activity on rdb1" in warns[0]
    assert "fx_orderbook" in warns[0].split(":", 1)[1]
    assert lines[-1] == "days:2 3 4 5 6", out.stdout + out.stderr
    assert os.path.exists(paths.generated_reporter_config)
