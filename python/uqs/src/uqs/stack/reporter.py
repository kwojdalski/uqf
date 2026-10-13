"""reporter1's checks: this tree's own list, validated against the deployment.

TorQ's reporter process (reporter1) reads one pipe-delimited file of reports
when reporter.q loads, arms a timer per row, and sends each row's query to the
row's process. Left alone it reads the SITE's `appconfig/reporter.csv`, whose
`rdbcountcheck` names whatever tables that site's demo published - on a
deployment that publishes none of them, every period logs
"Query execution failed on remote process rdb: <table>", forever, and says
nothing about the jobs that are actually running (#1209).

REPLACE, NEVER EDIT. Unlike DQE's list (stack/dqe.py), the site's rows are not
carried over: they name tables chosen for another stack, and a row cannot be
told apart from a valid one without reading its q. This tree's checks are an
explicit list (UQF_REPORTER_CHECKS), and bootstrap writes them into TORQDATA.
reporter1 is pointed at that copy by scripts/processes/uqs_reporter_config.q,
loaded BEFORE reporter.q (VENDORED_LOAD_OVERLAY in stack/procs.py) - after
every settings layer, so a site's `settings/reporter.q` cannot take it back.
The site's file is never read or written.

EXPLICIT THRESHOLDS. A table being a declared output does not promise rows
every minute - a reaction publishes when its trigger fires, a backfill when it
runs - so no check is inferred from the outputs. Each activity check names its
table, the window it looks back over, the rows it requires there, and the days
and hours it runs.

THE PLANT'S CLOCK, NOT THE QUERIED PROCESS'S. rdb1 and hdb1 run with
-localtime 1, so `.proc.cp[]`/`.proc.cd[]` there are the host's local time,
while the plant stamps `time` - and dates partitions - in TorQ's
`.eodtime.datatimezone`/`rolltimezone` (GMT unless a site says otherwise). A
query mixing the two is off by the zone offset: on a host east of UTC an
activity check alerted on fresh data, west of it never on stale data (#1247).
So an activity check looks back from `.z.p+.eodtime.getdailyadjustment[]`,
the stamp the plant would give a row now, and an hdbdate check reads the
partition before `.eodtime.getday[.z.p]`, the plant's current date - and
tests that partition's weekday itself, since reporter1 schedules by its own
local calendar.

VALIDATED AT GENERATION, against the resolved deployment: the tables published
by the runtime's processes and installed sidecar bundles, plus the external
producers' tables it holds (`deployment_tables`) - and, when a profile start
wrote the deployment set (stack/deployment.py), only by the processes in it.
A check on a table this tree defines but this deployment does not publish - a
profile runtime without that job, a sidecar bundle not installed for this
runtime, a `uqs start --profile` that did not select its producer (#1248) -
is left out, and the reason logged. A table no part of this tree defines is refused outright: that
is a typo or a stale name, and as a row it would be exactly the recurring
query failure this module exists to remove.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass

from uqs.logger import get_logger
from uqs.model import schemas
from uqs.model.dependencies import EXTERNAL_PRODUCERS, outputs_by_process
from uqs.model.runtime_members import _bundle_split, pipeline_procnames, plant_tables
from uqs.paths import UqsError, UqsPaths
from uqs.runtimes import Runtime
from uqs.stack import deployment

log = get_logger(__name__)

#: reporter.q's columns, in its order (csvloader in
#: lib/torq/code/processes/reporter.q, read as "s**s*Ssuuun*").
REPORTER_FIELDS = (
    "name",
    "query",
    "resulthandler",
    "gateway",
    "joinfunction",
    "proctype",
    "procname",
    "start",
    "end",
    "period",
    "timeoutinterval",
    "daysofweek",
)

#: A q date `mod 7` - `.proc.cd[] mod 7`, which reporter.q matches
#: daysofweek against: 2000.01.01 was a Saturday, so 0 is Saturday and 6 is
#: Friday.
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)
WEEKDAYS = (2, 3, 4, 5, 6)

_KINDS = ("memory", "activity", "hdbdate")
_HHMM = re.compile(r"^\d\d:\d\d$")
_TIMESPAN = re.compile(r"^\d+D\d\d:\d\d(:\d\d)?$")
_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class ReporterCheck:
    """One reporter row, with every threshold spelled out.

    `memory`   - `.checks.memorycheck[max_bytes]` on `procname`.
    `activity` - `.checks.tablecount`: fewer than `min_rows` rows in each of
                 `tables` with `timecol` inside the last `window` alerts.
    `hdbdate`  - `.checks.hdbdatecheck`: the plant's previous date's partition
                 of each of `tables` is empty.

    `start`/`end`/`period` are minutes (HH:MM) - `period` 00:00 runs once at
    `start`. `alert_every` throttles the warning a firing check logs. `days`
    are the days reporter1 runs the check on - except for `hdbdate`, which
    reporter1 runs every day, and whose `days` are the weekdays of the
    partitions that must hold rows, in the plant's calendar.
    """

    name: str
    kind: str
    proctype: str
    procname: str
    start: str
    end: str
    period: str
    days: tuple[int, ...]
    tables: tuple[str, ...] = ()
    window: str = ""
    min_rows: int = 0
    timecol: str = "time"
    max_bytes: int = 0
    timeout: str = "00:00:20"
    alert_every: str = "00:10"

    def __post_init__(self) -> None:
        def bad(why: str) -> UqsError:
            return UqsError(f"reporter check {self.name!r}: {why}")

        if not _NAME.match(self.name):
            raise bad("a name is lower-case letters, digits and underscores")
        if self.kind not in _KINDS:
            raise bad(f"kind is one of {', '.join(_KINDS)}, not {self.kind!r}")
        times = {"start": self.start, "end": self.end, "period": self.period,
                 "alert_every": self.alert_every}  # fmt: skip
        for label, value in times.items():
            if not _HHMM.match(value):
                raise bad(f"{label} is HH:MM, not {value!r}")
        if not self.days or not set(self.days) <= set(EVERY_DAY):
            raise bad(f"days are .proc.cd[] mod 7 values 0-6, not {self.days!r}")
        if self.kind == "memory":
            if self.tables or self.max_bytes <= 0:
                raise bad("a memory check names no tables and a positive max_bytes")
            return
        if not self.tables:
            raise bad(f"an {self.kind} check names the tables it watches")
        if self.kind == "activity":
            # Explicit, never defaulted: how often a table must update is a
            # fact about its producer, not about its being declared.
            if not _TIMESPAN.match(self.window):
                raise bad(f"an activity check's window is a q timespan (0D00:05), "
                          f"not {self.window!r}")  # fmt: skip
            if self.min_rows <= 0:
                raise bad("an activity check requires a positive min_rows")

    @property
    def query(self) -> str:
        """The q reporter.q sends to the process, on the plant's clock (see
        the module docstring). `(),` so one table is a list too:
        hdbdatecheck indexes its table list by a `where`."""
        tables = "(),`" + "`".join(self.tables)
        if self.kind == "memory":
            return f".checks.memorycheck[{self.max_bytes}]"
        if self.kind == "activity":
            return (
                f".checks.tablecount[{tables};{self.window};"
                f".z.p+.eodtime.getdailyadjustment[];`{self.timecol};{self.min_rows}]"
            )
        days = " ".join(str(d) for d in self.days)
        # `long$ first: PeachQ's `date mod 7` is a date, not KDB-X's int.
        return (
            f"{{$[((`long$x) mod 7) in {days};.checks.hdbdatecheck[x;y];([]messages:())]}}"
            f"[.eodtime.getday[.z.p]-1;{tables}]"
        )

    def row(self) -> dict[str, str]:
        return {
            "name": self.name,
            "query": self.query,
            # scripts/processes/uqs_reporter_config.q: a warning in reporter1's
            # log, at most once per alert_every - no mail server needed.
            "resulthandler": f".rp.uqfalert[{self.alert_every}]",
            "gateway": "",
            "joinfunction": "",
            "proctype": self.proctype,
            "procname": self.procname,
            "start": self.start,
            "end": self.end,
            "period": self.period,
            "timeoutinterval": self.timeout,
            "daysofweek": " ".join(
                str(d) for d in (EVERY_DAY if self.kind == "hdbdate" else self.days)
            ),
        }


#: This tree's checks. The memory warning is the starter pack's own; the
#: activity checks name the feeds that stream continuously while running -
#: fx_orderbook on FX's trading days, crypto_book every day - and nothing a
#: job publishes only when something happens.
UQF_REPORTER_CHECKS: tuple[ReporterCheck, ...] = (
    ReporterCheck(
        name="rdbmemorycheck", kind="memory", proctype="rdb", procname="rdb1",
        max_bytes=1_500_000_000, start="00:00", end="23:59", period="00:05", days=EVERY_DAY,
    ),
    ReporterCheck(
        name="fx_orderbook_activity", kind="activity", proctype="rdb", procname="rdb1",
        tables=("fx_orderbook",), window="0D00:05", min_rows=1,
        start="00:00", end="23:59", period="00:05", days=WEEKDAYS,
    ),
    ReporterCheck(
        name="crypto_book_activity", kind="activity", proctype="rdb", procname="rdb1",
        tables=("crypto_book",), window="0D00:05", min_rows=1,
        start="00:00", end="23:59", period="00:05", days=EVERY_DAY,
    ),
    ReporterCheck(
        name="fx_orderbook_hdbdate", kind="hdbdate", proctype="hdb", procname="hdb1",
        tables=("fx_orderbook",), start="05:00", end="05:00", period="00:00",
        days=WEEKDAYS, timeout="00:01:00",
    ),
)  # fmt: skip


def deployment_tables(runtime: Runtime, selected: Iterable[str] | None = None) -> set[str]:
    """The tables `runtime` has a producer for: those its pipeline processes
    publish, those of the sidecar bundles installed for it, and the tables it
    holds that a producer outside the stack writes (EXTERNAL_PRODUCERS).
    `selected`, a deployment set (stack/deployment.py), narrows the processes
    and bundles to those in it; None is every one."""
    members = pipeline_procnames(runtime)
    chosen = None if selected is None else set(selected)
    outputs = outputs_by_process()
    published = {
        t
        for proc, tables in outputs.items()
        if (members is None or proc in members) and (chosen is None or proc in chosen)
        for t in tables
    }
    _, excluded_procs, excluded_tables, included_tables = _bundle_split(runtime, chosen)
    published -= {t for p in excluded_procs for t in outputs.get(p, ())}
    published |= included_tables
    held = plant_tables(runtime)
    held = (schemas.table_names() - excluded_tables) if held is None else held
    published |= held & set(EXTERNAL_PRODUCERS)
    return published & held


def _defines_column(table: str, column: str) -> bool:
    return re.search(rf"[\] ;]{re.escape(column)}:", schemas.definition(table)) is not None


def reporter_plan(
    runtime: Runtime,
    procnames: Iterable[str],
    checks: Iterable[ReporterCheck] = UQF_REPORTER_CHECKS,
    selected: Iterable[str] | None = None,
) -> tuple[list[ReporterCheck], list[str]]:
    """(the checks reporter1 is given, why each other one was left out).
    `selected` is the deployment set, as deployment_tables.

    Raises UqsError for a check that could never run anywhere: a table no
    part of this tree defines, or a time column the table does not have.
    """
    known = schemas.table_names()
    chosen = None if selected is None else tuple(selected)
    available = deployment_tables(runtime, chosen)
    whole = available if chosen is None else deployment_tables(runtime)
    procs = set(procnames)
    kept: list[ReporterCheck] = []
    left_out: list[str] = []
    for check in checks:
        if unknown := [t for t in check.tables if t not in known]:
            raise UqsError(
                f"reporter check {check.name!r} names {', '.join(unknown)}, which no table "
                f"definition in this tree has - fix the name in UQF_REPORTER_CHECKS "
                f"(python/uqs/src/uqs/stack/reporter.py); this {runtime.name} deployment "
                f"publishes {', '.join(sorted(available)) or 'no tables'}"
            )
        if check.kind == "activity" and (
            missing := [t for t in check.tables if not _defines_column(t, check.timecol)]
        ):
            raise UqsError(
                f"reporter check {check.name!r} reads `{check.timecol} from "
                f"{', '.join(missing)}, which has no such column"
            )
        if check.procname not in procs:
            # The site's process.csv decides which processes exist: a site
            # without this one loses the check, not every uqs command.
            left_out.append(
                f"{check.name}: queries {check.procname}, which is not a process of the "
                f"{runtime.name} stack"
            )
            continue
        if absent := [t for t in check.tables if t not in available]:
            if all(t in whole for t in absent):
                left_out.append(
                    f"{check.name}: {', '.join(absent)} - its producer is not among the "
                    f"processes this {runtime.name} deployment was started with"
                )
                continue
            left_out.append(
                f"{check.name}: {', '.join(absent)} - not published by any process or "
                f"sidecar bundle of the {runtime.name} deployment"
            )
            continue
        kept.append(check)
    return kept, left_out


def write_reporter_config(
    paths: UqsPaths, procnames: Iterable[str], selected: Iterable[str] | None = None
) -> list[ReporterCheck]:
    """Write the file reporter1 reads (see the module docstring). `selected`
    is the deployment set a start is about to record (deployment.planned);
    None is the set already recorded, if any (deployment.members)."""
    if selected is None:
        selected = deployment.members(paths)
    kept, left_out = reporter_plan(paths.runtime_declaration, procnames, selected=selected)
    for reason in left_out:
        log.info("reporter1: leaving out check {}", reason)
    with paths.generated_reporter_config.open("w", newline="") as f:
        # Plain \n, as process.csv: q's 0: would glue a \r onto daysofweek.
        writer = csv.DictWriter(
            f, fieldnames=REPORTER_FIELDS, delimiter="|", lineterminator="\n",
            quoting=csv.QUOTE_NONE, escapechar=None,
        )  # fmt: skip
        writer.writeheader()
        writer.writerows(c.row() for c in kept)
    return kept
