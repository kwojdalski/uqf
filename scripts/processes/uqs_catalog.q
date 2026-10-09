/ uqs_catalog.q - the desk catalog's authored half (.qcat): what each
/ tickerplant table is for, and which are deliberately not browsable.
/ .
/ WHAT IS HERE AND WHAT IS NOT. The columns a table has, and their types, are
/ NOT here: they come from `meta` on a process that holds the data. That is
/ the same argument uqs's own `schema` command makes - a catalogue of
/ DECLARATIONS is confidently wrong exactly when it matters, when a
/ tickerplant failed to load its schema file or an RDB has not replayed. This
/ file carries only what cannot be derived: prose, and a decision.
/ .
/ Until this existed, both halves lived in python/uqf_frontend/catalog/ as two
/ CSVs - including a 203-row copy of every column and type, which needed a
/ drift test to keep it honest against src/etl/plant_tables.q. That
/ copy is gone; `meta` answers it.
/ .
/ WHO LOADS THIS. gateway1, via VENDORED_LOAD_OVERLAY in uqs/stack/procs.py.
/ The frontend's `Gateway.call` runs a program on the gateway process itself,
/ so this has to be there rather than on a data tier. The generated
/ database.q would have been the obvious home and is the wrong one: only stp1
/ is given -schemafile.
/ .
/ HIDDEN IS A DECLARATION, NOT AN ABSENCE. A table nobody describes is not
/ browsable either, so silence would be enough to hide one - and then
/ "deliberately not exposed" and "nobody got round to describing it" would
/ look identical. tests/q/test_catalog.q refuses a published table that is in
/ neither list, so the only way to hide one is to say so here, with a reason.

\d .qcat

/ Every table a desk may browse, and what it is for. The frontend joins this
/ against the live `meta` and browses the intersection, so an entry here for
/ a table the database does not have exposes nothing.
describe:(`symbol$())!();

describe[`arbitrage]:
    "Gross direct cross-source bid-over-ask opportunities, one status per pair and snapshot. Select the latest row per pair before filtering active; inactive rows clear previous opportunities";
describe[`config_change]:
    "Runtime configuration changes, one row per watched variable per change. old is empty on a variable's first observation, which records what the process started with. Values are rendered rather than typed, because one column holds every config type. WHO made a change is not here - join TorQ's usage log at the same timestamp";
describe[`cross_arbitrage]:
    "Synthetic-versus-direct cross-currency opportunities: one pair priced against a route through others (EURJPY against EURUSD x USDJPY). route carries the legs, skew how far apart they were quoted, fully_filled whether the notional can be worked through every leg. Like arbitrage, select the latest row per pair before filtering active";
describe[`crypto_book]:
    "Live venue order books published by cryptorust's kdb-market-data-recorder (uqs feed start crypto): top-of-book and depth per venue and symbol as per-row level vectors, the same shape as fx_orderbook. Two clocks: source_time is the venue's own stamp and time is the tickerplant's, stamped on receipt. Read source_time for when the market was in this state, and their difference for how long it took to get here";
describe[`crypto_sim_fills]:
    "Simulated (paper) fills from cryptorust's OMS fill model, run against live market data - not confirmed executions; those are crypto_trades. Kept apart so a P&L number always says which of the two it came from";
describe[`crypto_trades]:
    "Real confirmed exchange executions recorded from the OMS";
describe[`eq_orderbook]:
    "Live Databento MBP-10 folded into the book shape by databento1, using the same .qetl.transform transform the ODBC backfill applies - so a live row and a backfilled one are the same shape";
describe[`demo_deals]:
    "Generic analogue of an external relational deal source, landed by the demo_deals_backfill bounded worker. Synthetic by design - the real source is bank-internal and out of scope for this repository";
describe[`etl_coverage]:
    "Append-only completeness ledger: which [range_from, range_to) window of which dataset is published, at which source_version. A window with rows_published=0 still counts as covered - that is what distinguishes 'ran, found nothing' from 'never ran'";
describe[`event_tape]:
    "Per-event order/trade tape: add, cancel and trade events with the aggressor side on a trade. A superset of trades, so a tape filtered to action='trade' is trade-shaped. Sorted ascending by time by contract - see docs/architecture/event-tape.md";
describe[`demo_execution_quality]:
    "Post-trade markout per fill per horizon; a null markout_pips means no reference quote existed at that horizon, not a zero markout";
describe[`exec_bar]:
    "One-minute bars of executions per sym, built by exec_bars1 and rebuildable from the HDB by hdb_exec_bars_backfill: open, high, low, close, size-weighted vwap, volume and fill count for [bar_start, bar_start+1m) on the fill's source_time. A minute with no fills has no row; the minute a fill can still amend is not yet published, so the newest bar lags by about 5s past its end";
describe[`executions]:
    "Every fill table the stack carries, spelled one way by the executions normalizer: the FX trades and cryptorust's crypto_trades. source_time is the source's own stamp, time the plant's on the normalized row. This is what posbook1 reads";
describe[`fx_limit_breach]:
    "fxpositions1's alerts: one row per limit newly crossed, throttled so a standing breach does not republish on every tick. utilisation is the observed value over the cap";
describe[`fx_position]:
    "fxpositions1's snapshot: net exposure per (sym, book, product), republished whole on every timer tick rather than only what moved. base_qty and quote_qty are both carried because an FX position is two currencies and reporting one hides a cross; break_even is the rate at which closing out leaves the desk flat";
describe[`market_data]:
    "Full book snapshots per sym and source, normalized from quote and fx_orderbook (FX) and crypto_book (one source per venue). source_time is the source's own time where it has one, else the original receipt time; zero size or empty sides withdraw liquidity. posbook1 marks positions to the level-0 mid";
describe[`mkt_orderbook]:
    "vectorize1's output: wide_orderbook folded into the vector-column shape every pricing and execution function in src/ expects, one row per (time, sym)";
describe[`orders]:
    "Order flow from fx_orders_feed: every order, most of which never becomes a fill. order_status is what fxpositions1 filters on; book and product are the dimensions a position is keyed on beyond sym. For the fills these became, see executions";
describe[`position]:
    "Running position book marked to the prevailing mid, with realised and unrealised P&L";
describe[`fx_orderbook]:
    "FX top-of-book and depth as per-row level vectors";
describe[`superbook]:
    "Latest fresh direct FX liquidity merged across sources, bids descending and asks ascending, with aligned source and original timestamp vectors. Empty snapshots clear expired books";
describe[`trades]:
    "Client fills, shaped to match .qpos.apply_fill and .qexec.markout_at_horizons exactly";
describe[`wide_orderbook]:
    "The unfolded book widefeed1 publishes: one column per level per side, bids0..bids10 and asks0..asks10. vectorize1 folds it into mkt_orderbook; a reader wanting a book usually wants that one instead";

/ The six declared-but-not-yet-produced tables (see plant_tables.q's block on
/ them). Described rather than hidden: a desk SHOULD be able to see that the
/ shape exists and what it is meant to hold - the description is the only
/ place that says so, since the table itself is empty and will stay empty
/ until something produces it. Each says so plainly rather than reading as a
/ table that merely happens to have no rows today.
describe[`predictions]:
    "Model output per (time, sym, horizon_ms, model). DECLARED, NOT YET PRODUCED - nothing in this tree runs a model, so the shape is agreed and the table is empty";
describe[`ccy_exposure]:
    "Net exposure per currency at an instant, revalued into one reporting currency. DECLARED, NOT YET PRODUCED - .qpos.ccy_exposure_in computes this, but no job publishes it yet";
describe[`reference_data]:
    "Static instrument reference per pair, base and quote legs as .qccy.ccy_pair_legs names them. DECLARED, NOT YET PRODUCED - no loader, and no decision yet on where the static data lives";
describe[`order_routing]:
    "Routing decisions, one row per (order, venue) - not 1:1 with orders, since an order can split. DECLARED, NOT YET PRODUCED - nothing in this tree routes";
describe[`connections]:
    "Venue connection registry, one row per venue link. NOT process liveness, which is .qetl.hb and `uqs summary`. DECLARED, NOT YET PRODUCED";
describe[`economic_calendar]:
    "Scheduled macro releases, actual null until the event fires. DECLARED, NOT YET PRODUCED - needs an external data source and a licence for it";

/ Tables that exist and are deliberately NOT browsable, each with the reason.
/ The reason is data rather than a comment so the test can require one: a
/ hidden table with no reason is how "we forgot" gets recorded as a decision.
hidden:(`symbol$())!();
hidden[`databento_mbp10]:
    "the RAW Databento feed, published by an external Python handler and consumed only by databento1, which folds it into eq_orderbook. A desk browsing a book wants the folded one; this is the input to that fold";
hidden[`fx_position_open]:
    "fxpositions1's book as each day opens (#943), published so a restart restores the carried positions from the day's log. A record for recovery, not a view: fx_position is the book to browse";
hidden[`position_open]:
    "posbook1's book as each day opens (#943), for the same recovery; position is the one to browse";
hidden[`kafka_client_flow]:
    "the RAW Kafka consumer output, published by an external Python consumer and read only by kafka_flow1, which deduplicates it into client_flow. It holds every redelivery the broker sent, so a desk reading it would see replayed trades twice - client_flow is the one to browse";

/ Browsable tables with NO row in scripts/torqconfig/dataaccess/querypolicy.csv,
/ each with the reason (#889). The browser's /query runs a whole select on
/ the tier and only then truncates to max_rows; the policy file's maxrange,
/ required filters and byte limit bind only gateway logins that are not
/ trusted, and the frontend logs in as one that is. So a browsable table
/ without a policy is read unbounded, and that has to be a decision, held
/ by tests/q/test_catalog.q: every browsable table is in the policy file or
/ here. A new table therefore cannot become browsable unbounded by omission.
/ .
/ "NOT YET ASSESSED" is the honest reason for most of them today: they were
/ browsable before this list existed, and nobody has measured their rate
/ and written a policy with its basis. Each one assessed leaves this list
/ for the policy file.
unbounded:(`symbol$())!();
unbounded[`predictions`ccy_exposure`reference_data`order_routing`connections`economic_calendar]:
    6#enlist "declared, not yet produced: no rows to bound. Its policy row is due with its first producer";
unbounded[`config_change]:
    enlist "one row per watched variable per change - a handful a day";
unbounded[`exec_bar]:
    enlist "one row per sym per minute - 1440 a day for a sym that trades all day, not ticks; its policy row is due once a desk reads it through the gateway";
unbounded[`fx_limit_breach]:
    enlist "one row per limit newly crossed, throttled so a standing breach does not repeat";
unbounded[`etl_coverage]:
    enlist "one ledger row per published window per dataset - hundreds a day, not ticks";
unbounded[`flink_vwap]:
    enlist "one row per sym per one-minute window Flink closes - a few thousand a day";
unbounded[`deal_positions]:
    enlist "one row per pair per published window of demo_deals";
unbounded[`arbitrage`cross_arbitrage`crypto_book`crypto_sim_fills`crypto_trades`eq_orderbook`demo_deals`event_tape`executions`fx_position`market_data`orders`position`fx_orderbook`superbook`trades`wide_orderbook`client_flow`crypto_market_data`trades_copy`crypto_execution_quality]:
    21#enlist "NOT YET ASSESSED (#889): browsable before this list existed; read with no time range or byte limit, only max_rows, until a querypolicy.csv row with a measured basis replaces this";

/ The browsable surface: every described table that is not hidden.
/ .
/ `except` on the keys rather than a filter over a table, because both sides
/ are dictionaries keyed by table name and that is the whole operation.
/ @return a table of `table (symbol) and `description (string), one row per
/   browsable table, in key order
/ @eg .qcat.surface[]
surface:{[]
    visible:(key describe) except key hidden;
    ([] table:visible; description:describe visible)};

\d .

/ --- scaffolded entries --------------------------------------------------
/ .
/ `uqs job new` appends here, below the `\d .` above rather than inside the
/ namespace block, because APPEND is the only write mode the scaffold has and
/ it writes at the end of the file.
/ .
/ FULLY QUALIFIED, and that is not decoration: a bare `describe[...]` at this
/ point would create a ROOT-level `describe` dictionary and the catalog would
/ silently not gain the table - which tests/q/test_catalog.q would then fail
/ on, naming the table as neither described nor hidden. Moving an entry up
/ into the block above is a tidy-up, never a fix.
.qcat.describe[`duckdb_deals]:
    "one mock FX deal - pair, side, notional and rate - backfilled from a DuckDB file over ODBC";
.qcat.describe[`deal_positions]:
    "Net FX notional per pair for one published window of demo_deals - buys add, sells take away - built by the rebuild_positions reaction as each window is backfilled, and written where the deals were. Appended per release: a re-published window adds its rows again, so read the latest";
.qcat.describe[`client_flow]:
    "One client FX trade consumed off a Kafka topic, deduplicated by kafka_flow1 on the (partition;offset) the record carries - so a broker redelivery does not show the desk the same trade twice. Carries those coordinates, so any row can be traced back to the exact Kafka record";
.qcat.describe[`crypto_market_data]:
    "One moment of a crypto pair on one venue, replayed out of cryptorust's own recorded DuckDB capture: five book levels a side as vectors, the trade printed alongside them, and both clocks - the venue's source_time and the recorder's local_time, whose difference is the wire lag. The repeatable counterpart to crypto_book, which the same recorder fills live and which carries source_time but not the recorder's local_time";
.qcat.describe[`trades_copy]:
    "One trade copied out of another kdb+ HDB on this machine by hdb_transfer_backfill, with its notional (price times size, in the quote currency) added on the way - the worked example of moving data from one kdb+ database into another; scripts/examples/hdb_transfer_example.q runs it end to end";
.qcat.describe[`crypto_execution_quality]:
    "Post-trade markout per real crypto fill per horizon, in bps against the best mid across venues; a null markout_bps means no venue had a live book at that horizon, not a zero markout";
.qcat.describe[`last_value]:
    "One change to a pair's current price: the newest level-0 bid, ask and mid across sources, FX and crypto, with the source that quoted it and its own source_time. Appended only when a newer book replaces the held one, so read the latest row per sym (select by sym) for the one shared current price";
.qcat.hidden[`flink_vwap_raw]:
    "the RAW flink_vwap records, published by flink_vwap_streamer.py outside q and read only by flink_vwap, which reshapes them into flink_vwap";
.qcat.describe[`flink_vwap]:
    "Volume-weighted average price, total volume and trade count for one sym over one tumbling window, computed by an Apache Flink job outside q; each window appears once, keyed by its window_end";
