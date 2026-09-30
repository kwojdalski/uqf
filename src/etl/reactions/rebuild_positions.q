/ rebuild_positions.q - net deal position per pair and window, recomputed when
/ demo_deals is published (.qpipe.job.rebuild_positions).
/ .
/ The first reaction the tree runs rather than only tests (#529): each time
/ demo_deals_backfill publishes a window, the rows of THAT window are summed
/ into `deal_positions`, keyed by (sym; window). Nothing polls, and nothing
/ recomputes a window that did not change.
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
/ away. Keyed by the WINDOW as well as the pair, so a re-published window
/ replaces its own rows instead of adding to them - its old rows are dropped
/ first, which also takes out a pair the restatement no longer carries.
/ .
/ The deals are READ AS PUBLISHED, through .qetl.reaction.published, and not
/ by naming the dataset: under `uqs backfill` the worker writes HDB
/ partitions, and a handler that read `. `demo_deals found no table and
/ failed on every window, silently (#541). The published rows are the
/ window's rows whatever the IO manager was.
/ @param dataset the dataset just published, `demo_deals
/ @param range_from inclusive lower bound of the published window
/ @param range_to exclusive upper bound
/ @return the number of pairs written for this window
/ @eg .qetl.reaction.notify_rows[`demo_deals;2026.09.11D00:00;2026.09.12D00:00;1#.qpipe.source.demo_deals.fixture[]]
handler:{[dataset;range_from;range_to]
    deals:.qetl.reaction.published[];
    net:select net_notional:sum notional*?[side=`buy;1f;-1f], deals:count i
        by sym, window:range_from from deals;
    delete from `deal_positions where window=range_from;
    `deal_positions upsert net;
    count net}

\d .

/ What `handler` writes, keyed by (sym; window). Defined at load, and only when
/ absent, so reloading this file does not wipe positions already built.
if[not `deal_positions in key `.;
    `deal_positions set ([sym:`symbol$(); window:`timestamp$()] net_notional:`float$(); deals:`long$())];

/ on_writing, because this handler writes deal_positions: that puts it in the
/ job graph, where a cycle is refused at load. It is a CLAIM nothing checks -
/ keep it true when you change the handler.
.qetl.reaction.on_writing[`demo_deals;`rebuild_positions;enlist `deal_positions;.qpipe.job.rebuild_positions.handler];
