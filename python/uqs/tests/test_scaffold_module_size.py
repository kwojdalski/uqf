"""A table-defining `uqs job new` leaves every q module within its budget (#2503).

Every scaffold that defines a table appends its declaration to
src/etl/plant_tables.q. That file once also held the whole .qetl.plant API and
sat at exactly the q-module-size cap, so the documented first command left the
tree failing the gate on a file its author never edited. The API now lives in
src/etl/plant_registry.q and the declarations have the budget to themselves.

Each shape that defines a table is scaffolded into its own copy of the tree,
and the gate's own check is run over the copy - the same function
`python3 scripts/gates/check.py q-module-size` calls. No q needed.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from uqs.paths import PLANT_REGISTRY_FILE, STACK_TABLES_TEST, TABLES_FILE
from uqs.scaffold import jobs, write
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import definition_columns, parse_columns, table_definition
from uqs.scaffold.docs import SHOWCASE_PAGE, STACK_PAGE
from uqs.scaffold.external import external_feed
from uqs.scaffold.horizon import horizon_job
from uqs.scaffold.normalizer import normalizer
from uqs.scaffold.plan import ScaffoldPlan
from uqs.scaffold.profile import PROFILES_FILE

UQF_ROOT = Path(__file__).resolve().parents[3]

_SPEC = importlib.util.spec_from_file_location(
    "q_module_size", UQF_ROOT / "scripts" / "gates" / "policy" / "q_module_size.py"
)
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("q_module_size", gate)
_SPEC.loader.exec_module(gate)

_COLUMNS = "sym:symbol, mid:float"
_QUOTE = table_definition("quote", parse_columns("sym:symbol, bid:float, ask:float"))
_TRADE = table_definition("trade", parse_columns("sym:symbol, price:float"))

#: One plan per shape that defines a table, as `uqs job new` builds it.
SHAPES: dict[str, Callable[[], ScaffoldPlan]] = {
    "streaming": lambda: jobs.streaming_job("markout2", [], "markout2", _COLUMNS),
    "backfill": lambda: backfill.bounded_worker("fx_rates", "fx_rates", _COLUMNS),
    "normalizer": lambda: normalizer(
        "quotenorm",
        ["quote"],
        parse_columns(_COLUMNS),
        {"quote": definition_columns(_QUOTE)},
        known_tables={"quote"},
    ),
    # each quote evaluated 10s on, against trade
    "horizon": lambda: horizon_job(
        "quotehz",
        ["quote", "trade"],
        parse_columns(_COLUMNS),
        {"quote": definition_columns(_QUOTE), "trade": definition_columns(_TRADE)},
        "0D00:00:10",
        known_tables={"quote", "trade"},
    ),
    # two tables: the raw one the Python half publishes, and the q job's output
    "connector": lambda: external_feed(
        "ws", "ws_raw", "ws_quotes", _COLUMNS, known_tables={"quote"}, unprofiled="a test feed"
    ),
}


def _copy_of_the_tree(root: Path) -> Path:
    """What a scaffold writes into: src/, the process scripts, and the lists
    and pages it appends to."""
    shutil.copytree(UQF_ROOT / "src", root / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", root / "scripts" / "processes")
    for rel in (STACK_TABLES_TEST, PROFILES_FILE, STACK_PAGE, SHOWCASE_PAGE):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, root / rel)
    for rel in ("python/uqs/src/uqs/external", "python/uqs/tests"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def test_the_copy_starts_within_budget(tmp_path):
    """The precondition, so a failure below is the scaffold's doing."""
    assert gate.problems(_copy_of_the_tree(tmp_path)) == []


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_table_defining_scaffold_keeps_every_q_module_within_budget(tmp_path, shape):
    root = _copy_of_the_tree(tmp_path)
    registry = (root / PLANT_REGISTRY_FILE).read_text()
    before = (root / TABLES_FILE).read_text()
    plan = SHAPES[shape]()
    write.apply_plan(plan, root)
    assert (root / TABLES_FILE).read_text() != before, "the scaffold defined a table"
    assert (root / PLANT_REGISTRY_FILE).read_text() == registry, (
        "a scaffold appends declarations, never to the .qetl.plant API"
    )
    assert gate.problems(root) == []
