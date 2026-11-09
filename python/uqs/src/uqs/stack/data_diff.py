"""`uqs data diff`: run `.qetl.compare.run` in a q of its own and read the report (#2033).

Everything that decides what a comparison says is q's (src/etl/core/compare.q):
how each side is read, how a filter's text becomes a typed value, the row key,
what differs, the five outcomes and the label a truncated sample carries. This
module writes the one call, starts a short-lived q with the ETL tree loaded (as
dataset_verify.py does), and returns what it printed. The q opens each side
itself, at the address the command hands it - this machine's gateway, or the
local end of an ssh tunnel to a target's - so no row passes through Python
before it is compared.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import runs
from uqs.timerange import TimeRange

#: The outcomes, in the order q decides them (.qetl.compare.outcomes).
OUTCOMES = (
    "endpoint_unavailable",
    "missing_table",
    "differs",
    "not_comparable",
    "incomplete_coverage",
    "equal_in_sample",
)
#: The one outcome that is not a finding.
EQUAL = "equal_in_sample"

#: How many rows each side is read to when no --limit is given.
DEFAULT_LIMIT = 1000
#: The most a side may be read to: a comparison is a sample, not an export.
MAX_LIMIT = 100_000

#: Seconds the comparison's q may run before it is killed, from the command
#: line; the frontend passes its own request budget (#2529).
DEFAULT_TIMEOUT = 600

#: The endpoint naming this machine's own stack, beside the deploy targets.
LOCAL = "local"

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_SYMBOL = re.compile(r"[A-Za-z0-9_.:-]+")
_MARK = "UQS_DIFF "
#: One table's verdict per line: the verdicts collapse to a table whose cells
#: are dictionaries and tables, which PeachQ's .j.j does not serialise ('nyi),
#: while each verdict on its own does.
_TABLE_MARK = "UQS_DIFF_TABLE "
_TREE = ("src/init.q", "src/etl/init.q")


class DiffNotRun(UqsError):
    """The comparison never produced a report: no q interpreter, or the q
    run failed. A fault of this machine, not of the request (#2529)."""


class DiffTimedOut(UqsError):
    """The comparison's q ran past its timeout and was killed (#2529)."""


@dataclass(frozen=True)
class Endpoint:
    """One side, as the q will reach it: an address, or why there is none."""

    name: str
    address: str = ""
    error: str = ""

    def q(self) -> str:
        parts = ";".join(_q_text(v) for v in (self.name, self.address, self.error))
        return f"`name`address`error!({parts})"


def _q_text(text: str) -> str:
    """`text` as a q string literal - one character is enlisted, as q needs."""
    body = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'enlist "{body}"' if len(text) == 1 else f'"{body}"'


def _q_symbol(value: str | None, what: str) -> str:
    if value is None or value == "":
        return "`"
    if not _SYMBOL.fullmatch(value):
        raise UqsError(f"{what} {value!r}: expected letters, digits, _ . : or -")
    return f'`$"{value}"'


def _q_strings(items: list[str]) -> str:
    if not items:
        return "()"
    texts = [_q_text(i) for i in items]
    return f"enlist {texts[0]}" if len(texts) == 1 else f"({';'.join(texts)})"


def script(
    left: Endpoint,
    right: Endpoint,
    tables: list[str],
    window: TimeRange,
    filters: list[str],
    limit: int,
    source_version: str | None,
    partition: str | None,
    login_file: str = "login",
) -> str:
    """The q the child runs: load the tree, call `.qetl.compare.run`, print the marked report."""
    bad = [t for t in tables if not _NAME.fullmatch(t)]
    if bad or not tables:
        raise UqsError(f"{bad[0]!r} is not a table name" if bad else "name at least one --table")
    if not 0 < limit <= MAX_LIMIT:
        raise UqsError(f"--limit {limit}: expected 1 to {MAX_LIMIT} rows per side")
    start, end = window.bounds
    req = (
        "`range_from`range_to`filters`limit`source_version`partition`login!("
        f"{runs.to_q_timestamp(start)};{runs.to_q_timestamp(end)};{_q_strings(filters)};{limit}j;"
        f"{_q_symbol(source_version, '--source-version')};{_q_symbol(partition, '--slice')};"
        f'first read0 hsym `$"{login_file}")'
    )
    tabs = "(),`" + "`".join(tables)
    call = f".qetl.compare.run[{left.q()};{right.q()};{tabs};{req}]"
    return "\n".join(
        [
            *(f"\\l {f}" for f in _TREE),
            f"r:@[{{[x] {call}}};::;{{[e] enlist[`error]!enlist e}}];",
            f'{{[r] -1 "{_MARK}",.j.j `tables _ r; '
            f'{{-1 "{_TABLE_MARK}",.j.j x}} each r`tables}} r;',
            "exit 0",
            "",
        ]
    )


def compare(
    paths: UqsPaths,
    left: Endpoint,
    right: Endpoint,
    tables: list[str],
    window: TimeRange,
    *,
    filters: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    source_version: str | None = None,
    partition: str | None = None,
    login: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """The comparison of `tables` on two endpoints: `.qetl.compare.run`'s report.

    @raise DiffNotRun when there is no q or the q did not run; DiffTimedOut
    when it ran past `timeout`; UqsError when q refused the request itself - a
    filter that is not COLUMN OP VALUE, an empty window. An endpoint that
    cannot be reached is not a refusal: it is an outcome.
    """
    q = q_interpreter()
    if q is None:
        raise DiffNotRun("no q interpreter - set $QCMD, or put q on PATH; the comparison runs in q")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "diff.q"
        # the login in a file only this user reads, beside the script: never on
        # the child's command line, in its script or in its environment
        secret = Path(tmp) / "login"
        secret.touch(mode=0o600)
        secret.write_text(login)
        path.write_text(
            script(left, right, tables, window, filters or [], limit, source_version, partition,
                   str(secret))
        )  # fmt: skip
        try:
            result = subprocess.run(
                [str(q), str(path), "-q"],
                cwd=paths.repo_root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise DiffTimedOut(f"data diff: no result after {timeout:g}s") from None
    found = [line[len(_MARK) :] for line in result.stdout.splitlines() if line.startswith(_MARK)]
    if result.returncode != 0 or not found:
        raise DiffNotRun(
            f"the comparison did not run:\n{(result.stdout + result.stderr).strip()[-1500:]}"
        )
    report = json.loads(found[-1])
    marked = len(_TABLE_MARK)
    if "error" not in report:
        report["tables"] = [
            json.loads(line[marked:])
            for line in result.stdout.splitlines()
            if line.startswith(_TABLE_MARK)
        ]
    if "error" in report:
        raise UqsError(f"data diff: {report['error']}")
    return report
