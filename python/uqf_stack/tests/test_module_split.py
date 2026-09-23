"""Guards on the split of `core.py` into modules.

`core.py` was 1578 lines covering seven unrelated concerns. It is now a
facade over nine focused modules, and every existing call site still reaches
it as `core.thing` — about seventy distinct names across `cli/entry.py`,
`scaffold/wizard.py`, `uqf_stack_mcp.py` and the tests.

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

from uqf_stack import core

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "python" / "uqf_stack"

#: Every module the facade is built from, in dependency order. `core` itself
#: is excluded: it is the facade, not a layer. Dotted, because the package is
#: foldered: `model/` is what the stack declares, `stack/` is the running
#: fleet, `checks/` reads it and `external/` is the processes we start but do
#: not own.
MODULES = (
    "model.schemas",
    "model.pipeline",
    "model.pipelines",
    "paths",
    "stack.env",
    "stack.procs",
    "stack.listing",
    "stack.runtime",
    "stack.logs",
    "external.crypto",
    "checks.schema_view",
)

#: Files that reach this code as `core.thing`.
CONSUMERS = (
    PKG / "src" / "uqf_stack" / "cli" / "main.py",
    PKG / "src" / "uqf_stack" / "scaffold" / "wizard.py",
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
        f"`from uqf_stack.<module> import (...)` block in core.py"
    )


@pytest.mark.parametrize("module", MODULES)
def test_each_module_exists_and_imports(module):
    """Each layer must import on its own, without the facade.

    A module that only works when `core` has already been imported has a
    hidden dependency on import order, which is the kind of thing that works
    in the test suite and fails in a fresh process.
    """
    __import__(f"uqf_stack.{module}")


#: Modules already past the threshold when the rule was widened to cover the
#: whole package, and the size each was at. They may SHRINK but not grow: a
#: ratchet keeps them visible and stops "already over" becoming a licence.
#:
#: Both are real splits waiting to happen - scaffold/wizard.py is four recipe templates
#: beside a prompt loop, and model/pipeline_edges.py is a parser beside a set of
#: checks - and neither was worth bundling into the change that found them.
OVERSIZED_BY_HISTORY = {
    "scaffold.wizard": 683,
    "model.pipeline_edges": 494,
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

    `rglob`, not `glob`, and that distinction is the whole rule now the
    package is foldered: a flat glob would go on passing while checking only
    the two modules left at the top level. Same failure as the eleven-module
    list, one directory deeper.
    """
    package = PKG / "src" / "uqf_stack"
    oversized = {}
    for path in sorted(package.rglob("*.py")):
        module = str(path.relative_to(package).with_suffix("")).replace("/", ".")
        if path.name == "__init__.py":
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
    package = PKG / "src" / "uqf_stack"
    for module, cap in OVERSIZED_BY_HISTORY.items():
        path = package / (module.replace(".", "/") + ".py")
        assert path.is_file(), f"{module} is exempt but does not exist"
        n = len(path.read_text().splitlines())
        assert n > 400, f"{module} is under the threshold now - remove its exemption"
        assert n <= cap, f"{module} grew: {n} > {cap}"


#: What each folder is allowed to import from, and nothing else. The order is
#: the layering: `model/` is what the stack declares, `stack/` is the fleet
#: that runs, `external/` and `checks/` act on a running fleet, `core` is the
#: facade over all of it, and `cli/` and `scaffold/` are front ends.
#:
#: `paths` and `logger` are omitted from every list because everything may
#: import them: they are leaves that import nothing from this package, so they
#: cannot take part in a cycle.
ALLOWED_IMPORTS = {
    "model": set(),
    "stack": {"model"},
    "external": {"model", "stack"},
    "checks": {"model", "stack"},
    "scaffold": {"core"},
    "cli": {"core", "model", "scaffold", "external", "checks"},
}

_IMPORT = re.compile(r"^\s*(?:from|import) uqf_stack(?:\.(\w+))?(?: import ([\w, ]+))?", re.M)


def _folder_imports(folder: str) -> set[str]:
    """Which other folders (or `core`) the modules in `folder` reach into."""
    out: set[str] = set()
    for path in sorted((PKG / "src" / "uqf_stack" / folder).glob("*.py")):
        for match in _IMPORT.finditer(path.read_text()):
            head, names = match.group(1), match.group(2)
            # `from uqf_stack import core` - the target is in the
            # name list, not the dotted head, and missing it would make this
            # check blind to exactly the import the facade is reached by.
            targets = [head] if head else [n.strip() for n in (names or "").split(",")]
            out.update(t for t in targets if t and t != folder)
    return out - {"paths", "logger"}


@pytest.mark.parametrize("folder", sorted(ALLOWED_IMPORTS))
def test_the_folders_are_layers(folder):
    """The package was thirty-one flat modules; the folders are a claim about
    which of them may depend on which, and a claim in a README is not a rule.

    The one that matters is `model/` importing nothing: it is the DECLARED
    shape of the stack - which pipelines exist, what tables they write, what
    depends on what - and it has to be readable without the code that starts
    processes. `uqf-stack summary` on a dead fleet, the generated docs and the
    scaffolder all rely on that, and each would fail in a different confusing
    way if a `stack/` import crept in.
    """
    extra = _folder_imports(folder) - ALLOWED_IMPORTS[folder]
    assert not extra, (
        f"{folder}/ imports from {sorted(extra)}, which is above it in the layering - "
        f"it may only reach {sorted(ALLOWED_IMPORTS[folder]) or ['paths', 'logger']}"
    )


def test_every_folder_is_in_the_layering():
    """A new folder that nobody added to ALLOWED_IMPORTS would be exempt from
    the rule, which is how the flat package went 1270 lines unchecked."""
    package = PKG / "src" / "uqf_stack"
    folders = {
        d.name for d in package.iterdir() if d.is_dir() and d.name not in {"__pycache__", "logger"}
    }
    assert folders == set(ALLOWED_IMPORTS), folders ^ set(ALLOWED_IMPORTS)
