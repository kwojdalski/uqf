"""When a streaming job was down: `uqs job gaps` (#630).

A streaming job's output has no coverage ledger, so a job that died left a
hole in its table that nothing recorded. `src/etl/core/uptime.q` now records
each session a job was up and subscribed, in the status directory; this
reads it back, the way `stack/runs.py` reads the run ledger - a short-lived
q, because the processes that wrote it may be long gone.

It loads the whole ETL tree rather than the ledger's few files, because the
second question - which bounded worker can refill the hole - is answered by
the job declarations (.qetl.uptime.twins).
"""

from __future__ import annotations

import re
from datetime import datetime

from uqs.paths import UqsError, UqsPaths
from uqs.stack import runs

#: A job name as q declares it - checked before it is spliced into q.
_JOB = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

_TREE = ("src/init.q", "src/etl/init.q")


def gaps(
    paths: UqsPaths, job: str, range_from: datetime, range_to: datetime
) -> tuple[list[dict], dict[str, int | None], int]:
    """(the gaps in [range_from, range_to), the job's twins, its session count).

    The twins map each worker to the `align` it declares, in nanoseconds, or
    None when it builds no bars (#1323): a worker that refuses a range off its
    grain (#1306) must be advised one on it, and the declaration is the one
    place that grain is written.

    No session at all is not an error - it is a job that never ran here, or
    ran before uptime was recorded - and the whole range is then one gap.
    """
    if not _JOB.fullmatch(job):
        raise UqsError(f"{job!r} is not a streaming job name")
    if range_from >= range_to:
        raise UqsError(
            f"the range is empty: from {range_from.isoformat()} "
            f"is not before {range_to.isoformat()}"
        )
    bounds = f"{runs.to_q_timestamp(range_from)};{runs.to_q_timestamp(range_to)}"
    attach = ".qetl.uptime.attach[];"
    holes = runs.query(
        paths, f"{{{attach} .qetl.uptime.gaps[`{job};{bounds}]}}[]", attach=False, loads=_TREE
    )
    twins = runs.query(
        paths,
        '{([] worker:x; align:{"j"$(.qetl.job.bounded.def x)`align} each x)} '
        f".qetl.uptime.twins`{job}",
        attach=False,
        loads=_TREE,
    )
    seen = runs.query(
        paths,
        f"{{{attach} ([] n:enlist count select from .qetl.uptime.sessions[] where job=`{job})}}[]",
        attach=False,
        loads=_TREE,
    )
    grains = {str(t["worker"]): t["align"] for t in twins}
    return holes, grains, int(seen[0]["n"])
