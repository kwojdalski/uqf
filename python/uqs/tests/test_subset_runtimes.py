"""Runtimes with one profile's pipelines (#760): crypto and fx."""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs import runtimes
from uqs.model import profiles
from uqs.model.plant_schema import _generated_schema_content
from uqs.model.registry import PIPELINES
from uqs.model.runtime_members import pipeline_procnames, plant_tables
from uqs.paths import UqsError
from uqs.runtimes import Runtime
from uqs.stack import runtime_profiles
from uqs.stack.dqe import dqe_config_rows
from uqs.stack.procs import effective_process_rows

REPO = Path(stack_paths.__file__).resolve().parents[4]
SUBSETS = [r for r in runtimes.RUNTIMES.values() if r.profile is not None]
PIPELINE_NAMES = {p.procname for p in PIPELINES}


def _paths(name: str):
    return stack_paths.paths_for_root(REPO, name)


def test_there_are_subset_runtimes_and_each_names_a_real_profile():
    assert {r.name for r in SUBSETS} == {"crypto", "fx"}
    for runtime in SUBSETS:
        assert runtime.pipelines and runtime.overlays
        assert runtime.profile in profiles.PROFILES


def test_a_runtime_naming_a_profile_that_does_not_exist_is_refused():
    ghost = Runtime("ghost", "x", "uqs-ghost", True, True, 6450, profile="nope")
    with pytest.raises(UqsError, match=r"ghost runtime is declared from profile 'nope'"):
        pipeline_procnames(ghost)


@pytest.mark.parametrize("runtime", SUBSETS, ids=lambda r: r.name)
def test_a_subset_runtime_has_exactly_its_profile_s_pipelines(runtime):
    rows = effective_process_rows(_paths(runtime.name))
    pipelines = {r["procname"] for r in rows} & PIPELINE_NAMES
    assert pipelines == profiles.closure(profiles.PROFILES[runtime.profile])


def test_crypto_has_only_the_crypto_chain():
    assert pipeline_procnames(runtimes.RUNTIMES["crypto"]) == {"cryptomock1", "crypto_markout1"}


def test_uqf_and_torq_are_unchanged_by_subsets():
    uqf = {r["procname"] for r in effective_process_rows(_paths("uqf"))}
    torq = {r["procname"] for r in effective_process_rows(_paths("torq"))}
    assert PIPELINE_NAMES <= uqf
    assert not PIPELINE_NAMES & torq
    assert pipeline_procnames(runtimes.RUNTIMES["uqf"]) is None
    assert plant_tables(runtimes.RUNTIMES["uqf"]) is None


@pytest.mark.parametrize("runtime", SUBSETS, ids=lambda r: r.name)
def test_a_subset_schema_defines_only_the_tables_its_pipelines_use(runtime):
    paths = _paths(runtime.name)
    schema = _generated_schema_content(paths)
    full = _generated_schema_content(_paths("uqf"))
    wanted = plant_tables(runtime)
    assert wanted, f"{runtime.name} uses no tables"
    for table in wanted:
        if f"{table}:" in full:
            assert f"{table}:" in schema, f"{runtime.name} lacks {table}"
    assert len(schema) < len(full)


def test_crypto_s_schema_has_no_fx_tables():
    schema = _generated_schema_content(_paths("crypto"))
    assert "crypto_execution_quality:" in schema
    assert "fx_orderbook:" not in schema


def test_a_dqe_metatable_on_a_table_the_runtime_lacks_is_left_out():
    def metatables(name: str) -> list[str]:
        return [r["params"] for r in dqe_config_rows(_paths(name)) if r["query"] == "uqf_metatable"]

    assert metatables("uqf") and metatables("fx")
    assert metatables("crypto") == []


def test_a_subset_runtime_can_start_only_the_profiles_it_covers():
    crypto = runtime_profiles.known_processes(_paths("crypto"))
    assert crypto is not None
    startable = runtime_profiles.startable_profiles(crypto)
    assert "crypto" in startable and "essential" in startable
    assert "fx" not in startable


def test_the_plant_s_clients_fit_the_licence_with_room_to_spare():
    """The point of a focused runtime: far fewer plant subscribers."""
    for runtime in SUBSETS:
        names = {r["procname"] for r in effective_process_rows(_paths(runtime.name))}
        slots = profiles.plant_slots(names)
        full = profiles.plant_slots({r["procname"] for r in effective_process_rows(_paths("uqf"))})
        assert slots < full, (runtime.name, slots, full)
