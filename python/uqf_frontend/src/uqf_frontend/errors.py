"""Typed failures the API layer distinguishes, because the frontend must
react differently to each.

The distinction that matters most is transient-versus-fatal: the
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

    Raised *before* anything is sent to q. This is the security
    boundary: unvalidated client input never reaches query construction.
    """

    status_code = 422


class GatewayUnavailable(FrontendError):
    """The gateway could not be reached at all."""

    status_code = 503
    transient = True


class GatewayReloading(FrontendError):
    """The gateway is in its EOD reload window and is refusing queries.

    Transient by definition. The frontend should surface this as a
    known state, not a failure.
    """

    status_code = 503
    transient = True


class QueryTimedOut(FrontendError):
    """The gateway's own per-query timeout fired.

    HDB-backed historical queries are expected to be slower than
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


class WritesDisabled(FrontendError):
    """A control route was called while writes are switched off.

    A DISTINCT error from Forbidden, and the distinction is the useful part:
    Forbidden means "you may not", this means "nobody may, here, yet". The
    operator's next step is different in each case - check the policy, versus
    set UQF_FRONTEND_ENABLE_WRITES on the server - and a single 403 saying
    "forbidden" would send them to the wrong one.

    403 rather than 404: pretending the route does not exist would make a
    correctly-configured client look broken.
    """

    status_code = 403


class Forbidden(FrontendError):
    """An authorisation policy refused the request.

    403 rather than 401: there is no authentication to have failed, so
    "unauthenticated" would be the wrong claim. The request was understood
    and declined. See authz.py on why this layer has a seam rather than an
    auth system.
    """

    status_code = 403
