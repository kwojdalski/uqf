"""Coverage interval arithmetic: is a requested range actually published?

FE-09 requires that before querying a bounded dataset the caller can confirm
its inputs are covered. The rules come from the ETL framework, not from here:

- **ETL-08** - every coverage interval is half-open ``[range_from, range_to)``,
  and adjacent intervals compose **only at their common boundary**. So
  ``[Mon,Tue)`` and ``[Tue,Wed)`` merge into ``[Mon,Wed)``; ``[Mon,Tue)`` and
  ``[Wed,Thu)`` leave Tuesday as a gap and must not be merged across it.
- **ETL-09** - ``source_version`` is an immutable source-release label and
  coverage consumers **must filter on it**. Coverage recorded under one
  release says nothing about another.
- **ETL-10** - intervals from different versions are never merged to satisfy a
  dependency.

This module is deliberately pure so the interval logic is unit-testable
without a gateway, which is the same split ETL-04 asks for on the q side.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Interval:
    """A half-open ``[start, end)`` range. ``start`` first so sorting works."""

    start: dt.datetime
    end: dt.datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(
                f"an interval must be non-empty and forward-going, got [{self.start}, {self.end})"
            )

    def touches(self, other: Interval) -> bool:
        """True when the two overlap or meet exactly at a boundary.

        Meeting at a boundary counts, because with half-open intervals
        ``[a,b)`` and ``[b,c)`` are contiguous with nothing between them.
        """
        return self.start <= other.end and other.start <= self.end


def compose(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping and boundary-adjacent intervals, leaving real gaps.

    Composing only at a common boundary is ETL-08's rule, and it is the whole
    reason a naive "merge anything close" would be wrong: it would silently
    report a missing day as covered.
    """
    if not intervals:
        return []
    merged: list[Interval] = []
    for iv in sorted(intervals):
        if merged and merged[-1].touches(iv):
            last = merged[-1]
            merged[-1] = Interval(last.start, max(last.end, iv.end))
        else:
            merged.append(iv)
    return merged


def gaps(requested: Interval, covered: list[Interval]) -> list[Interval]:
    """The sub-ranges of *requested* that no interval in *covered* covers.

    An empty result means fully covered.
    """
    missing: list[Interval] = []
    cursor = requested.start
    for iv in compose(covered):
        if iv.end <= cursor:
            continue
        if iv.start >= requested.end:
            break
        if iv.start > cursor:
            missing.append(Interval(cursor, min(iv.start, requested.end)))
        cursor = max(cursor, iv.end)
        if cursor >= requested.end:
            break
    if cursor < requested.end:
        missing.append(Interval(cursor, requested.end))
    return missing


def from_rows(
    rows: list[dict], *, start_field: str = "range_from", end_field: str = "range_to"
) -> list[Interval]:
    """Build intervals from ``etl_coverage`` rows, skipping malformed ones.

    A row whose end is at or before its start would be rejected at write time
    by ETL-08, so encountering one here means the ledger itself is damaged. It
    is skipped rather than raised on, so one bad row cannot make an otherwise
    answerable coverage question unanswerable - but the caller is told how
    many were dropped.
    """
    out: list[Interval] = []
    for row in rows:
        start, end = row.get(start_field), row.get(end_field)
        if not isinstance(start, dt.datetime) or not isinstance(end, dt.datetime):
            continue
        try:
            out.append(Interval(_aware(start), _aware(end)))
        except ValueError:
            continue
    return out


def _aware(value: dt.datetime) -> dt.datetime:
    """Treat a naive timestamp from q as UTC, per ETL-08/R9.1.

    q stores UTC, and kola hands back naive datetimes for a timestamp column,
    so this is a re-labelling rather than a conversion.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
