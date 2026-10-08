/ rebuild_positions.q - net deal position per pair and window, recomputed when
/ demo_deals is published (.qpipe.job.rebuild_positions).
/ .
/ The first reaction the tree runs rather than only tests (#529): each time
/ demo_deals_backfill publishes a window, the rows of THAT window are summed
/ into `deal_positions`, one row per (sym; window), written where the deals
/ were - the HDB under `uqs backfill`. Nothing polls, and nothing recomputes
/ a window that did not change.
/ .
/ Runs inside whichever process publishes demo_deals - today deals_backfill1 -
/ once per published window, after the window's coverage is recorded. It has
/ no process of its own. A failure is recorded in .qetl.reaction.history and
/ never fails the publication; see src/etl/core/react.q.
/ .
/ `deal_positions`, not `positions`: the plant already carries `position`
/ (posbook1's output) and the library `.qpos`, and a third near-spelling of
/ one word is how a reader joins the wrong table.

\d .qpipe.job.rebuild_positions

/ Net notional by pair for [range_from;range_to): a buy adds, a sell takes
/ away. One row per (sym; window), with `time` the window's start - the
/ column the HDB writer partitions by.
/ .
/ READ AS PUBLISHED and WRITTEN WHERE THE WORKER WROTE (#541). The deals come
/ from .qetl.reaction.published, not the dataset by name, because under
/ `uqs backfill` the worker writes HDB partitions and there is no table to
/ name. The positions go out through .qetl.reaction.write - the worker's
/ own IO manager - because this runs inside the backfill process, which
/ exits when its range is done: a table kept here would go with it.
/ .
/ REPLACED per window, keyed by (sym; window): re-publishing a window - a
/ restatement or a re-run - leaves that window's positions as the latest
/ release computed them, one row per pair, as the worker's own upsert does
/ for demo_deals. A pair with no deals in the corrected window loses its row.
/ @param dataset the dataset just published, `demo_deals
/ @param range_from inclusive lower bound of the published window
/ @param range_to exclusive upper bound
/ @return the number of rows written for this window
/ @eg .qetl.reaction.notify_published[`demo_deals;2026.09.11D00:00;2026.09.12D00:00;1#.qpipe.source.demo_deals.fixture[];.qetl.io.memory]
handler:{[dataset;range_from;range_to]
    deals:.qetl.reaction.published[];
    / Netted by the library, not here (#885): .qdesk.apply_fills is the one
    / netting rule, and a change to its side convention reaches this too.
    / One call covers one window, so the book is keyed on sym alone (the
    / library's dimensions are symbols) and the window is added after.
    book:0!.qdesk.apply_fills[.qdesk.empty_book`sym;as_fills deals];
    net:select sym, window:range_from, net_notional:base_qty, deals:fill_count from book;
    .qetl.reaction.write[`deal_positions;`sym`window;`time`sym`window`net_notional`deals#update time:window from net]}

/ The deal source's side, as the library's +1 / -1.
side_sign:`buy`sell!1 -1

/ demo_deals rows as the fills .qdesk.apply_fills takes: the source spells
/ side `buy/`sell, converted ONCE here, at the edge, to +1/-1. A side that
/ is neither is refused - the inline netting this replaced counted it as a
/ sell, silently flipping a position.
/ @param deals demo_deals rows
/ @return sym, side (+1/-1), size (the notional) and price (the rate)
/ @throws error naming any side that is neither buy nor sell
/ @eg exec side from .qpipe.job.rebuild_positions.as_fills ([] sym:`EURUSD`EURUSD; side:`buy`sell; notional:1e6 2e6; rate:1.08 1.09) -> 1 -1
as_fills:{[deals]
    if[count bad:distinct (deals`side) except key side_sign;
        '"rebuild_positions: deal side must be buy or sell, not ",", " sv string bad];
    / A local, not the global by name: inside qSQL KDB-X resolves a bare
    / global at the ROOT, where no side_sign exists.
    sg:side_sign;
    select sym, side:sg side, size:notional, price:rate from deals}

\d .

/ on_writing, because this handler writes deal_positions: that puts it in the
/ job graph, where a cycle is refused at load. It is a CLAIM nothing checks -
/ keep it true when you change the handler.
.qetl.reaction.on_writing[`demo_deals;`rebuild_positions;enlist `deal_positions;.qpipe.job.rebuild_positions.handler];
