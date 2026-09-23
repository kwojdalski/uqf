"""Guards on the uqs package's shape: module size, and layering.

The package was once one 1578-line `core.py`. It was split into focused
modules, and there is no facade over them: a caller imports from the module
that defines what it needs, so there is one path to each name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "python" / "uqs"

#: Every module that must import on its own. Dotted, because the package is
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


@pytest.mark.parametrize("module", MODULES)
def test_each_module_exists_and_imports(module):
    """Each module must import on its own.

    A module that only works when another has already been imported has a
    hidden dependency on import order, which is the kind of thing that works
    in the test suite and fails in a fresh process.
    """
    __import__(f"uqs.{module}")


#: Modules already past the threshold when the rule was widened to cover the
#: whole package, and the size each was at. They may SHRINK but not grow: a
#: ratchet keeps them visible and stops "already over" becoming a licence.
#:
#: Empty: both modules that were listed have since been split or deleted.
OVERSIZED_BY_HISTORY: dict[str, int] = {}


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
    package = PKG / "src" / "uqs"
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
    package = PKG / "src" / "uqs"
    for module, cap in OVERSIZED_BY_HISTORY.items():
        path = package / (module.replace(".", "/") + ".py")
        assert path.is_file(), f"{module} is exempt but does not exist"
        n = len(path.read_text().splitlines())
        assert n > 400, f"{module} is under the threshold now - remove its exemption"
        assert n <= cap, f"{module} grew: {n} > {cap}"


#: What each folder is allowed to import from, and nothing else. The order is
#: the layering: `model/` is what the stack declares, `stack/` is the fleet
#: that runs, `external/` and `checks/` act on a running fleet, and `cli/` and
#: `scaffold/` are front ends, which import what they use from where it is
#: defined.
#:
#: `paths` and `logger` are omitted from every list because everything may
#: import them: they are leaves that import nothing from this package, so they
#: cannot take part in a cycle.
ALLOWED_IMPORTS = {
    "model": set(),
    "stack": {"model"},
    "external": {"model", "stack"},
    "checks": {"model", "stack"},
    "scaffold": {"model", "stack"},
    "cli": {"model", "stack", "scaffold", "external", "checks"},
}

_IMPORT = re.compile(r"^\s*(?:from|import) uqs(?:\.(\w+))?(?: import ([\w, ]+))?", re.M)


def _folder_imports(folder: str) -> set[str]:
    """Which other folders the modules in `folder` reach into."""
    out: set[str] = set()
    for path in sorted((PKG / "src" / "uqs" / folder).glob("*.py")):
        for match in _IMPORT.finditer(path.read_text()):
            head, names = match.group(1), match.group(2)
            # `from uqs import paths as stack_paths` - the target is in
            # the name list, not the dotted head, and may carry an alias.
            targets = (
                [head] if head else [n.split(" as ")[0].strip() for n in (names or "").split(",")]
            )
            out.update(t for t in targets if t and t != folder)
    return out - {"paths", "logger"}


@pytest.mark.parametrize("folder", sorted(ALLOWED_IMPORTS))
def test_the_folders_are_layers(folder):
    """The package was thirty-one flat modules; the folders are a claim about
    which of them may depend on which, and a claim in a README is not a rule.

    The one that matters is `model/` importing nothing: it is the DECLARED
    shape of the stack - which pipelines exist, what tables they write, what
    depends on what - and it has to be readable without the code that starts
    processes. `uqs summary` on a dead fleet, the generated docs and the
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
    package = PKG / "src" / "uqs"
    folders = {
        d.name for d in package.iterdir() if d.is_dir() and d.name not in {"__pycache__", "logger"}
    }
    assert folders == set(ALLOWED_IMPORTS), folders ^ set(ALLOWED_IMPORTS)
