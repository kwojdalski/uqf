"""Guards on the split of `core.py` into modules.

`core.py` was 1578 lines covering seven unrelated concerns. It is now a
facade over nine focused modules, and every existing call site still reaches
it as `core.thing` — about seventy distinct names across `cli.py`,
`wizard.py`, `torq_demo_mcp.py` and the tests.

That facade is the whole reason the split changed no call site, so these
tests hold it in place. Without them the facade could lose a name and the
failure would surface as an `AttributeError` at runtime in whichever
front end happened to use it — the CLI, or the MCP server, neither of which
the unit tests exercise end to end.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from torq_orchestrator import core

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "python" / "torq_orchestrator"

#: Every module the facade is built from, in dependency order. `core` itself
#: is excluded: it is the facade, not a layer.
MODULES = (
    "schemas",
    "pipelines",
    "paths",
    "env",
    "procs",
    "listing",
    "runtime",
    "logs",
    "crypto",
)

#: Files that reach this code as `core.thing`.
CONSUMERS = (
    PKG / "src" / "torq_orchestrator" / "cli.py",
    PKG / "src" / "torq_orchestrator" / "wizard.py",
    PKG / "torq_demo_mcp.py",
    # This file is excluded from its own scan: its prose says `core.thing`
    # and `core.X` to describe the pattern, and the reference regex cannot
    # tell an example from a call. Scanning itself made it demand that the
    # facade export `thing` and `X`.
    *sorted(p for p in (PKG / "tests").glob("*.py") if p.name != "test_module_split.py"),
)

#: `core.py` appears in prose constantly ("see core.py's start_crypto_recorder"),
#: and the reference regex cannot tell that from an attribute. Only this one.
PROSE_FALSE_POSITIVES = frozenset({"py"})


def _referenced_names() -> set[str]:
    names: set[str] = set()
    for path in CONSUMERS:
        if not path.is_file():
            continue
        names |= set(re.findall(r"\bcore\.([A-Za-z_][\w]*)", path.read_text()))
    return names - PROSE_FALSE_POSITIVES


def test_the_consumers_are_where_this_test_expects_them():
    """A glob that matched nothing would make the whole check pass vacuously.

    This repository has hit that failure four times — a lint hook scoped to a
    stale path, a drift test skipping every case, a dormant guard, a verifier
    nobody ran. The pattern is always the same: the check stays green because
    it is checking nothing.
    """
    present = [p for p in CONSUMERS if p.is_file()]
    assert len(present) >= 4, f"expected the consumers, found {present}"
    assert len(_referenced_names()) > 50, "expected ~70 core.X references, found far fewer"


def test_every_referenced_name_resolves_on_the_facade():
    """The property that makes the split safe.

    If this fails, some call site says `core.thing` and the facade does not
    export `thing` — which is an AttributeError in the CLI or the MCP server
    at runtime, not a test failure anywhere else.
    """
    missing = sorted(n for n in _referenced_names() if not hasattr(core, n))
    assert not missing, (
        f"the facade is missing {missing}; add them to the relevant "
        f"`from torq_orchestrator.<module> import (...)` block in core.py"
    )


@pytest.mark.parametrize("module", MODULES)
def test_each_module_exists_and_imports(module):
    """Each layer must import on its own, without the facade.

    A module that only works when `core` has already been imported has a
    hidden dependency on import order, which is the kind of thing that works
    in the test suite and fails in a fresh process.
    """
    __import__(f"torq_orchestrator.{module}")


def test_no_module_is_still_oversized():
    """The split exists because one file had grown to 1578 lines.

    400 is the threshold, not 1578: it is roughly the point at which a file
    stops being readable in one sitting, and every module here came out well
    under it. If one grows past it the right response is another split, not a
    larger number.
    """
    oversized = {}
    for module in MODULES:
        path = PKG / "src" / "torq_orchestrator" / f"{module}.py"
        n = len(path.read_text().splitlines())
        if n > 400:
            oversized[module] = n
    assert not oversized, f"modules past the 400-line threshold: {oversized}"


def test_the_facade_is_only_a_facade():
    """`core.py` must not regrow logic.

    It re-exports and documents; it defines nothing. A `def` or `class` here
    means a concern has started accumulating in the facade again, which is
    how the 1578 lines happened the first time.
    """
    source = (PKG / "src" / "torq_orchestrator" / "core.py").read_text()
    definitions = re.findall(r"^(?:def|class)\s+(\w+)", source, re.M)
    assert not definitions, f"core.py should re-export only, but defines {definitions}"


def test_shutil_is_reachable_through_the_facade():
    """`test_core` patches `core.shutil.which`.

    That patches the stdlib module object, so it is a global patch and works
    through the facade exactly as it did before — but only while `core.shutil`
    resolves at all. Dropping the import would make every one of those
    monkeypatches silently target nothing, and the tests would still pass.
    """
    import shutil

    assert core.shutil is shutil
