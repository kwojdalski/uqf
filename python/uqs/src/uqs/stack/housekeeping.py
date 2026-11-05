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
scripts/processes/uqs_housekeeping.q, loaded BEFORE housekeeping.q
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

#2150 extends this file: the full generated rule list, and compression
scheduled through TorQ's `.cmp`, are rows appended to `housekeeping_rows`.
"""

from __future__ import annotations

import csv
import io
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
#: scripts/processes/uqs_housekeeping.q (housekeeping.q runs only functions
#: in the root namespace).
TPLOG_FUNCTION = "tplogzip"

#: A segmented tickerplant's log directory for one day:
#: `<procname>_<YYYY.MM.DD>` (`.stplg.createdld` in TorQ's stplog.q).
TPLOG_DIR_MATCH = "*_[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"


def config_path(paths: UqsPaths) -> Path:
    """The generated file, in TORQDATA beside reporter.csv and process.csv."""
    return paths.torqdata / "housekeeping.csv"


def housekeeping_rows() -> list[dict[str, str]]:
    """This tree's rules, as the CSV's rows.

    Process logs as the starter pack keeps them: gzipped after ten days, the
    gzipped ones removed after thirty. Tickerplant log directories gzipped
    past the retention window, and nothing else done to them.
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
    ]


def write_housekeeping_config(paths: UqsPaths) -> None:
    """Write the generated copy housekeeping1 reads (see the module docstring)."""
    f = io.StringIO()
    # Plain \n, as process.csv: q's 0: reads the file.
    writer = csv.DictWriter(f, fieldnames=HOUSEKEEPING_FIELDS, restval="", lineterminator="\n")
    writer.writeheader()
    writer.writerows(housekeeping_rows())
    generated.write_text(config_path(paths), f.getvalue())
