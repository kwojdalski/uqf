"""What the tickerplant is told about: the generated `database.q`.

Split out of stack/procs.py when it crossed the module-size threshold
test_module_split.py holds it to. stack/procs.py composes process.csv - which
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

from uqs.logger import get_logger
from uqs.model.registry import PIPELINES
from uqs.model.schemas import (
    _definitions as _table_definitions,
)
from uqs.paths import UqsPaths

log = get_logger(__name__)


def _published_tables(pipelines: Iterable[Any]) -> set[str]:
    """Every table any pipeline sends rows to.

    `publishes` defaults to the pipeline's own `table`, so a pipeline that
    owns one table need not repeat itself; one that publishes two - or onto
    a table it does not own - says so, and both are counted here.
    """
    return set(_publishers(pipelines))


#: `name:([]...)` at the start of a line in a generated database.q. Same
#: convention model/schemas.py reads uqs_tables.q by, applied to the merged
#: file so vendored definitions count too.
_GENERATED_DEFINITION = re.compile(r"^([a-z_0-9]+):\(\[\]", re.MULTILINE)


def undefined_published_tables(paths: UqsPaths) -> list[str]:
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


def _generated_schema_content(paths: UqsPaths) -> str:
    """The vendored database.q's tables, plus uqf's own `quotes`/`wide_book`/
    `mkt_orderbook`/`crypto_book` tables appended - never edited in place, always
    read fresh from the vendored file. stp1's process.csv row (see
    _base_process_rows) is pointed at the generated copy this produces
    rather than the vendored file.
    """
    vendored = (paths.torqapphome / "database.q").read_text()
    # EVERY table uqs_tables.q defines. That file is the list of the
    # tables this tree puts on the plant - what its jobs publish, and what
    # the producers outside it publish (the Databento feed handler into
    # databento_mbp10, cryptorust's recorder into crypto_sim_fills) - so it is
    # the whole answer, with nothing to add by name. That no published table
    # is missing from it is held by test_generated_schema_covers_every_
    # published_table: .u.upd onto a table the plant was never told about
    # discards the rows in silence (#287, #288).
    #
    # Definition order among independent table declarations is immaterial to
    # q, so they are sorted for a stable file.
    owned = _table_definitions()
    definitions = [owned[t] for t in sorted(owned)]
    return vendored.rstrip("\n") + "\n" + "".join(d + "\n" for d in definitions)
