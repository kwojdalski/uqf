"""`uqs job new --transform passthrough|derive` (#714 for backfills, #742 for
streaming jobs).

What the plan writes, read as text, and what it refuses. That the written q
LOADS, forwards and fails verification where it should is
test_scaffold_loads.py's, under a real q.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.paths import UqsError
from uqs.scaffold import jobs
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import parse_columns, table_definition

runner = CliRunner()

#: The plant definition a streaming transform reads its input's shape from.
TICKS = table_definition("ticks", parse_columns("sym:symbol, px:float"))
DEFS = {"ticks": TICKS}


def _body(plan, suffix: str) -> str:
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


def _stream(
    name: str,
    transform: str,
    *,
    subscribe_to: list[str] | None = None,
    publishes: str = "out",
    columns: str | None = "sym:symbol, px:float",
    known_tables: set[str] | None = None,
    definitions: dict[str, str] | None = None,
    poll: bool = False,
    period: str | None = None,
):
    return jobs.streaming_job(
        name,
        ["ticks"] if subscribe_to is None else subscribe_to,
        publishes,
        columns,
        known_tables={"ticks"} if known_tables is None else known_tables,
        transform=transform,
        definitions=DEFS if definitions is None else definitions,
        poll=poll,
        period=period,
    )


# ------------------------------------------------------------------ backfill


def test_a_backfill_is_still_a_passthrough_by_default():
    body = _body(backfill.bounded_worker("fx", "fx_hist", "sym:symbol, mid:float"), "fx_backfill.q")
    assert ".qetl.transform.passthrough[`fx_passthrough;" in body
    assert "`fx_passthrough;.qpipe.job.fx_backfill.facts" in body
    assert "derive" not in body


def test_a_derived_backfill_declares_its_transform_with_a_failing_example():
    plan = backfill.bounded_worker("fx", "fx_hist", "sym:symbol, mid:float", transform="derive")
    body = _body(plan, "fx_backfill.q")
    assert "derive:{[batch]" in body and "not implemented" in body
    assert ".qetl.transform.define[`fx_backfill_transform;" in body
    assert ".qetl.plant.shape `fx_hist" in body
    assert "/ SCAFFOLDED: the rows derive returns" in body
    assert "`fx_backfill_transform;.qpipe.job.fx_backfill.facts" in body
    assert "passthrough" not in body
    assert any("fx_backfill.derive" in note for note in plan.notes)


def test_an_unknown_transform_is_refused():
    with pytest.raises(UqsError, match="passthrough, derive"):
        backfill.bounded_worker("fx", "fx_hist", "sym:symbol, mid:float", transform="copy")


# ----------------------------------------------------------------- streaming


def test_a_streaming_job_keeps_its_custom_handler_without_transform():
    body = _body(jobs.streaming_job("j", ["ticks"], "out", "sym:symbol, n:long"), "j.q")
    assert 'on_batch:{[t;x]\n    \'"j.on_batch: not implemented"' in body
    assert ".qetl.transform" not in body


def test_a_passthrough_writes_the_whole_handler_and_tests_that_pass():
    plan = _stream("echo", "passthrough")
    body = _body(plan, "echo.q")
    assert "not implemented" not in body
    assert "if[not t=`ticks;" in body
    assert "(cols .qpipe.job.echo.input)#x" in body
    assert ".qpipe.job.echo.publish[`out;rows]" in body
    assert ".qetl.transform.passthrough[`echo_passthrough;`batch;.qpipe.job.echo.input;" in body
    assert "fixture:{[] ([] sym:enlist `SCAFFOLD; px:enlist 1.0)}" in body
    test = _body(plan, "test_echo.q")
    for name in (
        "test_another_table_is_refused_not_forwarded",
        "test_an_empty_batch_publishes_nothing",
        "test_fixture_rows_reach_out",
        "contract_driver",
    ):
        assert name in test
    assert "SCAFFOLDED" not in test


def test_a_derive_writes_the_handler_and_leaves_only_the_transform():
    plan = _stream("notional", "derive", columns="sym:symbol, notional:float")
    body = _body(plan, "notional.q")
    assert "derive:{[batch]" in body
    assert ".qetl.transform.apply[`notional_derive;" in body
    assert ".qetl.transform.define[`notional_derive;" in body
    assert "/ SCAFFOLDED: the rows derive returns" in body
    test = _body(plan, "test_notional.q")
    assert "test_the_transform_returns_its_example" in test
    assert "test_derived_rows_reach_out" in test


def test_a_passthrough_between_different_shapes_is_refused_with_both_shapes():
    with pytest.raises(
        UqsError, match=r"ticks has sym:s, px:f; out has sym:s, n:j.*--transform derive"
    ):
        _stream("echo", "passthrough", columns="sym:symbol, n:long")


def test_a_passthrough_onto_an_existing_table_reads_its_shape_from_the_plant():
    defs = {**DEFS, "copy": table_definition("copy", parse_columns("sym:symbol, px:float"))}
    plan = _stream(
        "echo",
        "passthrough",
        publishes="copy",
        columns=None,
        known_tables={"ticks", "copy"},
        definitions=defs,
    )
    assert ".qpipe.job.echo.publish[`copy;rows]" in _body(plan, "echo.q")


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"subscribe_to": []}, "a feed subscribes to nothing"),
        ({"subscribe_to": [], "poll": True}, "--poll scaffolds a feed"),
        ({"subscribe_to": ["ticks", "quote"], "known_tables": {"ticks", "quote"}}, "reads 2"),
        ({"publishes": "out,ticks"}, "publishes 2"),
        ({"period": "0D00:00:01"}, "--period adds a timer"),
    ],
)
def test_a_streaming_transform_the_scaffold_cannot_write_is_refused(kw, message):
    with pytest.raises(UqsError, match=message):
        _stream("j", "derive", **kw)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            [
                "x",
                "--kind",
                "normalizer",
                "--subscribe-to",
                "trades",
                "--columns",
                "a:float",
                "--transform",
                "derive",
            ],
            "--transform does not apply to --kind normalizer",
        ),
        (["x", "--triggered-by", "demo_deals", "--transform", "derive"], "--transform"),
    ],
)
def test_transform_is_refused_where_it_does_not_apply(argv, message, monkeypatch):
    from uqs.cli import create, create_reaction

    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(create, "_die", record)
    monkeypatch.setattr(create_reaction, "_die", record, raising=False)
    result = runner.invoke(cli.app, ["job", "new", *argv, "--dry-run"])
    assert result.exit_code == 1
    assert refused and message in refused[0]
