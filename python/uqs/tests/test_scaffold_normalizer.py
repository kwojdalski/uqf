"""`uqs new-job --kind normalizer`: many sources, one canonical table.

The generated declaration is read back through the same reader the process
registry is built from, as test_scaffold.py does for streaming jobs - a
template that stopped matching it would scaffold a normalizer no process runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs.model.declarations import read_file_text
from uqs.model.pipeline import PipelineKind
from uqs.paths import UqsError
from uqs.scaffold import jobs
from uqs.scaffold.normalizer import definition_columns, normalizer

_QUOTE = "quote:([]time:`timestamp$(); sym:`g#`symbol$(); bid:`float$(); ask:`float$())"
_TRADES = "trades:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); size:`float$())"
_SOURCES = {"quote": definition_columns(_QUOTE), "trades": definition_columns(_TRADES)}
_PLANT = {"quote", "trades", "orders"}


def _plan(sources=("quote", "trades"), name="ticks", cols="source_time:timestamp, sym:symbol"):
    return normalizer(name, list(sources), jobs.parse_columns(cols), _SOURCES, known_tables=_PLANT)


def _file(plan, suffix):
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


def test_a_definition_is_read_into_columns_without_the_grouped_attribute():
    assert definition_columns(_QUOTE) == [
        ("time", "`timestamp$()"),
        ("sym", "`symbol$()"),
        ("bid", "`float$()"),
        ("ask", "`float$()"),
    ]


@pytest.mark.parametrize("sources", [("quote", "trades"), ("quote",)], ids=["two", "one"])
def test_the_scaffolded_normalizer_reads_back_as_one(sources):
    """One source is the case the reader got wrong: `(enlist `quote)` came back
    as two names, `(enlist` and `quote)`."""
    (d,) = read_file_text(_file(_plan(sources), "ticks.q"), Path("ticks.q"))
    assert (d.kind, d.procname, d.subscribes, d.publishes) == (
        PipelineKind.NORMALIZER,
        "ticks1",
        sources,
        ("ticks",),
    )


def test_the_output_has_no_time_and_the_plant_copy_does():
    """`.qnorm.define` refuses an output carrying `time`; the plant stamps it."""
    body = _file(_plan(), "ticks.q")
    assert "ticks:([] source_time:`timestamp$(); sym:`symbol$())" in body
    assert "ticks:([]time:`timestamp$()" in _file(_plan(), "uqs_tables.q")


def test_each_source_gets_its_schema_a_throwing_mapping_and_an_example():
    body = _file(_plan(), "ticks.q")
    for src in ("quote", "trades"):
        assert f".qxf.define[`ticks_from_{src};" in body
        assert f'\'"ticks.from_{src}: not implemented"' in body
        assert f"(enlist `{src})!enlist ([] time:enlist 2026.01.01D" in body, "an example row"


def test_it_gets_a_catalog_entry_and_a_contract_driver():
    plan = _plan()
    assert _file(plan, "tables.csv").startswith('ticks,"SCAFFOLDED:')
    assert "contract_driver:{[]" in _file(plan, "test_ticks.q")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "orders"}, "already a plant table"),
        ({"sources": ()}, "needs --subscribes"),
        ({"sources": ("quote", "nope")}, "no plant definition to read for source"),
    ],
)
def test_a_normalizer_that_cannot_be_scaffolded_is_refused(kwargs, message):
    with pytest.raises(UqsError, match=message):
        _plan(**kwargs)


def test_a_source_column_with_no_sample_value_is_refused():
    odd = {"odd": [("time", "`timestamp$()"), ("g", "`guid$()")]}
    with pytest.raises(UqsError, match="no sample value"):
        normalizer("n", ["odd"], jobs.parse_columns("v:float"), odd, known_tables={"odd"})
