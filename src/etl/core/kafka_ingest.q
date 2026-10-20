/ kafka_ingest.q - the transport-agnostic half of Kafka ingestion
/ (.qetl.kafka): envelope, decode-and-route, deduplication marks, and the
/ publish-then-commit cycle. Nothing here knows librdkafka exists.
/ .
/ The pieces, in the order a record meets them:
/ .
/   from_messages  a consumer's messages -> ENVELOPE rows (kafka_message)
/   cycle          poll, publish the envelope, THEN commit - the transport is
/                  four functions handed in, so a fake drives it in a test
/   process        envelope rows -> rows for each declared plant table, plus
/                  the dead letters (the topics' mappings: kafka_topics.q)
/   fresh/advance  the (topic;partition) high-water marks that make a
/                  redelivery harmless
/ .
/ WHAT THE COMMIT ORDER GUARANTEES, EXACTLY
/ .
/ cycle commits a partition's offset only after .qetl publish of the record
/ RETURNED. Over the TorQ transport that call is a synchronous .u.upd to the
/ segmented tickerplant, so "returned" means the STP ran its update handler
/ without error. In its default batch mode (`defaultbatch`) that handler has
/ also handed the record to its log file's write handle; in `memorybatch` it
/ has not even done that, and in either mode the write is the operating
/ system's to flush, not an fsync. So the claim is "the tickerplant had the
/ record when we committed", NOT "the record survives a host crash". What
/ the ordering buys is the direction of the failure:
/ .
/   crash/error after publish, before commit  ->  Kafka redelivers; the
/     envelope reaches kafka_message twice; kafka_flow's marks drop the
/     second copy BEFORE it can reach a normalized table.
/   a commit that fails or is refused after a rebalance  ->  the same.
/   a publish that throws  ->  nothing is committed, the polled records stay
/     in this process's backlog and are published again next cycle.
/ .
/ None of these loses a record, and none duplicates normalized output. The one
/ case that CAN lose a record is the platform's: an STP that acknowledged and
/ then lost its log before a host flush. That is outside what a consumer can
/ know, and is why kafka_message keeps every delivery rather than only the
/ first - the raw table is the audit trail the normalized ones are checked
/ against.
/ .
/ THE TRANSPORT, a dictionary of functions (all take an argument, because a
/ niladic function under a protected call misreports success on KDB-X):
/   poll[n]       up to n messages as a table of the consumer's columns
/                 mtype topic partition offset msgtime data key
/   publish[t;x]  publish rows onto plant table t; throws unless the plant
/                 has them
/   commit[t;o]   commit, SYNCHRONOUSLY, dict partition(int) -> next offset
/                 (long) for topic t; throws on failure
/   assigned[u]   the partitions this consumer holds now, a table of topic
/                 and partition - or :: when the transport cannot say

\d .

/ ----------------------------------------------------------------- SHAPES

/ The envelope as it is published on kafka_message (no `time`: the plant stamps it).
.qetl.kafka.envelope_columns:`topic`partition`offset`msg_key`broker_time`payload
.qetl.kafka.envelope_empty:([] topic:`symbol$(); partition:`long$(); offset:`long$(); msg_key:(); broker_time:`timestamp$(); payload:())

/ The dead letter as it is published on kafka_dead_letter.
.qetl.kafka.dead_empty:([] topic:`symbol$(); partition:`long$(); offset:`long$(); msg_key:(); broker_time:`timestamp$(); reason:`symbol$(); detail:(); payload:())

/ The functions a transport supplies, in the order a feed lists them.
.qetl.kafka.transport_keys:`poll`publish`commit`assigned

/ Most envelope rows a consumer holds unpublished before it stops polling.
.qetl.kafka.backlog_cap:50000

/ Most messages one poll asks for.
.qetl.kafka.poll_max:5000

/ Commit attempts a partition may fail in a row before its offset is dropped
/ (the broker redelivers from the last good commit, which the marks absorb).
.qetl.kafka.commit_tries:5

/ Private: a protected call that says whether it threw.
/ @param f a function
/ @param args its argument list (never empty)
/ @return (1b; result) or (0b; the error text)
/ @private
.qetl.kafka.try:{[f;args] .[{[f;a] (1b;f . a)};(f;args);{(0b;x)}]}

/ ------------------------------------------------------------ THE ENVELOPE

/ Envelope rows from a consumer's messages.
/ .
/ Only data messages: a message with an mtype is the client reporting
/ something (a partition end, an unknown topic), not a record. The record's
/ coordinates are the broker's own and are read off the message - never out of
/ the payload, which a producer controls. The payload and key are kept as the
/ characters received, undecoded.
/ @param msgs a table of the consumer's message columns
/ @return the envelope rows, in the order received
/ @eg count .qetl.kafka.from_messages flip `mtype`topic`partition`offset`msgtime`data`key!(enlist `;enlist `t;enlist 0i;enlist 5;enlist 2026.10.20D09:00:00;enlist 0x7b7d;enlist `byte$())  ->  1
.qetl.kafka.from_messages:{[msgs]
    if[not 98h=type msgs; '"from_messages: expects a table of messages"];
    d:select from msgs where null mtype;
    if[0=count d; :.qetl.kafka.envelope_empty];
    ([] topic:`symbol$d`topic; partition:`long$d`partition; offset:`long$d`offset;
        msg_key:{"c"$(),x} each d`key; broker_time:`timestamp$d`msgtime;
        payload:{"c"$(),x} each d`data)}

/ The client's own reports in a poll: the messages that are not records.
/ @param msgs a table of the consumer's message columns
/ @return those rows
/ @eg count .qetl.kafka.events ([] mtype:enlist `PARTITION_EOF; topic:enlist `t)  ->  1
.qetl.kafka.events:{[msgs] select from msgs where not null mtype}

/ ------------------------------------------------------ MARKS AND DEDUPE

/ The marks: the highest offset published, per topic and partition.
.qetl.kafka.marks_empty:([topic:`symbol$(); partition:`long$()] offset:`long$())

/ The rows above their partition's mark.
/ .
/ THE NULL IS LOAD-BEARING. A (topic;partition) never seen indexes to a null
/ offset, null long compares below every value, and so an unseen partition
/ keeps every row - including offset 0, which `0^mark` would drop. And the
/ topic is part of the key: two topics' offset 5 on partition 0 are different
/ records.
/ @param marks the marks table
/ @param rows envelope rows
/ @return the rows whose offset is above the mark of their (topic;partition)
/ @eg count .qetl.kafka.above_marks[.qetl.kafka.marks_empty; ([] topic:enlist `a; partition:enlist 0; offset:enlist 0)]  ->  1
.qetl.kafka.above_marks:{[marks;rows]
    if[0=count rows; :rows];
    k:([] topic:rows`topic; partition:rows`partition);
    rows where rows[`offset]>(marks k)`offset}

/ The rows with a repeated (topic;partition;offset) reduced to the first.
/ @param rows envelope rows
/ @return the rows, in their original order
/ @eg count .qetl.kafka.first_per_coordinate ([] topic:2#`a; partition:2#0; offset:2#5)  ->  1
.qetl.kafka.first_per_coordinate:{[rows]
    if[0=count rows; :rows];
    rows asc value exec first i by topic,partition,offset from rows}

/ The marks raised to the highest offset in rows; never lowered.
/ .
/ `|` against the previous mark: a batch that arrives out of order must not
/ pull a mark BACKWARDS, which would re-admit every record between the two.
/ @param marks the marks table
/ @param rows envelope rows
/ @return the new marks
/ @eg exec offset from .qetl.kafka.advance[.qetl.kafka.marks_empty; ([] topic:2#`a; partition:2#0; offset:3 7)]  ->  enlist 7
.qetl.kafka.advance:{[marks;rows]
    if[0=count rows; :marks];
    m:select max offset by topic,partition from rows;
    before:(marks key m)`offset;
    marks upsert update offset:offset|before from m}

/ Rows whose (topic;partition;offset) is not in a set of coordinates.
/ .
/ A SET, not a mark, because a restart's recovery cannot assume the processed
/ records were contiguous: a crash between two tables' publishes leaves a gap
/ below the highest offset, and a mark would call the gap done.
/ @param rows envelope rows
/ @param done a keyed table of topic, partition, offset with a `seen` column
/ @return the rows not in done
/ @eg count .qetl.kafka.not_in[([] topic:enlist `a; partition:enlist 0; offset:enlist 5); .qetl.kafka.coordinate_set 0#.qetl.kafka.envelope_empty]  ->  1
.qetl.kafka.not_in:{[rows;done]
    if[0=count rows; :rows];
    k:select topic,partition,offset from rows;
    rows where null (done k)`seen}

/ The coordinates of rows, as a set for not_in.
/ @param rows any table carrying topic, partition and offset
/ @return a keyed table of those coordinates with seen=1
/ @eg count .qetl.kafka.coordinate_set ([] topic:2#`a; partition:2#0; offset:2#5)  ->  1
.qetl.kafka.coordinate_set:{[rows]
    if[0=count rows; :([topic:`symbol$();partition:`long$();offset:`long$()] seen:`long$())];
    c:select distinct topic,partition,offset from rows;
    `topic`partition`offset xkey update seen:1 from c}

/ ---------------------------------------------------------- DECODE AND ROUTE

/ Private: dead-letter rows for envelope rows.
/ @param rows the envelope rows
/ @param reason why: no_mapping, empty_payload, malformed or mapping
/ @param details one string per row
/ @private
.qetl.kafka.dead_rows:{[rows;reason;details]
    if[0=count rows; :.qetl.kafka.dead_empty];
    ([] topic:rows`topic; partition:rows`partition; offset:rows`offset; msg_key:rows`msg_key;
        broker_time:rows`broker_time; reason:(count rows)#reason; detail:details; payload:rows`payload)}

/ Private: one topic's records through its mapping.
/ @param t the topic
/ @param rows its envelope rows
/ @return dict of `out` (the mapping's rows) and `dead` (dead letters)
/ @private
.qetl.kafka.decode_topic:{[t;rows]
    d:.qetl.kafka.topic.def t;
    inp:.qetl.kafka.topic.input_of["decode";d`mapping];
    schema:inp`schema;
    dec:.qetl.kafka.decoders d`format;
    out:(.qetl.transform.def d`mapping)`output;
    res:{[dec;schema;p] .qetl.kafka.try[dec;(schema;p)]}[dec;schema] each rows`payload;
    ok:$[count res; res[;0]; `boolean$()];
    bad:rows where not ok;
    dead:.qetl.kafka.dead_rows[bad;`malformed;res[;1] where not ok];
    if[count bad;
        none:0=count each bad`payload;
        dead:update reason:?[none;`empty_payload;reason] from dead];
    good:rows where ok;
    if[0=count good; :`out`dead!(0#out;dead)];
    gv:res[;1] where ok;
    fvals:{[gv;k] gv[;k]}[gv] each til count schema;
    tbl:(select topic,partition,offset,broker_time from good),'flip (key schema)!fvals;
    r:.qetl.kafka.try[.qetl.kafka.apply_mapping[d`mapping;inp`input];enlist tbl];
    if[first r; :`out`dead!(last r;dead)];
    / One refused row must not take its neighbours with it: map them singly.
    singles:{[mp;inp;tbl;i] .qetl.kafka.try[.qetl.kafka.apply_mapping[mp;inp];enlist tbl enlist i]}[d`mapping;inp`input;tbl] each til count tbl;
    mapped:singles[;0];
    refused:good where not mapped;
    dead:dead,.qetl.kafka.dead_rows[refused;`mapping;singles[;1] where not mapped];
    `out`dead!((,/) enlist[0#out],singles[;1] where mapped;dead)}

/ Private: apply a topic's mapping to its decoded table.
/ @private
.qetl.kafka.apply_mapping:{[mapping;inp;tbl] .qetl.transform.apply[mapping;(enlist inp)!enlist tbl]}

/ Envelope rows into rows for the plant tables their topics declare, and the
/ dead letters of those that could not be mapped.
/ .
/ Pure: it publishes nothing and keeps nothing, so the same rows give the
/ same answer on a replay.
/ @param rows envelope rows (a `time` column, if the plant added one, is ignored)
/ @return dict of `out` (plant table -> rows, only tables that got rows) and `dead` (dead-letter rows)
/ @eg count .qetl.kafka.process[.qetl.kafka.envelope_empty]`dead  ->  0
.qetl.kafka.process:{[rows]
    rows:.qetl.kafka.envelope_columns#rows;
    declared:.qetl.kafka.topic.defined[];
    unknown:rows where not rows[`topic] in declared;
    dead:.qetl.kafka.dead_rows[unknown;`no_mapping;{"no mapping is declared for topic ",string x} each unknown`topic];
    known:rows where rows[`topic] in declared;
    topics:distinct known`topic;
    parts:{[known;t] .qetl.kafka.decode_topic[t;select from known where topic=t]}[known] each topics;
    dead:dead,(,/) enlist[.qetl.kafka.dead_empty],parts[;`dead];
    names:{(.qetl.kafka.topic.def x)`output} each topics;
    outs:(distinct names)!{[names;parts;n] (,/) (parts[;`out]) where names=n}[names;parts] each distinct names;
    `out`dead!((where 0<count each outs)#outs;dead)}

/ ------------------------------------------------------------- THE CYCLE

/ A fresh consumer state: no marks, no pending commits, nothing unpublished.
/ @return the state dictionary
/ @eg count .qetl.kafka.state_new[]`backlog  ->  0
.qetl.kafka.state_new:{[]
    `marks`pending`backlog`stats!(
        .qetl.kafka.marks_empty;
        ([topic:`symbol$(); partition:`long$()] resume:`long$(); tries:`long$());
        .qetl.kafka.envelope_empty;
        `polled`published`duplicates`commits`commit_errors`publish_errors`poll_errors`events`revoked!9#0)}

/ Private: the state with a stat raised.
/ @private
.qetl.kafka.bump:{[st;k;n] st[`stats;k]:n+st[`stats;k]; st}

/ Private: merge the offsets to commit for rows into the pending commits.
/ @private
.qetl.kafka.plan_commit:{[st;rows]
    if[0=count rows; :st];
    p:select resume:1+max offset, tries:0 by topic,partition from rows;
    before:((st`pending) key p)`resume;
    st[`pending]:(st`pending) upsert update resume:resume|before from p;
    st}

/ Private: poll and hold what came back.
/ @private
.qetl.kafka.take:{[tr;st]
    r:.qetl.kafka.try[tr`poll;enlist .qetl.kafka.poll_max];
    if[not first r;
        .qetl.log.err[`kafka;"poll failed";enlist[`error]!enlist last r];
        :.qetl.kafka.bump[st;`poll_errors;1]];
    msgs:last r;
    ev:.qetl.kafka.events msgs;
    if[count ev;
        / Once per cause (kafka_diagnostics.q): a client report that repeats every poll was a log line every poll.
        {.qetl.kafka.diag.report[x`mtype;$[10h=type r:x`data; r; 4h=type r; "c"$r; ""]]} each ev;
        st:.qetl.kafka.bump[st;`events;count ev]];
    env:.qetl.kafka.from_messages msgs;
    st[`backlog]:(st`backlog),env;
    .qetl.kafka.bump[st;`polled;count env]}

/ Private: publish the backlog onto kafka_message; on success raise the marks
/ and owe the commits, on failure change nothing.
/ @private
.qetl.kafka.publish_backlog:{[tr;st]
    b:st`backlog;
    if[0=count b; :st];
    fresh:.qetl.kafka.first_per_coordinate .qetl.kafka.above_marks[st`marks;b];
    r:.qetl.kafka.try[{[tr;f] if[count f; tr[`publish][`kafka_message;f]]; count f};(tr;fresh)];
    if[not first r;
        .qetl.log.err[`kafka;"publish failed - nothing will be committed";`rows`error!(count fresh;last r)];
        :.qetl.kafka.bump[st;`publish_errors;1]];
    st[`marks]:.qetl.kafka.advance[st`marks;fresh];
    st:.qetl.kafka.plan_commit[st;b];
    st[`backlog]:0#b;
    st:.qetl.kafka.bump[st;`published;count fresh];
    .qetl.kafka.bump[st;`duplicates;(count b)-count fresh]}

/ Private: a (topic;partition) pair as one symbol, for membership tests.
/ @private
.qetl.kafka.pair_key:{[t;p] {`$(string x),"/",string y}'[(),t;(),p]}

/ Private: the rows of a keyed table whose topic and partition satisfy a test,
/ rekeyed. q has no keyed-table delete by key that every interpreter agrees on.
/ @private
.qetl.kafka.keep:{[keyed;mask] `topic`partition xkey (0!keyed) where mask}

/ Private: forget what the consumer no longer holds.
/ @private
.qetl.kafka.revoke:{[tr;st]
    if[not `assigned in key tr; :st];
    r:.qetl.kafka.try[tr`assigned;enlist 0b];
    if[not first r; :st];
    a:last r;
    if[not 98h=type a; :st];
    held:.qetl.kafka.pair_key[a`topic;a`partition];
    pend:0!st`pending;
    mine:.qetl.kafka.pair_key[pend`topic;pend`partition] in held;
    if[not all mine;
        .qetl.log.warn[`kafka;"partitions revoked - their uncommitted offsets are dropped; the broker redelivers and the marks absorb it";enlist[`partitions]!enlist sum not mine];
        st[`pending]:.qetl.kafka.keep[st`pending;mine];
        st:.qetl.kafka.bump[st;`revoked;sum not mine]];
    mk:0!st`marks;
    st[`marks]:.qetl.kafka.keep[st`marks;.qetl.kafka.pair_key[mk`topic;mk`partition] in held];
    st}

/ Private: commit one topic's pending offsets.
/ @private
.qetl.kafka.commit_topic:{[tr;st;t]
    pend:0!st`pending;
    p:select partition,resume from pend where topic=t;
    offs:(`int$p`partition)!p`resume;
    r:.qetl.kafka.try[tr`commit;(t;offs)];
    if[first r;
        st[`pending]:.qetl.kafka.keep[st`pending;not pend[`topic]=t];
        :.qetl.kafka.bump[st;`commits;count p]];
    .qetl.log.warn[`kafka;"offset commit failed - kept for the next cycle";`topic`error!(t;last r)];
    pend:update tries:tries+1 from pend where topic=t;
    spent:pend[`tries]>=.qetl.kafka.commit_tries;
    if[any spent;
        .qetl.log.err[`kafka;"offset commit given up on - the broker redelivers and the marks absorb it";`topic`partitions!(t;sum spent)]];
    st[`pending]:`topic`partition xkey pend where not spent;
    .qetl.kafka.bump[st;`commit_errors;1]}

/ Private: commit every pending offset, a topic at a time.
/ @private
.qetl.kafka.commit_pending:{[tr;st]
    if[0=count st`pending; :st];
    .qetl.kafka.commit_topic[tr]/[st;exec distinct topic from st`pending]}

/ Publish what is held, then commit it - the half of a cycle that needs no poll.
/ .
/ This is also what an orderly shutdown runs: whatever was polled and not yet
/ published goes out, and whatever was published and not yet committed is
/ committed, before the client leaves its group.
/ @param tr the transport
/ @param st the state
/ @return the new state
/ @eg count (.qetl.kafka.flush[()!();.qetl.kafka.state_new[]])`backlog  ->  0
.qetl.kafka.flush:{[tr;st]
    st:.qetl.kafka.publish_backlog[tr;st];
    st:.qetl.kafka.revoke[tr;st];
    .qetl.kafka.commit_pending[tr;st]}

/ One turn of the consumer: poll (unless too much is unpublished), publish,
/ then commit what was published. In that order and no other.
/ @param tr the transport
/ @param st the state
/ @return the new state
/ @eg count (.qetl.kafka.cycle[`poll`publish`commit!({[n] ([] mtype:`symbol$())};{[t;x] 0};{[t;o] 1b});.qetl.kafka.state_new[]])`backlog  ->  0
.qetl.kafka.cycle:{[tr;st]
    if[.qetl.kafka.backlog_cap>count st`backlog; st:.qetl.kafka.take[tr;st]];
    .qetl.kafka.flush[tr;st]}

\d .
