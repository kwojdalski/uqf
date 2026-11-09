/ demo_deals_feed.q - a polling feed over the demo_deals source: the STREAMING
/ producer of the demo_deals dataset (.qpipe.job.demo_deals_feed).
/ .
/ demo_deals_backfill fills the dataset by bounded windows; this feed fills the
/ same dataset by a cursor over the same source. They are the in-tree pair for
/ one dataset, which is what `uqs job check demo_deals` checks them as: each
/ against the destination's schema, and against each other. It is a generic
/ analogue (see src/etl/sources/demo_deals.q) and not a port of anything.
/ .
/ The cursor is the last row published, as (deal_time; deal_id) - a compound
/ cursor (#2458). A bare time lost a deal booked after a poll at the deal_time
/ that poll ended on (a block split into tickets at one instant): the next poll
/ read strictly after that time. Now the next poll reads from that time
/ inclusive and keeps the rows lexically after the cursor, so a later ticket at
/ the same instant, with a higher deal_id, is published. A deal back-dated
/ BELOW the cursor's time is still never fetched - the late-arrival gap any
/ polling cursor over a non-monotone timestamp has, and one `uqs job check`
/ reports.
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

/ Private: the cursor's fields, in the order the source's rows are sorted by.
/ @private
.qpipe.job.demo_deals_feed.cursor_fields:`deal_time`deal_id

/ [cursor] -> the deals after `cursor`, a table: a (deal_time; deal_id)
/ dictionary - (::) on a first run, which reads from the start. Through the
/ source's own window fetch from the cursor's time INCLUSIVE, so the rows are
/ held to the same contract a backfill window is, then cut to the rows
/ lexically after the cursor; the fixture when the source has no credential, a
/ live handle opened once otherwise.
/ @param cursor the deal_time and deal_id of the last row published, or (::)
/ @return the source's rows after the cursor, sorted by deal_time then deal_id
/ @eg count .qpipe.job.demo_deals_feed.fetch (::)  ->  5
/ @eg count .qpipe.job.demo_deals_feed.fetch `deal_time`deal_id!(2026.09.13D09:00:00.000000000;3)  ->  2
.qpipe.job.demo_deals_feed.fetch:{[cursor]
    if[(null .qpipe.job.demo_deals_feed.handle) and .qetl.source.has_credentials `demo_deals;
        cred:.qetl.source.require_credentials `demo_deals;
        `.qpipe.job.demo_deals_feed.handle set ((.qetl.source.for_source `demo_deals)`open) cred];
    first_run:(::)~cursor;
    start:$[first_run; -0Wp; cursor`deal_time];
    page:.qpipe.job.demo_deals_feed.cursor_fields xasc last .qetl.source.fetch_window[`demo_deals;.qpipe.job.demo_deals_feed.handle;start;0Wp];
    if[first_run; :page];
    later:page[`deal_time]>cursor`deal_time;
    tie:(page[`deal_time]=cursor`deal_time) and page[`deal_id]>cursor`deal_id;
    page where later or tie}

/ [page] -> the rows published to demo_deals, in the plant table's columns,
/ without `time` (the tickerplant stamps it): the page through the one transform
/ this feed and its backfill twin share, so a refill derives what the feed publishes.
/ @param page what fetch returned
/ @return the plant table's published columns, in order
/ @eg cols .qpipe.job.demo_deals_feed.normalize .qpipe.job.demo_deals_feed.fetch (::)  ->  `deal_id`deal_time`sym`side`notional`rate
.qpipe.job.demo_deals_feed.normalize:{[page]
    .qetl.transform.apply[`demo_deals_passthrough;enlist[`batch]!enlist (cols .qetl.plant.published `demo_deals)#page]}

/ [page] -> the cursor that acknowledges the page: its last row's deal_time and deal_id.
/ @param page what fetch returned, ordered by deal_time then deal_id
/ @return a dictionary of deal_time and deal_id
/ @eg .qpipe.job.demo_deals_feed.next_cursor .qpipe.job.demo_deals_feed.fetch (::)  ->  `deal_time`deal_id!(2026.09.15D09:00:00.000000000;5)
.qpipe.job.demo_deals_feed.next_cursor:{[page] .qpipe.job.demo_deals_feed.cursor_fields#last page}

/ [instant] -> the cursor just before `instant`: fetching after it returns the
/ deals at or after it, every deal_id at `instant` included.
/ @param instant a timestamp
/ @return a dictionary of deal_time and deal_id
/ @eg .qpipe.job.demo_deals_feed.start_cursor 2026.09.13D09:00:00.000000000  ->  `deal_time`deal_id!(2026.09.13D08:59:59.999999999;0W)
.qpipe.job.demo_deals_feed.start_cursor:{[instant] .qpipe.job.demo_deals_feed.cursor_fields!(instant-0D00:00:00.000000001;0W)}

\d .

/ The process registry is read from this declaration: `procname` is the
/ process that runs it, and `start_with_all` whether `uqs start all` starts it
/ (absent: on demand, until the connection budget has room).
.qetl.job.stream.define[`demo_deals_feed;`procname`subscribe_to`outputs`period`poll`transform`note!(
    `demo_deals_feed1;
    `symbol$();
    enlist `demo_deals;
    0D00:00:01;
    `fetch`normalize`next_cursor`source`close`row_key`load`save`advances`start_cursor!(
        .qpipe.job.demo_deals_feed.fetch;
        .qpipe.job.demo_deals_feed.normalize;
        .qpipe.job.demo_deals_feed.next_cursor;
        `demo_deals;
        .qpipe.job.demo_deals_feed.close;
        `deal_id;
        .qetl.job.continuous.load_cursor_value;
        .qetl.job.continuous.save_cursor_value;
        .qetl.job.continuous.lexically_after[.qpipe.job.demo_deals_feed.cursor_fields];
        .qpipe.job.demo_deals_feed.start_cursor);
    `demo_deals_passthrough;
    "polling feed over the demo_deals source, the streaming twin of demo_deals_backfill - started on demand, as an example of the pair `uqs job check` checks")];
