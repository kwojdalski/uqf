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
BOUNDED_DEFINE = "qetl.job.bounded.define"

#: Every declaration verb. Add a kind's verb here first; the tests then name
#: each place that still lacks it.
VERBS: tuple[DeclarationVerb, ...] = (
    DeclarationVerb(STREAM_DEFINE),
    DeclarationVerb(STREAM_NORMALIZE),
    DeclarationVerb(STREAM_HORIZONS, "qetl.job.stream.horizons"),
    DeclarationVerb(STREAM_BARS, "qetl.job.stream.bars"),
    DeclarationVerb(BOUNDED_DEFINE),
)

DECLARATION_VERBS: tuple[str, ...] = tuple(v.verb for v in VERBS)

#: The `uqs job new --kind` values.
SCAFFOLD_KINDS: tuple[str, ...] = (
    "streaming",
    "backfill",
    "normalizer",
    "external",
    "horizon",
)
