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

from typing import Any

from uqf_frontend import control, instants, remote_files
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, ValidationFailed


class CompareRefused(FrontendError):
    """uqs or q refused the comparison as asked: a filter that is not COLUMN OP
    VALUE, a value its column's type refuses, an empty window."""

    status_code = 400


#: The side that is this frontend's own stack, as uqs spells it.
LOCAL = "local"


def sides(left: str, right: str) -> list[str]:
    """The target sides of a request - every side that is not `local`."""
    return [s for s in (left, right) if s != LOCAL]


def run(settings: Settings, req: Any) -> dict[str, Any]:
    """`uqs data diff --json`'s document for `req` (a models.CompareRequest)."""
    from uqs import timerange
    from uqs.cli import data_diff
    from uqs.paths import UqsError, UqsUsageError

    # Exact or refused, before anything is reached: uqs's range is a datetime,
    # and `uqs data diff` refuses a bound finer than a microsecond rather than
    # compare a window up to 999 ns short of it - so does this (#2105).
    start = instants.bound("range_from", req.range_from)
    end = instants.bound("range_to", req.range_to)
    for name in sides(req.left, req.right):
        remote_files.target(settings, name)  # a registered target, or 404/409
    try:
        window = timerange.resolve(start.isoformat(), end.isoformat(), None, None)
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None
    # this API's own gateway credential when it has one; else uqs's default
    login = (
        {"user": settings.user, "passwd": settings.passwd}
        if settings.user and settings.passwd
        else {}
    )
    try:
        return data_diff.run(
            control._paths(settings), req.left, req.right, list(req.tables), window,
            filters=list(req.filters), limit=req.limit, source_version=req.source_version,
            partition=req.partition, **login,
        )  # fmt: skip
    except UqsUsageError as exc:
        raise ValidationFailed(str(exc)) from None
    except UqsError as exc:
        raise CompareRefused(str(exc)) from None
