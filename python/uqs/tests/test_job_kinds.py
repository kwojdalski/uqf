"""The kinds of job are one table (#966); the places that cannot read it are held to it.

`uqs.model.kinds` is read by the declaration parser, `uqs job remove` and the
`--kind` choices. What cannot read it - q's own entry points, the man-page
ratchet, the docs, the scaffold's dispatch - is checked here, and each failure
names the place that lacks the kind.
"""

from __future__ import annotations

import re
from pathlib import Path

from uqs.cli.create_kinds import SHAPES
from uqs.model.kinds import DECLARATION_VERBS, SCAFFOLD_KINDS, VERBS

ROOT = Path(__file__).resolve().parents[3]
_NS = re.compile(r"^\\d\s+(\.\S*)\s*$")
_ENTRY = re.compile(r"^(\w+):\{\[\w+;decl\]")
_CALL = re.compile(r"\.(qetl\.job\.(?:stream|bounded)\.\w+)\[\s*`")


def _q_entry_points() -> set[str]:
    """Every public `ns.verb:{[name;decl]` the ETL core defines in the job namespaces."""
    found: set[str] = set()
    for path in sorted((ROOT / "src/etl/core").glob("*.q")):
        ns, before = ".", ""
        for line in path.read_text().splitlines():
            if m := _NS.match(line):
                ns = m.group(1)
            elif (e := _ENTRY.match(line)) and ns in (".qetl.job.stream", ".qetl.job.bounded"):
                if before.strip() != "/ @private":
                    found.add(f"{ns[1:]}.{e.group(1)}")
            before = line
    return found


def test_the_verbs_are_the_q_entry_points():
    q = _q_entry_points()
    ours = set(DECLARATION_VERBS)
    assert ours == q, f"kinds.py lacks {sorted(q - ours)}; q lacks {sorted(ours - q)}"


def test_every_declaration_in_the_tree_uses_a_known_verb():
    for sub in ("streaming", "workers"):
        for path in sorted((ROOT / "src/etl" / sub).glob("*.q")):
            for verb in _CALL.findall(path.read_text()):
                assert verb in DECLARATION_VERBS, f"{path.name}: {verb} is not in kinds.py"


def test_every_option_shape_names_a_real_kind():
    named = {k for kinds in SHAPES.values() for k in kinds}
    assert named <= set(SCAFFOLD_KINDS), (
        f"create_kinds.SHAPES names {sorted(named - set(SCAFFOLD_KINDS))}"
    )


def test_every_scaffold_kind_is_dispatched_by_the_command():
    text = (ROOT / "python/uqs/src/uqs/cli/create.py").read_text()
    for kind in SCAFFOLD_KINDS:
        assert f'kind == "{kind}"' in text, f"cli/create.py does not handle --kind {kind}"


def test_a_kind_with_its_own_namespace_is_excepted_from_the_man_ratchet():
    text = (ROOT / "tests/q/test_man_registry.q").read_text()
    for v in VERBS:
        if v.namespace:
            assert f".{v.namespace}.installed" in text, (
                f"tests/q/test_man_registry.q does not exempt {v.namespace}.installed"
            )


def test_the_declaration_reference_documents_every_verb():
    text = (ROOT / "docs/reference/pipeline-declarations.md").read_text()
    for verb in DECLARATION_VERBS:
        assert f".{verb}" in text, f"pipeline-declarations.md never mentions .{verb}"
