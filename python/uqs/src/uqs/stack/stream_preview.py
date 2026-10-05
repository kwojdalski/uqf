"""`uqs stream preview`: one page of a polling feed, with nothing published (#663).

The preview runs in a short-lived q of its own, as `uqs run` reads the run
ledger: the whole ETL tree loaded, nothing wired, and the job's
`.qetl.job.stream.preview` called once. In that process the job's publish is
the unwired stub, so a publish reached by any path throws rather than lands -
and the running feed, in its own process, is never touched. The cursor is
read from the same status directory the running feed writes, and only read.
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

#: A job name as q declares it - checked before it is spliced into q.
_JOB = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

#: Marks the result line, so whatever the tree logs while loading is skipped.
_MARK = "UQS_PREVIEW "

_TREE = ("src/init.q", "src/etl/init.q")

#: `--last`: a positive whole number and a unit (#681).
_DURATION = re.compile(r"([0-9]+)(ms|s|m|h|d)")
_UNIT_NS = {"ms": 10**6, "s": 10**9, "m": 60 * 10**9, "h": 3600 * 10**9, "d": 86400 * 10**9}

#: A line .qetl.log writes outside TorQ: time|level|id|message (log.q's emit).
_LOG_LINE = re.compile(r"^[^|]+\|(TRC|DBG|INF|WARN|ERR)\|([^|]*)\|(.*)$")


def _call(job: str, sample: int, last_ns: int | None) -> str:
    """The q call the child makes: the next page, or a recent window."""
    if last_ns is None:
        return f".qetl.job.stream.preview[`{job};{int(sample)}]"
    return f".qetl.job.stream.preview_recent[`{job};{int(sample)};`timespan${int(last_ns)}]"


class PreviewFailed(UqsError):
    """A preview that did not produce a result, with what it traced before
    it stopped - so `--trace` still shows the query that failed or hung."""

    def __init__(self, message: str, trace: list[tuple[str, str, str]]):
        super().__init__(message)
        self.trace = trace


def duration_ns(raw: str) -> int:
    """`--last` as nanoseconds: `30s`, `5m`, `2h`, `1d` or `500ms`.

    @raise UqsError for anything else, and for zero - a window must cover time.
    """
    m = _DURATION.fullmatch(raw.strip())
    if not m:
        raise UqsError(
            f"--last {raw!r}: expected a whole number and a unit - ms, s, m, h or d - e.g. 30s"
        )
    ns = int(m.group(1)) * _UNIT_NS[m.group(2)]
    if ns <= 0:
        raise UqsError(f"--last {raw!r}: the window must be longer than zero")
    return ns


def trace_lines(output: str) -> list[tuple[str, str, str]]:
    """(level, id, message) for every .qetl.log line in the child's output."""
    out: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        if m := _LOG_LINE.match(line):
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def preview(
    paths: UqsPaths,
    job: str,
    sample: int = 5,
    timeout: float = 120,
    trace: bool = False,
    last_ns: int | None = None,
) -> dict:
    """What `job`'s next page would publish: `.qetl.job.stream.preview`'s result.

    With `trace`, the child runs with TRC and DBG on - every query a fetch
    sends through .qetl.source.ipc_call/ipc/local or .qetl.io.odbc.run_sql,
    before it goes and again when it returns or fails - and the result's
    `trace` holds those lines as (level, id, message). A preview that fails
    or times out raises PreviewFailed carrying them, so the query it stopped
    on is still shown.

    With `last_ns`, the preview is of the recent data instead -
    `.qetl.job.stream.preview_recent`: the rows in [now - last_ns, now),
    fetched from a temporary cursor; the saved one is not read (#681).

    @raise UqsError when there is no q, the name is not a job's, or the
    preview itself refuses or fails - with q's own message.
    """
    if not _JOB.fullmatch(job):
        raise UqsError(f"{job!r} is not a streaming job name")
    q = q_interpreter()
    if q is None:
        raise UqsError(
            "no q interpreter - set $QCMD, or put q on PATH; the preview runs the job in q"
        )
    script = "\n".join(
        [
            *(f"\\l {f}" for f in _TREE),
            *([".qetl.log.trace 1b;", ".qetl.log.debug 1b;"] if trace else []),
            f"r:@[{{[x] {_call(job, sample, last_ns)}}};::;{{[e] enlist[`error]!enlist e}}];",
            f'-1 "{_MARK}",.j.j r;',
            "exit 0",
            "",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "preview.q"
        path.write_text(script)
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
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            partial = partial.decode(errors="replace") if isinstance(partial, bytes) else partial
            raise PreviewFailed(
                f"preview {job}: no result after {timeout:g}s - a fetch that never returned",
                trace_lines(partial) if trace else [],
            ) from None
    traced = trace_lines(result.stdout) if trace else []
    found = [line[len(_MARK) :] for line in result.stdout.splitlines() if line.startswith(_MARK)]
    if result.returncode != 0 or not found:
        raise PreviewFailed(
            f"the preview did not run:\n{(result.stdout + result.stderr).strip()[-1500:]}", traced
        )
    answer = json.loads(found[-1])
    if "error" in answer:
        raise PreviewFailed(f"preview {job}: {answer['error']}", traced)
    if trace:
        answer["trace"] = traced
    return answer
