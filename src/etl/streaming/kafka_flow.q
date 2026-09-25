/ kafka_flow.q - client FX flow off a Kafka topic, deduplicated on the
/ record's own coordinates (.qpipe.job.kafka_flow).
/ .
/ Reads `kafka_client_flow`; publishes `client_flow`.
/ .
/ WHY THIS EXISTS, given the stack already has feeds
/ .
/ fx_feed, crypto_mock and databento_book already demonstrate a subscriber,
/ so a fourth would earn little. What Kafka brings that none of them face is
/ that a topic and a tickerplant log are THE SAME IDEA - two ordered,
/ replayable logs - and joining one to the other forces a question this tree
/ had no worked answer for: where does the offset commit sit relative to
/ .u.upd?
/ .
/ There are only two places to put it, and neither is free:
/ .
/   Commit BEFORE publishing is at-most-once. A crash in the gap loses the
/   record permanently, and nothing downstream can tell: the plant simply
/   never sees a row that existed. A silent hole in client flow is not
/   recoverable, and not detectable either.
/ .
/   Commit AFTER publishing is at-least-once. A crash in the gap replays the
/   record, and the plant sees it twice. A duplicate IS detectable, because
/   the record carries coordinates that are unique in the topic.
/ .
/ This example takes the second, on the rule that a recoverable failure beats
/ an undetectable one - and then has to actually do the recovering, which is
/ what this file is. external/kafka_feed.py commits only after .u.upd has
/ returned; this job drops anything it has already seen.
/ .
/ THAT IS WHY `partition` AND `offset` ARE COLUMNS. They are not consumer
/ bookkeeping that should have stayed in Python: the plant is the thing that
/ has to survive the redelivery, so the coordinates have to travel with the
/ row. They stay on the output too, so a desk querying client_flow can point
/ at any row and name the exact Kafka record it came from.
/ .
/ WHAT THIS DOES NOT SURVIVE, stated plainly: the high-water marks below are
/ this PROCESS's state. Restart kafka_flow1 and they are empty, so a replay
/ that straddles the restart is not caught. Seeding them from the plant on
/ startup is the obvious fix and is deliberately not done here - it needs a
/ query against rdb1 at wire time, which no other .qetl.job.stream job does, and
/ inventing that seam for an example would be the tail wagging the dog.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qpipe.job.kafka_flow

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `kafka_flow;

/ ------------------------------------------------------------- THE STATE

/ partition -> the highest offset already published from it.
/ .
/ Unlike a per-symbol cache this cannot grow without bound: a topic has a
/ fixed partition count, so this dict is as big as the topic is wide and
/ never bigger. There is nothing to evict.
high_water:(`long$())!`long$();

/ ------------------------------------------------------------- THE DEDUPE

/ Private: the rows of a batch this process has not published before.
/ .
/ THE NULL COMPARISON IS LOAD-BEARING. `high_water[p]` on a partition never
/ seen returns 0N, and in q `5 > 0N` is 1b - null long compares below every
/ value - so an unseen partition keeps every row with no branch. Writing it
/ as an explicit `p in key high_water` test would say the same thing louder;
/ writing it as `0^high_water[p]` would be WRONG, because offset 0 is a real
/ record and `0 > 0` drops it.
/ @param rows a batch, carrying `partition` and `offset`
/ @return the rows whose offset is above their partition's mark
above_high_water:{[rows]
    rows where rows[`offset] > .qpipe.job.kafka_flow.high_water rows`partition}

/ Private: one row per (partition;offset) in the batch, the first kept.
/ .
/ Belt as well as braces. A single Kafka fetch does not repeat a coordinate,
/ so in normal running this changes nothing - but a consumer that restarts
/ mid-batch and replays into the same buffer can, and a duplicate that got
/ past both this and the high-water mark would be indistinguishable from a
/ genuine second trade. `asc` on the indices because `group` returns them in
/ key order, not batch order, and the batch's own order is the topic's.
/ @param rows a batch, carrying `partition` and `offset`
/ @return the rows, with any repeated (partition;offset) reduced to its first
first_per_coordinate:{[rows]
    rows asc value {first each group flip (x`partition;x`offset)}[rows]}

/ Private: raise each partition's mark to the highest offset in the batch.
/ .
/ `|` against the previous mark rather than a plain upsert: the mark must
/ never move BACKWARDS. A batch arriving out of order would otherwise lower
/ it and re-admit every record between the two, which is precisely the
/ duplicate this job exists to stop.
/ @param rows the batch about to be published
/ @return nothing - it updates `high_water` in place
advance:{[rows]
    m:exec max offset by partition from rows;
    hw:.qpipe.job.kafka_flow.high_water;
    `.qpipe.job.kafka_flow.high_water set hw,(key m)!(hw key m)|value m;
    }

/ ---------------------------------------------------------------- THE JOB

/ Publish the records of this batch that have not been published before.
/ .
/ The batch arrives with the tickerplant's own `time` prepended, which the
/ output shape does not carry; it is dropped so .u.upd can stamp a fresh one
/ on the way back in. The broker's clock travels as `broker_time`, for the
/ reason databento_book keeps ts_event: `time` says when we HEARD about the
/ record, and their difference is the feed's lag.
/ .
/ Publishing nothing is a normal outcome, not a failure - a batch that is
/ entirely a replay is exactly what this job is for, and an empty publish
/ would put a zero-row write on the plant for no reason.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    if[not t=`kafka_client_flow; :()];
    if[0=count x; :()];
    rows:$[`time in cols x; ![x;();0b;enlist `time]; x];
    fresh:.qpipe.job.kafka_flow.first_per_coordinate .qpipe.job.kafka_flow.above_high_water rows;
    if[0=count fresh; :()];
    .qpipe.job.kafka_flow.advance fresh;
    out:select broker_time, sym, side, qty, price, client, trade_id, partition, offset from fresh;
    .qpipe.job.kafka_flow.publish[`client_flow;out];
    }

\d .

.qetl.job.stream.define[`kafka_flow;`procname`subscribe_to`publishes`on_batch`note!(
    `kafka_flow1;
    `kafka_client_flow;
    enlist `client_flow;
    .qpipe.job.kafka_flow.on_batch;
    "deduplicates client FX flow consumed off a Kafka topic, on the (partition;offset) the record carries. The raw rows are published by an EXTERNAL Python consumer (external/kafka_feed.py) - a q process cannot hold a Kafka subscription - so kafka_client_flow has a schema row but no producer in this list. That is why it does not start with the stack: on a default start nothing publishes the table it subscribes to, and it would hold one of the sixteen licensed plant connections to consume nothing. Start it with the consumer")];
