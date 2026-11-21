"""#1594: a scaffold that would close a dependency cycle is refused before
anything is written, naming the cycle - src/etl/init.q's check_acyclic would
otherwise stop the whole tree loading."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from uqs.paths import UqsError
from uqs.scaffold import jobs, reaction
from uqs.scaffold.cycles import dependency_cycle, refuse_cycles, tree_nodes

UQF_ROOT = Path(__file__).resolve().parents[3]


def test_the_tree_as_committed_is_acyclic():
    assert dependency_cycle(tree_nodes(UQF_ROOT)) is None


def test_a_job_that_reads_its_own_output_is_refused():
    plan = jobs.streaming_job("loopy", ["quote"], "quote", None, known_tables={"quote"})
    with pytest.raises(UqsError, match="subscribe to quote and publish it too"):
        refuse_cycles(UQF_ROOT, plan)


def test_a_job_closing_a_cycle_through_an_existing_job_is_refused_naming_it():
    """executions reads trades and publishes executions; a job reading
    executions and publishing trades closes the loop through it."""
    plan = jobs.streaming_job(
        "loopy", ["executions"], "trades", None, known_tables={"executions", "trades"}
    )
    with pytest.raises(UqsError, match="loopy -> trades -> executions -> executions -> loopy"):
        refuse_cycles(UQF_ROOT, plan)


def test_publishing_onto_an_existing_table_downstream_is_still_allowed():
    plan = jobs.streaming_job(
        "downstream", ["executions"], "last_value", None,
        known_tables={"executions", "last_value"},
    )  # fmt: skip
    refuse_cycles(UQF_ROOT, plan)


def test_job_new_refuses_the_cycle_before_writing(monkeypatch):
    from typer.testing import CliRunner  # noqa: PLC0415

    from uqs import cli  # noqa: PLC0415
    from uqs.cli.job import create  # noqa: PLC0415

    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(create, "_die", record)
    argv = ["job", "new", "loopy", "--subscribe-to", "executions", "--outputs", "trades",
            "--unprofiled", "probe", "--dry-run"]  # fmt: skip
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 1
    assert refused and "close a dependency cycle" in refused[0]


def test_a_reaction_writing_the_trigger_of_a_reaction_to_its_own_dataset_is_refused(
    tmp_path: Path,
):
    """#2826: rx5 on demo_deals writing duckdb_deals, then rx6 on duckdb_deals
    writing demo_deals - both were accepted, and the tree then stopped loading
    with `topological: cycle among demo_deals~rx5, duckdb_deals~rx6`."""
    shutil.copytree(UQF_ROOT / "src" / "etl", tmp_path / "src" / "etl")
    producers = {"demo_deals": ["demo_deals_backfill1"], "duckdb_deals": ["duckdb1"]}
    first = reaction.reaction("rx5", "demo_deals", ["duckdb_deals"], producers=producers,
                              taken=set(), streaming_tables=set())  # fmt: skip
    refuse_cycles(tmp_path, first)
    (tmp_path / first.actions[0].path).write_text(first.actions[0].body)
    second = reaction.reaction("rx6", "duckdb_deals", ["demo_deals"], producers=producers,
                               taken=set(), streaming_tables=set())  # fmt: skip
    cycle = "demo_deals~rx5 -> duckdb_deals -> duckdb_deals~rx6 -> demo_deals -> demo_deals~rx5"
    with pytest.raises(UqsError, match=cycle):
        refuse_cycles(tmp_path, second)
