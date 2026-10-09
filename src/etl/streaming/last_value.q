/ last_value.q - the current price of every sym, in one place (.qpipe.job.last_value).
/ .
/ Reads `market_data`; publishes `last_value`: one row for every change to a
/ sym's latest top of book. "The current price of EURUSD" is the last row per
/ sym - `select by sym from last_value` - from the RDB or through the
/ gateway, so a consumer and the browser read the same answer instead of
/ each keeping its own cache (posbook's last_mid is one such).
/ .
/ WHY A PUBLISHED TABLE, NOT A TABLE HELD IN THIS PROCESS. The gateway
/ serves the RDB, and the RDB serves what the plant publishes; a keyed table
/ inside this process would be reachable by nobody but a direct connection to
/ it. The latest-row-per-sym is therefore a query, the same one
/ docs/services/superbook.md gives for `arbitrage`, over a table that only
/ grows by an actual change. The job keeps the keyed state it needs to know
/ what changed, and rebuilds it by replay (a restart does not start it
/ empty), which the published table cannot do for it.
/ .
/ WHICH ROW WINS. For an FX sym, the newest source_time per sym, whatever
/ the source: the row says which `source` it was.
/ .
/ A CRYPTO SYM IS PRICED ACROSS VENUES (#990). Its row is the shared
/ reference .qmicro.best_across_venues gives - the best bid and best ask over
/ every venue quoting within .qmicro.reference_max_age, and their mid - with
/ `source` `venues. That is the price crypto_markout and posbook already use
/ (#886); the last venue's own book would be a third answer to "the current
/ price", and this table exists to be the one. So the job also holds the
/ newest top per (sym; venue), each venue's own book never rewound. A sym
/ with no live venue left has no row published for it. A row OLDER than the sym's current one is
/ dropped, so a delayed or replayed book cannot rewind the price; an equal
/ source_time follows arrival order, as superbook's does. Prices are level 0
/ of the book, and a book with an empty or non-positive side is dropped, not
/ marked at null: an empty ladder is that source withdrawing, and the last
/ price is the better answer than none (posbook's rule too).
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qpipe.job.last_value

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `last_value;

last_value:.qetl.plant.published `last_value
market_data:.qetl.plant.shape `market_data

/ The latest row per sym: what the job compares an arriving book with.
/ Rebuilt by replay after a restart.
state:1!last_value

/ The level-0 top of book of each usable book in a market_data batch.
/ @param x market_data rows
/ @return table sym, market, source, source_time, bid, ask, mid, in batch order
/ @eg .qpipe.job.last_value.tops ([] sym:`EURUSD`GBPUSD; market:`fx`fx; source:`a`a; source_time:2#2026.09.17D10:00:00; bid_prices:(enlist 1.0;`float$()); bid_sizes:(enlist 1f;`float$()); ask_prices:(enlist 1.2;enlist 1.3); ask_sizes:(enlist 1f;enlist 1f))  ->  ([] sym:enlist `EURUSD; market:enlist `fx; source:enlist `a; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1f; ask:enlist 1.2; mid:enlist 1.1)
tops:{[x]
    s:.qbook.top_sides x;
    t:select sym, market, source, source_time, bid:s`bid, ask:s`ask from x;
    t:select from t where not null bid, not null ask;
    update mid:(bid+ask)%2 from t}

/ Apply a batch of books to the latest rows.
/ .
/ Within the batch the newest source_time per sym wins (the last arrival on a
/ tie); against the state, a row older than the held one is dropped.
/ @param state the latest row per sym, keyed by sym
/ @param tob each crypto venue's newest top, keyed by sym and venue
/ @param batch market_data rows
/ @return a dict: `state the new keyed state, `tob the new venue tops, `changed the rows that replaced
/   or added a sym's latest, as published
/ @eg .qpipe.job.last_value.apply[.qpipe.job.last_value.state;.qpipe.job.last_value.tob;0#.qpipe.job.last_value.market_data]`changed  ->  0#.qpipe.job.last_value.last_value
apply:{[state;tob;batch]
    t:tops batch;
    / crypto: each venue's newest top, then the cross-venue reference per sym
    c:select from t where market=`crypto;
    c:0!select by sym, venue:source from `source_time xasc c;
    held:(exec (sym,'venue)!time from 0!tob) c[`sym],'c`venue;
    c:c where (null held) or c[`source_time]>=held;
    tob:tob upsert select sym, venue, time:source_time, bid, ask from c;
    syms:distinct c`sym;
    at:0!select time:max time by sym from tob where sym in syms;
    best:.qmicro.best_across_venues[0!tob;at;.qmicro.reference_max_age];
    crypto:select from ([] sym:at`sym; market:`crypto; source:`venues; source_time:at`time;
        bid:best`bid; ask:best`ask; mid:best`mid) where not null mid;
    t:(select from t where market<>`crypto),crypto;
    t:0!select by sym from `source_time xasc t;
    held:(exec sym!source_time from 0!state) t`sym;
    t:t where (null held) or (t`source_time)>=held;
    `state`tob`changed!(state upsert t;tob;cols[.qpipe.job.last_value.last_value]#t)}

/ Each crypto venue's newest top of book: what a crypto sym's reference is
/ priced from. Rebuilt by replay after a restart.
tob:2!([] sym:`symbol$(); venue:`symbol$(); time:`timestamp$(); bid:`float$(); ask:`float$())

/ Market data: hold the newest top of book per sym and publish what changed.
/ The state is advanced BEFORE publishing, as posbook's book is: the books
/ will not be redelivered, so a failed publish must not also lose them.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    if[not t=`market_data; :()];
    r:apply[.qpipe.job.last_value.state;.qpipe.job.last_value.tob;x];
    `.qpipe.job.last_value.state set r`state;
    `.qpipe.job.last_value.tob set r`tob;
    if[count r`changed; .qpipe.job.last_value.publish[`last_value;r`changed]];
    }

\d .

/ The state is this process's memory, so a restart used to start it empty
/ until every sym ticked again. Replaying the day's market_data rebuilds it;
/ publish is muted while it does, so rows already published are not repeated.
.qetl.job.stream.define[`last_value;`procname`subscribe_to`publishes`on_batch`replay`note`state!(
    `last_value1;
    enlist `market_data;
    enlist `last_value;
    .qpipe.job.last_value.on_batch;
    1b;
    "the newest top of book per sym, FX and crypto, as a published table: read the last row per sym (select by sym) for one shared answer to the current price instead of each consumer's own cache; on demand, in the fx profile";
    enlist `state)];
