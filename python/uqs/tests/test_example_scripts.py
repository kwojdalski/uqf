"""Execute the worked examples in ``scripts/*_example.q``.

It was asked whether examples are executed by tests. Until this module they
were not: five scripts, 1,100 lines, referenced from ``README.md`` and run by
nothing. They are the same kind of untested assertion as the ``@eg`` lines in
the qDoc blocks - prose that claims a call works, with no gate to notice when
it stops.

The gate is worth having because it is nearly free (all five load in about a
tenth of a second each) and because the rot it catches is silent: rename a
function in ``src/pricing/forwards.q`` and the test suite stays green while
every example that calls it breaks. Nobody discovers that until a reader
tries to run one.

``q script.q < /dev/null`` was verified to exit 1 on a load error, and to
stop at the failing line rather than continuing - so the exit code really is
load success and not merely "q started". That check matters: the alternative
would have been a gate that passes on a broken script, which is worse than
no gate at all.

KDB-X specifically, not any q: this tree targets KDB-X alone, so the gate skips
rather than substituting another interpreter: a pass obtained from something
the code is not verified on would be the misleading result.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.paths import q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]

#: Discovered, never listed. A hardcoded parametrize list is how
#: `test_catalog_drift.py` let two tables escape checking entirely while 233
#: tests passed; `test_examples_are_discovered` below closes the other half of
#: that hole, where the glob matches nothing and every parametrized test
#: silently vanishes.
EXAMPLES = sorted((UQF_ROOT / "scripts" / "examples").glob("*_example.q"))

#: Generous: the slowest of these is ~0.1s, so anything approaching this is a
#: script that has started waiting on something - a timer that never fires, a
#: prompt, a connection - and hanging CI is not a useful way to report that.
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


def test_examples_are_discovered() -> None:
    """The glob found scripts at all.

    Without this, deleting or renaming every example would empty the
    parametrized test below and the suite would report success for running
    nothing - the failure mode that makes a discovered list only half a fix.
    """
    assert EXAMPLES, "no scripts/*_example.q found; has the naming convention changed?"


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_example_script_runs_clean(script: Path) -> None:
    """The example loads end to end without throwing."""
    qbin, env = _kdbx()
    result = subprocess.run(
        [qbin, str(script.relative_to(UQF_ROOT)), "-q"],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        # q prints the failing line and a caret to stdout, which is the part
        # a reader needs; stderr is usually empty. Show the tail of both.
        out = result.stdout.decode(errors="replace").splitlines()[-25:]
        err = result.stderr.decode(errors="replace").splitlines()[-25:]
        pytest.fail(
            f"{script.name} exited {result.returncode}\n"
            + "\n".join(out)
            + ("\n--- stderr ---\n" + "\n".join(err) if err else "")
        )


#: Two lines, then ask the graph a question. If the ETL tree's load order is
#: wrong this exits non-zero, and if .qdag is unreachable the count is absent.
_ETL_STANDALONE = """\\l src/init.q
\\l src/etl/init.q
.qdag.adopt_all[];
-1 "JOBS:",string count .qdag.topological[];
"""


def test_the_etl_tree_loads_outside_the_test_harness(tmp_path: Path) -> None:
    """`src/etl/init.q` is loadable on its own, and the graph works there.

    Until `src/etl/init.q` existed, nothing loaded the ETL tree as a whole:
    each worker was loaded piecemeal and `tests/run_tests.q` held the only
    complete, correctly-ordered list in the repository. So `.qdag`'s job
    graph - whose entire point is that a q PROCESS can order and draw its own
    DAG - existed only inside the test runner. A capability that works only
    under the test harness is not a capability.

    This asserts the thing that was not true before. It is a separate lane
    from `q tests/run_tests.q` on purpose: the suite loads the tree itself, so
    it cannot notice that no one else can.

    The order is load-bearing and not obvious - `coercion.q` must precede
    `source_contract.q`, whose type table names `.qcoer.to_timestamp` at LOAD
    TIME. Getting it wrong aborts the file with a bare `.qcoer.to_symbol,
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
