r"""Server-authored q programs, and the coercion that feeds them.

The whole point of this module: **the q text below is a constant written
here, never assembled from client input.** A caller's table name, column
names and operator are validated against the catalog and then passed as
IPC *arguments*; a caller's values are passed as typed IPC arguments and
never rendered into text at all. That is the security guarantee, and it is
strictly stronger than escaping or quoting a concatenated string.

Three q details this depends on, all verified against a live KDB-X process
rather than assumed:

0. A TorQ backend evaluates a routed query with ``value`` (gateway.q:253), and
   ``value`` applied to a *list* **applies** rather than parses:
   ``value ("{[s] ...}"; `EURUSD)`` runs the lambda on the argument. That is
   what carries parameterisation through the gateway rather than losing it at
   the tier boundary. The lambda must be sent as a **char vector**, which
   means ``bytes`` from Python - kola maps ``str`` to a q *symbol*, and a
   symbol head makes ``value`` try to resolve a variable named the entire
   lambda text.

A naming hazard these programs must respect: **a q builtin cannot be used as
a lambda parameter name.** Doing so raises a bare ``'nyi`` when the lambda is
*called*, not when it is defined, and regardless of whether the body
references the parameter. ``{[ds;sv] 1+1}[\`a;\`b]`` fails because ``sv`` is
scalar-from-vector; ``{[ds;release] ...}`` is fine. ``test_q_programs.py``
checks every parameter in this module against the 182 reserved and ``.q``
names, because this trap has already cost this repository three separate
debugging sessions (``desc`` and ``tables`` in scripts/processes/torq_pipeline.q, and
``sv`` here).

1. A functional select accepts the table *name* as a symbol -
   ``?[`trades; ...; 0b; ()]`` - so there is no ``get`` on a
   caller-influenced symbol anywhere in the path.
2. In a functional where-clause a bare symbol is read as a *column name*.
   A symbol **value** must therefore be enlisted or it silently becomes a
   column reference. Any other type must **not** be enlisted, because a
   1-element list does not broadcast against a column vector and raises
   'length. The ``wrap`` lambda below encodes exactly that rule.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

import polars as pl

from uqf_frontend.catalog import LIST_OPERATORS, OPERATORS, QType, Table
from uqf_frontend.errors import ValidationFailed

#: Every table a data tier holds, with each column's `meta` type character.
#:
#: The browsable surface is the INTERSECTION of this and CATALOG below: this
#: says what exists, that says what a desk is allowed to see and what it is
#: for. Neither alone is the allowlist.
#:
#: Routed to a tier rather than run on the gateway, because the gateway holds
#: no data and `meta` there would answer about the gateway's own namespace.
#:
#: `kind` rather than `type`, which is a q keyword. An untyped column - a
#: vector-valued one like `bid_prices` - has a BLANK character here, and that
#: is the LIST type the frontend refuses to filter on.
#: A plain expression, not a niladic lambda: q returns a `{[] ...}` sent with
#: no arguments as the FUNCTION, which kola cannot deserialise. See
#: test_q_programs.py, which has caught this twice.
SCHEMA = """raze {[nm] m:0!meta nm; ([] table:nm; column:m`c; kind:m`t)} each tables[]"""

#: What each browsable table is for, from `.qcat` on the gateway.
#:
#: The prose half of the catalog, and the only half that cannot be derived.
#: It lives in scripts/processes/uqs_catalog.q, which gateway1 loads via
#: VENDORED_LOAD_OVERLAY in uqs/stack/procs.py. A table `.qcat` hides, or
#: never describes, is not in this answer and so is not browsable.
#:
#: `call`, not `route`: this runs on the gateway process itself, which is
#: where .qcat is loaded. Routing it would ask a data tier about a namespace
#: it does not have.
CATALOG = ".qcat.surface[]"

#: Read one browsable table under the caller's query policy (#889).
#:
#: A gateway function, not a lambda: the data login (a non-trusted role) may
#: run it and nothing else. It takes table, filter columns, operator names,
#: values, a row cap and the tiers, turns them into a .dataaccess.getdata
#: request, and getdata holds that to the table's policy - see
#: scripts/torqcode/gateway/browse.q. An unknown operator is refused there
#: too, a second line of defence behind the catalog check.
BROWSE = ".uqf.browse"

#: Coverage intervals for one dataset at one source release, as understood at
#: an instant.
#:
#: Consumers must filter on ``source_version``; doing it inside
#: the program rather than in Python means a caller cannot omit it. Restatement adds
#: the same reasoning one dimension over: a coverage row is true *until
#: superseded*, so a read without an as-of silently reports withdrawn claims
#: as current.
#:
#: The as-of is a REQUIRED parameter for the reason `source_version` is
#:: an optional filter is one a caller forgets, and forgetting this one
#: returns a plausible interval list rather than an error. Pass the gateway's
#: own `.z.p` for "now".
#:
#: `recorded_at<=at<superseded_at` — current rows carry `0Wp` rather than a
#: null precisely so this comparison needs no special case; see
#: `.qetl.coverage.still_current`.
#:
#: The parameter is `at`, not `asof`: **`asof` is a q builtin**, and a builtin
#: used as a lambda parameter raises a bare ``'nyi`` when the lambda is
#: CALLED - not when it is defined, and whether or not the body references it.
#: `test_q_programs.py` caught this before it shipped, which is the fourth
#: name this repository has lost to that trap.
#: `partition` is required for the same reason again, one dimension further
#: out (#185). A coverage read that names a dataset but not a partition
#: aggregates across every partition, so a range published for EURUSD alone
#: reports as covered for every symbol - the exact failure the column was
#: added to prevent, rebuilt on the HTTP side. The empty string maps to the
#: q null symbol `, which is .qetl.coverage's "this dataset has no partition
#: dimension" sentinel and matches only rows recorded under it.
#: Since #1081 a NAME on the gateway, not a lambda routed to the tiers: the
#: ledger is a file the workers persist (.qetl.coverage.ledger_path), not a
#: table any RDB or HDB holds, and gateway1 loads the reader. The same
#: arguments and the same answer as before - the claims current at the as-of.
COVERAGE = ".qetl.coverage.claims"

#: The claims a reader can query NOW - recorded at or before the dataset's
#: readiness watermark (#1094). COVERAGE means written: a backfill stages it
#: as it appends, before the HDB has finished and reloaded the date. /query's
#: require_coverage composes these too, so a short result is refused rather
#: than returned under a completeness guarantee.
READY_CLAIMS = ".qetl.coverage.ready_claims"

#: The coverage ledger's own interval arithmetic, run on the GATEWAY
#: (`Gateway.call`, not `route`): COVERAGE's rows come back razed from both
#: tiers, and only the gateway holds both halves, so that is where they are
#: composed. gateway1 loads src/etl/core/intervals.q for this. Names, not
#: lambdas - the arithmetic is the one copy a backfill also trusts, never a
#: second one here. `range_to>range_from` in COVERAGE drops a damaged row (the
#: ledger refuses one at write) rather than letting it fail the whole read.
#: GAPS is the one lambda: its bounds come from `coerce` as one-item
#: timestamp vectors, the only form kola carries a nanosecond in (#1371), and
#: it unwraps them for .qetl.coverage.gaps, which compares atoms.
COMPOSE = ".qetl.coverage.compose"
GAPS = "{[s;e;c] .qetl.coverage.gaps[first s;first e;c]}"


def coerce(value: Any, qtype: QType, column: str, *, as_list: bool) -> Any:
    """Turn one JSON value into the Python type kola maps to *qtype*.

    Verified kola conversions: ``str`` becomes a symbol atom, ``list[str]`` a
    symbol vector, ``float`` a float atom, ``int`` a long atom, ``bool`` a
    boolean atom, a polars ``Datetime("ns", "UTC")`` series a timestamp
    vector (`_utc`), a polars ``Duration("ns")`` series a timespan vector
    (`_nanoseconds`), and a polars ``Int16`` series a short vector (an `in`
    list for a short column, #1391).

    A timestamp must carry its timezone: this function requires UTC
    explicitly. That is not a workaround - it
    is the everything-is-UTC-internally rule enforced at the boundary
    where a browser's local time would otherwise leak in.
    """
    if as_list:
        if not isinstance(value, list) or not value:
            raise ValidationFailed(
                f"operator on column {column!r} needs a non-empty list of values"
            )
        values = [coerce(v, qtype, column, as_list=False) for v in value]
        if qtype is QType.SHORT:
            # A list of ints goes to q as a LONG vector, and KDB-X's `in`
            # refuses a short column against one with 'type (`1 2h in 1 2j`)
            # where `=` and an int column compare by value (#1391). The
            # gateway does not hold the table, so the column's type is known
            # here, from the catalog, and nowhere on the way: send the
            # column's own width. kola sends an Int16 series as a short
            # vector, which .uqf.browse passes through (browse_value).
            return pl.Series(column, values, dtype=pl.Int16)
        return values

    match qtype:
        case QType.SYMBOL:
            if not isinstance(value, str):
                raise ValidationFailed(f"column {column!r} is a symbol; expected a string")
            return value
        case QType.FLOAT:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationFailed(f"column {column!r} is a float; expected a number")
            return float(value)
        case QType.LONG:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationFailed(f"column {column!r} is a long; expected an integer")
            return value
        case QType.INT:
            return _bounded_integer(value, column, "int", _INT_LIMIT)
        case QType.SHORT:
            return _bounded_integer(value, column, "short", _SHORT_LIMIT)
        case QType.DATE:
            return _date(value, column)
        case QType.TIME:
            return _time_of_day(value, column)
        case QType.BOOLEAN:
            if not isinstance(value, bool):
                raise ValidationFailed(f"column {column!r} is a boolean; expected true or false")
            return value
        case QType.TIMESTAMP:
            return _utc(value, column)
        case QType.TIMESPAN:
            return _nanoseconds(value, column)
        case QType.GUID:
            # Validated here rather than passed through, so a malformed id is
            # a 422 naming the column instead of a q-side type error the
            # caller cannot act on. Returned as bytes, which kola sends as a
            # q char vector that .uqf.browse parses with "G"$ (#1042): kola
            # refuses a uuid.UUID outright, and a str would arrive as a
            # symbol, which a guid column rejects with 'type.
            if not isinstance(value, str):
                raise ValidationFailed(f"column {column!r} is a guid; expected a string")
            try:
                return str(uuid.UUID(value)).encode()
            except ValueError as exc:
                raise ValidationFailed(
                    f"column {column!r} is a guid; {value!r} is not one"
                ) from exc
        case QType.LIST | QType.CHAR:  # pragma: no cover - blocked by Table.filterable
            raise ValidationFailed(f"column {column!r} is a {qtype} column and cannot be filtered")

    raise ValidationFailed(f"column {column!r} has unsupported type {qtype}")  # pragma: no cover


#: The widest int and short q holds, less the infinity and null it reserves
#: at each end (0Wi, 0Ni): a filter value there would match a sentinel.
_INT_LIMIT = 2**31 - 1
_SHORT_LIMIT = 2**15 - 1


def _bounded_integer(value: Any, column: str, name: str, limit: int) -> int:
    """A JSON integer for an int or short column. Sent as a long, which q
    compares with the column by value (`5i=5j`, `5h<6j`, `1 2i in 1 2j`), so
    no cast is needed - only the range check, because a value q's int cannot
    hold would otherwise match nothing and say nothing. The exception is an
    `in` list for a short column, which `coerce` sends as a short vector:
    KDB-X refuses `1 2h in 1 2j` with 'type (#1391)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationFailed(f"column {column!r} is a {name}; expected an integer")
    if not -limit <= value <= limit:
        raise ValidationFailed(
            f"column {column!r} is a {name}; {value!r} is outside -{limit} to {limit}"
        )
    return value


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$")


def _date(value: Any, column: str) -> dt.date:
    """`YYYY-MM-DD` as the date kola sends as a q date."""
    if not isinstance(value, str) or not _DATE.match(value):
        raise ValidationFailed(f"column {column!r} is a date; expected YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailed(f"column {column!r} is a date; {value!r} is not one") from exc


def _time_of_day(value: Any, column: str) -> dt.time:
    """`HH:MM:SS[.fff]` as the time kola sends as a q time.

    q's `time` is milliseconds: kola truncates a finer fraction without a word
    (4005 us arrives as 4 ms), so more than three digits are refused here
    rather than moved.
    """
    m = _TIME.match(value) if isinstance(value, str) else None
    if m is None:
        raise ValidationFailed(
            f"column {column!r} is a time; expected HH:MM:SS or HH:MM:SS.fff (milliseconds)"
        )
    hh, mm, ss, frac = m.groups()
    try:
        return dt.time(int(hh), int(mm), int(ss), int((frac or "").ljust(3, "0")) * 1000)
    except ValueError as exc:
        raise ValidationFailed(f"column {column!r} is a time; {value!r} is not one") from exc


#: The widest timespan q holds, in nanoseconds: 0Wn. One further is 0Nn.
_SPAN_LIMIT = 2**63 - 1


def _nanoseconds(value: Any, column: str) -> pl.Series:
    """A whole number of nanoseconds as the one-item timespan vector kola
    sends as a q timespan, exact.

    Not a `timedelta`: that stops at the microsecond, so 1234 ns went to q as
    1 us and `skew eq 1234` matched nothing (#1329). kola has no Python atom
    that carries a nanosecond, but it sends a polars `Duration("ns")` series
    as a q timespan vector, and .uqf.browse unwraps a one-item one
    (browse_value). A fraction of a nanosecond is refused rather than
    rounded, and so is a count q has no timespan for.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationFailed(f"column {column!r} is a timespan; expected nanoseconds as a number")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValidationFailed(
                f"column {column!r} is a timespan; {value!r} is not a whole number of nanoseconds"
            )
        value = int(value)
    if not -_SPAN_LIMIT <= value <= _SPAN_LIMIT:
        raise ValidationFailed(
            f"column {column!r} is a timespan; {value!r} nanoseconds is past what q can hold"
        )
    return pl.Series([value], dtype=pl.Int64).cast(pl.Duration("ns"))


#: The span a q timestamp covers, as nanoseconds since 1970, both ends
#: exclusive: -0Wp and 0Wp. q counts from 2000, so its range is shifted by
#: this; kola holds 0Wp at the widest a polars datetime goes.
_Q_EPOCH = 946_684_800 * 10**9
_STAMP_LIMITS = (-(2**63 - 1) + _Q_EPOCH, 2**63 - 1)

#: The fraction of a second, matched on the text: `fromisoformat` takes any
#: number of digits there and keeps the first six without a word (#1317).
_FRACTION = re.compile(r"^(.*?\d{2}:\d{2}:\d{2})[.,](\d+)(.*)$")


def _utc(value: Any, column: str) -> pl.Series:
    """An ISO-8601 string (or an aware datetime) as the one-item timestamp
    vector kola sends as a q timestamp, exact to the nanosecond.

    Not a `datetime`: that stops at the microsecond, so a `.z.p`-stamped row
    could not be asked for by the time the Desk shows for it - since #1317
    the value was refused rather than truncated (#1371). The fraction is read
    from the text, up to nine digits; a tenth that is not zero is refused, as
    is an instant q has no timestamp for. .uqf.browse unwraps the vector
    (browse_value), as it does a timespan's.
    """
    if isinstance(value, dt.datetime):
        parsed, nanoseconds = value, value.microsecond * 1000
        parsed = parsed.replace(microsecond=0)
    elif isinstance(value, str):
        match = _FRACTION.match(value)
        head, digits, tail = match.groups() if match else (value, "", "")
        if digits[9:].strip("0"):
            raise ValidationFailed(
                f"column {column!r} is a timestamp; {value!r} is finer than a nanosecond, "
                "which q cannot hold"
            )
        nanoseconds = int(digits[:9].ljust(9, "0"))
        try:
            parsed = dt.datetime.fromisoformat(head + tail)
        except ValueError:
            raise ValidationFailed(
                f"column {column!r} is a timestamp; expected an ISO-8601 datetime, got {value!r}"
            ) from None
    else:
        raise ValidationFailed(f"column {column!r} is a timestamp; expected an ISO-8601 string")

    if parsed.tzinfo is None:
        raise ValidationFailed(
            f"column {column!r} needs an explicit timezone offset (e.g. "
            f"'2026-09-15T10:30:00Z'); everything is stored in UTC and a naive "
            f"local time would be silently wrong"
        )
    # Whole seconds as an exact integer, then the fraction added: a float
    # timestamp() would round the nanoseconds away.
    since = parsed - dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    total = (since // dt.timedelta(seconds=1)) * 10**9 + nanoseconds
    low, high = _STAMP_LIMITS
    if not low < total < high:
        raise ValidationFailed(
            f"column {column!r} is a timestamp; {value!r} is past what q can hold"
        )
    return pl.Series([total], dtype=pl.Int64).cast(pl.Datetime("ns", "UTC"))


def build_filters(
    tbl: Table, filters: list[tuple[str, str, Any]]
) -> tuple[list[str], list[str], list[Any]]:
    """Validate filters against the catalog and split them into the three
    parallel argument lists ``BROWSE`` expects.

    Every rejection here happens before any IPC call.
    """
    columns: list[str] = []
    operators: list[str] = []
    values: list[Any] = []

    for column, operator, value in filters:
        if column not in tbl.columns:
            known = ", ".join(sorted(tbl.filterable))
            raise ValidationFailed(
                f"unknown column {column!r} on table {tbl.name!r}; filterable columns are: {known}"
            )
        if column not in tbl.filterable:
            raise ValidationFailed(
                f"column {column!r} holds a vector per row and cannot be filtered on"
            )
        if operator not in OPERATORS:
            known = ", ".join(sorted(OPERATORS))
            raise ValidationFailed(f"unknown operator {operator!r}; supported: {known}")

        columns.append(column)
        operators.append(operator)
        values.append(
            coerce(value, tbl.columns[column], column, as_list=operator in LIST_OPERATORS)
        )

    return columns, operators, values
