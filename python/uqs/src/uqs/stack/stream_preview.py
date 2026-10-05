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


def preview(paths: UqsPaths, job: str, sample: int = 5, timeout: float = 120) -> dict:
    """What `job`'s next page would publish: `.qetl.job.stream.preview`'s result.

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
            f"r:@[{{[x] .qetl.job.stream.preview[`{job};{int(sample)}]}};::;"
            "{[e] enlist[`error]!enlist e}];",
            f'-1 "{_MARK}",.j.j r;',
            "exit 0",
            "",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "preview.q"
        path.write_text(script)
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
    found = [line[len(_MARK) :] for line in result.stdout.splitlines() if line.startswith(_MARK)]
    if result.returncode != 0 or not found:
        raise UqsError(
            f"the preview did not run:\n{(result.stdout + result.stderr).strip()[-1500:]}"
        )
    answer = json.loads(found[-1])
    if "error" in answer:
        raise UqsError(f"preview {job}: {answer['error']}")
    return answer
