"""reporter1's generated checks (stack/reporter.py, #1209): selected before
reporter.q loads, validated against the deployment's tables, thresholds
explicit, and the site's own reporter.csv never read or written."""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import io
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from uqs.model import profiles, runtime_members
from uqs.paths import UqsError, UqsPaths
from uqs.paths import default_paths as real_paths
from uqs.runtimes import RUNTIMES
from uqs.stack import deployment, reporter
from uqs.stack import procs as stack_procs
from uqs.stack import runtime as stack_runtime

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
    assert row["query"] == (
        ".checks.tablecount[(),`fx_orderbook;0D00:15;.z.p+.eodtime.getdailyadjustment[];`time;3]"
    )
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


def _q_prelude(repo_root: Path) -> str:
    """What a TorQ process with -localtime 1 has loaded when reporter.q's
    query reaches it: `.proc.cp`/`.proc.cd` on the host's local clock, and
    the common code the checks run against - the timezone table, `.eodtime`
    at its GMT defaults, monitoringchecks.q."""
    torq = repo_root / "lib" / "torq"
    return f"""
.proc.cp:{{.z.P}};.proc.cd:{{.z.D}};.proc.localtime:1b;
.lg.o:{{[x;y]}};.lg.e:{{[x;y] -1 "ERR|",y}};
.lg.w:{{[x;y] -1 "WARN|",y}};
.proc.getconfigfile:{{enlist hsym `$"{torq}/config/",x}};
\\l {torq}/code/common/timezone.q
\\l {torq}/code/common/eodtime.q
\\l {torq}/code/common/monitoringchecks.q
"""


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
        _q_prelude(repo_root)
        + f"""
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


# ---------------------------------------------- the plant's clock (#1247)


def _check(name: str) -> reporter.ReporterCheck:
    return next(c for c in reporter.UQF_REPORTER_CHECKS if c.name == name)


def test_no_generated_query_reads_the_queried_process_clock():
    """rdb1 and hdb1 run with -localtime 1: `.proc.cp[]`/`.proc.cd[]` there
    are local time, the plant's stamps and partitions are not."""
    for check in reporter.UQF_REPORTER_CHECKS:
        assert ".proc.cp" not in check.query and ".proc.cd" not in check.query, check.query


def test_an_hdbdate_check_runs_every_day_and_tests_the_partitions_weekday():
    """reporter1 schedules by its own local calendar, so the weekday that
    matters - the partition's - is tested in the query."""
    check = _check("fx_orderbook_hdbdate")
    assert check.row()["daysofweek"] == "0 1 2 3 4 5 6"
    assert "in 2 3 4 5 6;" in check.query


@pytest.mark.parametrize(
    "tz", ["UTC", "Europe/London", "America/New_York", "Asia/Tokyo", "Pacific/Kiritimati"]
)
def test_the_checks_are_right_in_every_host_zone(q_binary, repo_root, tmp_path, tz):
    """The generated queries, run on a host in `tz` against tables stamped as
    the plant stamps them (UTC, datatimezone GMT): fresh rows never alert,
    rows two hours old always do; a full previous partition never alerts, an
    empty one does on a weekday. Before #1247 a host east of UTC alerted on
    the fresh table and one west of it never on the stale one."""
    activity, hdbdate = _check("fx_orderbook_activity"), _check("fx_orderbook_hdbdate")
    script = tmp_path / "clock.q"
    script.write_text(
        _q_prelude(repo_root)
        + f"""
n:{{count (value x)`messages}};
fx_orderbook:([] time:.z.p-0D00:00:01*til 100);
-1 "fresh:",string n "{activity.query}";
fx_orderbook:([] time:.z.p-0D02:00:00+0D00:00:01*til 100);
-1 "stale:",string n "{activity.query}";
fx_orderbook:([] date:enlist ("d"$.z.p)-1);
-1 "full:",string n "{hdbdate.query}";
fx_orderbook:([] date:enlist "d"$.z.p);
-1 "empty:",string n "{hdbdate.query}";
exit 0
"""
    )
    q, env = q_binary
    out = subprocess.run(
        [q, str(script)], capture_output=True, text=True, timeout=60,
        env={**env, "TZ": tz}, cwd=repo_root,
    )  # fmt: skip
    labels = ("fresh:", "stale:", "full:", "empty:")
    got = dict(x.split(":", 1) for x in out.stdout.splitlines() if x.startswith(labels))
    yesterday = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    weekday = (yesterday - dt.date(2000, 1, 1)).days % 7
    assert got.keys() == {"fresh", "stale", "full", "empty"}, out.stdout + out.stderr
    assert got["fresh"] == "0" and got["stale"] != "0", (tz, got)
    assert got["full"] == "0", (tz, got)
    assert (got["empty"] != "0") == (weekday in reporter.WEEKDAYS), (tz, got)


# ---------------------------------------- the profile's processes (#1248)


def test_a_profile_without_the_producer_leaves_its_checks_out():
    """`--profile essential` starts no fxorderbookfeed1, fx_orderbook's only
    producer: its checks go, with the reason; crypto_book's stays - an
    external producer writes it."""
    essential = profiles.resolve(["essential"])
    kept, left_out = reporter.reporter_plan(RUNTIMES["uqf"], PROCS, selected=essential)
    assert [c.name for c in kept] == ["rdbmemorycheck", "crypto_book_activity"]
    assert left_out == [
        f"{name}: fx_orderbook - its producer is not among the processes this uqf "
        "deployment was started with"
        for name in ("fx_orderbook_activity", "fx_orderbook_hdbdate")
    ]


def test_a_full_start_keeps_every_check():
    kept, left_out = reporter.reporter_plan(RUNTIMES["uqf"], PROCS)
    assert [c.name for c in kept] == [c.name for c in reporter.UQF_REPORTER_CHECKS]
    assert left_out == []


def test_the_recorded_deployment_set_is_used_when_none_is_given(tmp_path):
    """A stop or summary bootstraps too: it rewrites the file for the set the
    last start recorded, not for the whole runtime."""
    paths = _paths(tmp_path, "uqf")
    deployment.select(paths, profiles.resolve(["essential"]))
    reporter.write_reporter_config(paths, PROCS)
    assert [r["name"] for r in _rows(paths)] == ["rdbmemorycheck", "crypto_book_activity"]


def test_a_profile_start_bootstraps_with_its_set(tmp_path, monkeypatch):
    """bootstrap writes reporter.csv before the start records the set: it is
    handed the set the start is about to record."""
    paths = _paths(tmp_path, "uqf")
    essential = profiles.resolve(["essential"])
    seen: list[Any] = []
    monkeypatch.setattr(stack_runtime.start_policy, "refuse_start", lambda *_a: None)
    monkeypatch.setattr(stack_runtime, "torq_launcher", lambda _p: Path("torq.sh"))
    monkeypatch.setattr(
        stack_runtime,
        "bootstrap",
        lambda _p, base_port=None, selected=None: seen.append(selected) or {},
    )
    monkeypatch.setattr(
        stack_runtime.subprocess, "run", lambda cmd, **_k: subprocess.CompletedProcess(cmd, 0)
    )
    stack_runtime.run_torq_sh(paths, ["start", " ".join(essential)], selected=essential)
    stack_runtime.run_torq_sh(paths, ["summary"])
    assert seen == [essential, None]
    assert deployment.members(paths) == essential
