"""Typed failures the API layer distinguishes, because the frontend must
react differently to each.

The distinction that matters most is transient-versus-fatal: per F-12 the
gateway blocks queries during its EOD reload window, and a UI that renders
that as an error trains its users to ignore errors.
"""

from __future__ import annotations


class FrontendError(Exception):
    """Base for every failure this package raises deliberately."""

    status_code = 500
    transient = False


class ValidationFailed(FrontendError):
    """Client input did not pass the catalog whitelist or type coercion.

    Raised *before* anything is sent to q. This is the boundary that F-14
    requires: unvalidated client input never reaches query construction.
    """

    status_code = 422


class GatewayUnavailable(FrontendError):
    """The gateway could not be reached at all."""

    status_code = 503
    transient = True


class GatewayReloading(FrontendError):
    """The gateway is in its EOD reload window and is refusing queries.

    Transient by definition - see F-12. The frontend should surface this as a
    known state, not a failure.
    """

    status_code = 503
    transient = True


class QueryTimedOut(FrontendError):
    """The gateway's own per-query timeout fired.

    Per F-11, HDB-backed historical queries are expected to be slower than
    RDB-backed current-session ones, so this is an ordinary outcome for a
    wide historical range rather than a defect.
    """

    status_code = 504
    transient = True


class QueryRejected(FrontendError):
    """q evaluated the query and returned an error.

    Distinct from ValidationFailed: this one got past the whitelist, so it
    indicates either a catalog that has drifted from the real schema or a
    genuine data-side problem.
    """

    status_code = 400


class CoverageIncomplete(FrontendError):
    """A coverage pre-check failed: the requested range is not fully published.

    409 rather than 4xx-validation: the request was well-formed and the data
    simply is not there yet, which is a state the caller can retry later.
    """

    status_code = 409
