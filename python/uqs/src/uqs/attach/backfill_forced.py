"""`--any-writer`: the one check a forced run keeps - completed history only (#1866).

A forced run skips the receiver, the contract, the schema and the batch journal, and
the end-of-day fence: the process at `--writer` is any q process of the stack, and
nothing of uqf's runs there. It does NOT skip the date bound, which no waiver lifts
(#1828): a row dated on or after the date that writer is filling belongs to native end
of day. The bound is asked of that writer itself - `writer_state`, the same read-only
operation a checked run asks, read by the same rule (`backfill_coordination.fence`) -
and handed to the runner as `-max_date`, where `.qetl.io.scoped_check_date` refuses a
row past it, with the message the checked path gives.
"""

from __future__ import annotations

from datetime import date, timedelta

from uqs.attach.backfill_coordination import EOD_GUARD, fence
from uqs.attach.remote import Endpoint, Ipc, IpcError
from uqs.paths import UqsError


def last_completed_date(host: str, port: int, ipc: Ipc) -> date:
    """The last date a forced run may write: the day before the writer's active date.

    @param host the --writer process's host
    @param port its port
    @param ipc the connection, with the attachment's credential
    @raise UqsError when the writer does not answer or does not say which date it is
        filling - an unknown date is never read as safe, so the run does not start
    """
    where = f"{host}:{port}"
    try:
        state = ipc.call(Endpoint(f"writer {where}", host, port), "writer_state", {})
    except IpcError as exc:
        raise UqsError(
            f"{where} does not answer writer_state ({exc.reason}): which date it is writing "
            "cannot be established, and --any-writer still keeps completed history only"
        ) from exc
    refusals: list = []
    active, _, _ = fence(state, [], EOD_GUARD, refusals)
    unknown = [b for b in refusals if b.code == "active-date-unknown"]
    if unknown or active is None:
        raise UqsError(
            unknown[0].message if unknown else f"{where} does not report which date it is writing"
        )
    return active - timedelta(days=1)
