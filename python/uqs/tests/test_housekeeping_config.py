"""housekeeping1's generated rules (stack/housekeeping.py, #2155): selected
before housekeeping.q loads, no rule touching a tickerplant log inside the
replay retention window, never a tarball, and a log housekeeping gzipped still
replayed by TorQ's own replay code."""

from __future__ import annotations

import csv
import io
import os
import subprocess
import time
from pathlib import Path

import pytest

from uqs.paths import UqsPaths
from uqs.paths import default_paths as real_paths
from uqs.stack import housekeeping
from uqs.stack import procs as stack_procs

OVERLAY = "${UQF_SCRIPTS}/processes/uqs_housekeeping_config.q"
DAY = 24 * 60 * 60
WINDOW = housekeeping.TPLOG_REPLAY_RETENTION_DAYS


def _paths(tmp_path: Path) -> UqsPaths:
    (tmp_path / "app" / "appconfig").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "uqs",
        runtime="uqf",
    )


def _tplog_rows() -> list[dict[str, str]]:
    return [r for r in housekeeping.housekeeping_rows() if r["path"].startswith("{KDBTPLOG}")]


# ------------------------------------------------------------- selection


def test_housekeeping1_loads_the_overlay_before_housekeeping_q():
    """housekeeping.q reads `.hk.inputcsv` once, as it loads - the file that
    sets it must come first."""
    real = real_paths()
    vendored = (real.torqapphome / "appconfig" / "process.csv").read_text()
    upstream = {r["procname"]: r["load"] for r in csv.DictReader(io.StringIO(vendored))}
    composed = {r["procname"]: r for r in stack_procs.effective_process_rows(real)}
    loads = composed["housekeeping1"]["load"].split()
    n = len(housekeeping.LOAD_BEFORE)
    assert loads[0] == OVERLAY and loads[n] == upstream["housekeeping1"]
    # The compression rule's files (#2150): the policy's path before TorQ's
    # compress.q reads it, and the wrapper TorQ's run goes through.
    assert loads[:n] == list(housekeeping.LOAD_BEFORE)
    assert loads[1:n] == [
        "${UQF_SCRIPTS}/processes/uqs_compression_config.q",
        "${KDBCODE}/common/compress.q",
        "${UQF_ROOT}/src/etl/core/log.q",
        "${UQF_SCRIPTS}/processes/torq_pipeline.q",
        "${UQF_SCRIPTS}/processes/torq_compress.q",
    ]


def test_the_generated_file_is_in_torqdata_and_the_sites_is_neither_read_nor_written(tmp_path):
    paths = _paths(tmp_path)
    site = paths.torqapphome / "appconfig" / "housekeeping.csv"
    site.write_text("function,path,match,exclude,age,agemin,checkfordirectory\nbogus,,,,,,\n")
    before = site.read_text()
    housekeeping.write_housekeeping_config(paths)
    assert housekeeping.config_path(paths) == paths.torqdata / "housekeeping.csv"
    text = housekeeping.config_path(paths).read_text()
    assert "bogus" not in text
    assert site.read_text() == before
    assert text.splitlines()[0] == ",".join(housekeeping.HOUSEKEEPING_FIELDS)


# ------------------------------------------------------------- the rules


def test_no_rule_targets_a_tickerplant_log_inside_the_retention_window():
    """Every rule under KDBTPLOG is aged in whole days (not minutes) by at
    least the declared window, and is the gzip-only directory function."""
    rows = _tplog_rows()
    assert rows, "the tickerplant-log rule is missing"
    for row in rows:
        assert row["function"] == housekeeping.TPLOG_FUNCTION, row
        assert int(row["age"]) >= WINDOW, row
        assert row.get("agemin", "") in ("", "0"), row


def test_no_rule_tars_or_deletes_a_tickerplant_log():
    rows = housekeeping.housekeeping_rows()
    assert not [r for r in rows if r["function"] == "tardir"]
    assert not [r for r in _tplog_rows() if r["function"] == "rm"]


def test_process_log_rules_are_kept_as_the_starter_pack_keeps_them():
    """The KDBLOG rows are the starter pack's, unchanged: none more eager."""
    starter = real_paths().torqapphome / "appconfig" / "housekeeping.csv"
    with starter.open(newline="") as f:
        theirs = [r for r in csv.DictReader(f) if r["path"].startswith("{KDBLOG}")]
    ours = [r for r in housekeeping.housekeeping_rows() if r["path"].startswith("{KDBLOG}")]
    key = ("function", "path", "match", "age")
    assert [tuple(r[k] for k in key) for r in ours] == [tuple(r[k] for k in key) for r in theirs]


# ------------------------------------------------------------- the q side

#: Writes two segmented log directories, as stp1 does: a log per table and the
#: stpmeta naming them. Each log holds three messages.
_MAKE_LOGS = r"""
mk:{[d]
  system "mkdir -p \"",d,"\"";
  files:{[d;t] f:hsym `$d,"/stp1_",string[t],"20261001000000"; f set (); h:hopen f;
    do[3; h enlist (`upd;t;(enlist .z.p;enlist `EURUSD))]; hclose h; f}[d] each `trade`quote;
  (hsym `$d,"/stpmeta") set ([] logname:files; tbls:enlist each `trade`quote);
  }
mk each getenv each `OLD`RECENT;
exit 0
"""

#: Runs TorQ's housekeeping.q, unedited, behind the overlay, against the
#: generated file - then replays the old directory with TorQ's own
#: getstplogs and readintofifo, read out of the vendored tickerlogreplay.q.
_RUN = r"""
.lg.o:{[x;y]}; .lg.w:{[x;y] -1 "W ",y}; .lg.e:{[x;y] -1 "E ",y};
.proc.sys:{[c] system c}; .proc.params:()!(); .proc.cp:{.z.p}; .proc.cd:{.z.d};
.proc.getconfigfile:{[f] `$getenv[`KDBAPPCONFIG],"/",f};
.rmvr.removeenvvar:{[s] ssr[ssr[s;"{KDBTPLOG}";getenv`KDBTPLOG];"{KDBLOG}";getenv`KDBLOG]};
.timer.rep:{[a;b;c;d;e;f;g]};
.hk.inputcsv:.proc.getconfigfile "housekeeping.csv";
.hk.runnow:0b;
\l scripts/processes/uqs_housekeeping_config.q
.qtorq.hk.gzreplayable:{[] getenv[`GZ]~enlist "1"};
\l lib/torq/code/processes/housekeeping.q
-1 "INPUTCSV ",inputcsv;
hkrun[];
src:read0 `:lib/torq/code/processes/tickerlogreplay.q;
fn:{[src;name] i:first where src like name,":{*"; n:1+first where (i _ src) like " };*";
  value (1+count name)_-1_"\n" sv src i+til n};
.replay.tablestoreplay:`trade`quote;
.replay.getstplogs:fn[src;"getstplogs"];
.replay.readintofifo:fn[src;"readintofifo"];
upd:{[t;x] msgs+:1};
msgs:0;
one:{[f] if[not f like "*.gz"; :-11!(0W;f)];
  p:.replay.readintofifo 1_string f; r:-11!(0W;hsym `$p); hdel hsym `$p; r};
one each .replay.getstplogs hsym `$getenv`OLD;
-1 "REPLAYED ",string msgs;
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
    """A generated housekeeping.csv, a site's that tars, and two log
    directories: one past the window, one just inside it."""
    q, env = q_binary
    paths = _paths(tmp_path)
    housekeeping.write_housekeeping_config(paths)
    site = tmp_path / "app" / "appconfig" / "housekeeping.csv"
    site.write_text(
        "function,path,match,exclude,age,agemin,checkfordirectory\ntardir,{KDBTPLOG}/,*,,0,,1\n"
    )
    tplogs = tmp_path / "tplogs"
    (tmp_path / "logs").mkdir()
    old, recent = tplogs / "stp1_2026.10.01", tplogs / "stp1_2026.10.30"
    env = {
        **env,
        "TORQDATA": str(paths.torqdata),
        "KDBAPPCONFIG": str(site.parent),
        "KDBTPLOG": str(tplogs),
        "KDBLOG": str(tmp_path / "logs"),
        "OLD": str(old),
        "RECENT": str(recent),
    }
    make = tmp_path / "make.q"
    make.write_text(_MAKE_LOGS)
    out = subprocess.run([q, str(make)], capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    _age(old, (WINDOW + 3) * DAY)
    # Inside the window by an hour: housekeeping must leave it alone.
    _age(recent, WINDOW * DAY - 3600)

    def run(gz: bool) -> list[str]:
        script = tmp_path / "run.q"
        script.write_text(_RUN)
        out = subprocess.run(
            [q, str(script)], capture_output=True, text=True, timeout=120,
            env={**env, "GZ": "1" if gz else "0"}, cwd=repo_root,
        )  # fmt: skip
        assert out.returncode == 0, out.stdout + out.stderr
        return [x for x in out.stdout.splitlines() if x.strip()]

    return run, paths, old, recent


#: The tests that replay a GZIPPED log need KDB-X (the `kdbx` fixture): TorQ's
#: replay reads one through a named pipe (readintofifo), and PeachQ's `-11!`
#: never returns from reading a fifo - the run blocks until it times out. The
#: ones that replay only plain logs run on either.


def _listing(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir())


def test_housekeeping_q_reads_the_generated_file_not_the_sites(stack):
    run, paths, _old, _recent = stack
    lines = run(gz=False)
    assert f"INPUTCSV :{housekeeping.config_path(paths)}" in lines


@pytest.mark.kdbx
def test_a_log_housekeeping_zipped_is_still_replayed(kdbx, stack):
    run, _paths_, old, _recent = stack
    lines = run(gz=True)
    assert _listing(old) == [
        "stp1_quote20261001000000.gz",
        "stp1_trade20261001000000.gz",
        "stpmeta.gz",
    ], lines
    assert "REPLAYED 6" in lines, lines


@pytest.mark.kdbx
def test_a_log_inside_the_window_is_never_touched(kdbx, stack):
    run, _paths_, _old, recent = stack
    before = {p.name: p.stat().st_mtime for p in recent.iterdir()}
    run(gz=True)
    assert {p.name: p.stat().st_mtime for p in recent.iterdir()} == before


@pytest.mark.kdbx
def test_no_tarball_is_ever_produced(kdbx, stack):
    """The site's file tars every log directory; the generated one is read."""
    run, _paths_, old, _recent = stack
    run(gz=True)
    tplogs = old.parent
    assert not list(tplogs.rglob("*.tar*"))
    assert sorted(p.name for p in tplogs.iterdir()) == ["stp1_2026.10.01", "stp1_2026.10.30"]


def test_a_failed_zip_leaves_the_directory_replayable_as_written(stack):
    """gzip cannot write one copy: stpmeta and every original stay as they were."""
    run, _paths_, old, _recent = stack
    (old / "stp1_quote20261001000000.gz").mkdir()
    lines = run(gz=True)
    names = _listing(old)
    assert {"stpmeta", "stp1_quote20261001000000", "stp1_trade20261001000000"} <= set(names)
    assert "stpmeta.gz" not in names
    assert "REPLAYED 6" in lines, lines


def test_where_replay_cannot_read_gzip_the_directory_is_left_alone(stack):
    run, _paths_, old, _recent = stack
    before = _listing(old)
    lines = run(gz=False)
    assert _listing(old) == before
    assert "REPLAYED 6" in lines, lines


# ------------------------------------------------------------- the HDB reload

#: The overlay alone, with TorQ's connection set-up and the reload recorded:
#: nothing connects as it loads, and `.qtorq.hk.reload` (what `uqs data
#: compress` sends) connects before it reloads - and still reloads when the
#: connection set-up fails, the reload reporting what it could not reach.
_RELOAD = r"""
.lg.o:{[x;y]}; .lg.w:{[x;y] -1 "W ",y}; .lg.e:{[x;y] -1 "E ",y};
calls:();
.servers.startup:{[] calls,:`startup; if[getenv[`FAIL]~enlist "1"; 'nodiscovery]};
.qtorq.reload_hdb:{[] calls,:`reload; `registered`opened`reloaded!1 1 1};
\l scripts/processes/uqs_housekeeping_config.q
-1 "LOADED ",-3!calls;
-1 "CONNECTIONS ",-3!.servers.CONNECTIONS;
r:.qtorq.hk.reload[];
-1 "CALLS ",-3!calls;
-1 "RELOADED ",string r`reloaded;
exit 0
"""


@pytest.mark.parametrize("fail", [False, True])
def test_the_reload_connects_first_and_nothing_connects_at_load(
    q_binary, repo_root, tmp_path, fail
):
    """#2714: one registration of the HDBs, connected when a reload needs them."""
    q, env = q_binary
    paths = _paths(tmp_path)
    housekeeping.write_housekeeping_config(paths)
    script = tmp_path / "reload.q"
    script.write_text(_RELOAD)
    out = subprocess.run(
        [q, str(script)], capture_output=True, text=True, timeout=60, cwd=repo_root,
        env={**env, "TORQDATA": str(paths.torqdata), "FAIL": "1" if fail else "0"},
    )  # fmt: skip
    assert out.returncode == 0, out.stdout + out.stderr
    lines = out.stdout.splitlines()
    assert "LOADED ()" in lines and "CONNECTIONS ,`hdb" in lines, lines
    assert "CALLS `startup`reload" in lines and "RELOADED 1" in lines, lines
    warned = any(
        x.startswith("W could not register the HDBs to reload: nodiscovery") for x in lines
    )
    assert warned == fail, lines
