"""What a scaffold writes past the job itself: a worked example, and doc stubs (#713).

`uqs job new` used to stop at "it loads". Seeing a new backfill publish meant
writing an example script by hand, and the process's Showcase card and its
stack.md line were notes the scaffold printed and nothing enforced. Now a
backfill gets scripts/examples/<name>_example.q, and every process gets a
SCAFFOLDED card and stack.md comment that test_no_scaffold_left.py holds open
until they are written.

That the example actually RUNS needs q: see
test_scaffold_loads.py::test_a_scaffolded_backfill_example_runs_straight_away.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from uqs.paths import UqsError
from uqs.scaffold import jobs, write
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import definition_columns, parse_columns, table_definition
from uqs.scaffold.docs import (
    SHOWCASE_PAGE,
    STACK_PAGE,
    showcase_card,
    with_card,
    without_stubs,
)
from uqs.scaffold.example import example_path
from uqs.scaffold.external import external_feed
from uqs.scaffold.normalizer import normalizer
from uqs.scaffold.plan import WriteMode

UQF_ROOT = Path(__file__).resolve().parents[3]
SHOWCASE = (UQF_ROOT / SHOWCASE_PAGE).read_text()


def _action(plan, path: Path):
    found = [a for a in plan.actions if a.path == path]
    assert len(found) == 1, [str(a.path) for a in plan.actions]
    return found[0]


def _section(plan) -> str:
    return _action(plan, SHOWCASE_PAGE).anchor


def test_a_backfill_gets_an_example_that_is_not_a_placeholder():
    plan = backfill.bounded_worker("tx", "tx_rows", "sym:symbol, px:float")
    body = _action(plan, example_path("tx")).body
    assert example_path("tx") == Path("scripts/examples/tx_example.q")
    assert "SCAFFOLDED" not in body, (
        "the example runs as written; the marker would demand a rewrite"
    )
    for needed in (
        "\\l src/etl/init.q",
        "`tx_backfill",
        "`tx_rows",
        "UQF_SOURCE_CRED_TX",
        "exit 1",
    ):
        assert needed in body, needed
    assert any("tx_example.q" in n for n in plan.notes), plan.notes


def test_only_a_local_source_builds_a_source_hdb():
    local = _action(
        backfill.bounded_worker("tx", "tx_rows", "sym:symbol, px:float", transport="local"),
        example_path("tx"),
    ).body
    ipc = _action(
        backfill.bounded_worker("tx", "tx_rows", "sym:symbol, px:float"), example_path("tx")
    ).body
    assert "write_source" in local and "query_is_stub" in local
    assert "write_source" not in ipc and "query_is_stub" not in ipc


def test_an_example_that_exists_already_is_refused(tmp_path):
    """hdb_transfer has a hand-written example; a scaffold must not overwrite it."""
    plan = backfill.bounded_worker("hdb_transfer", "copy2", "sym:symbol", define_table=False)
    for rel in [a.path for a in plan.actions if a.mode is WriteMode.APPEND] + [
        example_path("hdb_transfer")
    ]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, tmp_path / rel)
    with pytest.raises(UqsError, match="hdb_transfer_example.q already exists"):
        write.apply_plan(plan, tmp_path)


def test_every_process_gets_both_stubs_in_the_right_section():
    feed_table = table_definition("ticks", parse_columns("sym:symbol, px:float"))
    plans = {
        "Backfills": backfill.bounded_worker("tx", "tx_rows", "sym:symbol, px:float"),
        "Feeds": jobs.streaming_job("pulse", [], "pulse_ticks", "sym:symbol, px:float"),
        "Analytics": jobs.streaming_job(
            "stats", ["ticks"], "tick_stats", "sym:symbol, n:long", known_tables={"ticks"}
        ),
        "Normalisers": normalizer(
            "norm",
            ["ticks"],
            parse_columns("sym:symbol, mid:float"),
            {"ticks": definition_columns(feed_table)},
            known_tables={"ticks"},
        ),
        "External sources": external_feed(
            "ws", "ws_raw", "ws_quotes", "sym:symbol, px:float", known_tables={"quote"}
        ),
    }
    for section, plan in plans.items():
        assert _section(plan) == section, plan.name
        assert "SCAFFOLDED" in _action(plan, STACK_PAGE).body
        assert any("docs/services/README.md" in n for n in plan.notes), plan.notes


def test_a_card_lands_at_the_end_of_its_section():
    page = with_card(SHOWCASE, showcase_card("zz1"), "Backfills")
    at = page.index("`zz1`")
    assert page.rindex("## Backfills", 0, at) > page.rindex("## Analytics", 0, at)
    assert page.index("## Diagnostics") > at, "before the next section, not after it"


def test_a_missing_section_is_refused_not_guessed():
    with pytest.raises(UqsError, match="0 `## Backfills` headings"):
        with_card("# Showcase\n\n## Feeds\n", showcase_card("zz1"), "Backfills")


def test_removal_takes_only_what_still_says_scaffolded():
    """A card someone has written is theirs: remove leaves it, with a note."""
    page = with_card(SHOWCASE, showcase_card("zz1"), "Feeds")
    assert without_stubs(page, "zz1") == SHOWCASE
    written = page.replace(showcase_card("zz1"), "**`zz1` · a pulse** --- ticks, written up.")
    assert without_stubs(written, "zz1") == written
    assert without_stubs(page, "zz") == page, "a prefix of the name is not the name"
