/ torq_vectorize_etl.q - proof-of-concept ETL: subscribes to
/ torq_wide_book_feed.q's `wide_book` table (bids0..bids10/asks0..asks10,
/ one scalar column per depth level - the "incorrectly-ingested wide order
/ book" shape src/market_data/book.q's own header describes) and, on every batch,
/ folds it into forwards.q's vector-column book shape via uqf's own
/ .qbook.book_from_wide_levels/derive_level_groups - then publishes the
/ result back onto the tickerplant as a second table, `mkt_orderbook`
/ (bid_prices/ask_prices as level-0-first vectors). A literal "insert
/ records from a wide table into a vectorized version of it", transform
/ done by uqf rather than a straight copy - same proof-of-concept shape as
/ torq_cross_etl.q/cross1, exercising a different uqf module (book.q
/ instead of forwards.q's cross_book_at).
/ .
/ mkt_orderbook is a normal database table, not private state: like
/ quotes/wide_book, its schema is generated into database.q
/ (MKT_ORDERBOOK_TABLE_SCHEMA in core.py) and it's published through stp1,
/ so it flows through rdb1/wdb1/hdb exactly like any vendored table -
/ query it via rdb1 (or any other subscriber), not by connecting to
/ vectorize1 directly.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+27 - see VECTORIZE_ETL_PORT_OFFSET in
/ core.py). e.g. `uqf-stack query "select from mkt_orderbook" --port
/ <base+2>` (rdb1).

/ pull in uqf's own src/init.q and the stream transforms. The wide_book
/ schema and its level groups now live with the `mkt_orderbook` transform,
/ in .qstream.wide_book and .qstream.wide_level_groups.
.qpipe.load_uqf[];

/ receive wide_book ticks from the tickerplant subscription - x arrives
/ here as an actual table already matching wide_book's schema (confirmed
/ live via `type x` = 98h) - fold each batch with the `mkt_orderbook`
/ transform (src/etl/transforms/stream.q), then republish it onto the
/ tickerplant as mkt_orderbook (h, set up at the bottom) rather than keeping
/ it in a private local table. The transform's output carries no `time`;
/ .u.upd stamps its own on receipt, same as every other feed here.
upd:{[t;x]
  if[t=`wide_book;
    .qpipe.publish[h;`mkt_orderbook;.qxf.apply[`mkt_orderbook;enlist[`book]!enlist x]]];
 }

/ SOURCE + SINK in one call.
/ .
/ .qpipe.subscribe_etl does the sequence this file used to spell out: set
/ .servers.CONNECTIONS, .servers.startup[] (which opens the live,
/ access-listed handle to stp1 - vectorize1's proctype "metrics" in process.csv (core.py) borrows an
/ already-credentialed type for that, same as cross1.), block on
/ startupdepcycles until the tickerplant is confirmed up, find it, and
/ subscribe. It THROWS when no tickerplant is found, where the hand-rolled
/ version returned an empty list and left this process subscribed to
/ nothing while still reporting healthy.
/ .
/ The return is the publish handle this file used to acquire separately with
/ .servers.gethandlebytype - the same unauthenticated handle, from the same
/ call that already blocked until stp1 was up.
h:.qpipe.subscribe_etl[`vectorize;`wide_book];
