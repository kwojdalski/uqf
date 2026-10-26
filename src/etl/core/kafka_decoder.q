/ kafka_decoder.q - what a Kafka DECODER JOB does (.qetl.kafka.decoder, #1481):
/ judge the undecoded records on kafka_message against the job's marks, decode
/ them through the topics the job owns, publish the rows and the dead letters,
/ and rebuild its marks after a restart from what it published before.
/ .
/ This was kafka_flow.q's body. It moved here so a SIDECAR can declare a
/ decoder of its own - its own process, topics, mappings and output tables -
/ without editing a file in this tree. The job file keeps what the tooling
/ must read as literals (`publishes`, the publish calls) and hands everything
/ else to the functions below, keyed by the job's name:
/ .
/   .qpipe.job.<job>.publish          the stub .qetl.job.stream.wire replaces
/   .qpipe.job.<job>.publish_dead     {[x] .qpipe.job.<job>.publish[`kafka_dead_letter;x]}
/   .qpipe.job.<job>.publish_decoded  {[t;x] one literal branch per output table}
/   .qpipe.job.<job>.on_batch         {[t;x] .qetl.kafka.decoder.on_batch[`<job>;t;x]}
/   .qpipe.job.<job>.on_replayed      {[] .qetl.kafka.decoder.on_replayed `<job>}
/   state `marks`held`done            .qetl.kafka.decoder.init `<job>, then declared as `state
/ .
/ kafka_flow.q is the worked example; sidecars/kafka_example is a bundle's.
/ .
/ WHO DECODES A RECORD. A topic is declared by one job (kafka_topics.q) and
/ only that job handles its records: it marks them, publishes them, or
/ dead-letters them. Another decoder subscribed to the same kafka_message sees
/ the record and ignores it - no job handles it twice, and none dead-letters
/ what another job maps. The one exception is the CATCH-ALL, kafka_flow:
/ it also takes every topic NO job has claimed, and dead-letters those as
/ `no_mapping`. "Claimed" comes from the generated load plan
/ (.qetl.load.topics), which covers the tree and its installed bundles in
/ every process, so kafka_flow1 knows a sidecar's topics without loading its
/ decoder. A record is therefore always accounted for while kafka_flow1 runs.
/ Without it, a record for an unclaimed topic stays on kafka_message, raw and
/ undecoded, and a decoder that is started later catches up from the log.
/ .
/ The deduplication, the marks and the restart are explained where they began,
/ in kafka_flow.q's header; they are unchanged.

\d .

/ The one decoder that also accounts for topics nobody owns.
.qetl.kafka.decoder.catch_all:`kafka_flow

/ The job's state variables, in the order a job declares them as `state.
.qetl.kafka.decoder.state_names:`marks`held`done

/ Put a job's decoder state in its namespace, empty. Call it before the job is defined, so the declared state is captured as it is.
/ @param job the decoder job
/ @return the job
/ @eg .qetl.kafka.decoder.init `kafka_flow
.qetl.kafka.decoder.init:{[job]
    ns:.qetl.job.stream.namespace job;
    (` sv ns,`marks) set .qetl.kafka.marks_empty;
    (` sv ns,`held) set .qetl.kafka.envelope_empty;
    (` sv ns,`done) set .qetl.kafka.coordinate_set 0#.qetl.kafka.envelope_empty;
    job}

/ Private: a variable or function in the job's namespace.
/ @private
.qetl.kafka.decoder.var:{[job;k] get ` sv (.qetl.job.stream.namespace job),k}

/ Private: set a variable in the job's namespace.
/ @private
.qetl.kafka.decoder.put:{[job;k;v] (` sv (.qetl.job.stream.namespace job),k) set v; v}

/ The rows this job answers for: its own topics, and for the catch-all every
/ topic no other job owns.
/ @param job the decoder job
/ @param rows envelope rows
/ @return the rows of those topics
/ @eg count .qetl.kafka.decoder.mine[`kafka_flow;([] topic:enlist `nobody.owns.this)]  ->  1
.qetl.kafka.decoder.mine:{[job;rows]
    if[0=count rows; :rows];
    claims:.qetl.kafka.topic.claims[];
    / `where` over a dictionary answers the KEYS that are true.
    others:where not claims=job;
    $[job=.qetl.kafka.decoder.catch_all;
        rows where not rows[`topic] in others;
        rows where rows[`topic] in where claims=job]}

/ The topics a job owns whose output table the job does not publish.
/ @param job the decoder job
/ @return a symbol vector, empty when the job can publish every output it decodes into
/ @eg .qetl.kafka.decoder.unpublished `kafka_flow  ->  `symbol$()
.qetl.kafka.decoder.unpublished:{[job]
    pub:(.qetl.job.stream.def job)`publishes;
    exec topic from .qetl.kafka.topic.registry where owner=job, not output in pub}

/ Refuse a decoder job that is not whole, at load: an owned topic whose output it does not publish, no dead-letter table, or a missing function.
/ @param job the decoder job, already defined
/ @return the job
/ @throws error naming what the job lacks
/ @eg .qetl.kafka.decoder.check `kafka_flow
.qetl.kafka.decoder.check:{[job]
    who:"kafka decoder ",string job;
    ns:.qetl.job.stream.namespace job;
    if[count miss:`publish_dead`publish_decoded except key ns; 'who," defines no ",", " sv string miss];
    if[not `kafka_dead_letter in (.qetl.job.stream.def job)`publishes; 'who," does not publish kafka_dead_letter"];
    if[not (.qetl.job.stream.def job)[`state]~.qetl.kafka.decoder.state_names; 'who," must declare `marks`held`done as its state"];
    if[count bad:.qetl.kafka.decoder.unpublished[job];
        'who,": topic(s) ",(", " sv string bad)," declare an output it does not publish - add the table to its publishes and to publish_decoded"];
    if[0=count .qetl.kafka.topic.owned[job]; if[not job=.qetl.kafka.decoder.catch_all; 'who," owns no topic - declare one with .qetl.kafka.topic.define, job `",string job]];
    job}

/ Private: decode rows, publish what they became, then raise the marks.
/ .
/ PUBLISH FIRST. Raising the marks before publishing meant a publish that
/ threw left them raised over rows that never went out, so a redelivery was
/ dropped as already seen. Dead letters go first: a record that cannot be
/ mapped should be on record even if an output table's publish then fails.
/ @param job the decoder job
/ @param rows envelope rows, already judged fresh
/ @return the number of rows published onto output tables
/ @private
.qetl.kafka.decoder.handle:{[job;rows]
    if[0=count rows; :0];
    r:.qetl.kafka.process rows;
    if[count r`dead; .qetl.kafka.decoder.var[job;`publish_dead] r`dead];
    pd:.qetl.kafka.decoder.var[job;`publish_decoded];
    {[pd;t;x] if[count x; pd[t;x]]}[pd]'[key r`out;value r`out];
    .qetl.kafka.decoder.put[job;`marks;.qetl.kafka.advance_runs[.qetl.kafka.decoder.var[job;`marks];rows]];
    sum count each r`out}

/ Private: a batch of envelope rows, minus what has been handled before.
/ .
/ A partition whose rows all sit below the job's mark has had its offsets
/ restart (#1519): the feed has already reset ITS mark and republished the new
/ records (.qetl.kafka.restarted, one rule for both), so this job forgets the
/ partition's mark and handled coordinates too and decodes them. Left alone, the
/ mark would drop every one of them with no output, no dead letter and no log.
/ A redelivery wholly below the mark stays a redelivery when its records are not
/ newer than the mark's (restarted's rule).
/ @private
.qetl.kafka.decoder.fresh:{[job;rows]
    rows:.qetl.kafka.envelope_columns#rows;
    back:.qetl.kafka.restarted[.qetl.kafka.decoder.var[job;`marks];rows];
    if[count back;
        .qetl.log.warn[job;"partition offsets went backwards - its mark is reset and the new records are decoded";enlist[`partitions]!enlist count back];
        .qetl.kafka.decoder.put[job;`marks;.qetl.kafka.forget[.qetl.kafka.decoder.var[job;`marks];back]];
        .qetl.kafka.decoder.put[job;`done;.qetl.kafka.forget[.qetl.kafka.decoder.var[job;`done];back]]];
    .qetl.kafka.admit[.qetl.kafka.decoder.var[job;`marks];rows]}

/ Private: the marks, with each partition whose rows restart in the held log set to the highest offset of its last run.
/ .
/ Rows are in arrival order. A run ends where an offset falls below the highest
/ before it; the mark of a partition with more than one run is the top of the
/ last, which can be lower than the old run's top - the one case a mark moves
/ down.
/ @param marks the marks table
/ @param held the raw rows in arrival order
/ @return the marks
/ @private
.qetl.kafka.decoder.restart_marks:{[marks;held]
    tops:.qetl.kafka.restart_tops[held];
    $[count tops; marks upsert tops; marks]}

/ Handle a batch on one of the job's tables.
/ .
/ kafka_message rows of this job's topics are judged against the marks and
/ decoded; rows of the job's own outputs coming back (live) change nothing,
/ because their coordinates were marked when they were published; during a
/ replay both kinds are bookkeeping - outputs add to the handled set, raw rows
/ are held for on_replayed. Rows of another job's topics are not this job's.
/ .
/ Publishing nothing is a normal outcome, not a failure: a batch that is
/ entirely a redelivery is exactly what a decoder is for.
/ @param job the decoder job
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
/ @eg .qetl.kafka.decoder.on_batch[`kafka_flow;`quote;()]
.qetl.kafka.decoder.on_batch:{[job;t;x]
    if[0=count x; :()];
    if[t in (.qetl.job.stream.def job)`publishes;
        if[.qetl.job.stream.replaying;
            mine:.qetl.kafka.decoder.mine[job;x];
            .qetl.kafka.decoder.put[job;`done;(.qetl.kafka.decoder.var[job;`done]) upsert .qetl.kafka.coordinate_set mine]];
        :()];
    if[not t=`kafka_message; :()];
    rows:.qetl.kafka.decoder.mine[job;.qetl.kafka.envelope_columns#x];
    if[.qetl.job.stream.replaying;
        .qetl.kafka.decoder.put[job;`held;.qetl.kafka.decoder.var[job;`held],rows];
        :()];
    .qetl.kafka.decoder.handle[job;.qetl.kafka.decoder.fresh[job;rows]];
    }

/ After the replay: handle what was never handled.
/ .
/ Raises the marks to everything the log shows handled, then processes the held
/ raw rows whose coordinates are NOT in that set - they are exactly the records
/ that arrived while the job was down, or whose output never left it.
/ @param job the decoder job
/ @return the number of rows published onto output tables
/ @eg .qetl.kafka.decoder.on_replayed `kafka_flow
.qetl.kafka.decoder.on_replayed:{[job]
    done:0!.qetl.kafka.decoder.var[job;`done];
    .qetl.kafka.decoder.put[job;`marks;.qetl.kafka.advance[.qetl.kafka.decoder.var[job;`marks];done]];
    held:.qetl.kafka.decoder.var[job;`held];
    todo:.qetl.kafka.first_per_coordinate .qetl.kafka.not_in[held;.qetl.kafka.decoder.var[job;`done]];
    n:.qetl.kafka.decoder.handle[job;todo];
    / A partition whose log shows its offsets restart is marked by its LAST run of offsets, not by the old run's higher ones (#1519).
    .qetl.kafka.decoder.put[job;`marks;.qetl.kafka.decoder.restart_marks[.qetl.kafka.decoder.var[job;`marks];held]];
    .qetl.kafka.decoder.put[job;`held;0#held];
    .qetl.kafka.decoder.put[job;`done;.qetl.kafka.coordinate_set 0#.qetl.kafka.envelope_empty];
    if[count todo; .[{.qetl.log.info[x;y;z]};(job;"handled records that arrived while this job was down";enlist[`records]!enlist count todo);::]];
    n}

\d .
