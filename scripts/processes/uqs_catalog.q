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
/ drift test to keep it honest against scripts/processes/uqs_tables.q. That
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
    "Live venue order books published by cryptorust's kdb-market-data-recorder (uqs crypto start): top-of-book and depth per venue and symbol as per-row level vectors, the same shape as quotes. time is stamped by the tickerplant on receipt";
describe[`crypto_sim_fills]:
    "Simulated (paper) fills from cryptorust's OMS fill model, run against live market data - not confirmed executions; those are crypto_trades. Kept apart so a P&L number always says which of the two it came from";
describe[`crypto_trades]:
    "Real confirmed exchange executions recorded from the OMS";
describe[`databento_book]:
    "Live Databento MBP-10 folded into the book shape by databento1, using the same .qxf transform the ODBC backfill applies - so a live row and a backfilled one are the same shape";
describe[`demo_deals]:
    "Generic analogue of an external relational deal source, landed by the demo_deals_backfill bounded worker. Synthetic by design - the real source is bank-internal and out of scope for this repository";
describe[`etl_coverage]:
    "Append-only completeness ledger: which [range_from, range_to) window of which dataset is published, at which source_version. A window with rows_published=0 still counts as covered - that is what distinguishes 'ran, found nothing' from 'never ran'";
describe[`event_tape]:
    "Per-event order/trade tape: add, cancel and trade events with the aggressor side on a trade. A superset of trades, so a tape filtered to action='trade' is trade-shaped. Sorted ascending by time by contract - see docs/architecture/event-tape.md";
describe[`execution_quality]:
    "Post-trade markout per fill per horizon; a null markout_pips means no reference quote existed at that horizon, not a zero markout";
describe[`executions]:
    "Every fill table the stack carries, spelled one way by the executions normalizer: the FX trades and cryptorust's crypto_trades. source_time is the source's own stamp, time the plant's on the normalized row. This is what posbook1 reads";
describe[`fx_limit_breach]:
    "fxpositions1's alerts: one row per limit newly crossed, throttled so a standing breach does not republish on every tick. utilisation is the observed value over the cap";
describe[`fx_position]:
    "fxpositions1's snapshot: net exposure per (sym, book, product), republished whole on every timer tick rather than only what moved. base_qty and quote_qty are both carried because an FX position is two currencies and reporting one hides a cross; break_even is the rate at which closing out leaves the desk flat";
describe[`market_data]:
    "Direct FX full snapshots per pair and source, normalized from quote and quotes. source_time preserves original receipt time; zero size or empty sides withdraw liquidity";
describe[`marks]:
    "A mid per instrument from every book the stack carries, spelled one way by the marks normalizer: the vendored quote and crypto_book. This is what posbook1 marks positions against";
describe[`mkt_orderbook]:
    "vectorize1's output: wide_book folded into the vector-column shape every pricing and execution function in src/ expects, one row per (time, sym)";
describe[`orders]:
    "Order flow from fx_orders_feed: every order, most of which never becomes a fill. order_status is what fxpositions1 filters on; book and product are the dimensions a position is keyed on beyond sym. For the fills these became, see executions";
describe[`position]:
    "Running position book marked to the prevailing mid, with realised and unrealised P&L";
describe[`quotes]:
    "FX top-of-book and depth as per-row level vectors";
describe[`superbook]:
    "Latest fresh direct FX liquidity merged across sources, bids descending and asks ascending, with aligned source and original timestamp vectors. Empty snapshots clear expired books";
describe[`trades]:
    "Client fills, shaped to match .qpos.apply_fill and .qexec.markout_at_horizons exactly";
describe[`wide_book]:
    "The unfolded book widefeed1 publishes: one column per level per side, bids0..bids10 and asks0..asks10. vectorize1 folds it into mkt_orderbook; a reader wanting a book usually wants that one instead";

/ Tables that exist and are deliberately NOT browsable, each with the reason.
/ The reason is data rather than a comment so the test can require one: a
/ hidden table with no reason is how "we forgot" gets recorded as a decision.
hidden:(`symbol$())!();
hidden[`databento_mbp10]:
    "the RAW Databento feed, published by an external Python handler and consumed only by databento1, which folds it into databento_book. A desk browsing a book wants the folded one; this is the input to that fold";

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
/ `uqs new-job` appends here, below the `\d .` above rather than inside the
/ namespace block, because APPEND is the only write mode the scaffold has and
/ it writes at the end of the file.
/ .
/ FULLY QUALIFIED, and that is not decoration: a bare `describe[...]` at this
/ point would create a ROOT-level `describe` dictionary and the catalog would
/ silently not gain the table - which tests/q/test_catalog.q would then fail
/ on, naming the table as neither described nor hidden. Moving an entry up
/ into the block above is a tidy-up, never a fix.
