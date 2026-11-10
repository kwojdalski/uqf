"""Selective declaration loading (#902): what a process loads is the closure
of its own jobs, read as text, written down as src/etl/generated/load_plan.q.

The q half - init.q loading only the plan, against a schema without `quote` -
runs in test_peachq_pipelines.py, which needs an interpreter; which files a
selection names is tests/q/test_declaration_load.q's.
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from uqs.deploy import verify
from uqs.deploy.selection import Selection, smoke_args
from uqs.interpreter import identify, q_interpreter
from uqs.model import infra, load_plan
from uqs.paths import UqsError
from uqs.stack import qtree

REPO = Path(__file__).resolve().parents[3]

#: A sidecar with its own source, as a bundle installs one: nothing in it
#: names a demo job, so nothing of the demo may load with it.
SIDECAR = {
    "src/etl/sources/zz_side_src.q": (".qpipe.source.zz_side_src.source_name:`zz_side_src\n"),
    "src/etl/streaming/zz_side.q": (
        ".qpipe.job.zz_side.read:{[h] .qpipe.source.zz_side_src.source_name}\n"
        ".qetl.job.stream.define[`zz_side;`procname`subscribe_to`outputs`note!"
        '(`zzside1;`symbol$();enlist `zz_tape;"a sidecar")];\n'
    ),
}


TRANSFORM = ".qetl.transform.define[`{name};`inputs`output`fn`examples!()]\n"


@pytest.fixture(scope="module")
def plan() -> load_plan.LoadPlan:
    return load_plan.load_plan(REPO)


def test_the_committed_plan_is_the_one_the_tree_declares(plan):
    assert (REPO / load_plan.PLAN_FILE).read_text() == load_plan.render(plan)


def test_a_closure_takes_in_the_peer_job_whose_helpers_it_calls(plan):
    assert plan.for_proc("superbook1") == [
        "src/etl/streaming/market_data.q",
        "src/etl/streaming/superbook.q",
    ]


def test_a_closure_takes_in_sources_transforms_and_reactions(plan):
    assert plan.for_job("hdb_exec_bars_backfill") == [
        "src/etl/sources/hdb_executions.q",
        "src/etl/transforms/exec_bars.q",
        "src/etl/workers/hdb_exec_bars_backfill.q",
    ]
    # rebuild_positions watches demo_deals, which this worker writes.
    assert "src/etl/reactions/rebuild_positions.q" in plan.for_job("demo_deals_backfill")


def test_a_reference_in_a_comment_or_example_pulls_nothing_in(plan):
    # arbitrage.q's @eg lines name .qpipe.job.superbook; its code does not.
    assert plan.for_job("arbitrage") == ["src/etl/streaming/arbitrage.q"]


def test_a_sidecar_loads_without_the_demo_jobs_and_changes_no_other_closure(plan):
    with_sidecar = load_plan.load_plan(REPO, SIDECAR)
    assert with_sidecar.select(["zzside1"]) == list(SIDECAR)
    for proc in plan.procs:
        assert with_sidecar.for_proc(proc) == plan.for_proc(proc), proc


def test_a_source_check_loads_its_source_and_no_job(plan):
    assert plan.for_source("duckdb_deals") == ["src/etl/sources/duckdb_deals.q"]
    with_sidecar = load_plan.load_plan(REPO, SIDECAR)
    assert with_sidecar.for_source("zz_side_src") == ["src/etl/sources/zz_side_src.q"]
    # every source's closure is sources and transforms: never a job's file
    for name in plan.sources:
        assert not any("/streaming/" in p or "/workers/" in p for p in plan.for_source(name)), name


def test_the_infrastructure_only_selection_loads_nothing(plan):
    assert plan.select([]) == []


def test_a_selection_is_in_load_order_whatever_order_it_names(plan):
    forward = plan.select(["superbook1", "hdb_exec_bars_backfill"])
    assert forward == plan.select(["hdb_exec_bars_backfill", "superbook1"])
    order = [f.path for f in plan.files]
    assert forward == sorted(forward, key=order.index)


def test_an_unknown_name_is_refused(plan):
    with pytest.raises(UqsError, match="nosuchproc1"):
        plan.select(["nosuchproc1"])


def test_the_4_0_conversion_keeps_the_plan_and_converts_its_loader():
    texts = {
        rel: (REPO / rel).read_text()
        for rel in (load_plan.PLAN_FILE.as_posix(), "src/etl/core/declaration_load.q")
    }
    out = qtree.convert(REPO, texts)
    assert out[load_plan.PLAN_FILE.as_posix()] == texts[load_plan.PLAN_FILE.as_posix()]
    assert "\\d .qetl.load" not in out["src/etl/core/declaration_load.q"]


def test_the_smoke_loads_what_the_deployment_starts():
    args = smoke_args("essential", Selection(processes=["zzside1"]))
    assert args[0] == "-procs" and args[-1] == "zzside1" and "rdb1" in args


def test_readiness_does_not_wait_for_a_one_shot(monkeypatch):
    monkeypatch.setattr(
        verify.listing,
        "configured_ports",
        lambda *_a, **_k: dict.fromkeys([*infra.FULL_INFRA, *infra.ESSENTIAL_INFRA], 1),
    )
    expected, _ = verify._expected("essential", None)
    assert "tpreplay1" in infra.ESSENTIAL_INFRA
    assert "tpreplay1" not in expected and "rdb1" in expected


def test_two_files_registering_one_source_or_transform_are_refused():
    """q upserts a second define, so the plan refuses it (#1545)."""
    source = ".qpipe.source.zz_dup.source_name:`zz_dup\n"
    with pytest.raises(UqsError, match="source zz_dup is defined by both .*zz_a.q and .*zz_b.q"):
        load_plan.load_plan(
            REPO,
            {"src/etl/sources/zz_a.q": source, "src/etl/sources/zz_b.q": source},
        )
    transform = TRANSFORM.format(name="zz_dup")
    with pytest.raises(UqsError, match="transform zz_dup is defined by both"):
        load_plan.load_plan(
            REPO,
            {"src/etl/transforms/zz_a.q": transform, "src/etl/streaming/zz_b.q": transform},
        )


def test_a_job_reaches_the_helpers_of_the_transform_it_registers_by_their_name():
    """Registering transform `zz_t` must not hide `.qpipe.transform.zz_t.*`
    in another file (exec_bars, #2177)."""
    plan = load_plan.load_plan(
        REPO,
        {
            "src/etl/transforms/zz_t.q": ".qpipe.transform.zz_t.bars:{x}\n",
            "src/etl/streaming/zz_t.q": (
                ".qetl.transform.define[`zz_t;`inputs`output`fn`examples!(();();"
                ".qpipe.transform.zz_t.bars;())]\n"
                ".qetl.job.stream.define[`zz_t;`procname`subscribe_to`outputs`transform!"
                "(`zzt1;`symbol$();enlist `zz_tape;`zz_t)];\n"
            ),
        },
    )
    assert plan.for_proc("zzt1") == ["src/etl/transforms/zz_t.q", "src/etl/streaming/zz_t.q"]


def test_a_passthrough_registers_the_transform_a_job_declares(plan):
    """demo_deals_feed's transform is registered by `.qetl.transform.passthrough`
    in the backfill worker's file (#2177)."""
    assert "src/etl/workers/demo_deals_backfill.q" in plan.for_proc("demo_deals_feed1")


def test_a_twin_computing_through_a_live_job_loads_that_job(plan):
    """hdb_order_quality_backfill normalizes through order_outcomes and rolls
    through order_quality's registrations."""
    files = plan.for_proc("hdb_order_quality_backfill1")
    assert "src/etl/streaming/order_outcomes.q" in files
    assert "src/etl/streaming/order_quality.q" in files


def test_a_transform_namespace_beside_its_define_is_not_a_second_definition():
    """A shared transform's helpers live under .qpipe.transform.<name> in one
    file and the job registers the transform in another (book_liquidity_1m)."""
    load_plan.load_plan(
        REPO,
        {
            "src/etl/transforms/zz_ns.q": ".qpipe.transform.zz_t.width:1\n",
            "src/etl/streaming/zz_t.q": TRANSFORM.format(name="zz_t"),
        },
    )


#: What .qtorq.load_uqf does for a process naming what it runs, then every
#: transform that load registered run on its own examples - so a transform
#: whose helpers, registration or normalizer the plan left out fails here.
SELECTED_LOAD = """\
.qetl.load.only:enlist `$.z.x 0;
r:@[{system"l src/init.q"; system"l src/etl/init.q";
    bad:$[count .qetl.transform.defined[];
        select from .qetl.transform.verify_all[] where not passed; ()];
    $[count bad;
        "transform ",string[first bad`transform]," failed: ",first bad`detail;
        `ok]};::;{x}];
-1 $[r~`ok;"ok";r]; exit 0
"""


def test_every_process_loads_its_plan_alone_and_its_transforms_run(plan, tmp_path):
    """Every process in the plan, loaded on its own as TorQ loads it (#2177):
    a dependency the plan drops fails only a selected load, never the full
    load the rest of the suite does, so it reached production silently."""
    q = q_interpreter(os.environ)
    if q is None or identify(q) != "kdbx":
        pytest.skip("needs KDB-X - set $QCMD to it")
    script = tmp_path / "selected_load.q"
    script.write_text(SELECTED_LOAD)
    env = {k: v for k, v in os.environ.items() if not k.startswith("UQF_PEACHQ")}

    def load(proc: str) -> tuple[str, str]:
        out = subprocess.run(
            [str(q), str(script), proc, "-q"],
            cwd=REPO,
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
        return proc, (out.stdout.strip().splitlines()[-1:] or [out.stderr.strip()])[0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        failed = {p: r for p, r in pool.map(load, sorted(plan.procs)) if r != "ok"}
    assert not failed, failed


def _init_q_declaration_dirs() -> tuple[str, ...]:
    """The directories src/etl/init.q globs its declarations from, in order."""
    import re

    text = (REPO / "src" / "etl" / "init.q").read_text()
    m = re.search(r'^\.qetl\.load\.declarations\s+"src/etl/",/:\(([^)]*)\)', text, re.MULTILINE)
    assert m, "init.q no longer loads its declarations by .qetl.load.declarations"
    return tuple(re.findall(r'"(\w+)"', m.group(1)))


def test_the_declaration_dirs_are_init_qs_in_its_order():
    """DIRS restates init.q for the load order. A directory init.q loads and
    DIRS lacks is a file a job's closure misses; one DIRS keeps and init.q
    dropped is a file the plan loads that the tree does not (#1794)."""
    assert load_plan.DIRS == _init_q_declaration_dirs()


def test_the_declaration_dirs_are_every_declaring_directory_on_disk():
    """Every src/etl/ subdirectory but core/ and generated/ holds declarations
    (scripts/gates/policy/etl_layering.py's declaring_dirs): a new one on disk
    that DIRS does not name is a file no plan loads."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "etl_layering", REPO / "scripts" / "gates" / "policy" / "etl_layering.py"
    )
    assert spec and spec.loader
    layering = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(layering)
    assert set(load_plan.DIRS) == {d.name for d in layering.declaring_dirs(REPO)}
