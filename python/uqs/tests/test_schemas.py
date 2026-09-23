"""Tests for the reader in `uqs.model.schemas`.

The tickerplant table definitions live in `scripts/processes/uqs_tables.q`, and
this module reads them out by table name. Two things can go wrong with that
arrangement, and neither announces itself:

* **The reader stops matching.** If the `name:([]...)` convention in the q
  file changes, the regex finds nothing, every generated `database.q` loses
  uqf's tables, and the first symptom is a feed failing to publish into a
  table the tickerplant does not have.
* **A shape a consumer relies on breaks.** The wide book's levels must stay
  paired and contiguous, and nothing but a test reads them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uqs.model import schemas

#: The q suite's own list of tickerplant tables - the one hand-kept list,
#: which `uqs new-job` appends to. Read rather than copied: a second copy
#: here meant every new table was two edits, and the two could disagree.
#: Parsed by splitting the `expected:` symbol list, deliberately NOT with the
#: `schemas` regex under test, so this remains an independent check of it.
_Q_TABLE_TEST = Path(__file__).resolve().parents[3] / "tests" / "q" / "test_stack_tables.q"


def _expected_tables() -> set[str]:
    lines = [ln for ln in _Q_TABLE_TEST.read_text().splitlines() if ln.startswith("expected:")]
    assert len(lines) == 1, f"{_Q_TABLE_TEST} should hold exactly one `expected:` line"
    names = {n for n in lines[0].removeprefix("expected:").strip().split("`") if n}
    assert names, f"{_Q_TABLE_TEST}'s `expected:` list is empty"
    return names


EXPECTED_TABLES = _expected_tables()


def test_the_q_file_is_where_the_reader_expects_it():
    assert schemas.TABLES_Q.is_file(), f"table definitions not found at {schemas.TABLES_Q}"


def test_every_expected_table_is_found():
    # The failure this guards: a convention change that makes the regex match
    # nothing. Every generated database.q would silently lose uqf's tables.
    assert set(schemas._definitions()) == EXPECTED_TABLES


def test_definition_refuses_an_unknown_table():
    # Returning None would reach _generated_schema_content and produce a
    # tickerplant without the table; the first symptom would be a feed
    # failing to publish.
    with pytest.raises(KeyError, match="not defined"):
        schemas.definition("no_such_table")


def test_the_wide_book_levels_are_paired_and_contiguous_from_zero():
    # .qbook.derive_level_groups finds levels by a prefix plus a CONTIGUOUS
    # digit suffix, so a gap yields a shorter book rather than an error, and
    # a bid level with no ask is a book nothing can price. Read from the q
    # definition, which is the only place the level count is stated.
    definition = schemas.definition("wide_book")
    bids = [int(n) for n in re.findall(r"\bbids(\d+):", definition)]
    asks = [int(n) for n in re.findall(r"\basks(\d+):", definition)]
    assert bids, "wide_book declares no bid levels"
    assert bids == list(range(len(bids)))
    assert asks == bids


def test_a_comment_line_is_never_read_as_a_definition():
    # Every line of prose in that file starts with `/`, and the reader is
    # line-anchored - but a regex that lost its anchor would start pulling
    # table names out of the commentary.
    text = schemas.TABLES_Q.read_text()
    assert "/ uqs_tables.q" in text, "the file's header comment is missing"
    assert all(not name.startswith("/") for name in schemas._definitions())


def test_no_definition_carries_rows():
    # These are declarations. One arriving with data would seed the
    # tickerplant with rows nobody published.
    for definition in schemas._definitions().values():
        assert "([]" in definition, f"not an empty-table literal: {definition[:40]!r}"
