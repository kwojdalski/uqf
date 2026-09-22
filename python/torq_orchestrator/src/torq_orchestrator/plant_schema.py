"""What the tickerplant is told about: the generated `database.q`.

Split out of procs.py when it crossed the module-size threshold
test_module_split.py holds it to. procs.py composes process.csv - which
processes exist and how they are configured; this composes the schema file
those processes load - which TABLES exist. The two change for different
reasons and at different rates.

The rule this module exists to keep: a table any pipeline publishes onto is
a table stp1 is told about. `.u.upd` onto a table the plant does not define
neither lands nor complains, so the gap is invisible from every side - see
`undefined_published_tables` and #287.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from torq_orchestrator.logger import get_logger
from torq_orchestrator.paths import UqfStackPaths
from torq_orchestrator.pipelines import PIPELINES
from torq_orchestrator.schemas import (
    CRYPTO_SIM_FILLS_TABLE_SCHEMA,
    DATABENTO_MBP10_TABLE_SCHEMA,
)
from torq_orchestrator.schemas import (
    _definitions as _table_definitions,
)

log = get_logger(__name__)


def add_extra_table_schema(paths: UqfStackPaths, table_def: str) -> None:
    """Append one q table definition line (e.g. 'mytable:([]time:...;
    sym:...)') to extra_schema.q - _generated_schema_content()'s
    extension point for the `new-process` wizard, the same
    generate-never-edit-vendored approach as everything else here.
    """
    paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    with paths.extra_schema_path.open("a") as f:
        f.write(table_def.rstrip("\n") + "\n")


def _published_tables(pipelines: Iterable[Any]) -> set[str]:
    """Every table any pipeline sends rows to.

    `publishes` defaults to the pipeline's own `table`, so a pipeline that
    owns one table need not repeat itself; one that publishes two - or onto
    a table it does not own - says so, and both are counted here.
    """
    return set(_publishers(pipelines))


#: `name:([]...)` at the start of a line in a generated database.q. Same
#: convention schemas.py reads uqf_stack_tables.q by, applied to the merged
#: file so vendored definitions count too.
_GENERATED_DEFINITION = re.compile(r"^([a-z_0-9]+):\(\[\]", re.MULTILINE)


def undefined_published_tables(paths: UqfStackPaths) -> list[str]:
    """Tables a pipeline publishes that the generated database.q never defines.

    Empty means every pipeline's output has somewhere to land. A name here
    is a pipeline sending rows to a table the tickerplant does not know,
    which fails the way #287 did - no error at the publisher, none at the
    plant, and nothing downstream to read.

    A function rather than an assertion inside a test, so the rule can be
    checked from more than one place and, more to the point, so a test can
    prove it FIRES. A gate whose logic lives in the only test that runs it
    has never been shown to catch anything.
    """
    generated = _generated_schema_content(paths)
    defined = set(_GENERATED_DEFINITION.findall(generated))
    return sorted(
        f"{table} (published by {', '.join(sorted(by))})"
        for table, by in _publishers(PIPELINES).items()
        if table not in defined
    )


def _publishers(pipelines: Iterable[Any]) -> dict[str, set[str]]:
    """{table: the procnames that publish onto it}.

    Through `resolve_edges`, so a pipeline that defers its edges to its own
    q declaration is counted the same as one that spells them here. That
    resolution is strict: a deferred edge with nothing to read raises rather
    than resolving to empty, because an empty publish set would drop the
    pipeline's tables out of the generated database.q - and a table the plant
    does not define discards its rows in silence (#288).
    """
    by: dict[str, set[str]] = {}
    for pipeline in pipelines:
        for table in pipeline.published_tables:
            by.setdefault(table, set()).add(pipeline.procname)
    return by


def _generated_schema_content(paths: UqfStackPaths) -> str:
    """The vendored database.q's tables, plus uqf's own `quotes`/`wide_book`/
    `mkt_orderbook`/`crypto_book` tables and any add_extra_table_schema()
    additions (extra_schema.q) appended - never edited in place, always
    read fresh from the vendored file. stp1's process.csv row (see
    _base_process_rows) is pointed at the generated copy this produces
    rather than the vendored file.
    """
    vendored = (paths.torqapphome / "database.q").read_text()
    extra = paths.extra_schema_path.read_text() if paths.extra_schema_path.is_file() else ""
    # Definitions are resolved from what each pipeline says it PUBLISHES,
    # not from the `schema` field it happens to carry.
    #
    # The difference is the whole of #287. `schema` describes one table, and
    # a pipeline may publish two: fxpositions1 declares `fx_position` and
    # `fx_limit_breach`, owns no `schema`, and so contributed nothing here.
    # Both tables were defined in uqf_stack_tables.q the entire time. The
    # service computed a correct book and published it every five seconds
    # onto tables the tickerplant had never heard of - no error at the
    # publisher, none at the plant, and nothing downstream to read.
    #
    # Deriving from `publishes` closes it for good: a table a pipeline sends
    # rows to is a table stp1 is told about, and there is no second field to
    # forget. `quote` is published by fxfeed1 and is absent below because
    # the vendored file already defines it - which is the same rule read the
    # other way, since uqf_stack_tables.q defines only the tables this tree
    # owns.
    #
    # Definition order among independent table declarations is immaterial to
    # q, which is why grouping them this way is safe.
    owned = _table_definitions()
    definitions = [owned[t] for t in sorted(_published_tables(PIPELINES) & set(owned))] + [
        # Tables no pipeline publishes, so nothing above reaches them.
        # databento_mbp10 comes from the live feed handler
        # (databento_feed.py) and databento1 only subscribes;
        # crypto_sim_fills is written by cryptorust's recorder (see
        # start_crypto_fills_recorder), not by any process in this list.
        DATABENTO_MBP10_TABLE_SCHEMA,
        CRYPTO_SIM_FILLS_TABLE_SCHEMA,
    ]
    return vendored.rstrip("\n") + "\n" + "".join(d + "\n" for d in definitions) + extra
