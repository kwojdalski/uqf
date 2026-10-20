/ kafka_flow.q - decode Kafka records off the plant and deduplicate them on
/ their own coordinates (.qpipe.job.kafka_flow).
/ .
/ Reads `kafka_message` - every record a consumer delivered, undecoded - and
/ publishes the decoded rows of the topics it owns (`client_flow` for the client
/ trade topic) and `kafka_dead_letter` for what could not be decoded.
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
/ This example takes the second, on the rule that a recoverable failure beats an
/ undetectable one - and then has to actually do the recovering, which is
/ what this file is. The consumer (kafka_feed1, or the Python one) commits
/ only after .u.upd has returned (core/kafka_ingest.q has the exact guarantee);
/ this job drops anything it has already seen.
/ .
/ THE KEY IS (topic;partition;offset). Offsets are per partition and a
/ partition belongs to a topic, so two topics' offset 5 on partition 0 are two
/ records. The marks are therefore keyed on the pair, and every output table
/ carries all three columns - they are not consumer bookkeeping, the plant is
/ the thing that has to survive the redelivery, so the coordinates travel with
/ the row. A desk querying client_flow can name the exact Kafka record.
/ .
/ WHAT HAPPENS TO A RECORD, in order:
/   1. at or below its (topic;partition) mark   -> dropped, it was done before
/   2. no declaration for its topic             -> kafka_dead_letter, no_mapping
/   3. empty / not JSON / a field missing       -> kafka_dead_letter, empty_payload or malformed
/   4. the mapping refuses the row              -> kafka_dead_letter, mapping
/   5. otherwise                                -> the topic's declared plant table
/ .
/ RESTARTS. The marks are this process's state, and the consumer commits
/ offsets once the plant has the record - so records published while
/ kafka_flow1 was down reach the plant's log and nowhere else. The job
/ therefore declares replay 1b and restores from its own outputs:
/ .
/   during the replay  output and dead-letter rows add their coordinates to
/                      the SET of what has been handled; kafka_message rows
/                      are HELD, because the log puts each raw row before the
/                      row it produced, so a raw row cannot be judged until
/                      the whole log is read.
/   on_replayed        raises the marks to the handled set, then processes the
/                      held rows that are NOT in it - exactly those that
/                      arrived while the job was down or whose output never
/                      made it out.
/ .
/ A SET, NOT A MARK, for the recovery: a crash between two tables' publishes
/ leaves a gap below the highest handled offset, and a mark would call the gap
/ done. (Live, a mark is enough: records of a partition arrive in order.)
/ .
/ A PUBLISH THAT THROWS leaves the marks where they were and the batch
/ unhandled; the rows are in the log, so the next restart's replay handles them.
/ .
/ THE MACHINERY IS NOT HERE. Marks, dedupe, dead letters and the restart replay
/ are .qetl.kafka.decoder (core/kafka_decoder.q), parameterised by job name; this
/ file is the worked example built on it and the catch-all decoder: it owns the
/ client trade topic (declared below, with its mapping, so the topic's transform
/ loads wherever this job does) and dead-letters every topic no job has
/ claimed. A sidecar adds topics by declaring a decoder job of its own the same
/ way - sidecars/kafka_example, docs/services/kafka.md - and edits nothing here.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
.qpipe.job.kafka_flow.publish:.qetl.job.stream.unwired `kafka_flow;

/ ------------------------------------------------------------- THE STATE

/ marks, held and done, as .qetl.kafka.decoder keeps them: the highest offset
/ handled per (topic;partition); raw rows held during a replay; the coordinates
/ the replay found already handled.
.qetl.kafka.decoder.init `kafka_flow;

/ Publish a decoder's dead letters.
/ @param x dead-letter rows
/ @private
.qpipe.job.kafka_flow.publish_dead:{[x] .qpipe.job.kafka_flow.publish[`kafka_dead_letter;x]}

/ Publish decoded rows onto the plant table a topic declared.
/ .
/ One literal branch per output table, and that is the convention rather than an
/ oversight: the registry, the DAG and `uqs summary` read each job's publishes
/ and its publish calls as literals (test_publish_declarations.py), so a table
/ reached through a variable would be an output nothing could see. A topic
/ whose output is a new table adds its name to `publishes` below AND a branch
/ here; .qetl.kafka.decoder.check refuses a topic that did one without the other.
/ @param t the plant table
/ @param x its rows
/ @private
.qpipe.job.kafka_flow.publish_decoded:{[t;x]
    if[t=`client_flow; :.qpipe.job.kafka_flow.publish[`client_flow;x]];
    '"kafka_flow: has no publish for ",string[t]," - add it to publishes and to publish_decoded"}

/ ---------------------------------------------------------------- THE JOB

/ Handle a batch: see .qetl.kafka.decoder.on_batch.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
.qpipe.job.kafka_flow.on_batch:{[t;x] .qetl.kafka.decoder.on_batch[`kafka_flow;t;x]}

/ After the replay: handle what was never handled (.qetl.kafka.decoder.on_replayed).
/ @return the number of rows published onto output tables
.qpipe.job.kafka_flow.on_replayed:{[] .qetl.kafka.decoder.on_replayed `kafka_flow}

/ ------------------------------------------------------------ THE TOPIC

/ The client trade topic, owned by this job; its mapping is the shared transform.
.qetl.kafka.topic.define[`uqf.client.flow;`mapping`output`job!(`kafka_client_flow;`client_flow;`kafka_flow)];

.qetl.job.stream.define[`kafka_flow;`procname`subscribe_to`publishes`on_batch`replay`restore_from`on_replayed`note`state!(
    `kafka_flow1;
    enlist `kafka_message;
    `client_flow`kafka_dead_letter;
    .qpipe.job.kafka_flow.on_batch;
    1b;
    `client_flow`kafka_dead_letter;
    .qpipe.job.kafka_flow.on_replayed;
    "decodes the Kafka records on kafka_message into each declared topic's plant table (client_flow for the client trade topic), deduplicated on (topic;partition;offset), and sends what it cannot decode to kafka_dead_letter - including every topic no decoder job has claimed. The records are published by a consumer - the q feed kafka_feed1, or the legacy Python one - so kafka_message has no producer in a default start. That is why it does not start with the stack: it would hold one of the sixteen licensed plant connections to consume nothing. Start it with the consumer";
    `marks`held`done)];

.qetl.kafka.decoder.check `kafka_flow;
