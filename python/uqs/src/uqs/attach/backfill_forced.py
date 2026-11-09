"""`--override any-writer`: the one check a forced run keeps - completed history only (#1866).

A forced run skips the receiver, the contract, the schema and the batch journal, and
the end-of-day fence: the process at `--writer` is any q process of the stack, and
nothing of uqf's runs there. It does NOT skip the date bound, which no waiver lifts
(#1828): a row dated on or after the date that writer is filling belongs to native end
of day. The bound is asked of that writer itself - `writer_state`, the same read-only
operation a checked run asks, read by the same rule (`backfill_fence.fence`) -
and handed to the runner as `-max_date`, where `.qetl.io.scoped_check_date` refuses a
row past it, with the message the checked path gives.
"""

from __future__ import annotations

from datetime import date, timedelta

from uqs.attach import writer_calendar
from uqs.attach.backfill_coordination import EOD_GUARD
from uqs.attach.backfill_fence import fence
from uqs.attach.remote import Endpoint, Ipc, IpcError
from uqs.guardrails import Guardrail
from uqs.paths import UqsError

#: `job backfill --writer HOST:PORT --override any-writer` (a bare --any-writer
#: until #1852): write through any process of the stack, nothing checked but this.
ANY_WRITER = "any-writer"
GUARDRAILS = (
    Guardrail(
        ANY_WRITER,
        ("job backfill",),
        "with --writer: append through ANY q process of the stack (e.g. its wdb), into that "
        "process's own HDB over plain IPC, instead of a receiver - no contract, schema, "
        "verification, end-of-day fence or retry protection; completed history only",
        risk="a re-run or a retry duplicates rows, and no reader is told to reload",
        action="prefer `uqs job receiver start` on the stack's machine",
    ),
)


def completed_bounds(host: str, port: int, ipc: Ipc) -> tuple[date, str]:
    """The last date a forced run may write, and the calendar it files rows by.

    The date is the day before the writer's active date, in the writer's own calendar
    (#2193): the runner partitions by the same calendar, so a row's date is compared with it
    as the stack names it. The calendar is `-calendar`'s text, '' when the stack files by UTC
    midnight.

    @param host the --writer process's host
    @param port its port
    @param ipc the connection, with the attachment's credential
    @raise UqsError when the writer does not answer or does not say which date it is
        filling - an unknown date is never read as safe, so the run does not start - or
        stamps `time` in a data zone this run does not stamp
    """
    where = f"{host}:{port}"
    try:
        state = ipc.call(Endpoint(f"writer {where}", host, port), "writer_state", {})
    except IpcError as exc:
        raise UqsError(
            f"{where} does not answer writer_state ({exc.reason}): which date it is writing "
            "cannot be established, and --override any-writer still keeps completed history only"
        ) from exc
    refusals: list = []
    active, _, _ = fence(state, [], EOD_GUARD, refusals)
    unknown = [b for b in refusals if b.code in ("active-date-unknown", "native-calendar")]
    if unknown or active is None:
        raise UqsError(
            unknown[0].message if unknown else f"{where} does not report which date it is writing"
        )
    calendar = writer_calendar.of(state) or writer_calendar.Calendar()
    return active - timedelta(days=1), calendar.runner_text
