/ test_kafka_example.q - the kafka_example bundle's decoder
/ (.qt.qpipe.job.example_quotes_decoder).
/ .
/ The bundle adds a topic, its mapping, its table and its job to a tree it does
/ not edit. These cases are what that has to deliver: the topic decodes, two
/ topics stay two, a bad record is on record, a restart does not double output,
/ and the tree's own decoder leaves this topic alone.


.qt.qpipe.job.example_quotes_decoder.published:()

.qt.qpipe.job.example_quotes_decoder.recorder:{[t;x] `.qt.qpipe.job.example_quotes_decoder.published set .qt.qpipe.job.example_quotes_decoder.published,enlist (t;x); count x}

/ Every row published onto one table since the last reset, as one table.
.qt.qpipe.job.example_quotes_decoder.rows_of:{[tbl]
    p:.qt.qpipe.job.example_quotes_decoder.published;
    $[count s:p[;1] where tbl=p[;0]; (,/) s; ()]}

.qt.qpipe.job.example_quotes_decoder.quotes:{[] .qt.qpipe.job.example_quotes_decoder.rows_of `example_quote}

/ Empty the job's state and point its publish at the recorder.
.qt.qpipe.job.example_quotes_decoder.drive_ready:{[]
    .qetl.job.stream.reset `example_quotes_decoder;
    `.qt.qpipe.job.example_quotes_decoder.published set ();
    .qetl.job.stream.wire[`example_quotes_decoder;.qt.qpipe.job.example_quotes_decoder.recorder];
    }

/ The topic registry is shared: a case that declares a topic puts it back.
.qt.qpipe.job.example_quotes_decoder.registry_saved:()
.qt.qpipe.job.example_quotes_decoder.beforeNamespace_load:{[] `.qt.qpipe.job.example_quotes_decoder.registry_saved set .qetl.kafka.topic.registry;}
.qt.qpipe.job.example_quotes_decoder.afterNamespace_restore:{[] `.qetl.kafka.topic.registry set .qt.qpipe.job.example_quotes_decoder.registry_saved; .qetl.job.stream.reset `example_quotes_decoder;}

.qt.qpipe.job.example_quotes_decoder.t0:2026.10.20D09:00:00.000000000

/ One quote as a producer writes it; the bid varies with the offset so two rows are distinguishable.
.qt.qpipe.job.example_quotes_decoder.payload:{[off] "{\"sym\":\"EURUSD\",\"bid\":",string[1.08+0.0001*off],",\"ask\":",string[1.0802+0.0001*off],"}"}

/ A batch as it arrives from the plant: `time` prepended, which the job must drop.
.qt.qpipe.job.example_quotes_decoder.batch_on:{[tp;parts;offs;payloads]
    n:count offs;
    ([] time:.qt.qpipe.job.example_quotes_decoder.t0+1000000000*til n;
        topic:n#tp; partition:parts; offset:offs; msg_key:n#enlist "";
        broker_time:(.qt.qpipe.job.example_quotes_decoder.t0+1000000000*til n)-0D00:00:00.002;
        payload:payloads)}

.qt.qpipe.job.example_quotes_decoder.push_on:{[tp;parts;offs]
    .qpipe.job.example_quotes_decoder.on_batch[`kafka_message;.qt.qpipe.job.example_quotes_decoder.batch_on[tp;parts;offs;.qt.qpipe.job.example_quotes_decoder.payload each offs]]}

.qt.qpipe.job.example_quotes_decoder.push:{[parts;offs] .qt.qpipe.job.example_quotes_decoder.push_on[`example.quotes;parts;offs]}

/ --- it installs, and decodes ----------------------------------------------

.qt.qpipe.job.example_quotes_decoder.test_the_bundle_s_topic_is_declared_and_owned_by_its_job:{[t]
    .qunit.assertTrue[`example.quotes in .qetl.kafka.topic.owned `example_quotes_decoder;"the topic is this job's"];
    .qunit.assertEquals[.qetl.kafka.decoder.check `example_quotes_decoder;`example_quotes_decoder;"the job is whole"];
    .qunit.assertEquals[(.qetl.kafka.topic.def `example.quotes)`output;`example_quote;"and decodes into the bundle's own table"]};

.qt.qpipe.job.example_quotes_decoder.test_a_record_decodes_into_the_bundle_s_table:{[t]
    .qt.qpipe.job.example_quotes_decoder.drive_ready[];
    .qt.qpipe.job.example_quotes_decoder.push[enlist 0j;enlist 5j];
    r:first .qt.qpipe.job.example_quotes_decoder.quotes[];
    .qunit.assertEquals[(r`sym;r`topic;r`partition;r`offset);(`EURUSD;`example.quotes;0j;5j);"typed, with the record's coordinates"];
    .qunit.assertEquals[(r`bid;r`ask);1.0805 1.0807;"the prices the producer wrote"];
    .qunit.assertTrue[not `time in cols .qt.qpipe.job.example_quotes_decoder.quotes[];"the plant stamps time"]};

/ --- two topics stay two ---------------------------------------------------

.qt.qpipe.job.example_quotes_decoder.test_identical_partition_and_offset_on_two_topics_are_both_decoded:{[t]
    / The key is (topic;partition;offset): keyed on (partition;offset) the second topic would be dropped as a repeat of the first.
    .qetl.kafka.topic.define[`example.quotes.b;`mapping`output`job!(`example_quote_map;`example_quote;`example_quotes_decoder)];
    .qt.qpipe.job.example_quotes_decoder.drive_ready[];
    .qt.qpipe.job.example_quotes_decoder.push[0 0j;0 1j];
    .qt.qpipe.job.example_quotes_decoder.push_on[`example.quotes.b;0 0j;0 1j];
    r:.qt.qpipe.job.example_quotes_decoder.quotes[];
    .qunit.assertEquals[(r`topic;r`offset);(`example.quotes`example.quotes`example.quotes.b`example.quotes.b;0 1 0 1j);"four records: offsets 0 and 1 of each topic"];
    .qt.qpipe.job.example_quotes_decoder.push_on[`example.quotes.b;0 0j;0 1j];
    .qunit.assertEquals[count .qt.qpipe.job.example_quotes_decoder.quotes[];4;"a redelivery of either is dropped by its own mark"]};

/ --- the dead letters ------------------------------------------------------

.qt.qpipe.job.example_quotes_decoder.test_a_malformed_and_a_refused_record_are_dead_lettered_and_neighbours_publish:{[t]
    .qt.qpipe.job.example_quotes_decoder.drive_ready[];
    crossed:"{\"sym\":\"EURUSD\",\"bid\":1.2,\"ask\":1.1}";
    b:.qt.qpipe.job.example_quotes_decoder.batch_on[`example.quotes;0 0 0 0j;0 1 2 3j;(.qt.qpipe.job.example_quotes_decoder.payload[0];"not json";crossed;"{\"sym\":\"EURUSD\"}")];
    .qpipe.job.example_quotes_decoder.on_batch[`kafka_message;b];
    d:`offset xasc .qt.qpipe.job.example_quotes_decoder.rows_of[`kafka_dead_letter];
    .qunit.assertEquals[(d`offset;d`reason);(1 2 3j;`malformed`mapping`malformed);"each bad record on record with its reason"];
    .qunit.assertEquals[d`payload;("not json";crossed;"{\"sym\":\"EURUSD\"}");"and its payload as received"];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.example_quotes_decoder.quotes[];enlist 0j;"the good neighbour published"];
    .qpipe.job.example_quotes_decoder.on_batch[`kafka_message;b];
    .qunit.assertEquals[count .qt.qpipe.job.example_quotes_decoder.rows_of[`kafka_dead_letter];3;"a redelivery is not dead-lettered again"]};

/ --- the tree's decoder and this one never both answer ---------------------

.qt.qpipe.job.example_quotes_decoder.test_the_trees_decoder_leaves_this_topic_to_its_owner:{[t]
    r:.qt.qpipe.job.example_quotes_decoder.batch_on[`example.quotes;enlist 0j;enlist 0j;enlist .qt.qpipe.job.example_quotes_decoder.payload[0]];
    .qunit.assertEquals[count .qetl.kafka.decoder.mine[`kafka_flow;r];0;"kafka_flow neither decodes nor dead-letters it"];
    .qunit.assertEquals[count .qetl.kafka.decoder.mine[`example_quotes_decoder;r];1;"this job does"]};

.qt.qpipe.job.example_quotes_decoder.test_a_record_for_another_decoder_s_topic_is_ignored:{[t]
    .qt.qpipe.job.example_quotes_decoder.drive_ready[];
    .qt.qpipe.job.example_quotes_decoder.push_on[`uqf.client.flow;enlist 0j;enlist 0j];
    .qt.qpipe.job.example_quotes_decoder.push_on[`example.unclaimed;enlist 0j;enlist 0j];
    .qunit.assertEquals[count .qt.qpipe.job.example_quotes_decoder.published;0;"neither is this job's: no output and no dead letter"];
    .qunit.assertEquals[count .qpipe.job.example_quotes_decoder.marks;0;"and no mark moved"]};

/ --- the tree's suites, which hold every job to a contract ----------------

/ What test_job_output_contracts.q drives the job with: a good, a malformed and a refused record, so each published table is held to its plant table's shape.
.qt.qpipe.job.example_quotes_decoder.contract_driver:{[]
    .qetl.job.stream.reset `example_quotes_decoder;
    crossed:"{\"sym\":\"EURUSD\",\"bid\":1.2,\"ask\":1.1}";
    .qpipe.job.example_quotes_decoder.on_batch[`kafka_message;.qt.qpipe.job.example_quotes_decoder.batch_on[`example.quotes;0 0 0j;0 1 2j;(.qt.qpipe.job.example_quotes_decoder.payload[0];"not json";crossed)]];
    }

/ test_replay_equivalence.q scripts the tree's replaying jobs or excludes them with a reason; a bundle's says so here.
.qt.qpipe.job.example_quotes_decoder.replay_excluded:"no time-based rule; replayed raw rows are held for on_replayed (test_a_restart_handles_exactly_the_records_that_were_not_handled)"

/ --- restarts --------------------------------------------------------------

/ What the plant's log holds when this job handled offsets 0, 1 (a dead letter) and 3, went down, and the consumer published 2, 3 and 4 meanwhile.
.qt.qpipe.job.example_quotes_decoder.restart_log:{[]
    pl:(.qt.qpipe.job.example_quotes_decoder.payload[0];"not json";.qt.qpipe.job.example_quotes_decoder.payload[2];.qt.qpipe.job.example_quotes_decoder.payload[3];.qt.qpipe.job.example_quotes_decoder.payload[4]);
    raw:.qt.qpipe.job.example_quotes_decoder.batch_on[`example.quotes;5#0j;til 5;pl];
    out:.qetl.kafka.process .qetl.kafka.envelope_columns#raw 0 1 3;
    ((`kafka_message;raw 0 1 3);(`example_quote;out[`out]`example_quote);(`kafka_dead_letter;out`dead);
     (`kafka_message;raw 2 3 4))}

.qt.qpipe.job.example_quotes_decoder.replay_transport:{[replayed]
    `connect`publisher`subscribe`timer!(
        {[] };
        {[] .qt.qpipe.job.example_quotes_decoder.recorder};
        {[replayed;tbls;h;replay] if[replay; {[h;m] h . m}[h] each replayed];}[replayed];
        {[n;p;f] })}

.qt.qpipe.job.example_quotes_decoder.test_a_restart_handles_exactly_the_records_that_were_not_handled:{[t]
    .qt.qpipe.job.example_quotes_decoder.drive_ready[];
    .qetl.job.stream.start[`example_quotes_decoder;.qt.qpipe.job.example_quotes_decoder.replay_transport[.qt.qpipe.job.example_quotes_decoder.restart_log[]]];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.example_quotes_decoder.quotes[];2 4j;
        "offset 2 (the gap) and 4 (arrived while down); 0, 1 and 3 are not output a second time"];
    .qunit.assertEquals[count .qt.qpipe.job.example_quotes_decoder.rows_of[`kafka_dead_letter];0;"the dead letter at 1 is not repeated"];
    n:count .qt.qpipe.job.example_quotes_decoder.published;
    .qt.qpipe.job.example_quotes_decoder.push[5#0j;til 5];
    .qunit.assertEquals[count .qt.qpipe.job.example_quotes_decoder.published;n;"and the broker resending 0-4 publishes nothing"]};
