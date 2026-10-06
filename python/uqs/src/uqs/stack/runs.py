"""Reading the run ledger back: what `uqs run` shows (#530).

Every window a bounded worker publishes is attributed to a run
(`src/etl/core/run.q`): which execution produced it, and the facts it recorded
- row counts, extremes, the source release. The ledger was written on every
window and read by nothing an operator runs, which from their side is the same
as not keeping it.

The ledger lives in the status directory, beside the coverage ledger and the
checkpoints, because bounded workers exit when their range is done. So this
does not ask a running process: it starts a short-lived q that loads the few
files the ledger needs - the same subset `tests/q/read_runs.q` proves is
sufficient - attaches to the directory, asks one question and prints JSON.
Nothing here writes to the ledger except `migrate`, the one-off that upgrades
a ledger written before etl_runs gained each run's range and counts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths

#: The files attaching to the ledger needs, in load order - read_runs.q's.
_LOADS = (
    "src/init.q",
    "src/etl/core/backfill_state.q",
    "src/etl/core/intervals.q",
    "src/etl/core/materialisation.q",
    "src/etl/core/run.q",
    "src/etl/core/status.q",
)

#: A run id as the ledger prints it: a guid.
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: Printed for the reader instead of the table itself: every integer column as
#: a float. A run that never finished has null counts, and a float null is
#: what `.j.j` writes as JSON null. `tbl`, not `t`: `meta`'s own type column
#: is `t`, and a parameter of that name would shadow it inside the where.
_FOR_JSON = '{[tbl] ints:exec c from meta tbl where t in "hij"; @[tbl;ints;"f"$]}'


def status_dir(paths: UqsPaths, env: dict[str, str] | None = None) -> Path:
    """Where the fleet's processes keep the ledger: `$UQF_STATUS_DIR`, else
    `$TORQDATA/status` - `.qetl.status.status_dir`'s own rule."""
    source = os.environ if env is None else env
    given = source.get("UQF_STATUS_DIR")
    return Path(given) if given else paths.torqdata / "status"


def to_q_timestamp(moment: datetime) -> str:
    """A datetime as the q timestamp literal for the same instant, in UTC:
    2026.09.13D00:00:00.000000000, not 2026-09-13T00:00:00Z.

    The one formatter: `uqs backfill` writes its -from/-to with it, and the
    ledger reads here splice their bounds with it. There were two, and this
    one formatted the wall time it was given - a +02:00 bound reached q two
    hours off - while backfill.py's converted to UTC first. A naive datetime
    is taken to be UTC already, everything here being UTC, rather than this
    machine's local time, which is what `astimezone` would assume.
    """
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.strftime("%Y.%m.%dD%H:%M:%S.%f000")


def query(
    paths: UqsPaths,
    expr: str,
    *,
    directory: Path | None = None,
    attach: bool = True,
    loads: tuple[str, ...] = _LOADS,
) -> list[dict]:
    """The rows q expression `expr` returns, evaluated against the ledger.

    `expr` is built by its callers' own functions from validated parts,
    never from caller text, so no argument reaches q unchecked. `loads` is
    the q files the short-lived process reads first - the ledger's subset by
    default; a reader that needs the job declarations passes the tree.
    """
    q = q_interpreter()
    if q is None:
        raise UqsError(
            "no q interpreter - set $QCMD, or put q on PATH; the run ledger is read by q"
        )
    where = directory or status_dir(paths)
    if not where.is_dir():
        raise UqsError(f"no status directory at {where} - no bounded worker has run here yet")
    # `attach` validates the ledger's shape, which is exactly what `migrate`
    # must not do first - it exists to fix a shape attach refuses.
    script = "\n".join(
        [
            *(f"\\l {f}" for f in loads),
            *([".qetl.run.attach[];"] if attach else []),
            f"-1 .j.j {_FOR_JSON} 0!{expr};",
            "exit 0",
            "",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "runs.q"
        path.write_text(script)
        result = subprocess.run(
            [str(q), str(path), "-q"],
            cwd=paths.repo_root,
            env={**os.environ, "UQF_STATUS_DIR": str(where)},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    lines = [line for line in result.stdout.splitlines() if line.startswith("[")]
    if result.returncode != 0 or not lines:
        raise UqsError(
            f"reading the run ledger failed:\n{(result.stdout + result.stderr).strip()[-1500:]}"
        )
    return json.loads(lines[-1])


def migrate(paths: UqsPaths, **kw) -> int:
    """Upgrade a ledger written before etl_runs recorded each run's range and
    counts, returning how many rows were upgraded - 0 when it was current."""
    rows = query(paths, "([] upgraded:enlist .qetl.run.migrate[])", attach=False, **kw)
    return int(rows[0]["upgraded"])


def unfinished(paths: UqsPaths, **kw) -> list[dict]:
    """Runs that began and never finished - interrupted executions included."""
    return query(paths, ".qetl.run.unfinished[]", **kw)


def history(paths: UqsPaths, **kw) -> list[dict]:
    """Every run, newest first."""
    return query(paths, ".qetl.run.history[]", **kw)


def show(paths: UqsPaths, run_id: str, **kw) -> tuple[list[dict], list[dict]]:
    """One run's row, and every fact it recorded."""
    if not _GUID.match(run_id):
        raise UqsError(
            f"{run_id!r} is not a run id - they look like 0a1b2c3d-...; see `uqs run list`"
        )
    literal = f'"G"$"{run_id}"'
    return (
        query(paths, f".qetl.run.of_run[{literal}]", **kw),
        query(paths, f".qetl.run.facts_of[{literal}]", **kw),
    )


def _as_bound(ledger_value: str) -> str:
    """A ledger timestamp as `uqs backfill --from/--to` takes it.

    The ledger's JSON spells nanoseconds - 2026-09-13T00:00:00.000000000 -
    which `datetime.fromisoformat` refuses, so the command would not run as
    printed. A zero fraction is dropped (ISO, the common case); anything finer
    becomes the q literal `parse_bound` reads, which keeps every digit.
    """
    stamp, _, fraction = ledger_value.partition(".")
    if not fraction.strip("0"):
        return stamp
    date, _, clock = stamp.partition("T")
    return f"{date.replace('-', '.')}D{clock}.{fraction}"


def rerun_command(run: dict) -> str | None:
    """The command that re-runs `run`'s range - which, because coverage skips
    what is already covered, is also how a failed or interrupted run resumes.

    None when the row lacks what the command needs (a run begun before the
    ledger recorded ranges; see `migrate`).
    """
    needed = ("worker", "range_from", "range_to", "source_version")
    if any(not run.get(k) for k in needed):
        return None
    return (
        f"uqs backfill {run['worker']} --from {_as_bound(str(run['range_from']))} "
        f"--to {_as_bound(str(run['range_to']))} --version {run['source_version']}"
    )


def log_files(paths: UqsPaths, run: dict) -> list[Path]:
    """Where the process that ran `run` logged - torq.sh's out and err files
    for its procname, the ones `uqs logs <procname>` reads. Empty when the run
    recorded no process (plain q, outside TorQ, logs to its own console)."""
    from uqs.stack.logs import _expected_log_files

    process = run.get("process")
    return _expected_log_files(paths, [str(process)]) if process else []


def audit(
    paths: UqsPaths, dataset: str, range_from: datetime, range_to: datetime, **kw
) -> list[dict]:
    """Every fact recorded about one window of `dataset`, across all runs - the
    cross-run view that says whether two materialisations of it agree."""
    if not _NAME.match(dataset):
        raise UqsError(f"{dataset!r} is not a dataset name")
    expr = (
        f".qetl.run.facts_about[`{dataset};{to_q_timestamp(range_from)};{to_q_timestamp(range_to)}]"
    )
    return query(paths, expr, **kw)
