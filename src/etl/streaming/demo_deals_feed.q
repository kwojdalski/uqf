/ demo_deals_feed.q - a polling feed over the demo_deals source: the STREAMING
/ producer of the demo_deals dataset (.qpipe.job.demo_deals_feed).
/ .
/ demo_deals_backfill fills the dataset by bounded windows; this feed fills the
/ same dataset by a cursor over the same source. They are the in-tree pair for
/ one dataset, which is what `uqs job check demo_deals` checks them as: each
/ against the destination's schema, and against each other. It is a generic
/ analogue (see src/etl/sources/demo_deals.q) and not a port of anything.
/ .
/ The cursor is the time of the last row published, so a deal that arrives
/ AFTER the cursor with an earlier or equal deal_time is never fetched - the
/ late-arrival gap a polling cursor has, and one `uqs job check` reports.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
.qpipe.job.demo_deals_feed.publish:.qetl.job.stream.unwired `demo_deals_feed;

/ The source handle a live feed holds, 0Ni on the fixture.
.qpipe.job.demo_deals_feed.handle:0Ni

/ Private: release what fetch opened. @private
.qpipe.job.demo_deals_feed.close:{[]
    if[not null .qpipe.job.demo_deals_feed.handle;
        ((.qetl.source.for_source `demo_deals)`close) .qpipe.job.demo_deals_feed.handle;
        `.qpipe.job.demo_deals_feed.handle set 0Ni];}

/ [cursor] -> the deals after `cursor`, a table: a timestamp - null on a first
/ run, which reads from the start. Through the source's own window fetch, so the
/ rows are held to the same contract and the same half-open bounds a backfill
/ window is; the fixture when the source has no credential, a live handle
/ opened once otherwise.
/ @param cursor the deal_time of the last row published, or null
/ @return the source's rows with deal_time after the cursor, a table
/ @eg count .qpipe.job.demo_deals_feed.fetch 0Np  ->  5
.qpipe.job.demo_deals_feed.fetch:{[cursor]
    if[(null .qpipe.job.demo_deals_feed.handle) and .qetl.source.has_credentials `demo_deals;
        cred:.qetl.source.require_credentials `demo_deals;
        `.qpipe.job.demo_deals_feed.handle set ((.qetl.source.for_source `demo_deals)`open) cred];
    start:$[null cursor; -0Wp; cursor+1];
    `deal_time xasc last .qetl.source.fetch_window[`demo_deals;.qpipe.job.demo_deals_feed.handle;start;0Wp]}

/ [page] -> the rows published to demo_deals, in the plant table's columns,
/ without `time` (the tickerplant stamps it): the page through the one transform
/ this feed and its backfill twin share, so a refill derives what the feed publishes.
/ @param page what fetch returned
/ @return the plant table's published columns, in order
/ @eg cols .qpipe.job.demo_deals_feed.normalize .qpipe.job.demo_deals_feed.fetch 0Np  ->  `deal_id`deal_time`sym`side`notional`rate
.qpipe.job.demo_deals_feed.normalize:{[page]
    .qetl.transform.apply[`demo_deals_passthrough;enlist[`batch]!enlist (cols .qetl.plant.published `demo_deals)#page]}

/ [page] -> the cursor that acknowledges the page: the time of its last row.
/ @param page what fetch returned, ordered by deal_time
/ @return a timestamp
/ @eg .qpipe.job.demo_deals_feed.next_cursor .qpipe.job.demo_deals_feed.fetch 0Np  ->  2026.09.15D09:00:00.000000000
.qpipe.job.demo_deals_feed.next_cursor:{[page] last page`deal_time}

\d .

/ The process registry is read from this declaration: `procname` is the
/ process that runs it, and `start_with_all` whether `uqs start all` starts it
/ (absent: on demand, until the connection budget has room).
.qetl.job.stream.define[`demo_deals_feed;`procname`subscribe_to`publishes`period`poll`transform`note!(
    `demo_deals_feed1;
    `symbol$();
    enlist `demo_deals;
    0D00:00:01;
    `fetch`normalize`next_cursor`source`close`row_key!(
        .qpipe.job.demo_deals_feed.fetch;
        .qpipe.job.demo_deals_feed.normalize;
        .qpipe.job.demo_deals_feed.next_cursor;
        `demo_deals;
        .qpipe.job.demo_deals_feed.close;
        `deal_id);
    `demo_deals_passthrough;
    "polling feed over the demo_deals source, the streaming twin of demo_deals_backfill - started on demand, as an example of the pair `uqs job check` checks")];
