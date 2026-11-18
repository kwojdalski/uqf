"""Fuzzy row filtering for `--interactive` tables - the matching, without Textual.

A query is whitespace-separated terms, and a row is kept when EVERY term
matches one of its cells, fzf-style: as a substring if it can, else as a
subsequence - `upq` matches `uqs_plant_q`. Each term is matched within one
cell, never across two, so a short query does not match every row by picking
one letter from each column. Rows keep their order: the table was already
sorted the way the command was asked to sort it.

Case-insensitive. Positions are into the cell's plain text, for highlighting.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Which characters of which cells a row matched on: cell index -> positions.
Hits = dict[int, set[int]]


def terms(query: str) -> list[str]:
    """The query's terms, lower-cased. An empty query has none, and keeps every row."""
    return query.lower().split()


def substring(term: str, text: str) -> list[int] | None:
    """The positions of `term` as a contiguous run in `text`, or None."""
    at = text.lower().find(term)
    return None if at < 0 else list(range(at, at + len(term)))


def subsequence(term: str, text: str) -> list[int] | None:
    """The positions of `term`'s characters, in order and leftmost, in `text`, or None."""
    positions: list[int] = []
    for index, char in enumerate(text.lower()):
        if len(positions) < len(term) and char == term[len(positions)]:
            positions.append(index)
    return positions if len(positions) == len(term) else None


def match_row(row_terms: Sequence[str], cells: Sequence[str]) -> Hits | None:
    """What a row matched on, or None when some term matches none of its cells.

    A term found whole in any cell is highlighted there, even when an earlier
    cell holds it only scattered: `ok` should light up the `ok` in Heartbeat,
    not an o and a k somewhere in the process name.
    """
    hits: Hits = {}
    for term in row_terms:
        found = next(
            (
                (index, positions)
                for matcher in (substring, subsequence)
                for index, cell in enumerate(cells)
                if (positions := matcher(term, cell)) is not None
            ),
            None,
        )
        if found is None:
            return None
        index, positions = found
        hits.setdefault(index, set()).update(positions)
    return hits


def filter_rows(rows: Sequence[Sequence[str]], query: str) -> list[tuple[int, Hits]]:
    """The rows `query` keeps, as (index into `rows`, hits), in their original order."""
    row_terms = terms(query)
    kept = []
    for index, cells in enumerate(rows):
        hits = match_row(row_terms, cells)
        if hits is not None:
            kept.append((index, hits))
    return kept
