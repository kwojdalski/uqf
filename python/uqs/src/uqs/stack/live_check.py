"""`uqs config sources check`: is each named source reachable, authorised and
the shape it is declared, live (#840)?

Each source is checked by .qetl.livecheck.check (src/etl/core/live_check.q)
in a short-lived q of its own:

  - one process per source, so a driver that crashes or hangs takes down its
    own check and no other - the DuckDB driver has been seen to segfault on
    disconnect (scripts/dev/odbc_rosetta.sh);
  - under ONE overall budget: each source gets what is left of it, one that
    overruns is a `timeout` failure, and any not reached are reported as not
    checked rather than silently skipped;
  - with UQS_REQUIRE_LIVE_SOURCES=1, and the stack's own sources.csv loaded
    by the same rule TorQ uses (uqs.stack.source_settings.selected), so the
    credential checked is the one a worker would use;
  - with a private ODBC setup's variables when one is given
    (uqs.stack.odbc_home), so the driver loaded is the one the stack loads.

Nothing here publishes, moves a cursor or records coverage - nor does the q it
runs. Everything printed has been through uqs.stack.redact.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import source_settings
from uqs.stack.env import build_env
from uqs.stack.redact import redact

#: A source name as q declares it - checked before it is spliced into q.
_SOURCE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

#: The q every check loads: the library and the ETL tree, nothing of TorQ -
#: and of the tree's declarations only the source's own closure, so an
#: unrelated job whose plant table this site lacks cannot fail the check.
_LIBRARY = "src/init.q"
_ETL = "src/etl/init.q"

Runner = Callable[..., subprocess.CompletedProcess]


def _script(source: str, window_minutes: int, settings: Path | None) -> str:
    load = (
        [f'.qetl.source.load_settings[hsym `$"{settings}";`{"`".join(source_settings.PATH_VARS)}];']
        if settings
        else []
    )
    return "\n".join(
        [
            f"\\l {_LIBRARY}",
            f".qetl.load.only_sources:enlist `{source};",
            f"\\l {_ETL}",
            *load,
            f"-1 .j.j .qetl.livecheck.check[`{source};0D00:01*{window_minutes}];",
            "exit 0",
            "",
        ]
    )


def _failed(source: str, stage: str, diagnostic: str, elapsed_ms: int | None = None) -> dict:
    return {
        "source": source,
        "transport": "",
        "status": "failed",
        "stage": stage,
        "rows": None,
        "elapsed_ms": elapsed_ms,
        "diagnostic": diagnostic,
    }


def check(
    paths: UqsPaths,
    sources: Sequence[str],
    *,
    timeout: float,
    window_minutes: int = 60,
    odbc_env: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> list[dict]:
    """One result per source, in the order asked: status ok, empty or failed,
    the stage it stopped at, rows read, milliseconds and a redacted diagnostic."""
    for source in sources:
        if not _SOURCE.fullmatch(source):
            raise UqsError(f"{source!r} is not a source name")
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError("no q interpreter - set $QCMD, or put q on PATH")
    settings = source_settings.selected(paths)
    env = {
        **os.environ,
        **build_env(paths),
        **(odbc_env or {}),
        "UQS_REQUIRE_LIVE_SOURCES": "1",
    }
    deadline = clock() + timeout
    results = []
    for source in sources:
        left = deadline - clock()
        if left <= 0:
            results.append(
                _failed(source, "timeout", f"not checked: the {timeout:g}s overall timeout ran out")
            )
            continue
        results.append(_one(q, paths, source, window_minutes, settings, env, left, runner, clock))
    return results


def _one(q, paths, source, window_minutes, settings, env, left, runner, clock) -> dict:
    started = clock()
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "live_check.q"
        script.write_text(_script(source, window_minutes, settings))
        try:
            r = runner(
                [str(q), str(script), "-q"],
                cwd=paths.repo_root,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=left,
                check=False,
            )
        except subprocess.TimeoutExpired:
            ms = int((clock() - started) * 1000)
            return _failed(source, "timeout", f"no answer within {left:.1f}s", ms)
    ms = int((clock() - started) * 1000)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
    try:
        result = json.loads(lines[-1])
    except IndexError, ValueError:
        tail = "\n".join(((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-5:])
        return _failed(
            source,
            "process",
            redact(f"the check's q exited {r.returncode} without a result: {tail}"),
            ms,
        )
    result["diagnostic"] = redact(str(result.get("diagnostic") or ""))
    return result


def passed(results: list[dict]) -> bool:
    """Whether every source answered: ok, or a valid read of nothing."""
    return all(r.get("status") in ("ok", "empty") for r in results)
