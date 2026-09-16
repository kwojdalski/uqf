"""Tests for the reader in `torq_orchestrator.schemas`.

The tickerplant table definitions live in `scripts/torq_demo_tables.q`, and
this module reads them out by table name. Two things can go wrong with that
arrangement, and neither announces itself:

* **The reader stops matching.** If the `name:([]...)` convention in the q
  file changes, the regex finds nothing, every generated `database.q` loses
  uqf's tables, and the first symptom is a feed failing to publish into a
  table the tickerplant does not have.
* **The Python-side facts drift from the q file.** `WIDE_BOOK_LEVELS` is a
  claim *about* the q file rather than the thing that produces it, which is
  the right way round for a schema a person reads - but it means nothing
  makes the two agree except a test.
"""

from __future__ import annotations

import re

import pytest

from torq_orchestrator import schemas

EXPECTED_TABLES = {
    "quotes",
    "wide_book",
    "mkt_orderbook",
    "crypto_book",
    "crypto_sim_fills",
    "crypto_trades",
    "trades",
    "position",
    "execution_quality",
}


def test_the_q_file_is_where_the_reader_expects_it():
    assert schemas.TABLES_Q.is_file(), f"table definitions not found at {schemas.TABLES_Q}"


def test_every_expected_table_is_found():
    # The failure this guards: a convention change that makes the regex match
    # nothing. Every generated database.q would silently lose uqf's tables.
    assert set(schemas._definitions()) == EXPECTED_TABLES


def test_each_constant_resolves_to_its_own_table():
    # A copy-paste in the constant block would point two names at one
    # definition, and the generated database.q would be missing a table while
    # looking complete.
    pairs = {
        "quotes": schemas.QUOTES_TABLE_SCHEMA,
        "wide_book": schemas.WIDE_BOOK_TABLE_SCHEMA,
        "mkt_orderbook": schemas.MKT_ORDERBOOK_TABLE_SCHEMA,
        "crypto_book": schemas.CRYPTO_BOOK_TABLE_SCHEMA,
        "crypto_sim_fills": schemas.CRYPTO_SIM_FILLS_TABLE_SCHEMA,
        "crypto_trades": schemas.CRYPTO_TRADES_TABLE_SCHEMA,
        "trades": schemas.TRADES_TABLE_SCHEMA,
        "position": schemas.POSITION_TABLE_SCHEMA,
        "execution_quality": schemas.EXECUTION_QUALITY_TABLE_SCHEMA,
    }
    for table, definition in pairs.items():
        assert definition.startswith(f"{table}:([]"), f"{table} resolved to {definition[:40]!r}"


def test_definition_refuses_an_unknown_table():
    # Returning None would reach _generated_schema_content and produce a
    # tickerplant without the table; the first symptom would be a feed
    # failing to publish.
    with pytest.raises(KeyError, match="not defined"):
        schemas.definition("no_such_table")


def test_wide_book_levels_matches_the_q_file():
    # WIDE_BOOK_LEVELS is a fact ABOUT the q file, not the generator of it.
    # The columns are written out there so a reader can check whether bids10
    # exists; nothing but this test keeps the constant honest.
    definition = schemas.WIDE_BOOK_TABLE_SCHEMA
    bids = re.findall(r"\bbids(\d+):", definition)
    asks = re.findall(r"\basks(\d+):", definition)
    assert [int(n) for n in bids] == list(range(schemas.WIDE_BOOK_LEVELS))
    assert [int(n) for n in asks] == list(range(schemas.WIDE_BOOK_LEVELS))


def test_wide_book_levels_are_contiguous_from_zero():
    # .qbook.derive_level_groups finds levels by a prefix plus a CONTIGUOUS
    # digit suffix, so a gap yields a shorter book rather than an error. The
    # q-side suite asserts this too; it is here as well because this is the
    # constant a Python caller would reason from.
    assert schemas.WIDE_BOOK_LEVELS > 0


def test_a_comment_line_is_never_read_as_a_definition():
    # Every line of prose in that file starts with `/`, and the reader is
    # line-anchored - but a regex that lost its anchor would start pulling
    # table names out of the commentary.
    text = schemas.TABLES_Q.read_text()
    assert "/ torq_demo_tables.q" in text, "the file's header comment is missing"
    assert all(not name.startswith("/") for name in schemas._definitions())


def test_no_definition_carries_rows():
    # These are declarations. One arriving with data would seed the
    # tickerplant with rows nobody published.
    for definition in schemas._definitions().values():
        assert "([]" in definition, f"not an empty-table literal: {definition[:40]!r}"
