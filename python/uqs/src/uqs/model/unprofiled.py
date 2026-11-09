"""Standing processes no profile reaches, and why each is deliberate.

Apart from model/profiles.py because it is data that grows by one entry per
job: `uqs job new --unprofiled REASON` appends to it, and profiles.py sat at
the package's 400-line module budget, so the first exemption scaffolded
failed `test_no_module_is_still_oversized` on a file the author never touched
(#2462). Nothing but the scaffold, `uqs job remove` and the profile tests
reads it.
"""

from __future__ import annotations

#: Standing processes no profile reaches, and why each is deliberate.
#:
#: The same shape as a connector's `produces` and the catalog's hidden list: an
#: exemption carries its reason, so "nobody got round to it" cannot pass as
#: "deliberately not in a set". A process that is in neither a profile nor
#: here fails `test_every_standing_process_is_reachable`.
#:
#: Backfills are not here and do not need to be: they are bounded, hold no
#: plant connection and belong to no standing set.
UNPROFILED: dict[str, str] = {
    "demo_deals_feed1": (
        "on demand: the streaming twin of deals_backfill1 over the demo_deals source, the "
        "example pair `uqs job check demo_deals` verifies - `uqs start demo_deals_feed1`, "
        "never with the stack"
    ),
    "kafka_flow1": (
        "a live external feed - its rows come from a Kafka consumer (the q feed "
        "kafka_feed1, or `uqs connector start kafka`), so it is started with that or not at all"
    ),
    "kafka_feed1": (
        "a live external feed - it consumes a Kafka topic with KX's kfk library "
        "(UQF_KFK_HOME, UQF_KAFKA_BROKERS), so it is started on demand, never with the stack"
    ),
    "exec_bars1": (
        "on demand: it bars the executions tape, which the fx and crypto chains "
        "both publish, so it belongs to neither profile - `uqs start exec_bars1` "
        "alongside whichever is running"
    ),
    "order_quality1": (
        "on demand: it reads `order_outcomes`, which order_outcomes1 derives from "
        "the `orders` fxordersfeed1 publishes in the fx and default sets, but fx "
        "already holds all fourteen plant slots, so the job cannot join it - "
        "`uqs start order_quality1` once something else is stopped, or on its own "
        "with fxordersfeed1 and order_outcomes1"
    ),
    "order_outcomes1": (
        "on demand: it normalizes the `orders` fxordersfeed1 publishes into the "
        "order-outcome contract for order_quality1, and fx already holds all "
        "fourteen plant slots - `uqs start order_outcomes1` alongside "
        "order_quality1"
    ),
    "book_liquidity_1m1": (
        "on demand: it reads `market_data`, which marketdata1 publishes in the fx and "
        "default sets, but fx already holds all fourteen plant slots, so the job cannot "
        "join it - `uqs start book_liquidity_1m1` once something else is stopped, or "
        "alongside a market_data feed"
    ),
    "tap1": (
        "a diagnostic subscriber chosen at runtime: which table it taps is an "
        "argument, so there is no standing set it belongs to"
    ),
    "alert_sink1": (
        "an outbound sink that needs a webhook URL (UQF_SOURCE_CRED_ALERT_SINK) "
        "and refuses to run without one, so it starts only when asked"
    ),
    "fxmarkout1": (
        "on demand: it needs an executions and a market_data source a site "
        "wires in, and the fx profile has no free connection slot"
    ),
    "eq_markout1": "on demand: it needs live equity fills, which no feed here publishes",
    "fxmarkoutwide1": (
        "on demand: the wide layout of fx_markout1, started with it when a site "
        "wants both layouts; the fx profile has no free connection slot"
    ),
}
