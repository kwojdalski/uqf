"""Every q name the Python code mentions must exist on the q side.

The Python packages send q as text: programs in string constants
(uqf_frontend's queries, ops and control), declarations the scaffolder writes,
names read out of process logs. Nothing type-checks a string. A rename on the
q side leaves the Python sending a name that no longer exists, and the tests
that exercise it mock the gateway, so they still pass.

That has happened. `control.SET_WORKER_CONFIG` read `.qetl.cfg.overrides`,
`.qetl.cfg.yaml` and `.qetl.cfg.defaults`; the layers are `override_values`,
`yaml_values` and `default_values`. `PUT /control/worker-config` could never
have worked, and every test was green.

So every `.q<ns>.<name>` in the Python sources - code, strings and comments
alike, since a comment naming a function that is gone is the same drift - is
checked against the names the q source defines. The definitions are read from
the `.q` files rather than from `docs/reference/surfaces/current`, for two
reasons: the surface lists only what `src/init.q` loads, so it has nothing for
`scripts/` (`.qtorq`, `.qcat`), and reading text needs no q, so this runs
wherever pytest does - CI included, which has no q.

Only namespaces this tree defines are checked. A name under a namespace the q
source never opens (`.gw`, `.u`, TorQ's own) is someone else's, and is not
this test's to judge.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UQF_ROOT = Path(__file__).resolve().parents[3]
PYTHON_SOURCES = sorted(UQF_ROOT.glob("python/*/src/**/*.py"))
Q_SOURCES = sorted(p for d in ("src", "scripts", "tests/lib") for p in (UQF_ROOT / d).rglob("*.q"))

#: A q name as Python text spells it: `.qetl.cfg.explain`. The lookbehind
#: keeps `x.qetl` (an attribute) and `..qetl` out; the name must start with
#: `.q` because every namespace this tree defines does.
Q_NAME = re.compile(r"(?<![\w.])\.(q[a-z]+(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")

#: `\d .qetl.cfg` switches namespace; `\d .` switches back to root.
_D_LINE = re.compile(r"^\\d\s+\.([\w.]*)\s*$")

#: A definition at column 0: `name:` in the current namespace, or a
#: fully-qualified `.ns.name:` anywhere. Indented lines are bodies.
_DEF = re.compile(r"^(\.?[A-Za-z_][\w.]*)\s*::?")


def q_definitions() -> set[str]:
    """Every dotted name the q source defines, as `qetl.cfg.explain`.

    Text, not execution: a name defined inside a function body or built with
    `set` is missed. That errs towards reporting a name as absent, which is
    the safe direction for a test - a false alarm names the file to look at.
    """
    names: set[str] = set()
    for path in Q_SOURCES:
        ns = ""
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := _D_LINE.match(line):
                ns = m.group(1)
                continue
            if not (m := _DEF.match(line)):
                continue
            name = m.group(1)
            if name.startswith("."):
                names.add(name[1:])
            elif ns:
                names.add(f"{ns}.{name}")
    return names


def mentions() -> list[tuple[str, str, int]]:
    """(name, file relative to the repo, line) for every `.q...` in Python."""
    found = []
    for path in PYTHON_SOURCES:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for m in Q_NAME.finditer(line):
                found.append((m.group(1).rstrip("."), str(path.relative_to(UQF_ROOT)), number))
    return found


def resolves(name: str, defined: set[str], namespaces: set[str]) -> bool:
    """Whether `name` is a definition, a namespace, or a path into one.

    A namespace alone (`.qetl.cfg`), a prefix of one (`.qetl.job`, as an
    f-string template's `.qpipe.job.{name}` leaves it), and a definition
    followed by more (`.qetl.io.odbc.sources`, a key into a dict) all count.
    """
    if name in defined or name in namespaces:
        return True
    if any(ns.startswith(name + ".") for ns in namespaces):
        return True
    parts = name.split(".")
    return any(".".join(parts[:i]) in defined for i in range(len(parts) - 1, 1, -1))


def test_the_q_source_is_read():
    """A reader that found nothing would pass every name vacuously."""
    defined = q_definitions()
    assert "qetl.cfg.explain" in defined
    assert "qetl.cfg.set_override" in defined
    assert "qtorq.publish" in defined, "scripts/ must be read too - .qtorq lives there"


def test_the_python_sources_are_read():
    names = {name for name, _, _ in mentions()}
    assert "qetl.cfg.set_override" in names, (
        "control.SET_WORKER_CONFIG should be among the mentions"
    )


@pytest.mark.parametrize(
    "name",
    ["qetl.cfg.overrides", "qetl.cfg.yaml", "qetl.cfg.defaults"],
)
def test_the_names_that_were_wrong_are_caught(name):
    """The three names SET_WORKER_CONFIG used to send. A check that passed
    them would pass the bug this file was written for."""
    defined = q_definitions()
    namespaces = {n.rsplit(".", 1)[0] for n in defined}
    assert not resolves(name, defined, namespaces)


def test_every_q_name_python_mentions_is_defined():
    defined = q_definitions()
    namespaces = {n.rsplit(".", 1)[0] for n in defined}
    ours = {n.split(".")[0] for n in defined}
    missing = [
        f"{file}:{line}  .{name}"
        for name, file, line in mentions()
        if name.split(".")[0] in ours and not resolves(name, defined, namespaces)
    ]
    assert not missing, "q names in Python that no q file defines:\n" + "\n".join(missing)
