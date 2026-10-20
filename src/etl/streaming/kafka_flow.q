/ kafka_flow.q - decode Kafka records off the plant and deduplicate them on
/ their own coordinates (.qpipe.job.kafka_flow).
/ .
/ Reads `kafka_message` - every record a consumer delivered, undecoded - and
/ publishes each declared topic's decoded rows (`client_flow` for the client
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
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
.qpipe.job.kafka_flow.publish:.qetl.job.stream.unwired `kafka_flow;

/ ------------------------------------------------------------- THE STATE

/ (topic;partition) -> the highest offset handled from it.
/ .
/ Bounded by the topics' partition counts, so there is nothing to evict.
.qpipe.job.kafka_flow.marks:.qetl.kafka.marks_empty

/ Raw rows held during a replay, judged by on_replayed once the handled set is complete.
.qpipe.job.kafka_flow.held:.qetl.kafka.envelope_empty

/ The (topic;partition;offset) coordinates the replay found already handled.
.qpipe.job.kafka_flow.done:.qetl.kafka.coordinate_set 0#.qetl.kafka.envelope_empty

/ The tables this job publishes: set from the declaration below, which is where
/ they are written out (python/uqs reads `publishes` as a literal).
.qpipe.job.kafka_flow.outputs:`symbol$()

/ Publish decoded rows onto the plant table a topic declared.
/ .
/ One literal branch per output table, and that is the convention rather than an
/ oversight: the registry, the DAG and `uqs summary` read each job's publishes
/ and its publish calls as literals (test_publish_declarations.py), so a table
/ reached through a variable would be an output nothing could see. A new
/ topic whose output is a new table adds its name to `publishes` below AND a
/ branch here; the check after define refuses a topic that did one without the other.
/ @param t the plant table
/ @param x its rows
/ @private
.qpipe.job.kafka_flow.publish_decoded:{[t;x]
    if[t=`client_flow; :.qpipe.job.kafka_flow.publish[`client_flow;x]];
    '"kafka_flow: has no publish for ",string[t]," - add it to publishes and to publish_decoded"}

/ ---------------------------------------------------------------- THE JOB

/ Private: decode rows and publish what they became, then raise the marks.
/ .
/ PUBLISH FIRST. Raising the marks before publishing meant a publish that
/ threw left them raised over rows that never went out, so a redelivery was
/ dropped as already seen. Dead letters go first: a record that cannot be
/ mapped should be on record even if an output table's publish then fails.
/ @param rows envelope rows, already judged fresh
/ @return the number of rows published onto output tables
/ @private
.qpipe.job.kafka_flow.handle:{[rows]
    if[0=count rows; :0];
    r:.qetl.kafka.process rows;
    if[count r`dead; .qpipe.job.kafka_flow.publish[`kafka_dead_letter;r`dead]];
    {[t;x] if[count x; .qpipe.job.kafka_flow.publish_decoded[t;x]]}'[key r`out;value r`out];
    `.qpipe.job.kafka_flow.marks set .qetl.kafka.advance[.qpipe.job.kafka_flow.marks;rows];
    sum count each r`out}

/ Private: a batch of envelope rows, minus what has been handled before.
/ @private
.qpipe.job.kafka_flow.fresh:{[rows]
    .qetl.kafka.first_per_coordinate .qetl.kafka.above_marks[.qpipe.job.kafka_flow.marks;.qetl.kafka.envelope_columns#rows]}

/ Handle a batch.
/ .
/ kafka_message rows are judged against the marks and decoded; rows of the job's
/ own outputs coming back (live) change nothing, because their coordinates were
/ marked when they were published; during a replay both kinds are bookkeeping -
/ outputs add to the handled set, raw rows are held for on_replayed.
/ .
/ Publishing nothing is a normal outcome, not a failure: a batch that is
/ entirely a redelivery is exactly what this job is for.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
.qpipe.job.kafka_flow.on_batch:{[t;x]
    if[0=count x; :()];
    if[t in .qpipe.job.kafka_flow.outputs;
        if[.qetl.job.stream.replaying;
            `.qpipe.job.kafka_flow.done set (.qpipe.job.kafka_flow.done) upsert .qetl.kafka.coordinate_set x];
        :()];
    if[not t=`kafka_message; :()];
    rows:.qetl.kafka.envelope_columns#x;
    if[.qetl.job.stream.replaying;
        `.qpipe.job.kafka_flow.held set .qpipe.job.kafka_flow.held,rows;
        :()];
    .qpipe.job.kafka_flow.handle .qpipe.job.kafka_flow.fresh rows;
    }

/ After the replay: handle what was never handled.
/ .
/ Raises the marks to everything the log shows handled, then processes the held
/ raw rows whose coordinates are NOT in that set - they are exactly the records
/ that arrived while the job was down, or whose output never left it.
/ @return the number of rows published onto output tables
.qpipe.job.kafka_flow.on_replayed:{[]
    done:0!.qpipe.job.kafka_flow.done;
    `.qpipe.job.kafka_flow.marks set .qetl.kafka.advance[.qpipe.job.kafka_flow.marks;done];
    todo:.qetl.kafka.first_per_coordinate .qetl.kafka.not_in[.qpipe.job.kafka_flow.held;.qpipe.job.kafka_flow.done];
    n:.qpipe.job.kafka_flow.handle todo;
    `.qpipe.job.kafka_flow.held set 0#.qpipe.job.kafka_flow.held;
    `.qpipe.job.kafka_flow.done set .qetl.kafka.coordinate_set 0#.qetl.kafka.envelope_empty;
    if[count todo; .[{.qetl.log.info[x;y;z]};(`kafka_flow;"handled records that arrived while this job was down";enlist[`records]!enlist count todo);::]];
    n}

\d .

.qetl.job.stream.define[`kafka_flow;`procname`subscribe_to`publishes`on_batch`replay`restore_from`on_replayed`note`state!(
    `kafka_flow1;
    enlist `kafka_message;
    `client_flow`kafka_dead_letter;
    .qpipe.job.kafka_flow.on_batch;
    1b;
    `client_flow`kafka_dead_letter;
    .qpipe.job.kafka_flow.on_replayed;
    "decodes the Kafka records on kafka_message into each declared topic's plant table (client_flow for the client trade topic), deduplicated on (topic;partition;offset), and sends what it cannot decode to kafka_dead_letter. The records are published by a consumer - the q feed kafka_feed1, or the legacy Python one - so kafka_message has no producer in a default start. That is why it does not start with the stack: it would hold one of the sixteen licensed plant connections to consume nothing. Start it with the consumer";
    `marks`held`done)];

.qpipe.job.kafka_flow.outputs:(.qetl.job.stream.def `kafka_flow)`publishes;

/ A declared topic whose output this job does not publish would decode into
/ rows with nowhere to go: refused at load, naming the topic.
.qpipe.job.kafka_flow.unpublished:{[]
    exec topic from .qetl.kafka.topic.registry where not output in .qpipe.job.kafka_flow.outputs}

if[count bad:.qpipe.job.kafka_flow.unpublished[];
    '"kafka_flow: topic(s) ",(", " sv string bad)," declare an output kafka_flow does not publish - add the table to its publishes"];
