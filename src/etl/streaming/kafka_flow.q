/ kafka_flow.q - client FX flow off a Kafka topic, deduplicated on the
/ record's own coordinates (.qpipe.job.kafka_flow).
/ .
/ Reads `kafka_client_flow`; publishes `client_flow`.
/ .
/ WHY THIS EXISTS, given the stack already has feeds
/ .
/ fx_feed, crypto_mock and eq_orderbook already demonstrate a subscriber,
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
/ RESTARTS. The high-water marks below are this process's state, and the
/ consumer commits offsets once the plant has the row - so rows published
/ while kafka_flow1 was down reach the plant's log and nowhere else. The job
/ therefore declares replay 1b and restores from its own `client_flow`:
/ .
/   during the replay  client_flow rows raise the marks to what was
/                      published before the restart; kafka_client_flow
/                      rows are HELD, because the log puts each raw row
/                      before the client_flow row it produced, so a raw
/                      row cannot be judged until the whole log is read.
/   on_replayed        publishes the held rows above the restored marks -
/                      exactly those that arrived while the job was down.
/ .
/ Live, a client_flow batch is this job's own output coming back; raising
/ the marks from it again changes nothing.
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

/ Raw rows held during a replay, judged by on_replayed once the marks are
/ complete.
held:.qetl.plant.published `kafka_client_flow

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
/ @private
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
/ @private
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
/ @private
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
/ reason eq_orderbook keeps ts_event: `time` says when we HEARD about the
/ record, and their difference is the feed's lag.
/ .
/ Publishing nothing is a normal outcome, not a failure - a batch that is
/ entirely a replay is exactly what this job is for, and an empty publish
/ would put a zero-row write on the plant for no reason.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    if[0=count x; :()];
    if[t=`client_flow; :.qpipe.job.kafka_flow.advance[x]];
    if[not t=`kafka_client_flow; :()];
    rows:$[`time in cols x; ![x;();0b;enlist `time]; x];
    if[.qetl.job.stream.replaying;
        `.qpipe.job.kafka_flow.held upsert cols[.qpipe.job.kafka_flow.held]#rows;
        :()];
    .qpipe.job.kafka_flow.publish_fresh[rows];
    }

/ Private: publish the rows above the marks, then raise the marks.
/ .
/ PUBLISH FIRST. Raising the marks before publishing meant a publish that
/ threw left them raised over rows that never went out, so a redelivery
/ was dropped as already seen.
/ @private
publish_fresh:{[rows]
    fresh:.qpipe.job.kafka_flow.first_per_coordinate[.qpipe.job.kafka_flow.above_high_water[rows]];
    if[0=count fresh; :0];
    out:select broker_time, sym, side, qty, price, client, trade_id, partition, offset from fresh;
    .qpipe.job.kafka_flow.publish[`client_flow;out];
    .qpipe.job.kafka_flow.advance[fresh];
    count out}

/ After the replay: publish what arrived while the job was down.
/ .
/ The marks now hold everything published before the restart, so the held
/ rows above them are exactly the missed ones.
/ @return the number of rows published
on_replayed:{[]
    n:.qpipe.job.kafka_flow.publish_fresh[.qpipe.job.kafka_flow.held];
    `.qpipe.job.kafka_flow.held set 0#.qpipe.job.kafka_flow.held;
    if[n>0; .[{.qetl.log.info[x;y;z]};(`kafka_flow;"published rows that arrived while this job was down";enlist[`rows]!enlist n);::]];
    n}

\d .

.qetl.job.stream.define[`kafka_flow;`procname`subscribe_to`publishes`on_batch`replay`restore_from`on_replayed`note`state!(
    `kafka_flow1;
    `kafka_client_flow;
    enlist `client_flow;
    .qpipe.job.kafka_flow.on_batch;
    1b;
    enlist `client_flow;
    .qpipe.job.kafka_flow.on_replayed;
    "deduplicates client FX flow consumed off a Kafka topic, on the (partition;offset) the record carries. The raw rows are published by an EXTERNAL Python consumer (external/kafka_feed.py) - a q process cannot hold a Kafka subscription - so kafka_client_flow has a schema row but no producer in this list. That is why it does not start with the stack: on a default start nothing publishes the table it subscribes to, and it would hold one of the sixteen licensed plant connections to consume nothing. Start it with the consumer";
    enlist `held)];
