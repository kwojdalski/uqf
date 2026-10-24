"""What `uqs job new` writes must LOAD in q, and register what it declares.

test_scaffold.py and test_scaffold_options.py read the generated text back
through the registry's own reader, which proves the tree can PARSE it. Neither
can prove q accepts it: that needs an interpreter, and both templates have
failed exactly that way before (a fixture that threw stopped src/etl/init.q
loading; an empty one was refused by `.qetl.transform.define`). A core change
that a template does not follow - a new required key, a renamed function -
would otherwise go unseen until someone scaffolded a job.

So every kind is scaffolded into a copy of the tree, the whole ETL tree is
loaded the way a process loads it, and each declaration is looked up in the
registry it should have landed in. The handlers are then called, and must
throw "not implemented": a scaffold that loads but does nothing is the
failure the templates exist to prevent.

Needs q, so it skips without one - like test_example_scripts.py. Run it with
the q lanes, where the interpreter is.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from uqs.cli.create_backfill import twin_target
from uqs.interpreter import q_interpreter
from uqs.paths import STACK_TABLES_TEST
from uqs.scaffold import jobs, write
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import (
    definition_columns,
    parse_columns,
    parse_raw_columns,
    table_definition,
)
from uqs.scaffold.docs import SHOWCASE_PAGE, STACK_PAGE
from uqs.scaffold.example import example_path
from uqs.scaffold.horizon import horizon_job
from uqs.scaffold.normalizer import normalizer
from uqs.scaffold.reaction import reaction

UQF_ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_SECONDS = 120

#: One of each kind, plus the ODBC variant of a source. The etl and the
#: normalizer read the feed's table, so they are scaffolded after it.
_FEED_COLUMNS = "sym:symbol, px:float"

#: Each line is `label: ok` or `label: FAILED`. Checked by label, so a check
#: that never ran is missing rather than silently passing.
_CHECKS = """
\\l src/init.q
\\l src/etl/init.q
registered:{[def;name] @[{x y; 1b}[def];name;{[e] 0b}]}
unwritten:{[f] @[{x[]; 0b};f;{[e] e like "*not implemented*"}]}
check:{[label;ok] -1 label,": ",$[ok;"ok";"FAILED"];}
check["feed registered"; registered[.qetl.job.stream.def;`smokefeed]]
check["etl registered"; registered[.qetl.job.stream.def;`smokeetl]]
check["normalizer registered"; registered[.qetl.job.stream.def;`smokenorm]]
check["horizon job registered"; registered[.qetl.job.stream.horizons.def;`smokehz]]
check["ipc source registered"; registered[.qetl.source.def;`smokebf]]
check["odbc source registered"; registered[.qetl.source.def;`smokedb]]
check["odbc source declares its transport"; `odbc~.qetl.source.def[`smokedb]`transport]
check["odbc source reads its raw input"; (enlist `EVENTS)~.qetl.source.live_contracts[`smokedb][;1]]
check["ipc worker registered"; registered[.qetl.job.bounded.def;`smokebf_backfill]]
check["odbc worker registered"; registered[.qetl.job.bounded.def;`smokedb_backfill]]
check["local source declares its transport"; `local~.qetl.source.def[`smokelocal]`transport]
check["local worker registered"; registered[.qetl.job.bounded.def;`smokelocal_backfill]]
check["one-column source registered"; registered[.qetl.source.def;`smokeone]]
check["one-column worker registered"; registered[.qetl.job.bounded.def;`smokeone_backfill]]
check["feed on_timer throws not implemented"; unwritten {.qpipe.job.smokefeed.on_timer[]}]
check["poll feed declares poll and gets a generated timer";
    {d:.qetl.job.stream.def x; (`poll in key d) and `on_timer in key d}`smokepoll]
check["poll fetch throws not implemented"; unwritten {.qpipe.job.smokepoll.fetch 0Np}]
check["poll preview of an unwritten feed throws not implemented";
    unwritten {.qetl.job.stream.preview[`smokepoll;5]}]
check["compound poll declares the whole cursor trio";
    all `load`save`advances in key .qetl.job.stream.def[`smokepollc]`poll]
sent:([] t:`symbol$(); n:`long$())
day2:2026.01.02D00:00:00.000000000
implement:{[job]
    d:.qetl.job.stream.def job;
    page:([] ts:2026.01.01D00:00:00.000000000,day2; sym:`a`b; px:1 2f);
    / c is (::) on a first run with nothing saved - "from the beginning" -
    / and, for a compound cursor, a dictionary whose ts is the comparison.
    after:{[c] $[99h=type c; c`ts; c]};
    fetch:{[after;page;c] $[(::)~c; page; select from page where ts>after c]}[after;page];
    d[`poll]:d[`poll],`fetch`normalize!(fetch;{[p] select sym, px from p});
    if[not `load in key d`poll;
        d[`poll]:d[`poll],enlist[`next_cursor]!enlist {[p] last p`ts}];
    @[`.qetl.job.stream.jobs;job;:;enlist d];
    (` sv d[`ns],`publish) set {[t;x] `sent upsert (t;count x); count x};
    job}
quiet:{[job] r:.qetl.job.stream.preview[job;5]; (`previewed~r`state) and 0=count sent}
tick_now:{[job] (.qetl.job.stream.def[job]`on_timer)[]}
saved_value:{[job] .qetl.job.continuous.load_cursor_value job}
check["poll preview publishes nothing and saves no cursor";
    {implement x; quiet[x] and null .qetl.job.continuous.load_cursor x}`smokepoll]
check["poll timer publishes, then saves the cursor";
    {tick_now x; (2=exec sum n from sent) and day2=.qetl.job.continuous.load_cursor x}`smokepoll]
check["compound poll preview publishes nothing and saves no cursor";
    {`sent set 0#sent; implement x; quiet[x] and (::)~saved_value x}`smokepollc]
check["compound poll timer saves every cursor field";
    {tick_now x; (`ts`sym!(day2;`b))~saved_value x}`smokepollc]
check["etl on_batch throws not implemented"; unwritten {.qpipe.job.smokeetl.on_batch[`t;()]}]
check["reaction registered on its dataset";
    `smokerx in exec name from .qetl.reaction.for_dataset `smoke_hist]
check["reaction handler throws not implemented";
    unwritten {.qpipe.job.smokerx.handler[`smoke_hist;0Wp;0Wp]}]
.qetl.dag.adopt_reactions[];
check["reaction is the job-graph producer of what it writes";
    .qetl.dag.reaction_job[`smoke_hist;`smokerx] in .qetl.dag.producers `smoke_rx_out]
got:()
.qetl.job.stream.wire[`smokepass;{[t;x] `got set got,enlist (t;x)}]
ticks:{[job] update time:2026.01.01D00:00:00.000000000 from (get ` sv `.qpipe.job,job,`fixture)[]}
check["passthrough forwards its input to its output";
    {.qpipe.job.smokepass.on_batch[`smoke_ticks;ticks `smokepass];
     ((enlist `smoke_echo)~got[;0]) and .qpipe.job.smokepass.fixture[]~first got[;1]}[]]
check["passthrough refuses a table it does not subscribe to";
    @[{.qpipe.job.smokepass.on_batch[`smoke_other;ticks `smokepass]; 0b};::;
        {[e] e like "*subscribes to*"}]]
check["passthrough transform verifies as scaffolded";
    all exec passed from .qetl.transform.verify `smokepass_passthrough]
check["streaming derive transform fails verification until written";
    not all exec passed from .qetl.transform.verify `smokederive_derive]
check["streaming derive handler throws not implemented";
    unwritten {.qpipe.job.smokederive.on_batch[`smoke_ticks;ticks `smokederive]}]
check["backfill derive worker registered"; registered[.qetl.job.bounded.def;`smokebfd_backfill]]
check["backfill derive transform fails verification until written";
    not all exec passed from .qetl.transform.verify `smokebfd_backfill_transform]
check["a twin is its streaming job's twin"; `smoketwin_backfill in .qetl.uptime.twins `smokefeed]
exit 0
"""

LABELS = [line.split('"')[1] for line in _CHECKS.splitlines() if line.startswith('check["')]


def _kdbx() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


def _status_dir(env: dict[str, str], root: Path) -> dict[str, str]:
    """Cursors land in the copy, not wherever the caller's stack keeps them."""
    return {**env, "UQF_STATUS_DIR": str(root / "status")}


def _copy_of_the_tree(root: Path) -> None:
    """What loading and scaffolding touch: src/, the plant's q files, the
    vendored database.q the plant registry reads quote and trade from, the
    test list and the two doc pages a scaffold appends to. Not the whole
    repository."""
    shutil.copytree(UQF_ROOT / "src", root / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", root / "scripts" / "processes")
    vendored = Path("lib/torq-finance-starter-pack/database.q")
    for rel in (STACK_TABLES_TEST, vendored, STACK_PAGE, SHOWCASE_PAGE):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, root / rel)


def _scaffold_every_kind(root: Path) -> None:
    feed_table = table_definition("smoke_ticks", parse_columns(_FEED_COLUMNS))
    plans = [
        jobs.streaming_job("smokefeed", [], "smoke_ticks", _FEED_COLUMNS),
        jobs.streaming_job("smokepoll", [], "smoke_poll_ticks", _FEED_COLUMNS, poll=True),
        jobs.streaming_job(
            "smokepollc", [], "smoke_pollc_ticks", _FEED_COLUMNS, poll=True, cursor="ts,sym"
        ),
        jobs.streaming_job(
            "smokeetl",
            ["smoke_ticks"],
            "smoke_stats",
            "sym:symbol, n:long",
            known_tables={"smoke_ticks"},
        ),
        normalizer(
            "smokenorm",
            ["smoke_ticks"],
            parse_columns("sym:symbol, mid:float"),
            {"smoke_ticks": definition_columns(feed_table)},
            known_tables={"smoke_ticks"},
        ),
        # each smoke_ticks row evaluated 10s on, against smoke_poll_ticks (#949)
        horizon_job(
            "smokehz",
            ["smoke_ticks", "smoke_poll_ticks"],
            parse_columns("sym:symbol, px:float"),
            {
                "smoke_ticks": definition_columns(feed_table),
                "smoke_poll_ticks": definition_columns(
                    table_definition("smoke_poll_ticks", parse_columns(_FEED_COLUMNS))
                ),
            },
            "0D00:00:10",
            known_tables={"smoke_ticks", "smoke_poll_ticks"},
        ),
        backfill.bounded_worker("smokebf", "smoke_hist", "sym:symbol, px:float"),
        backfill.bounded_worker(
            "smokebfd", "smoke_bfd", "sym:symbol, px:float", transform="derive"
        ),
        # One table in, one out: the two --transform modes of a streaming job.
        jobs.streaming_job(
            "smokepass",
            ["smoke_ticks"],
            "smoke_echo",
            _FEED_COLUMNS,
            known_tables={"smoke_ticks"},
            transform="passthrough",
            definitions={"smoke_ticks": feed_table},
        ),
        jobs.streaming_job(
            "smokederive",
            ["smoke_ticks"],
            "smoke_derived",
            "sym:symbol, n:long",
            known_tables={"smoke_ticks"},
            transform="derive",
            definitions={"smoke_ticks": feed_table},
        ),
        backfill.bounded_worker(
            "smokedb",
            "smoke_db",
            "sym:symbol, amt:float",
            transport="odbc",
            raw=("EVENTS", parse_raw_columns("CREATED_AT:timestamp, PAYLOAD:any")),
        ),
        backfill.bounded_worker(
            "smokelocal", "smoke_local", "sym:symbol, amt:float", transport="local"
        ),
        # Only the time column: a one-element columns/types, not an atom (#1595).
        backfill.bounded_worker("smokeone", "smoke_one", "time:timestamp"),
        # On a dataset the worker above fills, and writing, so the graph path runs.
        reaction(
            "smokerx",
            "smoke_hist",
            ["smoke_rx_out"],
            producers={"smoke_hist": ["smokebf_backfill1"]},
            taken=set(),
        ),
    ]
    for plan in plans:
        write.apply_plan(plan, root)
    # A twin is planned from the tree, so after the feed it twins is written:
    # its dataset and columns are read from smokefeed's declaration (#716).
    dataset, cols = twin_target(root, "smokefeed", None, None, {"smoke_ticks": feed_table})
    write.apply_plan(backfill.bounded_worker("smoketwin", dataset, cols, define_table=False), root)


def test_the_checks_are_all_named():
    """A check this list does not name would be printed and never asserted."""
    assert len(LABELS) == _CHECKS.count('check["')
    assert len(set(LABELS)) == len(LABELS)


def test_every_scaffolded_kind_loads_and_registers(tmp_path):
    qbin, env = _kdbx()
    _copy_of_the_tree(tmp_path)
    _scaffold_every_kind(tmp_path)
    (tmp_path / "smoke.q").write_text(_CHECKS)
    result = subprocess.run(
        [qbin, "smoke.q", "-q"],
        cwd=tmp_path,
        env=_status_dir(env, tmp_path),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        text=True,
    )
    reported = dict(
        line.rsplit(": ", 1)
        for line in result.stdout.splitlines()
        if line.endswith((": ok", ": FAILED"))
    )
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-25:])
    assert result.returncode == 0, f"the scaffolded tree did not load:\n{tail}"
    missing = [label for label in LABELS if label not in reported]
    failed = [label for label in LABELS if reported.get(label) == "FAILED"]
    assert not missing, f"checks that never ran: {missing}\n{tail}"
    assert not failed, f"checks that failed: {failed}\n{tail}"


#: Every job `_scaffold_every_kind` writes, by the name `.qpipe.job.<job>` it registers under.
_SCAFFOLDED_JOBS = [
    "smokefeed", "smokepoll", "smokepollc", "smokeetl", "smokenorm", "smokehz",
    "smokebf_backfill", "smokebfd_backfill", "smokepass", "smokederive",
    "smokedb_backfill", "smokelocal_backfill", "smokeone_backfill", "smoketwin_backfill",
    "smokerx",
]  # fmt: skip

_SUITES = """
\\l tests/lib/qunit.q
\\l tests/lib/support.q
\\l src/init.q
\\l src/etl/init.q
.qt.support.load_suites[];
want:{[jobs] .qt.support.job_suite each jobs};
found:.qt.support.suites[];
-1 "SUITES:",", " sv string found;
/ one suite at a time, not .qunit.runTests: it razes every result table, and
/ joining tables whose same-named columns hold different types is 'type on
/ PeachQ (a table of one bool, joined to a table of one symbol) where KDB-X
/ makes a mixed list
res:.qunit.runNsTests each found;
-1 "ERRORED:",", " sv string raze {exec name from x where status=`error} each res;
-1 "RAN:",", " sv string distinct raze {exec namespace from x} each res;
exit 0
"""


def test_every_scaffolded_test_is_found_by_the_runner_and_runs(tmp_path):
    """The bug #350 closed, held the new way (#1412): a generated test must be
    FOUND - by what it holds, not by a list - and RUN, whatever kind of job it
    belongs to. Every kind is scaffolded into a copy, its test file loaded with
    the harness, and the suites the loader discovers must be exactly the jobs'
    own: `.qt.qpipe.job.<job>`, the name `jobs.test_namespace` computes. None
    may error - a stub fails by assertion, which is the point of a stub."""
    qbin, env = _kdbx()
    _copy_of_the_tree(tmp_path)
    for rel in ("tests/lib/qunit.q", "tests/lib/support.q", "tests/lib/discovery.q"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, tmp_path / rel)
    surface = Path("docs/reference/surfaces/current/variables.csv")
    (tmp_path / surface).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(UQF_ROOT / surface, tmp_path / surface)
    (tmp_path / "tests/q").mkdir(parents=True, exist_ok=True)
    _scaffold_every_kind(tmp_path)
    # the plant's table list was only there to be appended to; it is a suite of its own
    (tmp_path / STACK_TABLES_TEST).unlink()
    (tmp_path / "suites.q").write_text(_SUITES)
    result = subprocess.run(
        [qbin, "suites.q", "-q"],
        cwd=tmp_path,
        env=_status_dir(env, tmp_path),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        text=True,
    )
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-25:])
    lines = {
        ln.split(":", 1)[0]: ln.split(":", 1)[1] for ln in result.stdout.splitlines() if ":" in ln
    }
    assert result.returncode == 0 and "SUITES" in lines, f"the harness did not load:\n{tail}"
    suites = {s.strip() for s in lines["SUITES"].split(",")}
    expected = {f".{jobs.test_namespace(job)}" for job in _SCAFFOLDED_JOBS}
    assert suites == expected, (sorted(suites ^ expected), tail)
    ran = {s.strip() for s in lines["RAN"].split(",")}
    assert ran == expected, f"found but not run: {sorted(expected - ran)}"
    # A stub fails by assertion. Only a derive job's test errors, by design: its
    # derive throws "not implemented" until written, and the test calls it.
    errored = [n.strip() for n in lines["ERRORED"].split(",") if n.strip()]
    stray = [n for n in errored if not n.startswith(".qt.qpipe.job.smokederive.")]
    assert not stray, f"a scaffolded test errored instead of failing: {stray}\n{tail}"


def test_python_and_q_name_a_jobs_suite_the_same_way(tmp_path):
    """`jobs.test_namespace` and `.qt.support.job_suite` are one rule written
    twice, because the scaffold writes the name and the contract-driver lookup
    reads it; this holds them to the same answer."""
    qbin, env = _kdbx()
    script = tmp_path / "names.q"
    script.write_text(
        "\\l tests/lib/qunit.q\n\\l tests/lib/support.q\n"
        "-1 each string .qt.support.job_suite each "
        "`fx_markout`hdb_fx_markouts_backfill`rebuild_positions;\n"
        "exit 0\n"
    )
    result = subprocess.run(
        [qbin, str(script), "-q"],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    got = [ln for ln in result.stdout.splitlines() if ln.startswith(".qt.")]
    want = [
        f".{jobs.test_namespace(j)}"
        for j in ("fx_markout", "hdb_fx_markouts_backfill", "rebuild_positions")
    ]
    assert got == want


@pytest.mark.parametrize("transport", ["ipc", "local"])
def test_a_scaffolded_backfill_example_runs_straight_away(tmp_path, transport):
    """#713's acceptance: `uqs job new X --kind backfill`, then
    `q scripts/examples/X_example.q` publishes the fixture into an HDB.

    Straight after scaffolding, with nothing written: the fixture is a real
    row and a passthrough worker publishes it, so the example must exit 0.
    A `local` source's query is still the stub, so it takes the fixture path
    too and says so.
    """
    qbin, env = _kdbx()
    _copy_of_the_tree(tmp_path)
    write.apply_plan(
        backfill.bounded_worker("smokeex", "smoke_ex", "sym:symbol, px:float", transport=transport),
        tmp_path,
    )
    result = subprocess.run(
        [qbin, example_path("smokeex").as_posix(), "-q"],
        cwd=tmp_path,
        env=_status_dir(env, tmp_path),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        text=True,
    )
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-25:])
    assert result.returncode == 0, f"the scaffolded example did not run:\n{tail}"
    assert "ok    at least one row was written" in result.stdout, tail
    assert "ok    every published row is on disk" in result.stdout, tail
    assert ("query is still the scaffold's stub" in result.stdout) == (transport == "local"), tail
