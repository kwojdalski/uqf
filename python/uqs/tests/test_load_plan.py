"""Selective declaration loading (#902): what a process loads is the closure
of its own jobs, read as text, written down as src/etl/generated/load_plan.q.

The q half - init.q loading only the plan, against a schema without `quote` -
runs in test_selective_load.py, which needs an interpreter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs.deploy import verify
from uqs.deploy.selection import Selection, smoke_args
from uqs.model import infra, load_plan
from uqs.paths import UqsError
from uqs.stack import qtree

REPO = Path(__file__).resolve().parents[3]

#: A sidecar with its own source, as a bundle installs one: nothing in it
#: names a demo job, so nothing of the demo may load with it.
SIDECAR = {
    "src/etl/sources/zz_side_src.q": (
        "\\d .qpipe.source.zz_side_src\nsource_name:`zz_side_src\n\\d .\n"
    ),
    "src/etl/streaming/zz_side.q": (
        "\\d .qpipe.job.zz_side\n"
        "read:{[h] .qpipe.source.zz_side_src.source_name}\n"
        "\\d .\n"
        ".qetl.job.stream.define[`zz_side;`procname`subscribe_to`publishes`note!"
        '(`zzside1;`symbol$();enlist `zz_tape;"a sidecar")];\n'
    ),
}


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
    assert plan.for_job("eq_orderbook") == [
        "src/etl/sources/databento_mbp10.q",
        "src/etl/transforms/eq_orderbook.q",
        "src/etl/streaming/eq_orderbook.q",
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
    forward = plan.select(["superbook1", "eq_orderbook"])
    assert forward == plan.select(["eq_orderbook", "superbook1"])
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
