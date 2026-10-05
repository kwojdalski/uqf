"""`uqs job new NAME --triggered-by DATASET`: scaffolding a reaction.

What it must refuse matters as much as what it writes. A reaction on a dataset
no bounded worker fills loads, registers, and never runs - only a bounded
worker's published window fires one - so that is refused before a file is
written, as is a reaction that would make the job graph refuse to load.

Loading in q is test_scaffold_loads.py's; the round trip through
`uqs job remove` is test_remove_job.py's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.model.declarations import reaction_calls, read_declarations
from uqs.model.jobs import bounded_producers, job_rows
from uqs.paths import REACTION_DIR, RUN_TESTS_FILE, UqsError
from uqs.scaffold import write
from uqs.scaffold.reaction import reaction

UQF_ROOT = Path(__file__).resolve().parents[3]
_PRODUCERS = {"demo_deals": ["deals_backfill1"]}


def _plan(name="rebuild", dataset="demo_deals", writes=(), **kwargs):
    kwargs.setdefault("producers", _PRODUCERS)
    kwargs.setdefault("taken", set())
    return reaction(name, dataset, list(writes), **kwargs)


def test_the_file_registers_with_on_and_its_handler_throws():
    body = _plan().actions[0].body
    assert ".qetl.reaction.on[`demo_deals;`rebuild;.qpipe.job.rebuild.handler];" in body
    assert "handler:{[dataset;range_from;range_to]" in body
    assert '\'"rebuild: not implemented"' in body
    assert "SCAFFOLDED" in body
    assert "deals_backfill1" in body, "the file names the process it runs in"


def test_writes_registers_with_on_writing_instead():
    body = _plan(writes=["positions"]).actions[0].body
    assert (
        ".qetl.reaction.on_writing[`demo_deals;`rebuild;enlist `positions;"
        ".qpipe.job.rebuild.handler];"
    ) in body
    assert ".qetl.reaction.on[" not in body


def test_what_it_writes_is_read_back_by_the_tree_reader():
    """reaction_calls is what `uqs list jobs` and `uqs job remove` read; a
    template it cannot parse would be a reaction nothing lists."""
    for writes, expected in (((), ()), (["a", "b"], ("a", "b")), (["p"], ("p",))):
        [found] = reaction_calls(_plan(writes=writes).actions[0].body)
        assert (found.name, found.dataset, found.writes) == ("rebuild", "demo_deals", expected)


def test_it_writes_a_failing_test_in_its_own_namespace():
    actions = {a.path: a for a in _plan().actions}
    assert set(actions) == {
        REACTION_DIR / "rebuild.q",
        Path("tests/q/test_rebuild.q"),
        RUN_TESTS_FILE,
    }, "no table, catalog, profile or port: a reaction has no process"
    assert "\\d .rebuildrxtest" in actions[Path("tests/q/test_rebuild.q")].body
    assert actions[RUN_TESTS_FILE].body == "`.rebuildrxtest"


def test_a_dataset_no_bounded_worker_fills_is_refused():
    with pytest.raises(UqsError, match="no bounded worker fills 'nope'.*demo_deals"):
        _plan(dataset="nope")


def test_a_streaming_table_is_refused_with_the_reason():
    with pytest.raises(UqsError, match="streaming jobs only.*never run"):
        _plan(dataset="quote", streaming_tables={"quote"})


def test_writing_the_dataset_it_watches_is_refused():
    with pytest.raises(UqsError, match="cycle"):
        _plan(writes=["demo_deals"])


def test_a_name_another_job_holds_is_refused():
    with pytest.raises(UqsError, match="already exists.*namespace"):
        _plan(name="markout", taken={"markout"})


def test_the_real_tree_knows_which_datasets_can_trigger():
    producers = bounded_producers(UQF_ROOT)
    assert producers["demo_deals"], "demo_deals_backfill fills demo_deals"
    streamed = {t for d in read_declarations(UQF_ROOT) for t in d.publishes}
    assert "quote" in streamed and "quote" not in producers


def test_list_jobs_shows_a_reaction_where_it_runs(tmp_path):
    shutil.copytree(UQF_ROOT / "src", tmp_path / "src")
    (tmp_path / RUN_TESTS_FILE).parent.mkdir(parents=True)
    shutil.copy2(UQF_ROOT / RUN_TESTS_FILE, tmp_path / RUN_TESTS_FILE)
    producers = bounded_producers(tmp_path)
    write.apply_plan(_plan(writes=["positions"], producers=producers), tmp_path)
    [row] = [r for r in job_rows(tmp_path) if r["job"] == "rebuild"]
    assert row == {
        "job": "rebuild",
        "kind": "reaction",
        "procname": ", ".join(producers["demo_deals"]),
        "reads": "demo_deals",
        "writes": "positions",
        "starts": "triggered",
        "state": "scaffolded",
        "file": str(REACTION_DIR / "rebuild.q"),
    }
    assert "rebuild" not in {d.name for d in read_declarations(tmp_path)}, (
        "a reaction has no process, so it must never reach the process registry"
    )


def _refusal(argv: list[str], monkeypatch) -> str:
    """The message, not only the exit code: `_die` logs through loguru, which
    CliRunner cannot capture, so it is recorded - as test_scaffold_options.py does."""
    from uqs.cli import create, create_reaction

    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(create, "_die", record)
    monkeypatch.setattr(create_reaction, "_die", record)
    result = CliRunner().invoke(cli.app, ["job", "new", *argv, "--dry-run"])
    assert result.exit_code == 1
    assert refused
    return refused[0]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--period", "0D00:00:01"], "--period does not apply to a reaction"),
        (["--kind", "backfill"], "--kind does not apply to a reaction"),
        (["--procname", "x1"], "--procname does not apply to a reaction"),
    ],
)
def test_process_options_are_refused_not_ignored(extra, message, monkeypatch):
    assert message in _refusal(["rx", "--triggered-by", "demo_deals", *extra], monkeypatch)


def test_writes_without_triggered_by_is_refused(monkeypatch):
    assert "give --triggered-by" in _refusal(["rx", "--writes", "t"], monkeypatch)
