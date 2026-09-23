"""Does the running stack actually work? The checks CI cannot make.

Every bug found in this tree's ETL during the session that produced this
file was in the WIRING, and every one of them passed the unit suites:

  - a job whose `on_batch` threw on every real batch, because a real batch
    carries the tickerplant's `time` column and no test had ever handed it
    one;
  - a timer target that was the RESULT of calling a lambda rather than the
    lambda, so the work happened once at wiring time and never again;
  - a process publishing onto a table the plant had never been told about.

None of them is detectable from a test that calls the job's functions
directly, because in each case the functions were correct. And none of them
was visible from outside either: TorQ traps a handler error into the
process's own stderr log, so the process stays up, heartbeats, and reports
healthy while doing nothing.

That leaves exactly two observations worth making against a live stack, and
between them they would have caught all three:

  1. every table a RUNNING pipeline declares it publishes has rows;
  2. no running process's error log grew while we watched.

This module is the pure half - what to expect and how to read it. The lane
that starts the stack and waits is `scripts/test.py`'s `stack-smoke`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from uqf_stack.logger import get_logger
from uqf_stack.model.plant_schema import _publishers
from uqf_stack.model.registry import PIPELINES

log = get_logger(__name__)

#: Tables a running pipeline publishes that may legitimately still be empty,
#: and why. An entry here is a claim that emptiness carries no information,
#: so it needs a reason that is about the DATA rather than about the test
#: being inconvenient.
#:
#: Held closed in both directions by test_stack_smoke.py: an entry naming a
#: table nothing publishes is dead and gets removed, because left in place
#: it would silently excuse whatever later takes that name.
MAY_BE_EMPTY: dict[str, str] = {
    "fx_limit_breach": (
        "published only when a position crosses a declared limit, which a "
        "short run on synthetic order flow need not produce"
    ),
    "config_change": (
        "published only by a process whose job declares watched configuration, "
        "and both of those are on-demand"
    ),
}


class FindingKind(StrEnum):
    """What kind of thing the smoke check found.

    A StrEnum rather than a plain Enum because these values are printed in
    `__str__` and compared against in tests - a bare Enum would render as
    `FindingKind.EMPTY_TABLE` and change every line of the report.
    """

    #: A table a running process declares it publishes, with no rows in it.
    #: The process is up and publishing nothing, which is what a trapped
    #: handler error looks like from outside.
    EMPTY_TABLE = "empty-table"
    #: A process that wrote to its error log while the check watched.
    PROCESS_ERRORS = "process-errors"
    #: A partition missing a table the generated database.q declares. Not a
    #: missing column somewhere - one absent directory fails EVERY
    #: cross-table query against the history, and kdb+ names whichever
    #: table sorts first rather than the partition that is short (#348).
    HDB_NOT_RECTANGULAR = "hdb-not-rectangular"


@dataclass(frozen=True)
class SmokeFinding:
    """One thing wrong with the running stack."""

    kind: FindingKind
    subject: str  # the table or process name
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.subject} - {self.detail}"


def expected_tables(running: set[str]) -> dict[str, set[str]]:
    """{table: the running processes that publish it}, for tables that should
    have rows.

    Derived from the registry rather than listed, so a new pipeline is
    covered by this check the day it is declared - which is the property
    that makes the check worth having, since the bugs it catches are in
    processes nobody thought to look at.
    """
    by_table: dict[str, set[str]] = {}
    for table, publishers in _publishers(PIPELINES).items():
        if table in MAY_BE_EMPTY:
            continue
        live = publishers & running
        if live:
            by_table[table] = live
    return by_table


def error_log_path(logs_dir: Path, procname: str) -> Path:
    """Where TorQ keeps a process's trapped-error log.

    The alias, not the dated file: `err_<proc>.log` is a symlink TorQ
    repoints on each roll, so reading it follows the current one.
    """
    return logs_dir / f"err_{procname}.log"


def error_log_sizes(logs_dir: Path, procnames: set[str]) -> dict[str, int]:
    """{procname: lines in its error log}, missing files counted as zero.

    Lines rather than bytes: a partially written line would make a byte
    count wobble on its own, and the thing being counted is errors.
    """
    sizes: dict[str, int] = {}
    for procname in sorted(procnames):
        path = error_log_path(logs_dir, procname)
        try:
            sizes[procname] = len(path.read_text().splitlines())
        except OSError:
            sizes[procname] = 0
    return sizes


def new_error_lines(
    logs_dir: Path, before: dict[str, int], procnames: set[str]
) -> dict[str, list[str]]:
    """{procname: the error lines it wrote since `before`}.

    A DIFFERENCE, not a total: a stack that has been up for a while has old
    errors in its logs, and failing on those would make this check useless
    on any machine that had ever run anything. What it asserts is that
    nothing went wrong *while we were watching*.
    """
    fresh: dict[str, list[str]] = {}
    for procname in sorted(procnames):
        path = error_log_path(logs_dir, procname)
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        added = lines[before.get(procname, 0) :]
        if added:
            fresh[procname] = added
    return fresh


def findings(
    row_counts: dict[str, int],
    expected: dict[str, set[str]],
    fresh_errors: dict[str, list[str]],
) -> list[SmokeFinding]:
    """Everything wrong, as a list - empty means the stack is working.

    Both checks in one place so a run reports all of it at once. A smoke
    test that stops at the first problem makes you run it again to find the
    second, and these take minutes.
    """
    out: list[SmokeFinding] = []
    for table, publishers in sorted(expected.items()):
        if row_counts.get(table, 0) <= 0:
            out.append(
                SmokeFinding(
                    FindingKind.EMPTY_TABLE,
                    table,
                    f"{', '.join(sorted(publishers))} declares it and it has no rows - "
                    "the process is up and publishing nothing, which is what a "
                    "trapped handler error looks like from outside",
                )
            )
    for procname, lines in sorted(fresh_errors.items()):
        first = lines[0][:160]
        out.append(
            SmokeFinding(
                FindingKind.PROCESS_ERRORS,
                procname,
                f"wrote {len(lines)} line(s) to its error log while we watched, starting: {first}",
            )
        )
    return out
