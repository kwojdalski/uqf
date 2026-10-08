"""The vendored TorQ processes each profile starts beneath its pipelines.

Apart from profiles.py, which closes each profile over the job graph,
because these are not derived from anything: they are the starter pack's
processes, chosen by hand, and a runtime with none of this tree's pipelines
(torq) starts nothing else.
"""

from __future__ import annotations

#: The vendored TorQ processes every profile needs: the plant itself, service
#: discovery, the databases, the writedown path, the gateway and the
#: monitoring that makes `summary`'s Heartbeat column answer.
#:
#: Taken from the vendored rows that start by default today rather than
#: chosen afresh, so a profile start produces the same infrastructure a
#: `start all` does and nothing moves underneath this change. Four of these
#: hold a plant slot (VENDORED_PLANT_CLIENTS); the rest do not subscribe.
CORE_INFRA: tuple[str, ...] = (
    "discovery1",
    "stp1",
    "rdb1",
    "hdb1",
    "hdb2",
    "wdb1",
    "sort1",
    "sortworker1",
    "sortworker2",
    "gateway1",
    "monitor1",
    "housekeeping1",
    "sctp1",
    "metrics1",
)

#: The TorQ stack with nothing of uqf's on top: capture (discovery, the plant
#: and the chained plant), store (rdb, the intraday writedown, the sort and
#: one worker, hdb1), query (the gateway), keep an eye on it (monitor,
#: metrics, housekeeping, reporter) and replay (tpreplay1). CORE_INFRA less
#: its second hdb and second sort worker - one of each is enough for a stack
#: nothing of uqf's runs on - plus the two below that no other profile starts.
#:
#: THE SORT PROCESSES. At end of day wdb1 hands its intraday writedown to
#: sort1, which sorts it into the HDB with sortworker1. With no sort
#: process, TorQ's wdb logs "no sortandreload process detected" as an ERROR
#: every evening and sorts on wdb1 itself (informsortandreload in
#: lib/torq/code/processes/wdb.q).
#:
#: PLANT SLOTS: four. rdb1, wdb1, sctp1 and metrics1 subscribe. reporter1
#: holds handles to the gateway, the rdb and the hdb (CONNECTIONS in
#: lib/torq/config/settings/reporter.q), not to the plant - but those count
#: against each of THEIR licence caps, which is why the starter pack ships it
#: off on the community licence.
#:
#: tpreplay1 STARTS AND EXITS. It is the one-shot replay `uqs data replay`
#: aims with a log, a schema and an HDB on its start line. Started from a
#: profile it has none of them, and tickerlogreplay.q exits at startup
#: (.err.exitifnull on schemafile and hdbdir) - before it reads or empties
#: anything. So it shows as down once started, and that is it working.
ESSENTIAL_INFRA: tuple[str, ...] = (
    "discovery1",
    "stp1",
    "rdb1",
    "hdb1",
    "wdb1",
    "sort1",
    "sortworker1",
    "gateway1",
    "monitor1",
    "housekeeping1",
    "sctp1",
    "metrics1",
    "reporter1",
    "tpreplay1",
)

#: Processes that start, do their one job and exit (tpreplay1, above). A
#: readiness check must not wait for them to answer: down is them working (#902).
ONE_SHOT: frozenset[str] = frozenset({"tpreplay1"})

#: Profiles that start an infrastructure set other than CORE_INFRA, and
#: which. Every other profile starts all of CORE_INFRA. Composed profiles take
#: the union, so `essential,fx` is the full infrastructure `fx` needs.
#: essential plus the starter pack's own demo feed, which publishes `trade`
#: and `quote` from `rand`. For the torq runtime: uqf turns feed1 off, because
#: its own fxfeed1 publishes `quote` and two producers would interleave.
FEED_INFRA: tuple[str, ...] = (*ESSENTIAL_INFRA, "feed1")

#: CORE_INFRA - what a `start all` brings up - plus feed1: the starter pack's
#: whole default fleet, with the feed uqf would replace.
FULL_INFRA: tuple[str, ...] = (*CORE_INFRA, "feed1")

#: What the starter pack runs on PeachQ today, and nothing that dies there:
#: the plant, the rdb, the hdb, the gateway, housekeeping, the chained plant,
#: metrics and feed1, so trade and quote come in. Left out, each for a PeachQ
#: gap (docs/guides/uqs.md#runtimes): wdb1 (no .Q.chk), sort1 (refuses
#: -s -2), reporter1 (`type` loading reporter.q), monitor1 (ignores stop).
CAPTURE_INFRA: tuple[str, ...] = (
    "discovery1",
    "stp1",
    "rdb1",
    "hdb1",
    "gateway1",
    "housekeeping1",
    "sctp1",
    "sortworker1",
    "metrics1",
    "feed1",
)

PROFILE_INFRA: dict[str, tuple[str, ...]] = {
    "essential": ESSENTIAL_INFRA,
    "feed": FEED_INFRA,
    "full": FULL_INFRA,
    "capture": CAPTURE_INFRA,
}
