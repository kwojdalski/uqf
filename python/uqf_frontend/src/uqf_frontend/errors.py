"""Typed failures the API layer distinguishes, because the frontend must
react differently to each.

The distinction that matters most is transient-versus-fatal: the
gateway blocks queries during its EOD reload window, and a UI that renders
that as an error trains its users to ignore errors.

Every status a deliberate failure answers, one row per class, whichever module
defines it. test_error_shape.py holds every FrontendError subclass to this
table, and to its three rules (#2224, #2225): 422 for any refusal of what the
caller sent; 404 for a named thing that does not exist, path segment or not;
400 only for QueryRejected, where q failed on input that passed validation.

    QueryRejected            400  q failed on a query that passed validation
    WriteTokenRequired       401  the write token is missing or wrong
    Forbidden                403  a policy refused the caller
    InspectionMode           403  inspection mode refuses writes
    WritesDisabled           403  writes are off on this server
    ActionNotFound           404  no such lifecycle action
    ChartNotFound            404  no such prepared chart
    PathNotFound             404  no such path or push below a target
    ProcessNotFound          404  no such process in this runtime
    TargetNotFound           404  no such deployment target
    CoverageIncomplete       409  the range is not fully published yet
    RestoreConflict          409  a file changed since the push
    SettingsConflict         409  the settings changed since they were read
    StackBusy                409  another lifecycle action holds the lock
    TargetNotScoped          409  the target is not a workspace
    TargetUnusable           409  the target is declared but cannot be used
    UploadExists             409  the file exists and overwrite was not asked
    HistoryPruned            410  retention removed the push's history
    UploadTooLarge           413  the file is over the upload limit
    BrowseRefused            422  the path is refused (outside, a link)
    CompareRefused           422  uqs or q refused the comparison as asked
    TunnelPlanRefused        422  the tunnel choices cannot make a plan
    UploadRefused            422  the destination may not be written
    ValidationFailed         422  the input failed validation
    TooManyRequests          429  over the unauthenticated event rate
    BackfillNotStarted       502  torq.sh did not start the backfill
    CompareFailed            502  the comparison's q did not run
    RemoteFailed             502  ssh or the remote helper failed
    TargetUnavailable        502  the target's gateway could not be reached
    TunnelDiscoveryFailed    502  the target's processes could not be read
    UploadIncomplete         502  the bytes did not arrive whole
    GatewayReloading         503  the gateway is in its EOD reload window
    GatewayUnavailable       503  the gateway could not be reached
    CompareTimedOut          504  the comparison's q ran past its budget
    DeleteOutcomeUncertain   504  a deletion may have happened in part
    QueryTimedOut            504  the gateway's query timeout fired
    RemoteTimedOut           504  a remote read timed out
    RestoreOutcomeUncertain  504  a restore may have happened in part
    UploadOutcomeUncertain   504  an upload may or may not have happened
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


class StackBusy(FrontendError):
    """Another start, stop or restart holds the stack's lifecycle lock (#2028).

    409, and refused rather than queued: a second Start waiting behind the
    first would run against whatever the first left, long after the operator
    who clicked it has looked away.
    """

    status_code = 409


class BackfillNotStarted(FrontendError):
    """TorQ's launcher (torq.sh start) did not start the backfill process.

    502, not 422: the request was valid, the thing behind this API failed.
    The message names the process, so `uqs logs <procname>` finds why.
    """

    status_code = 502


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


class WriteTokenRequired(FrontendError):
    """A request that needs the write token came without it, or with the
    wrong one. Raised by a handler that learns only from the body that the
    token is needed - /compare naming a target (#2066); a request whose path
    says so is refused by write_guard's middleware under the same name."""

    status_code = 401


class Forbidden(FrontendError):
    """An authorisation policy refused the request.

    403 rather than 401: there is no authentication to have failed, so
    "unauthenticated" would be the wrong claim. The request was understood
    and declined. See authz.py on why this layer has a seam rather than an
    auth system.
    """

    status_code = 403


class InspectionMode(Forbidden):
    """A write refused because inspection mode is on (inspection.py, #2032):
    for the whole frontend, or for the target the request would change.
    A type of its own, so the browser and the audit can tell "inspection
    mode" from "not granted to you"."""


class TooManyRequests(FrontendError):
    """More unauthenticated browser events than activity's rate allows (#2019).
    Transient: the same batch is accepted once the rate allows it again."""

    status_code = 429
    transient = True
