"""Tests for edges the registry defers to a job's own q declaration.

WHY THIS FILE EXISTS. A streaming job's `.qstream.define` already names the
tables it subscribes to and publishes; the `Pipeline` entry used to name them
again, and `verify_pipeline_edges` existed to check the two copies agreed -
a check that only had a job to do because the duplication existed.

The registry now defers with `FROM_DECLARATION` and the q file is the single
declaration. What has to stay true of that indirection is what is tested
here, and the first of them is the one that would hurt:

  * a deferred edge with nothing to read RAISES rather than resolving to
    empty. An empty publish set drops the pipeline's tables out of the
    generated database.q, and `.u.upd` onto a table the plant does not define
    discards its rows in silence (#288) - which is the failure this
    indirection could otherwise reintroduce.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from uqs.model import plant_schema
from uqs.model.pipeline import FROM_DECLARATION, STREAM_RUNNER_SCRIPT
from uqs.model.pipeline_edges import _stream_edge_cache, resolve_edges
from uqs.model.registry import PIPELINES
from uqs.paths import UqsError, repo_root


def _deferred():
    return [p for p in PIPELINES if p.subscribes is FROM_DECLARATION]


def test_the_registry_actually_defers():
    """If nothing defers, every assertion below passes vacuously."""
    assert _deferred(), "no pipeline defers its edges - these tests prove nothing"


def test_a_deferred_edge_resolves_to_what_the_q_file_declares():
    edges = _stream_edge_cache(repo_root())
    for pipeline in _deferred():
        declared = edges[pipeline.procname]
        assert pipeline.subscribed_tables == declared[0]


def test_a_deferred_publish_resolves_to_what_the_q_file_declares():
    edges = _stream_edge_cache(repo_root())
    for pipeline in PIPELINES:
        if pipeline.publishes is FROM_DECLARATION:
            assert pipeline.published_tables == edges[pipeline.procname][1]


def test_a_deferred_edge_with_no_declaration_raises_rather_than_resolving_empty():
    """The property that keeps this safe. Resolving to `()` would drop the
    pipeline's tables out of database.q, and a table the plant does not define
    discards its rows without an error - loudly wrong beats silently empty."""
    orphan = replace(_deferred()[0], procname="no_such_process1")
    with pytest.raises(UqsError, match="FROM_DECLARATION"):
        resolve_edges(orphan)


def test_the_refusal_says_what_to_do_about_it():
    """A procname that no job claims is the likeliest cause, and it is not
    obvious from the symptom - so the message names all three."""
    orphan = replace(_deferred()[0], procname="no_such_process1")
    with pytest.raises(UqsError) as excinfo:
        resolve_edges(orphan)
    message = str(excinfo.value)
    assert "no_such_process1" in message
    assert "procname" in message


def test_a_spelled_out_edge_is_left_alone():
    """Deferring is opt-in. A pipeline that states its edges - every backfill,
    and the feeds that subscribe to nothing - must not start reading files."""
    spelled = replace(_deferred()[0], subscribes=("a", "b"), publishes=("c",))
    assert resolve_edges(spelled) == (("a", "b"), ("c",))


def test_publishes_none_still_falls_back_to_the_owned_table():
    """`None` predates FROM_DECLARATION and means something else: default to
    `(table,)`. A pipeline owning one table still says nothing."""
    pipeline = replace(_deferred()[0], subscribes=(), publishes=None, table="one_table")
    assert resolve_edges(pipeline)[1] == ("one_table",)


def test_a_pipeline_with_no_table_and_no_publishes_publishes_nothing():
    pipeline = replace(_deferred()[0], subscribes=(), publishes=None, table=None)
    assert resolve_edges(pipeline)[1] == ()


def test_every_deferred_pipeline_runs_the_generic_stream_runner():
    """Only a streaming job declares itself in q. A backfill that deferred
    would resolve against a file that does not exist - caught by the raise,
    but the registry should not get there."""
    for pipeline in _deferred():
        assert pipeline.script == STREAM_RUNNER_SCRIPT


def test_the_plant_schema_sees_every_deferred_publisher():
    """The consumer that matters: `_publishers` feeds the generated
    database.q. A deferred pipeline missing from it is a table the plant
    never defines."""
    publishers = plant_schema._publishers(PIPELINES)
    published = {proc for procs in publishers.values() for proc in procs}
    for pipeline in PIPELINES:
        if pipeline.published_tables:
            assert pipeline.procname in published
