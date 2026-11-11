"""housekeeping1's rules: this tree's own list, with tickerplant logs kept replayable.

TorQ's housekeeping process (housekeeping1) reads one CSV of rules when
housekeeping.q loads and runs each row on a daily timer: a `function` applied
to every file (or directory) under `path` that matches `match`, does not match
`exclude`, and is older than `age` days. Left alone it reads the SITE's
`appconfig/housekeeping.csv` - KDBAPPCONFIG comes first in `.proc.getconfig`
(lib/torq/torq.q) - and the starter pack's zips tickerplant logs after one day
and tars their directories (`tardir`) the same day. TorQ's replay reads a
gzipped log but never a tarball (tickerlogreplay.q's `getlogdir` and
`replayinner`), so a log still needed for replay could stop being replayable
the morning after it was written (#2155).

REPLACE, NEVER EDIT - as reporter1's checks (stack/reporter.py). This tree's
rules are an explicit list (`housekeeping_rows`); bootstrap writes them into
TORQDATA, and housekeeping1 is pointed at that copy by
scripts/processes/uqs_housekeeping_config.q, loaded BEFORE housekeeping.q
(VENDORED_LOAD_OVERLAY in stack/procs.py) - after every settings layer, so
neither TorQ's `settings/housekeeping.q` nor a site's can point it back. The
site's file is never read or written.

TICKERPLANT LOGS. Inside `TPLOG_REPLAY_RETENTION_DAYS` no rule touches one.
Past it, a whole segmented log directory (`<procname>_<YYYY.MM.DD>`, one per
day) is gzipped by `tplogzip`, never tarred: gzip is the one compressed form
TorQ's replay reads, and only when the directory's `stpmeta` is gzipped with
its logs, so the directory is the unit, not the file. `tplogzip` keeps it
replayable at every step, and leaves it alone where TorQ cannot replay a
gzipped log at all (off Linux, kdb+ before 4.0).

NO RULE DELETES ONE. A tickerplant log is the only copy of a day's data that
has not reached the HDB - after a failed end of day, or a write-down that
lost a table - and nothing here can tell those days from the rest. Deleting
is the operator's decision, made knowing which days are safely in the HDB;
gzip already takes a log to a fraction of its size.

HDB COMPRESSION (#2150). One more row runs `hdbcompress` once per run on the
HDB root: TorQ's `.cmp`, driven by the declared compression policy (#2152,
.cmp.inputcsv) through `.qtorq.compress.run_held` - the selection and the hold
`uqs data compress --apply` makes (a partition the coverage ledger says is still
being written is left alone, #2176), partition by partition under the HDB write
lock, the checked apply, then the HDB reload. Never housekeeping.q's
`kdbzip`, which hard-codes one algorithm for every file. Which partitions are old
enough is the policy's `minage`, table by table, so the row itself is not aged:
`.cmp` leaves the young ones alone. The function and what housekeeping1 loads for
it are scripts/processes/uqs_housekeeping_config.q and VENDORED_LOAD_OVERLAY.

THE SCHEDULE. When housekeeping1 runs its rules is declared here too
(`HOUSEKEEPING_RUNTIMES`), written beside the rules and read into `.hk.runtimes`
by the same overlay, so `uqs config housekeeping show` shows what runs, and when.

`scripts/generate/derive.py housekeeping-config` keeps a committed rendering
(scripts/torqconfig/housekeeping.csv and housekeeping_schedule.csv) current, so
a change to the rules is reviewed as the file housekeeping1 will read.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from pathlib import Path

from uqs.paths import UqsPaths
from uqs.stack import generated

#: housekeeping.q's columns, in its order (csvloader in
#: lib/torq/code/processes/housekeeping.q, read as "S***IBB").
HOUSEKEEPING_FIELDS = ("function", "path", "match", "exclude", "age", "agemin", "checkfordirectory")

#: The replay retention window, in days: how long a tickerplant log is kept
#: exactly as the plant wrote it. The one place it is declared - the rules
#: below, and through them housekeeping1, take it from here.
#:
#: A week covers what replays a log: an RDB recovering today's, `uqs data
#: replay` re-writing a day whose end of day failed or lost a table, noticed
#: over a weekend. `age` is find's `-mtime +N`, so a directory is touched
#: only once its last change is more than this many whole days old.
TPLOG_REPLAY_RETENTION_DAYS = 7

#: The rule function for tickerplant log directories, defined at the root by
#: scripts/processes/uqs_housekeeping_config.q (housekeeping.q runs only functions
#: in the root namespace).
TPLOG_FUNCTION = "tplogzip"

#: A segmented tickerplant's log directory for one day:
#: `<procname>_<YYYY.MM.DD>` (`.stplg.createdld` in TorQ's stplog.q).
TPLOG_DIR_MATCH = "*_[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"


def config_path(paths: UqsPaths) -> Path:
    """The generated file, in TORQDATA beside reporter.csv and process.csv."""
    return paths.torqdata / "housekeeping.csv"


#: The rule function for the HDB, defined at the root by
#: scripts/processes/uqs_housekeeping_config.q: TorQ's `.cmp` through
#: `.qtorq.compress.run_held`, by the declared compression policy.
COMPRESS_FUNCTION = "hdbcompress"

#: The HDB root's name under TORQDATA (UqsPaths.hdb_dir, TorQ's KDBHDB).
#: housekeeping's find lists a directory's entries, so the HDB is found as
#: the entry of TORQDATA with this name.
HDB_DIR_NAME = "hdb"

#: When housekeeping1 runs every rule, each day: `.hk.runtimes`, times of day
#: on the process's own clock (`.proc.cd`), TorQ's settings/housekeeping.q
#: default. The small hours: after end of day has written yesterday's
#: partition, before the desk is in.
HOUSEKEEPING_RUNTIMES: tuple[str, ...] = ("02:00:00",)

#: The schedule file's one column, as uqs_housekeeping_config.q reads it.
SCHEDULE_FIELDS = ("runtime",)

#: What housekeeping1 loads BEFORE housekeeping.q (VENDORED_LOAD_OVERLAY in
#: stack/procs.py): this tree's rules and schedule (uqs_housekeeping_config.q), then
#: what the compression rule needs - the policy's path set and TorQ's
#: common/compress.q loaded again to read it (.cmp reads it once, as the common
#: code loads, before any file named here), and the .qetl.log and .qtorq
#: reload_hdb torq_compress.q's run uses. Before, not after: housekeeping.q
#: runs its rules as it loads when given -runnow.
LOAD_BEFORE = (
    "${UQF_SCRIPTS}/processes/uqs_housekeeping_config.q",
    "${UQF_SCRIPTS}/processes/uqs_compression_config.q",
    "${KDBCODE}/common/compress.q",
    "${UQF_ROOT}/src/etl/core/log.q",
    "${UQF_SCRIPTS}/processes/torq_pipeline.q",
    "${UQF_SCRIPTS}/processes/torq_compress.q",
)


def schedule_path(paths: UqsPaths) -> Path:
    """The generated schedule, in TORQDATA beside the rules (config_path)."""
    return paths.torqdata / "housekeeping_schedule.csv"


def housekeeping_rows() -> list[dict[str, str]]:
    """This tree's rules, as the CSV's rows.

    Process logs as the starter pack keeps them: gzipped after ten days, the
    gzipped ones removed after thirty. Tickerplant log directories gzipped
    past the retention window, and nothing else done to them. The HDB brought
    to the compression policy, last: a failure there is logged and stops no
    other rule, and the logs are tidied first whatever it does.
    """
    if TPLOG_REPLAY_RETENTION_DAYS < 1:
        raise ValueError("TPLOG_REPLAY_RETENTION_DAYS must be at least 1: today's log is live")
    return [
        {"function": "zip", "path": "{KDBLOG}/", "match": "*.log", "age": "10"},
        {"function": "rm", "path": "{KDBLOG}/", "match": "*.gz", "age": "30"},
        {
            "function": TPLOG_FUNCTION,
            "path": "{KDBTPLOG}/",
            "match": TPLOG_DIR_MATCH,
            "age": str(TPLOG_REPLAY_RETENTION_DAYS),
            "checkfordirectory": "1",
        },
        # find's `-mmin +0`: the HDB root once nothing has written to it for
        # a minute - an end of day hours before the run, normally. The
        # policy ages each table's partitions; a whole-day `age` here would
        # skip a root that every end of day touches (GNU find's `-mtime +0`
        # is more than 24 hours).
        {
            "function": COMPRESS_FUNCTION,
            "path": "{TORQDATA}/",
            "match": HDB_DIR_NAME,
            "age": "0",
            "agemin": "1",
            "checkfordirectory": "1",
        },
    ]


def render_housekeeping_csv() -> str:
    """The rules as housekeeping.q reads them."""
    f = io.StringIO()
    # Plain \n, as process.csv: q's 0: reads the file.
    writer = csv.DictWriter(f, fieldnames=HOUSEKEEPING_FIELDS, restval="", lineterminator="\n")
    writer.writeheader()
    writer.writerows(housekeeping_rows())
    return f.getvalue()


def render_housekeeping_schedule() -> str:
    """The schedule as uqs_housekeeping_config.q reads it into `.hk.runtimes`."""
    if not HOUSEKEEPING_RUNTIMES:
        raise ValueError("HOUSEKEEPING_RUNTIMES is empty: housekeeping1 would never run")
    for runtime in HOUSEKEEPING_RUNTIMES:
        hh, mm, ss = (int(part) for part in runtime.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
            raise ValueError(f"housekeeping runtime {runtime!r} is not a time of day")
    return "\n".join((*SCHEDULE_FIELDS, *HOUSEKEEPING_RUNTIMES)) + "\n"


def write_housekeeping_config(paths: UqsPaths) -> None:
    """Write the generated copies housekeeping1 reads (see the module docstring)."""
    generated.write_text(config_path(paths), render_housekeeping_csv())
    generated.write_text(schedule_path(paths), render_housekeeping_schedule())


def effective_schedule(
    paths: UqsPaths, env: Mapping[str, str], localtime: bool = True
) -> list[dict[str, str]]:
    """What housekeeping1 runs, and when: `uqs config housekeeping show`'s rows.

    The generated files in TORQDATA, which housekeeping1 reads, where bootstrap
    has written them; before it has, what it will write. One row per rule, in
    the order housekeeping.q runs them, its `{VAR}` placeholders resolved
    against `env` as TorQ's .rmvr.removeenvvar resolves them. `localtime` is
    housekeeping1's process.csv column: whether its clock, and so each
    runtime, is local time or UTC.
    """
    rules = config_path(paths)
    schedule = schedule_path(paths)
    rules_text = rules.read_text() if rules.is_file() else render_housekeeping_csv()
    schedule_text = schedule.read_text() if schedule.is_file() else render_housekeeping_schedule()
    zone = "local" if localtime else "UTC"
    runs = ", ".join(f"daily {t} {zone}" for t in schedule_text.split()[1:])

    def resolved(text: str) -> str:
        return re.sub(r"\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), text)

    return [
        {
            "runs": runs,
            "function": row["function"],
            "on": "directories" if row["checkfordirectory"] == "1" else "files",
            "matching": resolved(row["path"]) + row["match"],
            "except": row["exclude"],
            "older_than": f"{row['age']} {'minutes' if row['agemin'] == '1' else 'days'}",
        }
        for row in csv.DictReader(io.StringIO(rules_text))
    ]
