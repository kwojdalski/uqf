"""--interactive's row filter: which rows a query keeps, and what it highlights."""

from __future__ import annotations

from uqs.cli.table_filter import filter_rows, match_row, subsequence, substring

ROWS = [
    ["rdb1", "up", "ok"],
    ["hdb1", "down", "error"],
    ["uqs_plant_q", "up", "warning"],
]


def kept(query: str) -> list[int]:
    return [index for index, _ in filter_rows(ROWS, query)]


def test_an_empty_query_keeps_every_row():
    assert kept("") == [0, 1, 2]
    assert kept("   ") == [0, 1, 2]


def test_one_letter_keeps_every_row_containing_it():
    assert kept("u") == [0, 2]


def test_a_query_matches_case_insensitively():
    assert kept("RDB") == [0]


def test_a_term_matches_as_a_subsequence_within_one_cell():
    assert kept("upq") == [2]


def test_a_term_never_matches_across_cells():
    # r from rdb1, o from ok: a subsequence of the joined row, of no one cell.
    assert kept("rok") == []


def test_every_space_separated_term_must_match():
    assert kept("up ok") == [0]
    assert kept("db error") == [1]
    assert kept("db missing") == []


def test_rows_keep_their_order():
    assert kept("1") == [0, 1]


def test_a_whole_match_is_preferred_to_a_scattered_one():
    # "ok" is scattered through the first cell (o-a-k) and whole in the last:
    # the whole word is what lights up.
    hits = match_row(["ok"], ["oak_keeper", "up", "ok"])
    assert hits == {2: {0, 1}}


def test_substring_and_subsequence_positions():
    assert substring("db", "rdb1") == [1, 2]
    assert substring("x", "rdb1") is None
    assert subsequence("upq", "uqs_plant_q") == [0, 4, 10]
    assert subsequence("qu", "uqs") is None
