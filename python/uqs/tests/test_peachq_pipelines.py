"""The experimental PeachQ pipeline runtime: peachq-etl, its flattened q tree
(stack/qtree.py) and the capability check before a worker starts
(stack/capabilities.py).

The unit tests build a small tree under tmp_path and run the real converter
over it, with a stand-in interpreter that says what a test needs it to. The
tests marked with PEACHQ run the real PeachQ binary (UQF_PEACHQ): the tree this
checkout converts must load on it, and the capability probe must find what is
missing. UQF_PEACHQ_STACK_TEST=1 also starts a whole stack from a copy of this
checkout - a sidecar bundle's streaming job publishing into an RDB, and its
bounded worker recording coverage - which takes a minute and the 6550 ports.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs import runtimes
from uqs.model import runtime_members, schemas
from uqs.model.declarations import read_file_text
from uqs.paths import UqsError, UqsPaths
from uqs.runtimes import Runtime
from uqs.stack import capabilities, gateway_access, qtree, runtime, runtime_bundles
from uqs.stack.env import build_env

REPO = Path(stack_paths.__file__).resolve().parents[4]
PEACHQ = os.environ.get("UQF_PEACHQ", "")
needs_peachq = pytest.mark.skipif(
    not (PEACHQ and Path(PEACHQ).is_file()), reason="UQF_PEACHQ names no PeachQ binary"
)

NESTED = "\\d .demo.inner\noffset:2\nadd:{[amount] amount+offset}\n\\d .\n"
REFUSED = "\\d .demo.inner\n\\l other.q\n\\d .\n"


def _tree(root: Path) -> Path:
    """A checkout the converter can run over: src/, scripts/ and the real
    converter, plus a top-level entry the tree links rather than copies."""
    (root / "src" / "etl").mkdir(parents=True)
    (root / "src" / "init.q").write_text(NESTED)
    (root / "src" / "etl" / "init.q").write_text("/ the ETL tree\n")
    (root / "scripts" / "torqconfig").mkdir(parents=True)
    (root / "scripts" / "torqconfig" / "sources.csv").write_text("source,transport\n")
    converter = root / qtree.CONVERTER
    converter.parent.mkdir(parents=True)
    shutil.copy(REPO / qtree.CONVERTER, converter)
    (root / "docs").mkdir()
    return root


def _paths(root: Path, name: str = "peachq-etl") -> UqsPaths:
    return UqsPaths(
        repo_root=root,
        torqhome=root / "lib" / "torq",
        torqapphome=root / "lib" / "torq-finance-starter-pack",
        torqdata=root / "output" / "data",
        scripts_dir=root / "scripts",
        orchestrator_dir=root / "python" / "uqs",
        runtime=name,
    )


def _interpreter(tmp_path: Path, says: str = qtree.SENTINEL, code: int = 0) -> Path:
    """A stand-in q: records each call, prints `says`, exits `code`."""
    fake = tmp_path / "fakeq"
    fake.write_text(f'#!/bin/sh\necho "$PWD" >> "{tmp_path}/calls"\necho "{says}"\nexit {code}\n')
    fake.chmod(0o755)
    return fake


def _calls(tmp_path: Path) -> int:
    calls = tmp_path / "calls"
    return len(calls.read_text().splitlines()) if calls.is_file() else 0


# ------------------------------------------------------------ declarations


def test_peachq_etl_is_declared_experimental_on_a_flattened_tree():
    r = runtimes.RUNTIMES["peachq-etl"]
    assert (r.interpreter, r.pipelines, r.overlays) == ("peachq", True, True)
    assert (r.q_tree, r.experimental, r.profile) == ("flattened", True, "capture")
    assert runtimes.RUNTIMES["peachq"].pipelines is False, "the capture-only runtime stays"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({}, "cannot load this tree's pipelines as written"),
        ({"q_tree": "flattened"}, "pipelines on PeachQ are experimental"),
        ({"q_tree": "rewritten", "experimental": True}, "q_tree is source or flattened"),
    ],
)
def test_pipelines_on_peachq_need_the_flattened_tree_and_the_experimental_flag(fields, message):
    with pytest.raises(ValueError, match=message):
        Runtime("ghost", "x", "uqs-ghost", True, True, 6950, interpreter="peachq", **fields)


def test_only_a_flattened_runtime_points_its_processes_at_the_tree(tmp_path):
    env = build_env(_paths(tmp_path))
    tree = tmp_path / "output" / "data" / qtree.TREE
    assert env["UQF_ROOT"] == str(tree)
    assert env["UQF_SCRIPTS"] == str(tree / "scripts")
    assert env["KDBSERVCONFIG"] == str(tree / "scripts" / "torqconfig")
    assert env["KDBSERVCODE"] == str(tree / "scripts" / "torqcode")
    plain = build_env(_paths(tmp_path, "uqf"))
    assert (plain["UQF_ROOT"], plain["UQF_SCRIPTS"]) == (str(tmp_path), str(tmp_path / "scripts"))


# ------------------------------------------------------------- the q tree


def test_prepare_publishes_a_converted_copy_and_never_writes_the_checkout(tmp_path):
    root = _tree(tmp_path / "repo")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    paths = _paths(root)
    tree = qtree.prepare(paths, str(_interpreter(tmp_path)), dict(os.environ))
    assert tree == paths.torqdata / qtree.TREE
    flat = (tree / "src" / "init.q").read_text()
    assert ".demo.inner.add:{[amount] amount+.demo.inner.offset}" in flat
    assert (tree / "scripts" / "torqconfig" / "sources.csv").read_text() == "source,transport\n"
    assert (tree / "docs").is_symlink() and (tree / "docs").resolve() == root / "docs"
    assert not (tree / "output").exists(), "the tree never links the output holding it"
    report = json.loads((paths.torqdata / qtree.REPORT).read_text())
    assert report["transformed"] == ["src/init.q"] and report["loaded"] == qtree.SENTINEL
    assert {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file() and "output" not in p.parts
    } == before


def test_an_unchanged_source_reuses_the_tree_and_a_change_rebuilds_it(tmp_path):
    root = _tree(tmp_path / "repo")
    paths, fake = _paths(root), str(_interpreter(tmp_path))
    qtree.prepare(paths, fake, dict(os.environ))
    qtree.prepare(paths, fake, dict(os.environ))
    assert _calls(tmp_path) == 1, "a stop or a summary does not convert again"
    (root / "src" / "etl" / "init.q").write_text("/ changed\n")
    qtree.prepare(paths, fake, dict(os.environ))
    assert _calls(tmp_path) == 2
    assert (paths.torqdata / qtree.TREE / "src" / "etl" / "init.q").read_text() == "/ changed\n"


def test_a_load_that_exits_zero_without_the_sentinel_publishes_nothing(tmp_path):
    """q exits 0 whatever happened while it loaded: only the sentinel counts."""
    root = _tree(tmp_path / "repo")
    paths = _paths(root)
    qtree.prepare(paths, str(_interpreter(tmp_path)), dict(os.environ))
    published = paths.torqdata / qtree.TREE / "src" / "init.q"
    good = published.read_text()
    (root / "src" / "init.q").write_text(NESTED + "x:1\n")
    (tmp_path / "broken").mkdir()
    broken = str(_interpreter(tmp_path / "broken", says="'type at src/init.q:5"))
    with pytest.raises(UqsError, match=r"does not load .*exit 0, and no UQS_QTREE_LOADED"):
        qtree.prepare(paths, broken, dict(os.environ))
    assert published.read_text() == good, "the previous tree is exactly as it was"
    assert not (paths.torqdata / f"{qtree.TREE}.staging").exists()
    failed = json.loads((paths.torqdata / qtree.FAILED).read_text())
    assert "src/init.q:5" in failed["error"]


def test_a_refused_conversion_names_the_place_and_publishes_nothing(tmp_path):
    root = _tree(tmp_path / "repo")
    (root / "src" / "etl" / "loader.q").write_text(REFUSED)
    paths = _paths(root)
    with pytest.raises(
        UqsError, match=r"(?s)no tree was prepared.*src/etl/loader\.q:2:\d+ system-in-context"
    ):
        qtree.prepare(paths, str(_interpreter(tmp_path)), dict(os.environ))
    assert not (paths.torqdata / qtree.TREE).exists()
    assert _calls(tmp_path) == 0, "nothing was loaded, either"


def test_a_dry_run_converts_planned_files_in_memory_and_writes_nothing(tmp_path):
    root = _tree(tmp_path / "repo")
    before = sorted(p for p in root.rglob("*"))
    assert qtree.check(root, {"src/etl/streaming/job.q": NESTED})["q_files"] == 3
    with pytest.raises(UqsError, match=r"src/etl/streaming/job\.q:2"):
        qtree.check(root, {"src/etl/streaming/job.q": REFUSED})
    assert sorted(p for p in root.rglob("*")) == before


def test_only_a_starting_verb_prepares_the_tree(monkeypatch, tmp_path):
    """stop and summary must work even when the tree cannot be built; start
    and restart both load it."""
    prepared: list[list[str]] = []
    monkeypatch.setattr(runtime.start_policy, "refuse_start", lambda *_a: None)
    monkeypatch.setattr(runtime, "bootstrap", lambda paths, base_port: {"QCMD": "q"})
    monkeypatch.setattr(qtree, "prepare", lambda paths, qcmd, env: prepared.append(qcmd))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.delenv("UQS_TORQ_LAUNCHER", raising=False)
    paths = _paths(tmp_path)
    for verb in ("stop", "summary"):
        runtime.run_torq_sh(paths, [verb, "all"])
    assert prepared == []
    runtime.run_torq_sh(paths, ["start", "all"])
    runtime.run_torq_sh(paths, ["restart", "all"])
    assert prepared == ["q", "q"]
    runtime.run_torq_sh(_paths(tmp_path, "uqf"), ["start", "all"])
    assert prepared == ["q", "q"], "a runtime on the source tree prepares nothing"


# ------------------------------------------------- bundles and membership

BUNDLE_JOB = (
    "\\d .qpipe.job.synth_feed\nsent:0\non_timer:{[] sent+:1}\n\\d .\n"
    ".qetl.job.stream.define[`synth_feed;`procname`subscribe_to`publishes!"
    "(`synthfeed1;`symbol$();enlist `synth_tape)];\n"
)


def test_a_bundle_runtime_takes_declared_and_explicit_bundles(tmp_path, monkeypatch):
    def bundle(name: str) -> Path:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "bundle.json").write_text(json.dumps({"name": name, "version": "1"}))
        (folder / "synth_feed.q").write_text(BUNDLE_JOB)
        return folder

    declared, explicit = bundle("declared"), bundle("explicit")
    root = tmp_path / "repo"
    root.mkdir()
    (root / runtime_bundles.DECLARATION).write_text(json.dumps({"peachq-etl": ["../declared"]}))
    monkeypatch.delenv(runtime_bundles.DECLARATION_ENV, raising=False)
    members = runtime_bundles.resolve("peachq-etl", root, [explicit])
    assert [(m.bundle.name, m.source) for m in members] == [
        ("declared", "runtime"),
        ("explicit", "explicit"),
    ]
    assert declared.is_dir()


def test_a_bundle_installed_for_peachq_etl_joins_no_other_runtime(monkeypatch):
    entry = {
        "jobs": [{"name": "synth_feed", "kind": "streaming", "procname": "synthfeed1"}],
        "tables": ["synth_tape"],
        "runtimes": ["peachq-etl"],
    }
    monkeypatch.setattr(runtime_members, "_ledger", lambda: {"synthpq": entry})
    monkeypatch.setattr(runtime_members.profiles, "closure", lambda procs: set(procs))
    rts = runtimes.RUNTIMES
    assert runtime_members.pipeline_procnames(rts["peachq-etl"]) == {"synthfeed1"}
    assert "synthfeed1" not in (runtime_members.pipeline_procnames(rts["crypto"]) or set())
    assert runtime_members._bundle_split(rts["uqf"])[1] == {"synthfeed1"}, "excluded from uqf"


def test_each_runtime_is_started_with_only_its_own_data_access_tables(monkeypatch, tmp_path):
    """TorQ refuses to start an hdb whose -dataaccess list names a table its
    database lacks - which every runtime of a subset of the tables would be."""
    paths = _paths(tmp_path)
    props = paths.scripts_dir / gateway_access.TABLE_PROPERTIES
    props.parent.mkdir(parents=True)
    props.write_text("proctype,tablename,time\n,mkt_orderbook,time\n,synth_tape,time\n")
    monkeypatch.setattr(gateway_access, "plant_tables", lambda r: {"synth_tape"})
    assert gateway_access.table_properties_lines(paths) == [
        "proctype,tablename,time",
        ",synth_tape,time",
    ]
    monkeypatch.setattr(gateway_access, "plant_tables", lambda r: None)
    every = gateway_access.table_properties_lines(paths)
    assert every[1:3] == [",mkt_orderbook,time", ",synth_tape,time"], "None keeps every row"
    assert len(every) == 3 + len(schemas.table_names() - {"mkt_orderbook"}), (
        "and lists every other plant table too"
    )


def test_every_held_plant_table_is_listed_for_getdata(tmp_path: Path, monkeypatch) -> None:
    """#889: the browser reads through getdata, which serves no table it is
    not told about - so a held plant table the file omits gets a row."""
    paths = _paths(tmp_path)
    props = paths.scripts_dir / gateway_access.TABLE_PROPERTIES
    props.parent.mkdir(parents=True)
    props.write_text("proctype,tablename,primarytimecolumn\n,mkt_orderbook,time\n")
    monkeypatch.setattr(
        gateway_access, "plant_tables", lambda r: {"mkt_orderbook", "config_change"}
    )
    assert gateway_access.table_properties_lines(paths)[1:] == [
        ",mkt_orderbook,time",
        gateway_access.derived_row("config_change"),
    ]
    assert gateway_access.derived_row("config_change").split(",")[2] == "time"


# ------------------------------------------------------------ capabilities

WORKER = ".qetl.job.bounded.define[`w;`source`dataset`width`procname!(`s;`d;1D;`w1)];\n"


def _worker(root: Path, text: str, transport: str = "ipc"):
    (root / "src" / "etl" / "sources").mkdir(parents=True, exist_ok=True)
    (root / "src" / "etl" / "sources" / "s.q").write_text(f"transport:`{transport}\n")
    (decl,) = read_file_text(text, root / "w.q")
    return decl


@pytest.mark.parametrize(
    ("transport", "mode", "needs"),
    [
        ("ipc", None, []),
        ("local", None, []),
        ("odbc", "dry-run", ["native"]),
        ("odbc", None, ["native"]),
        ("odbc", "plan", []),
        ("odbc", "validate", []),
    ],
)
def test_what_a_worker_needs_depends_on_its_source_and_the_mode(
    tmp_path, monkeypatch, transport, mode, needs
):
    """An HDB write needs nothing: io_hdb.q writes and finishes partitions on
    PeachQ itself. Only an ODBC driver - a shared library - is out of reach."""
    monkeypatch.setattr(capabilities.transports, "default", lambda root: "ipc")
    decl = _worker(tmp_path, WORKER, transport)
    assert [cap for cap, _why in capabilities.requirements(tmp_path, decl, mode)] == needs


def test_a_bundle_source_in_a_file_of_another_name_is_still_found(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities.transports, "default", lambda root: "ipc")
    decl = _worker(tmp_path, WORKER)
    (tmp_path / "src" / "etl" / "sources" / "s.q").unlink()
    (tmp_path / "src" / "etl" / "sources" / "pb.q").write_text("source_name:`s\ntransport:`odbc\n")
    assert [c for c, _ in capabilities.requirements(tmp_path, decl, None)] == ["native"]


def test_a_worker_the_interpreter_cannot_run_is_refused_before_it_starts(tmp_path, monkeypatch):
    decl = _worker(tmp_path, WORKER, "odbc")
    monkeypatch.setattr(capabilities.transports, "default", lambda root: "ipc")
    monkeypatch.setattr(capabilities, "read_declarations", lambda root: [decl])
    monkeypatch.setattr(capabilities, "with_interpreter", lambda paths: {"QCMD": "/pq/q"})
    monkeypatch.setattr(
        capabilities, "probe", lambda qcmd: dict.fromkeys(capabilities.CAPABILITIES, False)
    )
    with pytest.raises(UqsError) as caught:
        capabilities.refuse_unsupported(_paths(tmp_path), "w", None)
    said = str(caught.value)
    assert "w cannot run on the peachq-etl runtime: /pq/q lacks" in said
    assert "loading a shared library" in said and "read through an ODBC driver" in said
    assert "Nothing was started" in said


def test_a_kdbx_runtime_is_never_probed(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities, "probe", lambda qcmd: pytest.fail("probed"))
    capabilities.refuse_unsupported(_paths(tmp_path, "uqf"), "anything", None)


# ----------------------------------------------------------- real PeachQ


@needs_peachq
def test_peachq_reports_what_it_cannot_do():
    capabilities.probe.cache_clear()
    have = capabilities.probe(PEACHQ)
    assert set(have) == set(capabilities.CAPABILITIES)
    assert all(isinstance(v, bool) for v in have.values()), "an answer for each, never a guess"


@needs_peachq
def test_this_checkouts_tree_converts_and_loads_on_peachq(tmp_path, monkeypatch):
    monkeypatch.setenv(stack_paths.DATA_ROOT_ENV, str(tmp_path))
    paths = stack_paths.paths_for_root(REPO, "peachq-etl")
    env = {**os.environ, "TORQAPPHOME": str(paths.torqapphome)}
    tree = qtree.prepare(paths, PEACHQ, env)
    assert (tree / "src" / "etl" / "init.q").is_file()
    assert json.loads((paths.torqdata / qtree.REPORT).read_text())["transformed"]


@needs_peachq
def test_a_load_failure_is_caught_though_peachq_exits_zero(tmp_path):
    tree = tmp_path / "tree"
    (tree / "src" / "etl").mkdir(parents=True)
    (tree / "src" / "init.q").write_text("x:1\n")
    (tree / "src" / "etl" / "init.q").write_text("y:til 2.5\n")
    with pytest.raises(UqsError, match=r"does not load .*no UQS_QTREE_LOADED"):
        qtree.probe(tree, PEACHQ, dict(os.environ))
    (tree / "src" / "etl" / "init.q").write_text("y:til 2\n")
    qtree.probe(tree, PEACHQ, dict(os.environ))


# ------------------------------------------- a whole stack, from a copy

STACK = os.environ.get("UQF_PEACHQ_STACK_TEST") == "1"
SYNTH = {
    "bundle.json": '{"name": "synthpq", "version": "0.1.0"}\n',
    "synth_feed.q": (
        "\\d .qpipe.job.synth_feed\n"
        "publish:.qetl.job.stream.unwired `synth_feed;\n"
        "sent:0\n"
        "on_timer:{[]\n"
        "    if[sent>=5; :(::)];\n"
        "    sent+:1;\n"
        "    publish[`synth_tape;([] time:enlist .z.p; sym:enlist `SYNTH; seq:enlist sent; "
        "px:enlist 100f+sent)];\n"
        "    }\n"
        "\\d .\n"
        ".qetl.job.stream.define[`synth_feed;`procname`subscribe_to`publishes`period`on_timer`note!(\n"
        "    `synthfeed1;`symbol$();enlist `synth_tape;0D00:00:01.000;"
        '.qpipe.job.synth_feed.on_timer;"five deterministic rows")];\n'
    ),
    "synth_src.q": (
        "\\d .qpipe.source.synth_src\n"
        'source_name:`synth_src\ncolumns:`time`sym`seq`px\ntypes:"psjf"\n'
        "target:`synth_hist\ntime_column:`time\nrow_key:`seq\ntz:`UTC\n"
        "/ The tape a day back: the tickerplant stamps today's time on every row,\n"
        "/ and today's partition is its own, so a backfill writes yesterday's.\n"
        "query:{[h;range_from;range_to]\n"
        "    t:.qetl.source.ipc[h;{[from_ts;to_ts]\n"
        "        select time, sym, seq, px from `synth_tape where time>=from_ts, time<to_ts\n"
        "      };range_from+1D;range_to+1D];\n"
        "    update time:time-1D from t}\n"
        "fixture:{[] ([] time:2026.09.11D09:00+1000000000*til 5; sym:5#`SYNTH; seq:1+til 5; "
        "px:101f+til 5)}\n"
        ".qetl.source.define[source_name;\n"
        "    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz!\n"
        "    (source_name;`synth_tape;target;time_column;row_key;columns;types;query;"
        "fixture;tz)];\n"
        "\\d .\n"
    ),
    "synth_hist_backfill.q": (
        ".qetl.transform.passthrough[`synth_hist_passthrough;`batch;"
        "0#.qpipe.source.synth_src.fixture[];.qpipe.source.synth_src.fixture[]];\n"
        ".qetl.job.bounded.define[`synth_hist_backfill;\n"
        "    `source`dataset`width`transform`procname`note`source_version!\n"
        "        (`synth_src;`synth_hist;0D01:00:00;`synth_hist_passthrough;`synthhist1;\n"
        '         "copies synth_tape into the HDB";`v1)];\n'
    ),
    "tables.q": (
        "synth_tape:([]time:`timestamp$();sym:`symbol$();seq:`long$();px:`float$())\n"
        "synth_hist:([]time:`timestamp$();sym:`symbol$();seq:`long$();px:`float$())\n"
    ),
    "catalog.q": (
        '.qcat.describe[`synth_tape]:"five deterministic rows";\n'
        '.qcat.describe[`synth_hist]:"synth_tape, backfilled";\n'
    ),
}


@pytest.fixture
def stack_copy(tmp_path: Path):
    """This checkout, copied - nothing here writes into it - with uqs run from
    the copy, so its repository root is the copy's."""
    repo = tmp_path / "repo"
    ignore = shutil.ignore_patterns(".venv", "output", "node_modules", ".git", "__pycache__")
    for entry in REPO.iterdir():
        if entry.name in ("lib",):
            (repo / entry.name).parent.mkdir(parents=True, exist_ok=True)
            (repo / entry.name).symlink_to(entry)
        elif entry.is_dir() and entry.name not in (".venv", "output", "node_modules", ".git"):
            shutil.copytree(entry, repo / entry.name, ignore=ignore, symlinks=True)
        elif entry.is_file():
            repo.mkdir(exist_ok=True)
            shutil.copy2(entry, repo / entry.name)
    bundle = tmp_path / "synthpq"
    bundle.mkdir()
    for name, text in SYNTH.items():
        (bundle / name).write_text(text)
    with (repo / "scripts" / "torqconfig" / "sources.csv").open("a") as f:
        f.write("synth_src,ipc,localhost:6552:admin:{secret},SYNTH_PW\n")
    env = {
        **os.environ,
        "PYTHONPATH": str(repo / "python" / "uqs" / "src"),
        "UQS_RUNTIME": "peachq-etl",
        "SYNTH_PW": "admin",
    }

    def uqs(*args: str, timeout: float = 300) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", "from uqs.cli import main; main()", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    yield repo, bundle, uqs, env
    for proc in ("all", "feed1", "synthfeed1"):
        uqs("stop", proc)


@needs_peachq
@pytest.mark.skipif(not STACK, reason="UQF_PEACHQ_STACK_TEST=1 starts a PeachQ stack")
def test_a_bundles_jobs_run_on_peachq(stack_copy):
    repo, bundle, uqs, _env = stack_copy
    ledger = repo / "src" / "etl" / "installed_bundles.json"
    dry = uqs("runtime", "prepare", "--bundle", str(bundle), "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "convert for PeachQ" in dry.stdout and not ledger.exists(), "a dry run writes nothing"
    prep = uqs("runtime", "prepare", "--bundle", str(bundle))
    assert prep.returncode == 0, prep.stdout + prep.stderr
    assert json.loads(ledger.read_text())["synthpq"]["runtimes"] == ["peachq-etl"]

    started = uqs("start", "--profile", "capture")
    assert started.returncode == 0, started.stdout + started.stderr
    assert uqs("start", "synthfeed1").returncode == 0
    rows = ""
    for _ in range(30):
        rows = uqs("query", "exec seq from synth_tape", "--port", "6552", timeout=30).stdout
        if "1 2 3 4 5" in rows:
            break
        time.sleep(1)
    assert "1 2 3 4 5" in rows, f"the RDB holds {rows!r}"
    px = uqs("query", "exec px from synth_tape", "--port", "6552", timeout=30).stdout
    assert "101 102 103 104 105" in px

    day = datetime.now(UTC).date() - timedelta(days=1)
    run = uqs(
        "backfill",
        "synth_hist_backfill",
        "--from",
        str(day),
        "--to",
        str(day + timedelta(days=1)),
        "--wait",
    )
    assert run.returncode == 0 and "completed" in run.stdout, run.stdout + run.stderr
    status = repo / "output" / "uqs-peachq-etl" / "status"
    state = json.loads((status / "airflow_status_synthhist1.txt").read_text())
    assert (state["state"], state["rows_published"], state["windows_completed"]) == (
        "completed",
        5,
        24,
    )
    part = repo / "output" / "uqs-peachq-etl" / "hdb" / str(day).replace("-", ".") / "synth_hist"
    read = part.parent.parent / "read.q"
    read.write_text(f'-1 .Q.s1 get `$":{part}/seq";\n-1 .Q.s1 get `$":{part}/px";\nexit 0\n')
    written = subprocess.run(
        [PEACHQ, str(read), "-q"], capture_output=True, text=True, timeout=60, check=False
    ).stdout.splitlines()
    assert written == ["1 2 3 4 5", "101 102 103 104 105f"], "yesterday's partition, in order"


#: The starter pack's own tables, which a managed TorQ install need not have.
DEMO_TABLES = ("quote", "trade", "packets")


@needs_peachq
@pytest.mark.skipif(not STACK, reason="UQF_PEACHQ_STACK_TEST=1 starts a PeachQ stack")
def test_a_sidecar_runs_against_a_managed_schema_without_the_demo_tables(stack_copy, tmp_path):
    """#902: every process used to load every declaration, so the demo's
    market_data - which asks for `quote` at load time - stopped a sidecar from
    starting against a schema without it. Now the sidecar loads its own."""
    repo, bundle, uqs, env = stack_copy
    managed = tmp_path / "managed-starter-pack"
    shutil.copytree(REPO / "lib" / "torq-finance-starter-pack", managed, symlinks=True)
    schema = managed / "database.q"
    schema.write_text(
        "".join(
            line
            for line in schema.read_text().splitlines(keepends=True)
            if not line.startswith(tuple(f"{t}:" for t in DEMO_TABLES))
        )
    )
    env["TORQAPPHOME"] = str(managed)
    prep = uqs("runtime", "prepare", "--bundle", str(bundle))
    assert prep.returncode == 0, prep.stdout + prep.stderr
    plan = (repo / "src" / "etl" / "generated" / "load_plan.q").read_text()
    assert '.qetl.load.procs[`synthfeed1]:enlist "src/etl/streaming/synth_feed.q"' in plan

    started = uqs("start", "discovery1", "stp1", "rdb1", "synthfeed1")
    assert started.returncode == 0, started.stdout + started.stderr
    rows = ""
    for _ in range(30):
        rows = uqs("query", "exec seq from synth_tape", "--port", "6552", timeout=30).stdout
        if "1 2 3 4 5" in rows:
            break
        time.sleep(1)
    assert "1 2 3 4 5" in rows, f"the RDB holds {rows!r}"
    px = uqs("query", "exec px from synth_tape", "--port", "6552", timeout=30).stdout
    assert "101 102 103 104 105" in px
    # The deploy smoke over this composition, on the tree it started from,
    # passes; one that needs quote fails, naming it.
    tree = repo / "output" / "uqs-peachq-etl" / "qtree"
    smoke = [PEACHQ, "scripts/deploy_smoke.q", "-q", "-procs", "rdb1", "synthfeed1"]
    ok = subprocess.run(smoke, cwd=tree, env=env, capture_output=True, text=True, timeout=120)
    assert "DEPLOY_SMOKE_OK" in ok.stdout, ok.stdout + ok.stderr
    assert "synthfeed1" in ok.stdout
    bad = subprocess.run(
        [*smoke, "superbook1"], cwd=tree, env=env, capture_output=True, text=True, timeout=120
    )
    assert "DEPLOY_SMOKE_OK" not in bad.stdout
    assert "no table quote" in bad.stderr and str(schema) in bad.stderr, bad.stderr

    # Nothing unselected was started, and no worker ran.
    assert not (
        repo / "output" / "uqs-peachq-etl" / "status" / "airflow_status_synthhist1.txt"
    ).exists()
