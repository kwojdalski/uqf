"""`src/etl/init.q` loads on its own, outside the test harness.

The worked examples under scripts/examples/ used to be run here too, as
well as by `scripts/test.py`'s q-scripts lane - two runners, each claiming
to be the only one, with different discovery and process handling (#891).
The lane is now their one runner. What stays here tests something else.

KDB-X specifically, not any q: this tree targets KDB-X alone, so the test
skips rather than substituting another interpreter - a pass obtained from
something the code is not verified on would be the misleading result.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]


#: Generous: the load takes well under a second, so anything approaching this
#: is a process waiting on something, and hanging CI is not a useful report.
TIMEOUT_SECONDS = 120


def _kdbx() -> tuple[str, dict[str, str]]:
    """Locate KDB-X, or skip. Deliberately does not fall back to the
    repository-root ``./q``: see the module docstring.
    """
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is not None:
        env.setdefault("QHOME", str(Path.home() / ".kx"))
        return str(q), env
    pytest.skip("no q interpreter - set $QCMD, or put q on PATH")


#: Two lines, then ask the graph a question. If the ETL tree's load order is
#: wrong this exits non-zero, and if .qetl.dag is unreachable the count is absent.
_ETL_STANDALONE = """\\l src/init.q
\\l src/etl/init.q
.qetl.dag.adopt_all[];
-1 "JOBS:",string count .qetl.dag.topological[];
"""


def test_the_etl_tree_loads_outside_the_test_harness(tmp_path: Path) -> None:
    """`src/etl/init.q` is loadable on its own, and the graph works there.

    Until `src/etl/init.q` existed, nothing loaded the ETL tree as a whole:
    each worker was loaded piecemeal and `tests/run_tests.q` held the only
    complete, correctly-ordered list in the repository. So `.qetl.dag`'s job
    graph - whose entire point is that a q PROCESS can order and draw its own
    DAG - existed only inside the test runner. A capability that works only
    under the test harness is not a capability.

    This asserts the thing that was not true before. It is a separate lane
    from `q tests/run_tests.q` on purpose: the suite loads the tree itself, so
    it cannot notice that no one else can.

    The order is load-bearing and not obvious - `coercion.q` must precede
    `source_coercion.q`, whose type table names `.qetl.coerce.to_timestamp` at LOAD
    TIME. Getting it wrong aborts the file with a bare `.qetl.coerce.to_symbol,
    which is how this was found while writing the loader.
    """
    qbin, env = _kdbx()
    script = tmp_path / "load_etl.q"
    script.write_text(_ETL_STANDALONE)
    result = subprocess.run(
        [qbin, str(script), "-q"],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
    )
    out = result.stdout.decode(errors="replace")
    err = result.stderr.decode(errors="replace")
    assert result.returncode == 0, f"the ETL tree did not load:\n{out}\n{err}"
    # Not just "it loaded": the graph has to have jobs in it. A tree that
    # loaded but registered nothing would pass an exit-code-only check.
    assert "JOBS:" in out, f"no job count reported:\n{out}"
    count = int(out.split("JOBS:")[1].split("\n")[0])
    assert count > 1, f"expected a graph with several jobs, got {count}"
