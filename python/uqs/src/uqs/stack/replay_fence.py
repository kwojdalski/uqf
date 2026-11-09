"""The end-of-day fence, asked of q, for `uqs data replay` (#2196).

See stack/replay.py: only a day end of day has handed over may be replayed. The
rule is the ETL's (`.qetl.io.require_handed_over`, src/etl/core/io_hdb_eod.q),
run in a q of its own so there is one implementation of it.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, repo_root

#: The q that asks the ETL's own HDB fence (src/etl/core/io_hdb_eod.q), the one
#: rule backfills write under, rather than restating it. Inputs come as
#: command-line words (.z.x: mode, hdb, wdb or `none`, dates), never in the
#: script text, so a path cannot become q.
_FENCE_Q = r"""
\l {eod}
r:hsym `$.z.x 1;
w:.z.x 2;
if[not w~"none"; .qetl.io.watch_eod[r;hsym `$w]];
d:"D"$" " vs .z.x 3;
if[(.z.x 0)~"require";
  -1 .[{.qetl.io.require_handed_over[x;`replay;y];"OK "};(r;d);{"ERR ",x}]; exit 0];
-1 "OK ",{" " sv string x}.qetl.io.handed_over[r;d];
exit 0
"""


def _ask_fence(mode: str, dates: list[str], hdb_dir: Path, wdb_dir: Path | None) -> str:
    """Run the fence in a q of its own; the text after its `OK `, or a refusal."""
    q = q_interpreter()
    if q is None:
        raise UqsError("no q interpreter to ask the end-of-day fence - set QCMD")
    eod = repo_root() / "src" / "etl" / "core" / "io_hdb_eod.q"
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "fence.q"
        script.write_text(_FENCE_Q.replace("{eod}", str(eod)))
        done = subprocess.run(
            [
                str(q),
                str(script),
                "-q",
                mode,
                f":{hdb_dir}",
                str(wdb_dir or "none"),
                " ".join(dates),
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith(("OK ", "ERR "))]
    if not lines:
        raise UqsError(
            f"the end-of-day fence did not answer: {(done.stdout + done.stderr).strip()}"
        )
    return lines[-1]


def replayable_days(dates: list[str], hdb_dir: Path, wdb_dir: Path | None) -> frozenset[str]:
    """Which of these `YYYY.MM.DD` days end of day has handed over to the HDB."""
    if not dates:
        return frozenset()
    answer = _ask_fence("handed", dates, hdb_dir, wdb_dir)
    if not answer.startswith("OK "):
        raise UqsError(f"the end-of-day fence failed: {answer}")
    return frozenset(answer[3:].split())


def require_handed_over(date: str, hdb_dir: Path, wdb_dir: Path | None) -> None:
    """Refuse a day end of day has not handed over, with the fence's own error.

    @throws UqsError naming the day, as `.qetl.io.require_handed_over` does
    """
    answer = _ask_fence("require", [date], hdb_dir, wdb_dir)
    if answer.startswith("ERR "):
        raise UqsError(answer[4:])
