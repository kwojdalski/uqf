"""housekeeping1 compresses the HDB by the declared policy (stack/housekeeping.py,
#2150): one rule, `hdbcompress`, running TorQ's `.cmp` through
`.qtorq.compress.run_held` - the selection and hold `uqs data compress` makes,
the checked apply, then the HDB reload - on the schedule declared beside the
rules, with the tickerplant-log rules of #2155 unchanged.

End to end: TorQ's housekeeping.q, unedited, loaded after the overlay files
housekeeping1's composed `load` column names, runs `hkrun[]` over the generated
rules against a real HDB, tickerplant logs and process logs. Only what a TorQ
process supplies is stood in for: .lg, .proc, .timer, .rmvr and the discovery
table `.qtorq.reload_hdb` reads, whose one HDB records the reload it is sent.

KDB-X only: the pinned PeachQ has no -19!, so it can compress nothing (#2153).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from uqs.interpreter import PEACHQ, q_impl
from uqs.paths import UqsPaths
from uqs.paths import default_paths as real_paths
from uqs.stack import compression, housekeeping
from uqs.stack import procs as stack_procs

pytestmark = pytest.mark.skipif(
    q_impl() == PEACHQ, reason="PeachQ has no -19!, so .cmp can compress nothing (#2153)"
)

DAY = 24 * 60 * 60
WINDOW = housekeeping.TPLOG_REPLAY_RETENTION_DAYS
#: Days ago of each partition: past every minage the policy declares (two
#: weeks at most), and inside every one (three days at least).
OLD, YOUNG = 30, 1
#: A table the policy does not name, so it takes the stack default (gzip).
TABLE = "hk_trade"
SITE_RULES = (
    "function,path,match,exclude,age,agemin,checkfordirectory\ntardir,{KDBTPLOG}/,*,,0,,1\n"
)


def _paths(tmp_path: Path, repo_root: Path) -> UqsPaths:
    (tmp_path / "app" / "appconfig").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=repo_root / "lib" / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=repo_root / "scripts",
        orchestrator_dir=tmp_path / "uqs",
        runtime="uqf",
    )


def _loads(paths: UqsPaths, repo_root: Path) -> list[str]:
    """housekeeping1's `load` column as stack/procs.py composes it, resolved."""
    row = next(
        r
        for r in stack_procs.effective_process_rows(real_paths())
        if r["procname"] == "housekeeping1"
    )
    subst = {
        "UQF_SCRIPTS": str(repo_root / "scripts"),
        "UQF_ROOT": str(repo_root),
        "KDBCODE": str(repo_root / "lib" / "torq" / "code"),
    }
    return [re.sub(r"\$\{(\w+)\}", lambda m: subst[m.group(1)], f) for f in row["load"].split()]


#: Writes the HDB - TABLE on OLD and YOUNG days ago - and two segmented
#: tickerplant log directories, as stp1 does.
_MAKE = r"""
root:hsym `$getenv`HDB;
t:{[n] ([] sym:n?`EURUSD`GBPUSD; px:1.0+n?0.01; qty:til n; flag:n#01b)};
part:{[root;d] (` sv root,(`$string d),`$getenv[`TABLE],"/") set .Q.en[root;t 20000]};
part[root] each .z.D - "J"$" " vs getenv`DAYS;
mk:{[d]
  system "mkdir -p \"",d,"\"";
  files:{[d;t] f:hsym `$d,"/stp1_",string[t],"20261001000000"; f set (); h:hopen f;
    do[3; h enlist (`upd;t;(enlist .z.p;enlist `EURUSD))]; hclose h; f}[d] each `trade`quote;
  (hsym `$d,"/stpmeta") set ([] logname:files; tbls:enlist each `trade`quote);
  }
mk each getenv each `TPOLD`TPRECENT;
exit 0
"""

#: housekeeping1, standing in only for what TorQ supplies: its common code
#: (compress.q, pointed at the SITE's config first, as TorQ loads it), then the
#: composed load column - the overlay files and housekeeping.q - then hkrun[].
_RUN = r"""
.lg.o:{[x;y] -1 "O ",y}; .lg.w:{[x;y] -1 "W ",y}; .lg.e:{[x;y] -1 "E ",y};
.proc.sys:{[c] system c}; .proc.params:()!(); .proc.cp:{.z.p}; .proc.cd:{.z.d};
.proc.getconfigfile:{[f] enlist `$getenv[`KDBAPPCONFIG],"/",f};
.rmvr.removeenvvar:{[s] {[s;v] ssr[s;"{",v,"}";getenv `$v]}/[s;("TORQDATA";"KDBTPLOG";"KDBLOG")]};
.timer.rep:{[start;e;p;f;m;d;x] -1 "TIMER ",string start};
.servers.CONNECTIONS:`symbol$();
.servers.SERVERS:([] proctype:enlist `hdb);
.servers.startup:{[] -1 "SERVERS STARTUP ",-3!.servers.CONNECTIONS};
reloads:();
.servers.getservers:{[a;b;c;d;e] ([] w:enlist {[m] reloads,:enlist first m; 1b})};
.hk.runnow:0b;
\l lib/torq/code/common/compress.q
{[f] system "l ",f} each " " vs getenv`LOADS;
-1 "INPUTCSV ",inputcsv;
-1 "CMPCSV ",.cmp.inputcsv;
-1 "CONNECTIONS ",-3!.servers.CONNECTIONS;
/ HOLD: a backfill's coverage claim on TABLE's OLD day, as the writer leaves it -
/ unreleased (no readiness watermark yet), or released (its readers caught up).
if[count hold:getenv`HOLD;
  tb:`$getenv`TABLE; d:.z.D-"J"$getenv`HOLD_DAYS;
  system "mkdir -p \"",.qetl.job.bounded.state.lock_dir[],"\"";
  claim:`dataset`partition`source_version`range_from`range_to!(tb;`;`v1;"p"$d;"p"$d+1);
  claim,:`rows_published`recorded_at`superseded_at`run_id`touched!(100;.z.p;0Wp;0Ng;`date$());
  ledger:.qetl.coverage.empty_ledger[] upsert claim;
  .qetl.job.bounded.state.durable_set[.qetl.coverage.ledger_path[];ledger];
  key1:([] dataset:enlist tb; partition:enlist `; source_version:enlist `v1);
  if["released"~hold; .qetl.coverage.mark_ready[key1;.z.p]];
  -1 "UNRELEASED ",-3!.qetl.coverage.unreleased[.z.p]];
hkrun[];
-1 "RELOADS ",-3!reloads;
root:hsym `$getenv`HDB;
under:{[d] ` sv' d,/:key d};
files:raze under each raze under each ` sv' root,/:p where (p:key root) like "[0-9]*";
files:files where not (string files) like "*.d";
{[f] -1 "FILE ",(1_string f)," ",$[count z:-21!f;string z`algorithm;"-"]} each files;
-1 "DONE";
exit 0
"""


def _age(path: Path, seconds: float) -> None:
    """Set *path* and everything in it to *seconds* old, the directory last."""
    stamp = time.time() - seconds
    for child in path.iterdir():
        os.utime(child, (stamp, stamp))
    os.utime(path, (stamp, stamp))


@pytest.fixture
def stack(q_binary, repo_root, tmp_path):
    q, env = q_binary
    paths = _paths(tmp_path, repo_root)
    housekeeping.write_housekeeping_config(paths)
    assert compression.write_compression_config(paths)
    appconfig = paths.torqapphome / "appconfig"
    (appconfig / "housekeeping.csv").write_text(SITE_RULES)
    # The site's compression config would compress everything at once.
    (appconfig / "compressionconfig.csv").write_text(
        "table,minage,column,calgo,cblocksize,clevel\ndefault,0,default,1,17,0\n"
    )
    data = paths.torqdata
    logs, tplogs = data / "logs", data / "tplogs"
    logs.mkdir()
    tpold, tprecent = tplogs / "stp1_2026.10.01", tplogs / "stp1_2026.10.30"
    env = {
        **env,
        "TORQDATA": str(data),
        "KDBAPPCONFIG": str(appconfig),
        "KDBTPLOG": str(tplogs),
        "KDBLOG": str(logs),
        "HDB": str(paths.hdb_dir),
        "TABLE": TABLE,
        "DAYS": f"{OLD} {YOUNG}",
        "TPOLD": str(tpold),
        "TPRECENT": str(tprecent),
        "LOADS": " ".join(_loads(paths, repo_root)),
    }
    make = tmp_path / "make.q"
    make.write_text(_MAKE)
    out = subprocess.run(
        [q, str(make)], capture_output=True, text=True, timeout=120, env=env, cwd=repo_root
    )
    assert out.returncode == 0, out.stdout + out.stderr
    # An end of day's hours ago, as at the scheduled run: find's -mmin +0
    # (BSD find's rounding) passes over a root written this minute.
    hour_ago = time.time() - 3600
    os.utime(paths.hdb_dir, (hour_ago, hour_ago))
    _age(tpold, (WINDOW + 3) * DAY)
    _age(tprecent, WINDOW * DAY - 3600)
    (logs / "old.log").write_text("x\n")
    days_ago = time.time() - 11 * DAY
    os.utime(logs / "old.log", (days_ago, days_ago))
    (logs / "new.log").write_text("x\n")
    script = tmp_path / "run.q"
    script.write_text(_RUN)

    def run(**extra: str) -> list[str]:
        out = subprocess.run(
            [q, str(script)],
            capture_output=True,
            text=True,
            timeout=300,
            env={**env, **extra},
            cwd=repo_root,
        )
        assert out.returncode == 0, out.stdout + out.stderr
        lines = [x for x in out.stdout.splitlines() if x.strip()]
        assert "DONE" in lines, out.stdout + out.stderr
        return lines

    return run, paths, tpold, tprecent


def _day(days_ago: int) -> str:
    """The partition q's .z.D - days_ago names: date arithmetic, as q's is.
    Counting days_ago*24h back in seconds lands on the next day instead when a
    clock change falls in between and it is the last hour before midnight."""
    return (date.today() - timedelta(days=days_ago)).strftime("%Y.%m.%d")


def _files(lines: list[str], days_ago: int) -> dict[str, str]:
    """Each column file of a partition, and -21!'s algorithm for it (- when plain)."""
    day = _day(days_ago)
    out = {}
    for line in lines:
        if line.startswith("FILE ") and f"/{day}/" in line:
            path, algo = line[5:].rsplit(" ", 1)
            out[Path(path).name] = algo
    return out


def _zipped(lines: list[str]) -> list[str]:
    """The process logs housekeeping.q's zip rule ran on. Its gzip is /bin/gzip,
    which macOS does not have, so the rule running is what is asserted, and
    the file only on Linux."""
    return sorted(Path(x.split(" ", 2)[2]).name for x in lines if x.startswith("O zipping "))


def _reloads(lines: list[str]) -> str:
    return next(x for x in lines if x.startswith("RELOADS "))


# ------------------------------------------------------------- the rules


def test_the_compression_rule_runs_the_root_wrapper_never_kdbzip():
    rows = housekeeping.housekeeping_rows()
    assert not [r for r in rows if r["function"] == "kdbzip"]
    hdb = [r for r in rows if r["function"] == housekeeping.COMPRESS_FUNCTION]
    assert len(hdb) == 1
    assert hdb[0]["path"] == "{TORQDATA}/" and hdb[0]["checkfordirectory"] == "1"


def test_the_rule_finds_the_hdb_bootstrap_points_torq_at(tmp_path):
    paths = _paths(tmp_path, real_paths().repo_root)
    assert paths.hdb_dir == paths.torqdata / housekeeping.HDB_DIR_NAME


def test_the_compression_rule_touches_no_tickerplant_log():
    """KDBTPLOG is under TORQDATA: the rule matches the HDB's name alone."""
    row = next(
        r
        for r in housekeeping.housekeeping_rows()
        if r["function"] == housekeeping.COMPRESS_FUNCTION
    )
    assert not any(ch in row["match"] for ch in "*?[")


def test_the_schedule_is_written_beside_the_rules(tmp_path):
    paths = _paths(tmp_path, real_paths().repo_root)
    housekeeping.write_housekeeping_config(paths)
    assert housekeeping.schedule_path(paths).read_text().splitlines() == [
        "runtime",
        *housekeeping.HOUSEKEEPING_RUNTIMES,
    ]


@pytest.mark.parametrize("runtimes", [(), ("24:00:00",), ("02:60:00",)])
def test_a_schedule_that_never_runs_or_is_no_time_of_day_is_refused(monkeypatch, runtimes):
    monkeypatch.setattr(housekeeping, "HOUSEKEEPING_RUNTIMES", runtimes)
    with pytest.raises(ValueError, match="never run|not a time of day"):
        housekeeping.render_housekeeping_schedule()


def test_uqs_config_shows_the_generated_schedule_resolved(tmp_path):
    """`uqs config housekeeping show` reads what housekeeping1 reads: the generated
    files, edited here to prove it, with placeholders resolved."""
    paths = _paths(tmp_path, real_paths().repo_root)
    housekeeping.write_housekeeping_config(paths)
    housekeeping.schedule_path(paths).write_text("runtime\n03:30:00\n")
    rows = housekeeping.effective_schedule(paths, {"TORQDATA": "/d", "KDBLOG": "/l"}, False)
    assert [r["function"] for r in rows] == [
        r["function"] for r in housekeeping.housekeeping_rows()
    ]
    assert {r["runs"] for r in rows} == {"daily 03:30:00 UTC"}
    hdb = next(r for r in rows if r["function"] == housekeeping.COMPRESS_FUNCTION)
    assert hdb["matching"] == "/d/hdb" and hdb["on"] == "directories"
    assert rows[0]["matching"] == "/l/*.log" and rows[0]["older_than"] == "10 days"


def test_before_bootstrap_it_shows_what_bootstrap_will_write(tmp_path):
    paths = _paths(tmp_path, real_paths().repo_root)
    rows = housekeeping.effective_schedule(paths, {}, True)
    assert {r["runs"] for r in rows} == {
        f"daily {t} local" for t in housekeeping.HOUSEKEEPING_RUNTIMES
    }
    assert len(rows) == len(housekeeping.housekeeping_rows())


# ------------------------------------------------------------- end to end


def test_housekeeping_q_reads_the_generated_rules_schedule_and_policy(stack):
    run, paths, _tpold, _tprecent = stack
    lines = run()
    assert f"INPUTCSV :{housekeeping.config_path(paths)}" in lines
    assert f"CMPCSV :{paths.torqdata / 'compressionconfig.csv'}" in lines
    timers = [x for x in lines if x.startswith("TIMER ")]
    assert [t.split("D")[1][:8] for t in timers] == list(housekeeping.HOUSEKEEPING_RUNTIMES)
    assert "CONNECTIONS ,`hdb" in lines


def test_housekeeping1_connects_to_no_hdb_until_a_rule_reloads(stack):
    """#2714: the HDBs are registered once, and connected when the compression
    rule runs - not as the overlay files load."""
    run, _paths_, _tpold, _tprecent = stack
    lines = run()
    loaded = lines.index("CONNECTIONS ,`hdb")
    starts = [i for i, x in enumerate(lines) if x.startswith("SERVERS STARTUP ")]
    assert starts and min(starts) > loaded, lines


def test_old_partitions_are_compressed_through_cmp_and_the_hdb_reloaded(stack):
    run, _paths_, _tpold, _tprecent = stack
    lines = run()
    old = _files(lines, OLD)
    assert set(old) == {"sym", "px", "qty", "flag"}, lines
    # The policy's default for a table it does not name: gzip (2).
    assert set(old.values()) == {"2"}, old
    assert _reloads(lines) == "RELOADS ,`reload", lines
    # TorQ made no connection at startup; the rule registers the HDBs first.
    assert "SERVERS STARTUP ,`hdb" in lines


def test_young_partitions_are_untouched(stack):
    run, paths, _tpold, _tprecent = stack
    young_dir = paths.hdb_dir / _day(YOUNG)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime) for p in (young_dir / TABLE).iterdir()}
    lines = run()
    assert set(_files(lines, YOUNG).values()) == {"-"}, lines
    after = {p.name: (p.read_bytes(), p.stat().st_mtime) for p in (young_dir / TABLE).iterdir()}
    assert after == before


def test_a_second_run_changes_nothing_and_reloads_nothing(stack):
    run, _paths_, _tpold, _tprecent = stack
    run()
    lines = run()
    assert set(_files(lines, OLD).values()) == {"2"}
    assert _reloads(lines) == "RELOADS ()", lines


def test_a_partition_a_backfill_is_still_writing_is_never_compressed(stack):
    """#2176: the coverage ledger claims TABLE's OLD day with no readiness
    watermark - a backfill still writing it. `uqs data compress` holds that
    partition, and so does the schedule: hkrun[] leaves every file of it as it
    was, and with nothing else old enough, changes nothing and reloads nothing."""
    run, paths, _tpold, _tprecent = stack
    old_dir = paths.hdb_dir / _day(OLD) / TABLE
    before = {p.name: (p.read_bytes(), p.stat().st_mtime) for p in old_dir.iterdir()}
    lines = run(HOLD="unreleased", HOLD_DAYS=str(OLD))
    assert any(x.startswith("UNRELEASED ") and _day(OLD) in x for x in lines), lines
    assert set(_files(lines, OLD).values()) == {"-"}, lines
    after = {p.name: (p.read_bytes(), p.stat().st_mtime) for p in old_dir.iterdir()}
    assert after == before
    assert _reloads(lines) == "RELOADS ()", lines


def test_a_released_partition_is_compressed(stack):
    """The same claim once its readers are released to see it: the day is
    compressed as any other."""
    run, _paths_, _tpold, _tprecent = stack
    lines = run(HOLD="released", HOLD_DAYS=str(OLD))
    unreleased = [x for x in lines if x.startswith("UNRELEASED ")]
    assert len(unreleased) == 1 and _day(OLD) not in unreleased[0], lines
    assert set(_files(lines, OLD).values()) == {"2"}, lines
    assert _reloads(lines) == "RELOADS ,`reload", lines


def test_tickerplant_log_and_process_log_rules_still_hold(stack):
    """#2155's rules run in the same hkrun: an old tickerplant log directory is
    left replayable (gzipped only where TorQ replays gzip, as written
    elsewhere), the one inside the window is not touched, nothing is tarred,
    and an old process log is gzipped."""
    run, paths, tpold, tprecent = stack
    recent = {p.name: p.stat().st_mtime for p in tprecent.iterdir()}
    old = sorted(p.name for p in tpold.iterdir())
    lines = run()
    assert {p.name: p.stat().st_mtime for p in tprecent.iterdir()} == recent
    now = sorted(p.name for p in tpold.iterdir())
    assert now in (old, sorted(f"{n}.gz" for n in old))
    assert not list((paths.torqdata / "tplogs").rglob("*.tar*"))
    assert _zipped(lines) == ["old.log"]


@pytest.fixture
def broken_policy(stack):
    """A policy TorQ's own checkcsv refuses (algorithm 4) in place of the generated one."""
    run, paths, tpold, tprecent = stack
    conf = paths.torqdata / "compressionconfig.csv"
    conf.write_text("table,minage,column,calgo,cblocksize,clevel\ndefault,7,default,4,17,6\n")
    return stack


def test_a_compression_failure_is_logged_and_stops_no_other_rule(broken_policy):
    run, paths, _tpold, _tprecent = broken_policy
    lines = run()
    errors = [x for x in lines if x.startswith("E Failed to compress ")]
    assert len(errors) == 1 and "incorrect compression algo" in errors[0], lines
    assert set(_files(lines, OLD).values()) == {"-"}
    assert _reloads(lines) == "RELOADS ()"
    # The process-log rule ran in the same hkrun.
    assert _zipped(lines) == ["old.log"]


def test_a_failure_in_the_first_rule_still_lets_the_next_rules_run(broken_policy):
    """The trap is what lets the rules after it run: the compression rule moved
    first, ahead of the process-log rules, does not stop them."""
    run, paths, _tpold, _tprecent = broken_policy
    rules = housekeeping.config_path(paths).read_text().splitlines()
    first = [r for r in rules[1:] if r.startswith(housekeeping.COMPRESS_FUNCTION + ",")]
    rest = [r for r in rules[1:] if r not in first]
    housekeeping.config_path(paths).write_text("\n".join([rules[0], *first, *rest]) + "\n")
    lines = run()
    assert any(x.startswith("E Failed to compress ") for x in lines), lines
    assert _zipped(lines) == ["old.log"]


def test_the_site_compression_config_is_not_the_one_used(stack):
    """The site's config compresses everything with algorithm 1 at minage 0:
    the young partition would be compressed if .cmp read it."""
    run, _paths_, _tpold, _tprecent = stack
    lines = run()
    assert set(_files(lines, YOUNG).values()) == {"-"}
