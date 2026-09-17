/ torq_posbook_etl.q - proof-of-concept "ETL" process for the uqf stack,
/ alongside torq_cross_etl.q/torq_vectorize_etl.q's own new rows:
/ subscribes to torq_fx_trades_feed.q's `trades` table and, per fill,
/ folds it through uqf's own .qpos.apply_fill (src/portfolio/positions.q) to
/ maintain a running position book, then marks it to the prevailing mid
/ (from the vendored `quote` table, which torq_fx_feed.q also writes FX
/ top-of-book into) via .qrisk.pnl (src/portfolio/risk.q) for a live unrealized
/ P&L - this is the process that closes the gap the uqf stack previously
/ had: every other feed/ETL here moves market-data shapes around, none of
/ them ran uqf's actual position/risk logic against live data.
/ .
/ Mark-to-mid, not mark-to-last-trade: an earlier version of this process
/ marked to whatever price the fill itself just traded at, to avoid a
/ second subscription - but that meant unrealized_pnl tracked the most
/ recent random fill rather than the market, which isn't how a real eFX
/ book marks P&L. `.posbook.last_mid` (updated off the same `quote`
/ subscription torq_markout_etl.q also uses) fixes that; falls back to
/ the fill's own trade_price only for a sym with no quote seen yet.
/ .
/ `position` (POSITION_TABLE_SCHEMA in python/torq_orchestrator/src/
/ torq_orchestrator/core.py) is republished onto the tickerplant like
/ vectorize1's `mkt_orderbook` - a normal database table, not private
/ process state like cross1's cross_quotes - so position/PnL history
/ survives past posbook1 restarting and flows through rdb1/wdb1/hdb like
/ any vendored table. `.posbook.book` itself (the keyed running position
/ book apply_fill threads through) is never published directly - it's
/ type 99h (keyed), which the tickerplant's upd/.u.upd machinery rejects
/ (Rule S7) - only the flat position snapshot below is.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+30 - see POSBOOK_PORT_OFFSET in core.py).
/ e.g. `uqf-stack query "select from position" --port <base+2>` (rdb1).

/ pull in uqf's own src/init.q and the stream transforms FIRST -
/ .posbook.book below is built from the transform's declared book table.
.qpipe.load_uqf[];

\d .posbook

/ the running position book - .qpos's own keyed shape (sym -> qty/
/ avg_price/realized_pnl). The `position` transform takes it as an input
/ and upd rebuilds it from the transform's output, so the book only ever
/ changes through a computation that has expected tables.
book:1!.qstream.position_book;

/ last-seen mid per sym, off the `quote` subscription - updated on every
/ quote tick, read (with a trade_price fallback for a sym never quoted
/ yet) when marking a fill. Plain dict, not a table: only ever needs a
/ point lookup by sym, never queried as a table.
last_mid:(`symbol$())!`float$();

\d .

/ receive trades/quote ticks from the tickerplant subscription - x
/ arrives as an actual table. Trades: the `position` transform
/ (src/etl/transforms/stream.q) applies every fill in the batch in arrival
/ order - already time order off the tickerplant - marks each to the
/ current last_mid, and returns one position row per fill. The book is
/ rebuilt from those rows BEFORE publishing, as it was updated before
/ publishing when this was inline: the fills will not be redelivered, so a
/ failed publish must not also lose them from the book. Quotes: just
/ refresh last_mid.
upd:{[t;x]
  $[t=`trades;
    [out:.qxf.apply[`position;`book`trades`marks!(
        0!.posbook.book;
        select time,sym,side,trade_price,size,pip_factor from x;
        ([] sym:key .posbook.last_mid; mid:value .posbook.last_mid))];
     `.posbook.book set 1!.qstream.next_book[0!.posbook.book;out];
     .qpipe.publish[h;`position;out]];
   t=`quote;
    .posbook.last_mid[x`sym]:((x`bid)+x`ask)%2;
   ()];
 }

/ SOURCE + SINK in one call.
/ .
/ .qpipe.subscribe_etl does the sequence this file used to spell out: set
/ .servers.CONNECTIONS, .servers.startup[] (which opens the live,
/ access-listed handle to stp1 - posbook1's proctype "metrics" in process.csv (core.py) borrows an
/ already-credentialed type for that.), block on
/ startupdepcycles until the tickerplant is confirmed up, find it, and
/ subscribe. It THROWS when no tickerplant is found, where the hand-rolled
/ version returned an empty list and left this process subscribed to
/ nothing while still reporting healthy.
/ .
/ The return is the publish handle this file used to acquire separately with
/ .servers.gethandlebytype - the same unauthenticated handle, from the same
/ call that already blocked until stp1 was up.
h:.qpipe.subscribe_etl[`posbook;`trades`quote];
