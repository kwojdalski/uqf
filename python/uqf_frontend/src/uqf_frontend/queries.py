r"""Server-authored q programs, and the coercion that feeds them.

The whole point of this module: **the q text below is a constant written
here, never assembled from client input.** A caller's table name, column
names and operator are validated against the catalog and then passed as
IPC *arguments*; a caller's values are passed as typed IPC arguments and
never rendered into text at all. That is what satisfies FE-14, and it is
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
import uuid
from typing import Any

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

#: Filter a whitelisted table by validated (column, operator, value) triples.
#:
#: Parameters arrive as IPC arguments: t=table name symbol, fc=column symbols,
#: fo=operator symbols, fv=values, lim=row cap (0 for no cap).
#:
#: The operator dictionary is defined *inside* q and looked up by key, so an
#: unrecognised operator raises there too - a second line of defence behind
#: the catalog check, rather than relying on it alone.
SELECT = """{[t;fc;fo;fv;lim]
  ops:`eq`ne`lt`le`gt`ge`in!(=;<>;<;<=;>;>=;in);
  if[not all fo in key ops;'"uqf_frontend: unknown operator"];
  wrap:{$[11h=abs type x; enlist x; x]};
  wc:{[o;c;v;m;w] (m o;c;w v)}[;;;ops;wrap]'[fo;fc;fv];
  r:?[t;wc;0b;()];
  $[lim>0; lim sublist r; r]}"""

#: Coverage intervals for one dataset at one source release, as understood at
#: an instant.
#:
#: ETL-09 requires consumers to filter on ``source_version``; doing it inside
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
#: `.qmatz.still_current`.
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
#: q null symbol `, which is .qmatz's "this dataset has no partition
#: dimension" sentinel and matches only rows recorded under it.
COVERAGE = """{[ds;part;release;at]
  select range_from, range_to from etl_coverage
    where dataset=ds, partition=part, source_version=release,
          recorded_at<=at, at<superseded_at}"""


def coerce(value: Any, qtype: QType, column: str, *, as_list: bool) -> Any:
    """Turn one JSON value into the Python type kola maps to *qtype*.

    Verified kola conversions: ``str`` becomes a symbol atom, ``list[str]`` a
    symbol vector, ``float`` a float atom, ``int`` a long atom, ``bool`` a
    boolean atom, and a **timezone-aware** ``datetime`` a timestamp.

    A naive datetime is rejected by kola itself with an unhelpful TypeError,
    so this function requires UTC explicitly. That is not a workaround - it
    is ETL-08/R9.1 (everything is UTC internally) enforced at the boundary
    where a browser's local time would otherwise leak in.
    """
    if as_list:
        if not isinstance(value, list) or not value:
            raise ValidationFailed(
                f"operator on column {column!r} needs a non-empty list of values"
            )
        return [coerce(v, qtype, column, as_list=False) for v in value]

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
        case QType.BOOLEAN:
            if not isinstance(value, bool):
                raise ValidationFailed(f"column {column!r} is a boolean; expected true or false")
            return value
        case QType.TIMESTAMP:
            return _utc(value, column)
        case QType.TIMESPAN:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationFailed(
                    f"column {column!r} is a timespan; expected nanoseconds as a number"
                )
            return dt.timedelta(microseconds=float(value) / 1000.0)
        case QType.GUID:
            # Validated here rather than passed through, so a malformed id is
            # a 422 naming the column instead of a q-side type error the
            # caller cannot act on. Returned as a string: the q side parses it
            # with "G"$, and a uuid object would not survive the IPC encoding
            # this layer uses.
            if not isinstance(value, str):
                raise ValidationFailed(f"column {column!r} is a guid; expected a string")
            try:
                return str(uuid.UUID(value))
            except ValueError as exc:
                raise ValidationFailed(
                    f"column {column!r} is a guid; {value!r} is not one"
                ) from exc
        case QType.LIST:  # pragma: no cover - blocked earlier by Table.filterable
            raise ValidationFailed(
                f"column {column!r} holds a vector per row and cannot be filtered"
            )

    raise ValidationFailed(f"column {column!r} has unsupported type {qtype}")  # pragma: no cover


def _utc(value: Any, column: str) -> dt.datetime:
    """Parse an ISO-8601 string (or accept a datetime) as an aware UTC value."""
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
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
    return parsed.astimezone(dt.UTC)


def build_filters(
    tbl: Table, filters: list[tuple[str, str, Any]]
) -> tuple[list[str], list[str], list[Any]]:
    """Validate filters against the catalog and split them into the three
    parallel argument lists ``SELECT`` expects.

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
