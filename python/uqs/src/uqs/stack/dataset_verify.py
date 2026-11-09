"""`uqs job check`: run `.qetl.verify.run` in a q of its own and read the report (#1851).

Everything that decides a verdict is q's - which producers fill the dataset, which
columns are keys, what the destination generates, what is refused. This module starts
a short-lived q with the ETL tree loaded (as stream_preview.py does), calls the one
function, and returns what it printed. It builds no producer list and holds no rule:
the same call works from a q prompt without it.

The q is a fresh process, never a running one, so fault injection has no production
process to reach; q refuses it as well, in any process named by TorQ.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import runs
from uqs.timerange import TimeRange

#: A dataset or column as q declares it - checked before it is spliced into q.
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
#: A scope value's text, which q types by its column (.qetl.verify.typed_scope).
_VALUE = re.compile(r"[A-Za-z0-9_.]+")
_ENDPOINT = re.compile(r"[A-Za-z0-9_.-]+:\d+")

_MARK = "UQS_VERIFY "
_TREE = ("src/init.q", "src/etl/init.q")


def parse_scope(items: list[str]) -> dict[str, str]:
    """`--scope col=value` options as a dict.

    @raise UqsError for a pair that is not col=value, a name or value q would read
    as something else, or a column given twice.
    """
    scope: dict[str, str] = {}
    for item in items:
        column, sep, value = item.partition("=")
        if not sep or not _NAME.fullmatch(column) or not _VALUE.fullmatch(value):
            raise UqsError(f"--scope {item!r}: expected column=value, e.g. sym=EURUSD")
        if column in scope:
            raise UqsError(f"--scope names {column} twice")
        scope[column] = value
    return scope


def _q_scope(scope: dict[str, str]) -> str:
    """The scope as a q dictionary of text values, which q types by each column's
    declared type: guessed here from the text, `sym=0005` was the long 5 and
    `day=2026.09.13` a symbol, and each matched no row (#2473). _VALUE keeps the
    text free of anything a q string literal would read otherwise."""
    if not scope:
        return "(`symbol$())!()"
    keys = "".join(f"`{k}" for k in scope)
    vals = [f'"{v}"' for v in scope.values()]
    return f"enlist[{keys}]!enlist {vals[0]}" if len(vals) == 1 else f"{keys}!({';'.join(vals)})"


def script(
    dataset: str, scope: dict[str, str], window: TimeRange, behaviours: bool, gateway: str | None
) -> str:
    """The q the child runs: load the tree, call `.qetl.verify.run`, print the marked report."""
    if not _NAME.fullmatch(dataset):
        raise UqsError(f"{dataset!r} is not a dataset name")
    if gateway is not None and not _ENDPOINT.fullmatch(gateway):
        raise UqsError(f"--gateway {gateway!r}: expected HOST:PORT")
    start, end = window.bounds
    reader = (
        f'.qetl.verify.getdata_reader[hopen `$":{gateway}";1D]' if gateway is not None else "(::)"
    )
    call = (
        f".qetl.verify.run[`{dataset};{_q_scope(scope)};{runs.to_q_timestamp(start)};"
        f"{runs.to_q_timestamp(end)};{reader};{'1b' if behaviours else '0b'}]"
    )
    return "\n".join(
        [
            *(f"\\l {f}" for f in _TREE),
            f"r:@[{{[x] {call}}};::;{{[e] enlist[`error]!enlist e}}];",
            f'-1 "{_MARK}",.j.j r;',
            "exit 0",
            "",
        ]
    )


def verify(
    paths: UqsPaths,
    dataset: str,
    scope: dict[str, str],
    window: TimeRange,
    *,
    behaviours: bool = True,
    gateway: str | None = None,
    timeout: float = 600,
) -> dict:
    """The report for `dataset` over `window`: `.qetl.verify.run`'s result as a dict.

    @raise UqsError when there is no q, the q did not run, or q refused - with q's own
    message (a dataset nobody fills, an interval that is empty, a process that is not
    a throwaway).
    """
    if not _NAME.fullmatch(dataset):
        raise UqsError(f"{dataset!r} is not a dataset name")
    q = q_interpreter()
    if q is None:
        raise UqsError("no q interpreter - set $QCMD, or put q on PATH; verification runs in q")
    text = script(dataset, scope, window, behaviours, gateway)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "verify.q"
        path.write_text(text)
        try:
            result = subprocess.run(
                [str(q), str(path), "-q"],
                cwd=paths.repo_root,
                env={**os.environ, "UQF_STATUS_DIR": str(runs.status_dir(paths))},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise UqsError(f"verify {dataset}: no result after {timeout:g}s") from None
    found = [line[len(_MARK) :] for line in result.stdout.splitlines() if line.startswith(_MARK)]
    if result.returncode != 0 or not found:
        raise UqsError(
            f"the verification did not run:\n{(result.stdout + result.stderr).strip()[-1500:]}"
        )
    report = json.loads(found[-1])
    if "error" in report:
        raise UqsError(f"verify {dataset}: {report['error']}")
    return report


def worst(report: dict) -> str:
    """FAIL if any category failed, else UNVERIFIED if any is, else PASS."""
    statuses = {c["status"] for c in report["checks"]}
    return "FAIL" if "FAIL" in statuses else "UNVERIFIED" if "UNVERIFIED" in statuses else "PASS"
