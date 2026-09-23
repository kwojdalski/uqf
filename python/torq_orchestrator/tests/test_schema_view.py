"""Tests for `uqf-stack schema`'s reading of a live process.

The IPC itself is not mocked into a fake q - what is worth pinning here is
the translation layer between what `meta` hands back and what a reader sees,
because that is where a wrong answer would be plausible rather than obvious.
A column rendered as `float` when it is a float VECTOR describes a different
table, and nothing downstream would complain.
"""

from __future__ import annotations

import pytest

from torq_orchestrator import core
from torq_orchestrator.checks.schema_view import ATTR_NAMES, TYPE_NAMES, type_name


def test_an_atom_column_is_named_plainly():
    assert type_name("f") == "float"
    assert type_name("p") == "timestamp"
    assert type_name("s") == "symbol"


def test_a_vector_column_is_distinguished_from_an_atom():
    # The distinction this function exists for. `quotes` and `mkt_orderbook`
    # are built around vector columns, and every pricing function in src/
    # expects that shape - rendering `F` as "float" would describe a
    # different table entirely.
    assert type_name("F") == "float vector"
    assert type_name("J") == "long vector"
    assert type_name("f") != type_name("F")


def test_a_general_column_is_named_rather_than_blank():
    # q reports a general column as a SPACE, which renders as an empty cell
    # and reads as "no type recorded" rather than "mixed". The same invisible
    # character silently broke the contract surface's CSV until it was quoted.
    assert type_name(" ") == "general"
    assert type_name("") == "general"


def test_an_unknown_type_character_is_passed_through_not_swallowed():
    # A q version that grows a type this map does not know should show the
    # raw character, not a confident wrong name and not a blank.
    assert type_name("Q") == "Q"


def test_every_type_name_is_lowercase_so_case_carries_only_the_vector_fact():
    # If a value in the map were capitalised, `type_name` could not use case
    # to mean vector-ness without ambiguity.
    for char, name in TYPE_NAMES.items():
        assert name == name.lower(), (char, name)


def test_attributes_are_expanded():
    # `g` on a sym column is the difference between a fast lookup and a scan,
    # and its silent disappearance has no error attached - so it is shown by
    # name rather than as a letter a reader has to know.
    assert ATTR_NAMES["g"] == "grouped"
    assert ATTR_NAMES["s"] == "sorted"
    assert ATTR_NAMES["p"] == "parted"
    assert ATTR_NAMES["u"] == "unique"


def test_resolve_port_finds_a_declared_process():
    paths = core.default_paths()
    # rdb1 is base_port+2 in the vendored csv. Asserted through the registry
    # rather than by adding 2 here, which is the point of the function.
    assert core.resolve_port(paths, "rdb1", 6050) == 6052


def test_resolve_port_refuses_an_unknown_process_and_names_the_real_ones():
    # A typo should not produce a connection attempt against a port derived
    # from nothing - and the message has to be actionable, because "not a
    # declared process" alone leaves the reader guessing at spelling.
    paths = core.default_paths()
    with pytest.raises(core.UqfStackError, match="known:") as exc:
        core.resolve_port(paths, "rbd1", 6050)
    assert "rdb1" in str(exc.value)


def test_the_default_process_is_one_that_exists():
    # DEFAULT_PROC is a string constant; nothing else would catch it going
    # stale if the registry renamed the process.
    paths = core.default_paths()
    assert core.resolve_port(paths, core.DEFAULT_SCHEMA_PROC, 6050) > 0


def test_an_exact_name_is_a_pattern_that_matches_itself(monkeypatch):
    # One code path, not two: `schema quotes` and `schema 'quot*'` go through
    # the same matcher, so an exact name cannot behave differently from a
    # pattern that happens to select one table.
    from torq_orchestrator.checks import schema_view

    monkeypatch.setattr(schema_view, "table_names", lambda *a, **k: ["quotes", "trades", "quote"])
    assert schema_view.match_tables("quotes", 0) == ["quotes"]


def test_a_prefix_pattern_selects_the_group(monkeypatch):
    from torq_orchestrator.checks import schema_view

    monkeypatch.setattr(
        schema_view,
        "table_names",
        lambda *a, **k: ["crypto_book", "crypto_trades", "quotes", "trades"],
    )
    assert schema_view.match_tables("crypto*", 0) == ["crypto_book", "crypto_trades"]


def test_an_infix_pattern_matches_anywhere_in_the_name(monkeypatch):
    from torq_orchestrator.checks import schema_view

    monkeypatch.setattr(
        schema_view,
        "table_names",
        lambda *a, **k: ["crypto_trades", "trades", "trade", "quotes"],
    )
    assert schema_view.match_tables("*trade*", 0) == ["crypto_trades", "trades", "trade"]


def test_matching_is_case_sensitive(monkeypatch):
    # fnmatchcase, not fnmatch: q table names are case-sensitive, and
    # fnmatch's default normalises case on some platforms, which would make
    # this command behave differently on macOS than on Linux.
    from torq_orchestrator.checks import schema_view

    monkeypatch.setattr(schema_view, "table_names", lambda *a, **k: ["Trades", "trades"])
    assert schema_view.match_tables("trades", 0) == ["trades"]


def test_no_match_returns_empty_rather_than_raising(monkeypatch):
    # The CLI turns this into a message naming the available tables; the
    # matcher itself has nothing to say about it.
    from torq_orchestrator.checks import schema_view

    monkeypatch.setattr(schema_view, "table_names", lambda *a, **k: ["quotes"])
    assert schema_view.match_tables("nope*", 0) == []


def test_order_follows_the_process_not_the_pattern(monkeypatch):
    # Whatever order q reported the tables in is preserved, so two runs
    # against the same process render in the same order.
    from torq_orchestrator.checks import schema_view

    monkeypatch.setattr(schema_view, "table_names", lambda *a, **k: ["z_tbl", "a_tbl"])
    assert schema_view.match_tables("*_tbl", 0) == ["z_tbl", "a_tbl"]
