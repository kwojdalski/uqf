"""Execute the worked examples in ``scripts/*_example.q``.

B-08 asked whether examples are executed by tests. Until this module they
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

KDB-X specifically, not any q: three of these scripts say in their own
headers that they need real kdb+ because ``lib/log4q.q`` uses a
mid-expression assignment PeachQ does not support. Skipping when only the
repository's ``./q`` is present is therefore correct, and a pass under PeachQ
would be the misleading result.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

UQF_ROOT = Path(__file__).resolve().parents[3]

#: Discovered, never listed. A hardcoded parametrize list is how
#: `test_catalog_drift.py` let two tables escape checking entirely while 233
#: tests passed; `test_examples_are_discovered` below closes the other half of
#: that hole, where the glob matches nothing and every parametrized test
#: silently vanishes.
EXAMPLES = sorted((UQF_ROOT / "scripts").glob("*_example.q"))

#: Generous: the slowest of these is ~0.1s, so anything approaching this is a
#: script that has started waiting on something - a timer that never fires, a
#: prompt, a connection - and hanging CI is not a useful way to report that.
TIMEOUT_SECONDS = 120


def _kdbx() -> tuple[str, dict[str, str]]:
    """Locate KDB-X, or skip. Deliberately does not fall back to the
    repository-root ``./q``: see the module docstring.
    """
    env = os.environ.copy()
    override = env.get("UQFQ")
    if override and Path(override).is_file():
        return override, env
    kdbx = Path.home() / ".kx" / "bin" / "q"
    if kdbx.is_file():
        env.setdefault("QHOME", str(Path.home() / ".kx"))
        return str(kdbx), env
    pytest.skip("no KDB-X interpreter (~/.kx/bin/q or $UQFQ); PeachQ cannot run log4q")


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
