# Services - Showcase

Everything the uqf stack runs, one short card per process: what it does, what
goes in and what comes out, and how to see it. The groups follow the data, from
where rows are made to what is computed from them. A card with **Details** links
to a full page on how it is built and why.

The fastest way to see a group running is its start profile:
`uqs up --profile fx` starts what the FX chain needs and streams its logs. The
whole stack is `uqs start`. Starting and stopping, config and logs are covered
in [running the uqf stack](../guides/uqs.md). Ports, subscriptions and the full
table list are in the generated [process table](../reference/processes.md).

![How the stack's processes differ: where their rows come from, then what they do with the tables they read](../diagrams/services-showcase.svg)

The tree sorts every process by where its rows come from, then by what it does
with the tables it reads. Databento and Kafka are worth a second look: the
publisher is a Python handler outside q, because a q process can't hold either
subscription, and `databento1` and `kafka_flow1` are ordinary subscribers to the
raw table it publishes.

The dashed box on top is what's **vendored**: the TorQ Finance Starter Pack
under `lib/`, which is never edited - the tickerplant, storage, gateway and
fleet processes everything here runs on, and its own tables. Every process in
the tree is uqf's own; a vendored table it reads or writes is marked **(TorQ)**.

## Feeds

These invent or replay data, so the rest of the stack has something to work on.

**`fxfeed1` · synthetic FX quotes** --- top-of-book quotes for EURUSD, GBPUSD,
USDJPY and AUDUSD: a small random walk around a fixed spot, one pip wide.
Publishes `quote`. [Details](synthetic-feeds.md)

**`fxorderbookfeed1` · synthetic FX books** --- depth-aware FX quotes, three
levels a side. Publishes `fx_orderbook`, which the depth tools and the cross
calculator read. [Details](synthetic-feeds.md)

**`fxtradesfeed1` · synthetic FX fills** --- client trades against the invented
market, the fills markouts and positions are computed from. Publishes `trades`.

**`fxordersfeed1` · synthetic order flow** --- orders, most of which never
become a fill: the input to position limits. Publishes `orders`.
[Details](fx-positions.md)

**`widefeed1` · a wide order book** --- a book with one column per level
(`bid_px_00`, `bid_px_01` and so on), the shape a vendor CSV usually has.
Publishes `wide_orderbook`, and runs as a pair with `vectorize1`. Profile
`depth`.

**`cryptomock1` · crypto books and fills** --- a stand-in for cryptorust's two
kdb recorders, for when they aren't running. Publishes `crypto_book` and
`crypto_trades`. Profile `crypto`, run *instead of* the real recorders, never
alongside them. [Details](crypto-recorder.md)

## External sources

Each of these needs a feed handler outside q, because a q process can't hold the
subscription itself.

**`databento1` · live Databento depth** --- Databento's MBP-10 updates,
published by `external/databento_feed.py`, folded into the stack's book shape.
Reads `databento_mbp10`, publishes `eq_orderbook`. [Details](databento.md)

**`kafka_flow1` · client flow from Kafka** --- client FX trades consumed off a
Kafka topic by `external/kafka_feed.py`, deduplicated on the record's
(partition; offset) so a redelivery is never counted twice. Reads
`kafka_client_flow`, publishes `client_flow`. [Details](kafka.md)

**`crypto_book_decoder1` · cryptorust books from Kafka** --- the sampled full
books cryptorust's `kafka-market-data-recorder` publishes to
`cryptorust.book_snapshots`, five levels a side in wide columns, deduplicated on
the record's (topic; partition; offset). Reads `kafka_message`, publishes
`crypto_book_snapshot`. In the `crypto` profile; needs `kafka_feed1`.
[Details](kafka.md#cryptorust-book-snapshots)

**`flink_vwap1` · VWAP windows from Apache Flink** --- per-sym one-minute VWAP
windows computed by a PyFlink job in `external/flink_vwap_streamer.py`, each
published once: a window Flink emits again is dropped on `(sym; window_end)`.
Reads `flink_vwap_raw`, publishes `flink_vwap`. Start it with
`uqs start flink_vwap1`, then `uqs connector start flink_vwap`, which needs
`apache-flink` and Java.

## Normalisers

Several sources say the same thing in different shapes. A normaliser folds them
into one table, so whatever reads it handles one market and one shape.

**`executions1` · every fill as one table** --- FX `trades` and `crypto_trades`
become `executions`.

**`marketdata1` · every venue's book** --- FX `quote` and `fx_orderbook`, and
`crypto_book`, as `market_data`, each row keeping its source and the source's
own time. `posbook1` values positions from it, and it heads the arbitrage chain,
which takes the FX books only. Profile `fx`. [Details](superbook.md)

**`order_outcomes1` · every order source as one contract** --- normalizes each
order source's status events onto `order_outcomes`: the source's own time, an
identity scoped by source (`source` + `request_id`), a normalized outcome
(`filled`, `rejected`, `cancelled`, `unresolved` or `unmapped`) and the book,
product and venue where the source gives them (venue is never inferred). Today
one source, `orders`; another is one declared `.qetl.transform` and an entry in
its declaration. Unlike `executions` it keeps the rejects and cancels. Reads
`orders`, publishes `order_outcomes`, which `order_quality1` reads. On demand,
no profile. [Counting
policy](../reference/pipeline-declarations.md#order-outcome-contract-----order_outcomes)

## Analytics

**`posbook1` · positions and P&L** --- a running position per instrument from
`executions`, each fill marked from `market_data` at the mid as of its own time:
an FX pair's level-0 mid, a crypto sym's best mid across venues no more than
five seconds old (the reference `crypto_markout1` scores against). One book
carries FX and crypto, because it reads the normalisers rather than each market.
Publishes `position`. Profile `fx`.

**`demo_markout1` · execution quality** --- each fill against the mid one and
ten seconds later: did the price move for or against the trade? Reads `trades`
and `quote`, publishes `demo_execution_quality`. Profile `fx`.
[Details](markouts.md)

**`fxpositions1` · exposure and limits** --- net exposure by symbol, book and
product from `executions`, the fills `posbook1` nets too, with a breach row
whenever a limit is crossed. Publishes `fx_position` and `fx_limit_breach`.
Profile `fx`. [Details](fx-positions.md)

**`superbook1` · one book from every source** --- the latest book from each
source merged per pair, with stale liquidity expired on a timer. Publishes
`superbook`. Profile `arbitrage`. [Details](superbook.md)

**`arbitrage1` · crossed prices across sources** --- where one source's bid is
above another's ask on the same pair. Publishes `arbitrage`. Profile
`arbitrage`. [Details](superbook.md)

**`crossarb1` · crossed prices through other pairs** --- the direct book against
a synthetic route, for example EURJPY against EURUSD × USDJPY. Publishes
`cross_arbitrage`. Profile `arbitrage`. [Details](cross-arbitrage.md)

**`vectorize1` · wide book to vector columns** --- `wide_orderbook`'s per-level
columns folded into one vector per side, the shape the pricing functions take.
Publishes `mkt_orderbook`. Profile `depth`.

**`cross1` · synthetic crosses** --- cross rates derived from `fx_orderbook`,
kept as the process's own state and published nowhere. A leaf you can stop
without anything downstream noticing. Profile `depth`.

**`alert_sink1` · limit breaches to a webhook** --- POSTs each `fx_limit_breach`
row as JSON to the URL in `UQF_SOURCE_CRED_ALERT_SINK`, throttled, retried three
times and then recorded in `dead`. At least once, in memory. Refuses to run with
no URL. On demand, no profile. See
[pipeline-declarations](../reference/pipeline-declarations.md#sinks-----alert_sink).

**`order_quality1` · rolling fill and reject rates** --- per book, over the last
minute, five minutes and hour: how many requests, how many filled and rejected,
and the two ratios, re-evaluated every five seconds from `order_outcomes`
(`order_outcomes1`'s normalized order sources). A request counts once, at its
latest terminal outcome, however many updates or redeliveries a source sends. A
book quiet in a short window shows 0 orders and null ratios, not a rate of zero.
A **rolling job** (`.qetl.job.stream.at_rolling`), so late orders amend it, it
rolls across midnight and it rebuilds its window from the log on restart.
Publishes `order_quality`. On demand, no profile (`fx` has no free plant slot);
start it with `order_outcomes1`, which feeds it.
[Details](../reference/pipeline-declarations.md#rolling-window-job-----qetljobstreamrolling)

**`last_value1` · the current price of every sym** --- the newest level-0 bid,
ask and mid per sym from `market_data`, FX and crypto, as a published table. An
older book never overwrites a newer one. Read the last row per sym for the one
shared answer: `select by sym from last_value`, from the RDB or through the
gateway. Publishes `last_value`. Profile `fx`; not in `uqs start`.

**`fxmarkout1` · normalized FX markouts, long** --- scores each execution of
`executions` against `market_data` at -1s, 0, +1s and +10s: the markout, the
execution residual, each cross leg's allocation and the reconciling residual, a
row per component. Nothing in it names a source; a deal tape or order book is an
adapter onto those two tables. Publishes `fx_markout`. On demand, not in a
profile. [Details](fx-markout.md)

**`fxmarkoutwide1` · normalized FX markouts, wide** --- `fxmarkout1`'s
calculation in a row per execution and horizon: the markout, the execution
residual and the legs side by side. Publishes `fx_markout_wide`. On demand, not
in a profile. [Details](fx-markout.md)

## Backfills

A backfill takes a date range, fills it, records which windows are covered, and
exits. Run one with `uqs job backfill <worker> --from … --to … [--wait]`;
running it again skips everything already covered. None starts with the stack.

**`deals_backfill1` · demo deals** --- FX deals from a q source, the reference
example of a bounded worker.

**`events_backfill1` · an event tape** --- events from a q source, into
`event_tape`.

**`upstream_backfill1` · another q process** --- trades read from an upstream q
process over IPC.

**`duckdb_deals_backfill1` · deals from DuckDB** --- mock FX deals copied out of
a DuckDB file over ODBC, a day at a time.

**`databento_backfill1` · Databento history** --- past MBP-10 depth over ODBC,
folded with the same transform `databento1` applies live.

**`crypto_market_data_backfill1` · recorded crypto** --- cryptorust's recorded
crypto books and trades replayed from DuckDB, an hour at a time.
[Details](crypto-recorder.md)

**`hdb_demo_markouts_backfill1` · markouts from history** --- the HDB's fills
marked out against its quotes, into `demo_execution_quality`. It refills what
`demo_markout1` missed while it was down; `uqs job gaps markout` names those
holes. [Details](markouts.md)

**`hdb_transfer_backfill1` · one kdb+ database into another** --- trades read
from another HDB's files, with notional added, into `trades_copy`. The worked
example: `q scripts/examples/hdb_transfer_example.q` runs it end to end.
[Details](../scaffolding/hdb-transfer.md)

**`crypto_book_1s1` and `hdb_crypto_book_backfill1` · one-second book
snapshots** --- the last book state per venue and sym per second, bucketed on
the venue's `source_time` rather than the tickerplant's `time`. Reads
`crypto_book`, publishes `crypto_book_1s`; a second with no update has no row
(no forward fill). `crypto_book_1s1` is a bars job, a second closing half a
second after it ends; the backfill rebuilds the same rows from the HDB's
`crypto_book` with the same pick, replacing the live job's under
`venue, sym, bar_start`. Profile `crypto`; not in `uqs start`. Start it with
`uqs start crypto_book_1s1`; the worked example:
`q scripts/examples/hdb_crypto_book_example.q`.

**`hdb_fx_markouts_backfill1` · FX markouts from the HDB** --- re-derives
`fx_markout` for a window of the HDB's `executions` against its `market_data`,
with the calculation `fxmarkout1` applies live, replacing its rows under (venue;
fill_id; horizon; kind; step). [Details](fx-markout.md)

**`hdb_fx_markout_wide_backfill1` · FX markouts from the HDB, wide** --- the
same, into `fx_markout_wide` under (venue; fill_id; horizon).
[Details](fx-markout.md) **`hdb_order_quality_backfill1` · the rolling
order-quality series, from the HDB** --- the bounded twin of `order_quality1`.
For each aligned hour it reads the HDB's `orders` and the longest window's worth
before it (`hdb_orders` source), runs them through the live pair's own adapter
(`order_outcomes_from_orders`) and statistic (`order_quality`), and writes
`order_quality` at every five-second grid point, replacing the live rows under
`book, window, as_of`. `uqs job gaps order_quality` names it. An hour with no
orders publishes nothing (a bounded worker runs no transform on an empty
window); the live job would still publish identity rows after a quiet start.
Worked example: `q scripts/examples/hdb_order_quality_example.q`.

**`book_liquidity_1m1` and `hdb_book_liquidity_1m_backfill1` · one-minute FX
book liquidity** --- per FX liquidity provider and requested size (1m, 5m, 10m),
the minute's average sweep price, quantity actually available and full-fill
ratio on each side, from `market_data`, bucketed on the book's `source_time`.
The average is over book snapshots, each weighing the same, not over time; an
empty side counts as zero quantity and a partial sweep is never full (see [the
reference](../reference/book-liquidity.md)). Reads `market_data`, publishes
`book_liquidity_1m`. `book_liquidity_1m1` is a bars job, a minute closing five
seconds after it ends; the backfill rebuilds the same rows from the HDB's
`market_data` with the same composition of the extracted transforms, replacing
the live job's under `sym, source, settlement, category, req_size, bar_start`.
In no profile and not in `uqs start` (the fx profile already uses every plant
connection the community licence has): `uqs start book_liquidity_1m1` alongside
a running `marketdata1`.

## Diagnostics

**Tickerplant inspector · `tap1`** --- point it at any table and it prints what
arrives. The first thing to start when a table stays empty and you don't know
whether rows are arriving. Started on demand. [Details](tap.md)
