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


STREAM_DEFINE = "qetl.job.stream.define"
STREAM_NORMALIZE = "qetl.job.stream.normalize"
STREAM_HORIZONS = "qetl.job.stream.at_horizons"
STREAM_BARS = "qetl.job.stream.at_bars"
STREAM_ROLLING = "qetl.job.stream.at_rolling"
BOUNDED_DEFINE = "qetl.job.bounded.define"

#: Every declaration verb. Add a kind's verb here first; the tests then name
#: each place that still lacks it.
VERBS: tuple[DeclarationVerb, ...] = (
    DeclarationVerb(STREAM_DEFINE),
    DeclarationVerb(STREAM_NORMALIZE),
    DeclarationVerb(STREAM_HORIZONS, "qetl.job.stream.horizons"),
    DeclarationVerb(STREAM_BARS, "qetl.job.stream.bars"),
    DeclarationVerb(STREAM_ROLLING, "qetl.job.stream.rolling"),
    DeclarationVerb(BOUNDED_DEFINE),
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

#: The `uqs job new --kind` values.
SCAFFOLD_KINDS: tuple[str, ...] = (
    "streaming",
    "backfill",
    "normalizer",
    "connector",
    "horizon",
)
