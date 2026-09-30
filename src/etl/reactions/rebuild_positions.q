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
/ APPEND, like the dataset it is computed from: re-publishing a window adds
/ that release's positions again rather than replacing the first. That is
/ how every write through an IO manager behaves in this tree; a reader that
/ wants one answer per window takes the latest.
/ @param dataset the dataset just published, `demo_deals
/ @param range_from inclusive lower bound of the published window
/ @param range_to exclusive upper bound
/ @return the number of rows written for this window
/ @eg .qetl.reaction.notify_published[`demo_deals;2026.09.11D00:00;2026.09.12D00:00;1#.qpipe.source.demo_deals.fixture[];.qetl.io.memory]
handler:{[dataset;range_from;range_to]
    deals:.qetl.reaction.published[];
    net:0!select net_notional:sum notional*?[side=`buy;1f;-1f], deals:count i
        by sym, window:range_from from deals;
    .qetl.reaction.write[`deal_positions;`time`sym`window`net_notional`deals#update time:window from net]}

\d .

/ on_writing, because this handler writes deal_positions: that puts it in the
/ job graph, where a cycle is refused at load. It is a CLAIM nothing checks -
/ keep it true when you change the handler.
.qetl.reaction.on_writing[`demo_deals;`rebuild_positions;enlist `deal_positions;.qpipe.job.rebuild_positions.handler];
