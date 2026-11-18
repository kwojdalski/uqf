"""The kinds of job, written once (#966).

The set of kinds used to be spelled in the declaration parser, the remover, the
`uqs job new` choices and the man-page ratchet, so a new kind was found
incomplete by what broke. Those places now read this table, and
tests/test_job_kinds.py holds it equal to the verbs q registers and to the
places that cannot read it (the q ratchet, the docs).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeclarationVerb:
    """A q call that declares a job or worker."""

    verb: str  # the q name, without the leading dot
    namespace: str = ""  # the sub-namespace q installs its surface into, if any
    #: The framework function that ends a `--once` unit of a job this verb declares (#2798): the
    #: one runner per kind, which no job implements for itself. Empty is refused by
    #: tests/test_once_contract.py - a verb names its runner or says in that test why not.
    once_runner: str = ""


STREAM_DEFINE = "qetl.job.stream.define"
STREAM_NORMALIZE = "qetl.job.stream.normalize"
STREAM_HORIZONS = "qetl.job.stream.at_horizons"
STREAM_BARS = "qetl.job.stream.at_bars"
STREAM_ROLLING = "qetl.job.stream.at_rolling"
BOUNDED_DEFINE = "qetl.job.bounded.define"

#: Every declaration verb. Add a kind's verb here first; the tests then name
#: each place that still lacks it.
#: The runner every streaming job's one unit goes through - a poll, a timer cycle or an input
#: batch, chosen by what the job declares - and a bounded worker's (src/etl/core/once.q).
STREAM_ONCE = "qetl.job.stream.once.run"
BOUNDED_ONCE = "qetl.job.bounded.once.limit"

VERBS: tuple[DeclarationVerb, ...] = (
    DeclarationVerb(STREAM_DEFINE, once_runner=STREAM_ONCE),
    DeclarationVerb(STREAM_NORMALIZE, once_runner=STREAM_ONCE),
    DeclarationVerb(STREAM_HORIZONS, "qetl.job.stream.horizons", once_runner=STREAM_ONCE),
    DeclarationVerb(STREAM_BARS, "qetl.job.stream.bars", once_runner=STREAM_ONCE),
    DeclarationVerb(STREAM_ROLLING, "qetl.job.stream.rolling", once_runner=STREAM_ONCE),
    DeclarationVerb(BOUNDED_DEFINE, once_runner=BOUNDED_ONCE),
)

DECLARATION_VERBS: tuple[str, ...] = tuple(v.verb for v in VERBS)

#: The kind `uqs job list` reports for each declaration verb (#1889): the
#: scaffold vocabulary below, so a job `job new --kind streaming` wrote lists
#: as `streaming`. A bar or rolling job is a streaming job with no scaffold of
#: its own. Whether a streaming job reads anything - feed or etl, which picks
#: its proctype and access list - is the listing's `subscribes`, not a kind.
LISTED_KIND: dict[str, str] = {
    STREAM_DEFINE: "streaming",
    STREAM_NORMALIZE: "normalizer",
    STREAM_HORIZONS: "horizon",
    STREAM_BARS: "streaming",
    STREAM_ROLLING: "streaming",
    BOUNDED_DEFINE: "backfill",
}
#: A reaction's kind in `job list`: no declaration verb of the above, and no
#: process of its own.
REACTION = "reaction"

#: The declaration verb each `uqs job new --kind` writes, or None for a kind that declares no job
#: in q (a connector is a process outside the stack). The `--once` contract reads this: a kind
#: whose verb has a runner takes part automatically (tests/test_once_contract.py, #2798).
SCAFFOLD_VERB: dict[str, str | None] = {
    "streaming": STREAM_DEFINE,
    "backfill": BOUNDED_DEFINE,
    "normalizer": STREAM_NORMALIZE,
    "connector": None,
    "horizon": STREAM_HORIZONS,
}

#: The `uqs job new --kind` values.
SCAFFOLD_KINDS: tuple[str, ...] = (
    "streaming",
    "backfill",
    "normalizer",
    "connector",
    "horizon",
)
