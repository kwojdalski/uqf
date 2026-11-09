"""The Compare view's one call: a table on two endpoints, as `uqs data diff`
compares it (#2033).

NOTHING HERE COMPARES. Reaching each side - this stack's gateway, or a
target's through an ssh tunnel - is uqs's (`uqs.cli.data_diff.run`, the
function the command calls), and every rule that decides the result is q's
(src/etl/core/compare.q): how a filter's text is typed, the row key, what
differs, the five outcomes and the label a truncated sample carries. This
module checks that a target side is one the registry declares (as browsing
does), turns the request into uqs's arguments, and a refusal into the API's
error. The browser only shows what comes back.

uqs is imported inside the function, as tunnels.py imports it: the read path
for /query and /catalog loads nothing of the orchestrator.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from uqf_frontend import control, instants, remote_files
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, ValidationFailed


class CompareRefused(FrontendError):
    """uqs or q refused the comparison as asked: a filter on a column the
    table lacks, a value its column's type refuses, an empty window. 422, a refusal
    of what the caller sent (#2225) - as /query's ValidationFailed is."""

    status_code = 422


class CompareFailed(FrontendError):
    """The comparison never ran: no q interpreter on the server, or its q run
    failed. A fault of the server, not of the request (#2529)."""

    status_code = 502


class CompareTimedOut(FrontendError):
    """The comparison's q ran past the request's budget and was killed (#2529)."""

    status_code = 504
    transient = True


#: The side that is this frontend's own stack, as uqs spells it.
LOCAL = "local"

#: models.Filter.op as the COLUMN OP VALUE text `uqs data diff --filter` takes
#: (.qetl.compare.ops in src/etl/core/compare.q): `in` is `=` with a comma
#: list. The API speaks /query's words (#2223); this is the one place the text
#: grammar is written, and q types each value by its column, as it did.
TEXT_OPS = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=", "in": "="}

_COLUMN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _value_text(column: str, value: Any) -> str:
    """One value as q's parse_filter reads it: no comma, which would make it
    a list, and nothing that is not a string, a number or a boolean."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if not isinstance(value, str | int | float):
        raise ValidationFailed(f"filter on {column}: a value is a string, a number or a boolean")
    text = str(value)
    if not text or "," in text:
        raise ValidationFailed(
            f"filter on {column}: a value is not empty and holds no comma - "
            "give several values with the `in` operator"
        )
    return text


def filter_texts(filters: Sequence[Any]) -> list[str]:
    """Each models.Filter as the COLUMN OP VALUE text uqs takes."""
    texts = []
    for f in filters:
        if not _COLUMN.fullmatch(f.column):
            raise ValidationFailed(f"filter column {f.column!r} is not a column name")
        if f.op == "in":
            if not isinstance(f.value, list) or not f.value:
                raise ValidationFailed(f"filter on {f.column}: `in` takes a non-empty list")
            values = ",".join(_value_text(f.column, v) for v in f.value)
        elif isinstance(f.value, list):
            raise ValidationFailed(f"filter on {f.column}: only `in` takes a list")
        else:
            values = _value_text(f.column, f.value)
        texts.append(f"{f.column}{TEXT_OPS[f.op]}{values}")
    return texts


def sides(left: str, right: str) -> list[str]:
    """The target sides of a request - every side that is not `local`."""
    return [s for s in (left, right) if s != LOCAL]


def run(settings: Settings, req: Any) -> dict[str, Any]:
    """`uqs data diff --json`'s document for `req` (a models.CompareRequest)."""
    from uqs import timerange
    from uqs.cli import data_diff
    from uqs.paths import UqsError, UqsUsageError
    from uqs.stack.data_diff import DiffNotRun, DiffTimedOut

    # Exact or refused, before anything is reached: uqs's range is a datetime,
    # and `uqs data diff` refuses a bound finer than a microsecond rather than
    # compare a window up to 999 ns short of it - so does this (#2105).
    start = instants.bound("range_from", req.range_from)
    end = instants.bound("range_to", req.range_to)
    filters = filter_texts(req.filters)
    for name in sides(req.left, req.right):
        remote_files.target(settings, name)  # a registered target, or 404/409
    try:
        window = timerange.resolve(start.isoformat(), end.isoformat(), None, None)
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None
    try:
        doc = data_diff.run(
            control._paths(settings), req.left, req.right, list(req.tables), window,
            # each side capped as /query's read is (#2524)
            filters=filters, limit=min(req.limit, settings.max_rows),
            source_version=req.source_version,
            partition=req.slice, **settings.uqs_login,
            # killed inside the browser's own wait, so it sees a 504, not an
            # anonymous timeout while q runs on (#2529)
            timeout=settings.lifecycle_timeout,
        )  # fmt: skip
    except UqsUsageError as exc:
        raise ValidationFailed(str(exc)) from None
    except DiffTimedOut as exc:
        raise CompareTimedOut(str(exc)) from None
    except DiffNotRun as exc:
        raise CompareFailed(str(exc)) from None
    except UqsError as exc:
        raise CompareRefused(str(exc)) from None
    # The ledger's q column is `partition`; the API's word for it is `slice`,
    # mapped here at the boundary rather than renamed on disk (#2261).
    doc = {k: v for k, v in doc.items() if k != "partition"} | {"slice": doc.get("partition")}
    # q answers a slice it did not check as "" (the null symbol); the API says
    # null, as it was asked, so "" means only the no-slice-dimension slice (#2221).
    if req.source_version is None:
        doc = {**doc, "source_version": None, "slice": None}
    # The filters as they were sent, not the text they were rendered to.
    return {**doc, "filters": [f.model_dump() for f in req.filters]}
