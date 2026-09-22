"""Guards on the split of `core.py` into modules.

`core.py` was 1578 lines covering seven unrelated concerns. It is now a
facade over nine focused modules, and every existing call site still reaches
it as `core.thing` — about seventy distinct names across `cli.py`,
`wizard.py`, `uqf_stack_mcp.py` and the tests.

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
    "pipeline",
    "pipelines",
    "paths",
    "env",
    "procs",
    "listing",
    "runtime",
    "logs",
    "crypto",
    "schema_view",
)

#: Files that reach this code as `core.thing`.
CONSUMERS = (
    PKG / "src" / "torq_orchestrator" / "cli.py",
    PKG / "src" / "torq_orchestrator" / "wizard.py",
    PKG / "uqf_stack_mcp.py",
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


#: Modules already past the threshold when the rule was widened to cover the
#: whole package, and the size each was at. They may SHRINK but not grow: a
#: ratchet keeps them visible and stops "already over" becoming a licence.
#:
#: Both are real splits waiting to happen - wizard.py is four recipe templates
#: beside a prompt loop, and pipeline_edges.py is a parser beside a set of
#: checks - and neither was worth bundling into the change that found them.
OVERSIZED_BY_HISTORY = {
    "wizard": 683,
    "pipeline_edges": 494,
}


def test_no_module_is_still_oversized():
    """The split exists because one file had grown to 1578 lines.

    400 is the threshold, not 1578: it is roughly the point at which a file
    stops being readable in one sitting. If one grows past it the right
    response is another split, not a larger number.

    EVERY module in the package is checked, not the eleven that came out of
    the original core.py split. That list was the reason cli.py reached 1270
    lines across twenty-four commands without anything objecting: it was
    never in it, so the rule it appeared to be under never applied to it.
    """
    package = PKG / "src" / "torq_orchestrator"
    oversized = {}
    for path in sorted(package.glob("*.py")):
        module = path.stem
        if module == "__init__":
            continue
        n = len(path.read_text().splitlines())
        cap = OVERSIZED_BY_HISTORY.get(module, 400)
        if n > cap:
            oversized[module] = f"{n} > {cap}"
    assert not oversized, f"modules past their line budget: {oversized}"


def test_the_oversized_list_has_not_become_the_rule():
    """An exemption per module would be no rule at all. Two is the number
    that existed when the check was widened; a third needs an argument, not
    an entry."""
    assert len(OVERSIZED_BY_HISTORY) <= 2, OVERSIZED_BY_HISTORY


def test_every_exempt_module_still_exists_and_is_still_over():
    """An entry for a module that has since been split, or deleted, is dead -
    and left in place it silently exempts whatever later takes that name."""
    package = PKG / "src" / "torq_orchestrator"
    for module, cap in OVERSIZED_BY_HISTORY.items():
        path = package / f"{module}.py"
        assert path.is_file(), f"{module} is exempt but does not exist"
        n = len(path.read_text().splitlines())
        assert n > 400, f"{module} is under the threshold now - remove its exemption"
        assert n <= cap, f"{module} grew: {n} > {cap}"
