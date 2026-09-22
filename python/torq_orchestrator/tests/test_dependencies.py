"""The process dependency graph, and the warnings derived from it (#290)."""

from __future__ import annotations

from dataclasses import replace

from torq_orchestrator import core, dependencies


def test_every_declared_input_has_a_named_source():
    """No input table may be a dead end.

    A process subscribing to a table with neither a declared producer nor
    an EXTERNAL_PRODUCERS entry is one that can never receive anything, and
    nothing else in the tree would say so - the subscription itself
    succeeds, because the table is defined on the plant either way.
    """
    for row in dependencies.dependency_rows():
        assert row["published_by"] != "(nothing declares it)", (
            f"{row['process']} subscribes to {row['needs']!r}, which nothing "
            "publishes and no external source claims"
        )


def test_no_external_producer_entry_is_dead():
    """The other direction, so the exemption list cannot rot.

    An entry naming a table nothing subscribes to excuses nothing, and left
    in place it would silently excuse whatever later takes that name - the
    same failure mode RUNS_WITHOUT_A_PROCESS and WORKERS_WITHOUT_A_PROCESS
    are guarded against.
    """
    subscribed = {t for tables in dependencies.inputs_by_process().values() for t in tables}
    dead = set(dependencies.EXTERNAL_PRODUCERS) - subscribed
    assert not dead, f"EXTERNAL_PRODUCERS entries nothing subscribes to: {sorted(dead)}"


def test_a_consumer_started_alone_is_warned_about():
    """The case that prompted this.

    `uqf-stack start superbook1` brings up a process that subscribes to
    `market_data` successfully, heartbeats, reports `up`, and receives
    nothing - because marketdata1 is not running and nothing said so.
    """
    warnings = dependencies.unfed_inputs("superbook1", running=set())
    assert len(warnings) == 1, warnings
    assert "market_data" in warnings[0]
    assert "marketdata1" in warnings[0]


def test_starting_a_whole_chain_at_once_is_silent():
    """Processes named in the same invocation count as present.

    Otherwise the recommended command for the direct-arbitrage chain would
    warn about two of the three processes it is itself starting, and a
    warning that fires on correct usage trains the reader to ignore it.
    """
    chain = {"marketdata1", "superbook1", "arbitrage1"}
    running = chain | {"fxfeed1", "quotesfeed1"}
    for procname in sorted(chain):
        assert dependencies.unfed_inputs(procname, running) == []


def test_an_external_source_is_context_not_a_fault():
    """crypto_book has a declared producer AND an external one.

    cryptomock1 is off by default precisely so it does not interleave with
    cryptorust's recorder, which is the normal source. Reporting marks1 as
    broken on every healthy stack would be a warning that is usually wrong.
    """
    (warning,) = dependencies.unfed_inputs("marks1", running={"fxfeed1", "marks1"})
    assert "crypto_book" in warning
    assert "cryptorust" in warning
    assert "unless" in warning


def test_only_running_processes_are_reported_as_starved():
    """A stopped consumer with a stopped producer is a stopped chain, not a
    fault. Only a process that is up while receiving nothing is worth a
    line, because that is the state with no other symptom."""
    assert dependencies.starved_processes(running=set()) == {}
    starved = dependencies.starved_processes(running={"superbook1"})
    assert set(starved) == {"superbook1"}


def test_a_dynamic_subscriber_declares_no_inputs_to_check():
    """tap1 chooses its tables at runtime from -tables, so there is no fixed
    edge to check and it must not be reported as depending on nothing."""
    assert "tap1" not in dependencies.inputs_by_process()
    assert dependencies.unfed_inputs("tap1", running=set()) == []


def test_the_graph_is_the_one_the_generated_schema_is_built_from():
    """Producers come from the same `_publishers` that generates database.q.

    Two derivations of "who publishes what" could disagree, and then the
    warning would describe a different system from the one running.
    """
    invented = replace(
        core.PIPELINE_BY_NAME["cross1"],
        procname="ghost1",
        subscribes=(),
        publishes=("market_data",),
    )
    producers = dependencies.producers_by_table([*core.PIPELINES, invented])
    assert producers["market_data"] == {"marketdata1", "ghost1"}


def test_outputs_resolve_the_same_way_the_generated_schema_does():
    """A pipeline's outputs are its explicit `publishes` if it declares one,
    otherwise its single `table` - the rule `_publishers` applies. Two
    derivations of "what does this publish" could disagree, and then the
    summary's Outputs column would describe a different system from the one
    whose tables the plant defines."""
    outputs = dependencies.outputs_by_process()
    assert outputs["cryptomock1"] == ("crypto_book", "crypto_trades")
    assert outputs["fxfeed1"] == ("quote",)


def test_depends_on_names_processes_not_tables():
    """`inputs_by_process` answers "what table does this need"; this answers
    "who do I have to start to get it", which is the question behind every
    `up, but idle` process."""
    depends = dependencies.depends_on_by_process()
    assert "executions1" in depends["posbook1"]
    assert "marks1" in depends["posbook1"]
    assert "executions" not in depends["posbook1"], "processes, not tables"


def test_a_table_produced_outside_the_process_list_is_named_as_external():
    """ "nothing in this list provides it" and "nothing provides it" are
    different facts, and only one of them is a problem. Dropping the edge
    would report databento1 as depending on nothing at all."""
    depends = dependencies.depends_on_by_process()
    assert any("external" in d for d in depends["databento1"])


def test_a_process_is_never_its_own_dependency():
    """A normalizer republishes onto tables it also reads from, so a naive
    lookup makes it depend on itself - which reads as a cycle the operator
    has to resolve, and there is nothing to resolve."""
    for procname, sources in dependencies.depends_on_by_process().items():
        assert procname not in sources
