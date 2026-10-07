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

from uqs.interpreter import q_interpreter
from uqs.paths import RUN_TESTS_FILE, STACK_TABLES_TEST
from uqs.scaffold import jobs, write
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import definition_columns, parse_columns, table_definition
from uqs.scaffold.docs import SHOWCASE_PAGE, STACK_PAGE
from uqs.scaffold.example import example_path
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
check["ipc source registered"; registered[.qetl.source.def;`smokebf]]
check["odbc source registered"; registered[.qetl.source.def;`smokedb]]
check["odbc source declares its transport"; `odbc~.qetl.source.def[`smokedb]`transport]
check["ipc worker registered"; registered[.qetl.job.bounded.def;`smokebf_backfill]]
check["odbc worker registered"; registered[.qetl.job.bounded.def;`smokedb_backfill]]
check["local source declares its transport"; `local~.qetl.source.def[`smokelocal]`transport]
check["local worker registered"; registered[.qetl.job.bounded.def;`smokelocal_backfill]]
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
    two test lists and the two doc pages a scaffold appends to. Not the whole
    repository."""
    shutil.copytree(UQF_ROOT / "src", root / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", root / "scripts" / "processes")
    vendored = Path("lib/torq-finance-starter-pack/database.q")
    for rel in (RUN_TESTS_FILE, STACK_TABLES_TEST, vendored, STACK_PAGE, SHOWCASE_PAGE):
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
        backfill.bounded_worker("smokedb", "smoke_db", "sym:symbol, amt:float", transport="odbc"),
        backfill.bounded_worker(
            "smokelocal", "smoke_local", "sym:symbol, amt:float", transport="local"
        ),
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
