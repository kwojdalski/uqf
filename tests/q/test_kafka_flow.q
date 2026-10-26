/ test_kafka_flow.q - the kafka_flow streaming job (.qt.qpipe.job.kafka_flow).
/ .
/ EVERY CASE HERE IS A DELIVERY ANOMALY, because that is the only thing this
/ job does. The mapping is a declared transform and verified on its own; what
/ can be wrong is which records survive, and every way of getting that wrong
/ is SILENT. Admit a replay and the desk sees a client trade twice; drop a
/ genuine record and it never existed. Neither throws.
/ .
/ The `0^` trap has its own test for that reason: seeding an unseen
/ partition's mark with zero instead of null reads as harmless and quietly
/ eats the first record of every partition.
/ .
/ The consumer's half (publish, then commit) is test_kafka_ingest.q; the cases
/ at the end of this file join the two, which is where "a crash between
/ publish and commit redelivers but does not duplicate" actually lives.


/ --- a recorder, standing in for the tickerplant ---------------------------

/ One (table; rows) pair per publish call, in call order.
/ .
/ A plain list rather than a table of enlisted batches: the batches are the
/ thing under test and a column holding them needs unwrapping at every use,
/ which is one more place for a helper to be wrong than this suite can afford.
.qt.qpipe.job.kafka_flow.published:()

.qt.qpipe.job.kafka_flow.recorder:{[t;x] `.qt.qpipe.job.kafka_flow.published set .qt.qpipe.job.kafka_flow.published,enlist (t;x); count x}

/ Every row published onto one table since the last reset, as ONE table.
/ `,/` and not `raze`: raze flattens one level, so over a list of tables it
/ gives back a list of tables and `count` then answers how many BATCHES.
.qt.qpipe.job.kafka_flow.rows_of:{[tbl]
    p:.qt.qpipe.job.kafka_flow.published;
    $[count s:p[;1] where tbl=p[;0]; (,/) s; ()]}

/ client_flow's rows.
.qt.qpipe.job.kafka_flow.all_rows:{[] .qt.qpipe.job.kafka_flow.rows_of `client_flow}

/ The table name of each publish call, in order.
/ NOT `tables`, which is a q builtin - assigning over it throws 'assign at load.
.qt.qpipe.job.kafka_flow.published_to:{[] first each .qt.qpipe.job.kafka_flow.published}

/ How many times the job called publish. Distinct from the row count: a job
/ that published an empty batch would be wrong in a way a row count hides.
.qt.qpipe.job.kafka_flow.calls:{[] count .qt.qpipe.job.kafka_flow.published}

/ --- driving it ------------------------------------------------------------

/ Empty the job's state AND point its publish somewhere, before driving it.
/ .
/ The wiring is the part that matters, and test_cross_arbitrage.q records why
/ at length: `publish` starts as `.qetl.job.stream.unwired`, which THROWS, and
/ on_batch only reaches it when a batch produces output. A test that drove
/ the job and happened to publish nothing would pass while leaving publish
/ unwired - and half this suite's cases assert that nothing is published.
.qt.qpipe.job.kafka_flow.drive_ready:{[]
    .qetl.job.stream.reset `kafka_flow;
    `.qt.qpipe.job.kafka_flow.published set ();
    .qetl.job.stream.wire[`kafka_flow;.qt.qpipe.job.kafka_flow.recorder];
    }

/ The topic registry is shared: a case that declares a topic puts it back.
.qt.qpipe.job.kafka_flow.registry_saved:()
.qt.qpipe.job.kafka_flow.beforeNamespace_load:{[] `.qt.qpipe.job.kafka_flow.registry_saved set .qetl.kafka.topic.registry;}
.qt.qpipe.job.kafka_flow.afterNamespace_restore:{[] `.qetl.kafka.topic.registry set .qt.qpipe.job.kafka_flow.registry_saved; .qetl.job.stream.reset `kafka_flow;}

.qt.qpipe.job.kafka_flow.t0:2026.09.25D09:00:00.000000000

.qt.qpipe.job.kafka_flow.topic:`uqf.client.flow

/ One client trade as a producer writes it; the price varies with the offset
/ only so two rows are distinguishable.
.qt.qpipe.job.kafka_flow.payload:{[off] "{\"sym\":\"EURUSD\",\"side\":\"buy\",\"qty\":1000000,\"price\":",string[1.1+0.001*off],",\"client\":\"ACME\",\"trade_id\":",string[100+off],"}"}

/ A batch as it arrives FROM THE PLANT: `time` prepended, because .u.upd
/ stamped it on the way in. The job has to drop it, and a fixture without it
/ would never prove that.
.qt.qpipe.job.kafka_flow.batch_on:{[tp;parts;offs;payloads]
    n:count offs;
    ([] time:.qt.qpipe.job.kafka_flow.t0+1000000000*til n;
        topic:n#tp; partition:parts; offset:offs; msg_key:n#enlist "";
        broker_time:(.qt.qpipe.job.kafka_flow.t0+1000000000*til n)-0D00:00:00.002;
        payload:payloads)}

.qt.qpipe.job.kafka_flow.batch:{[parts;offs]
    .qt.qpipe.job.kafka_flow.batch_on[.qt.qpipe.job.kafka_flow.topic;parts;offs;.qt.qpipe.job.kafka_flow.payload each offs]}

.qt.qpipe.job.kafka_flow.push:{[parts;offs] .qpipe.job.kafka_flow.on_batch[`kafka_message;.qt.qpipe.job.kafka_flow.batch[parts;offs]]}

/ The mark of one (topic;partition), null when unseen.
.qt.qpipe.job.kafka_flow.mark:{[tp;p] (.qpipe.job.kafka_flow.marks (tp;p))`offset}

/ --- the happy path --------------------------------------------------------

.qt.qpipe.job.kafka_flow.test_a_fresh_batch_publishes_every_row:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.calls[];1;"one publish for one batch"];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];3;"nothing is dropped when nothing has been seen"]};

.qt.qpipe.job.kafka_flow.test_the_published_table_is_the_one_declared:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0j;0 1j];
    .qunit.assertEquals[first .qt.qpipe.job.kafka_flow.published_to[];`client_flow;
        "published onto client_flow, not onto the raw table it read"]};

.qt.qpipe.job.kafka_flow.test_the_payload_arrives_as_typed_columns:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[enlist 0j;enlist 5j];
    r:first .qt.qpipe.job.kafka_flow.all_rows[];
    .qunit.assertEquals[(r`sym;r`side;r`qty;r`client;r`trade_id);(`EURUSD;`buy;1000000f;`ACME;105j);"decoded from the JSON"];
    .qunit.assertEquals[r`price;1.105;"the price the producer wrote"]};

/ --- the redelivery, which is the whole point ------------------------------

.qt.qpipe.job.kafka_flow.test_the_same_batch_delivered_twice_publishes_once:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.calls[];1;
        "the replay publishes NOTHING - not an empty batch, no call at all"];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];3;"the three original rows, once"]};

/ --- a partition whose offsets restart (#1519) ---------------------------------

/ The feed resets its mark and republishes records 0-2 of a recreated topic after 0-4; kafka_flow's own mark was still 4 and dropped all three.
.qt.qpipe.job.kafka_flow.push_later:{[parts;offs]
    b:.qt.qpipe.job.kafka_flow.batch[parts;offs];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;update broker_time:broker_time+0D01:00:00 from b]}

.qt.qpipe.job.kafka_flow.test_records_after_a_partitions_offsets_restart_are_decoded:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[5#0j;0 1 2 3 4j];
    .qt.qpipe.job.kafka_flow.push_later[3#0j;0 1 2j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];8;"five records, then the three that restarted: eight rows"];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j];2j;"the mark follows the restarted run"];
    / and a redelivery of that run is a plain redelivery again
    .qt.qpipe.job.kafka_flow.push_later[3#0j;0 1 2j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];8;"a redelivery that reaches the mark is still dropped"]};

/ A rebalance that re-reads batches the job has already decoded is wholly below the mark, but it is not a restart: the records are not newer.
.qt.qpipe.job.kafka_flow.test_a_redelivery_wholly_below_the_mark_is_not_decoded_again:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[8#0j;til 8];
    .qt.qpipe.job.kafka_flow.push[3#0j;2 3 4j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];8;"offsets 2-4 again, after 0-7: dropped, not mistaken for a restart"];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j];7j;"and the mark did not move"]};

.qt.qpipe.job.kafka_flow.test_a_restart_on_one_partition_does_not_touch_another:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[(3#0j),3#1j;0 1 2 0 1 2j];
    .qt.qpipe.job.kafka_flow.push_later[(2#0j),1#1j;0 1 2j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];8;"partition 0 restarted; partition 1's 2 is only a redelivery"]};

/ The log of a run in which partition 0 went 0 1 2, then restarted with new records 0 1 (a later broker_time), and kafka_flow decoded only the new 0 before going down.
.qt.qpipe.job.kafka_flow.offset_restart_log:{[]
    raw:.qt.qpipe.job.kafka_flow.batch_on[.qt.qpipe.job.kafka_flow.topic;5#0j;0 1 2 0 1j;.qt.qpipe.job.kafka_flow.payload each 0 1 2 5 6j];
    raw:update broker_time:broker_time+0D01:00:00 from raw where i>=3;
    env:.qetl.kafka.envelope_columns#raw;
    old:(.qetl.kafka.process env 0 1 2)[`out]`client_flow;
    new0:(.qetl.kafka.process env enlist 3)[`out]`client_flow;
    ((`kafka_message;raw 0 1 2);(`client_flow;old);(`kafka_message;raw 3 4);(`client_flow;new0))}

.qt.qpipe.job.kafka_flow.test_a_replay_after_an_offset_restart_handles_the_restarted_record_that_was_not_handled:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qetl.job.stream.start[`kafka_flow;.qt.qpipe.job.kafka_flow.replay_transport[.qt.qpipe.job.kafka_flow.offset_restart_log[]]];
    r:.qt.qpipe.job.kafka_flow.all_rows[];
    .qunit.assertEquals[(r`offset;r`trade_id);(enlist 1j;enlist 106j);"only the restarted offset 1 is owed: its coordinates match a record handled before, its broker_time does not"];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j];1j;"and the mark is the top of the last run, not the old run's 2"];
    n:count .qt.qpipe.job.kafka_flow.published;
    .qt.qpipe.job.kafka_flow.push[(enlist 0j);enlist 2j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.published;n+1;"the new run's offset 2 is not mistaken for the old run's"]};

/ #1659: a decoder down across a topic restart holds both runs in its log; keyed on the offset alone, the new record at each reused offset was dropped.
.qt.qpipe.job.kafka_flow.test_a_replay_holding_both_runs_of_a_restarted_partition_decodes_every_record:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    raw:.qt.qpipe.job.kafka_flow.batch_on[.qt.qpipe.job.kafka_flow.topic;5#0j;0 1 2 0 1j;.qt.qpipe.job.kafka_flow.payload each 0 1 2 5 6j];
    raw:update broker_time:broker_time+0D01:00:00 from raw where i>=3;
    .qetl.job.stream.start[`kafka_flow;.qt.qpipe.job.kafka_flow.replay_transport[enlist (`kafka_message;raw)]];
    r:.qt.qpipe.job.kafka_flow.all_rows[];
    .qunit.assertEquals[r`trade_id;100 101 102 105 106j;"both runs decode, none dropped for sharing an offset"];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j];1j;"the mark is the top of the last run"];
    n:count .qt.qpipe.job.kafka_flow.published;
    .qt.qpipe.job.kafka_flow.push_later[2#0j;0 1j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.published;n;"a redelivery of the new run is still dropped"]};

/ #1659: one live batch carrying the old run's tail and then the restarted records.
.qt.qpipe.job.kafka_flow.test_a_live_batch_with_the_old_tail_and_restarted_records_decodes_both:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[3#0j;0 1 2j];
    raw:.qt.qpipe.job.kafka_flow.batch_on[.qt.qpipe.job.kafka_flow.topic;4#0j;3 4 0 1j;.qt.qpipe.job.kafka_flow.payload each 3 4 5 6j];
    raw:update broker_time:broker_time+0D01:00:00 from raw where i>=2;
    .qpipe.job.kafka_flow.on_batch[`kafka_message;raw];
    r:.qt.qpipe.job.kafka_flow.all_rows[];
    .qunit.assertEquals[count r;7;"three, then the old tail 3 4 and the restarted 0 1: none lost"];
    .qunit.assertEquals[r`trade_id;100 101 102 103 104 105 106j;"the restarted record at reused offset 0 is decoded"];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j];1j;"the mark is the top of the restarted run, not the old tail's 4"];
    n:count .qt.qpipe.job.kafka_flow.published;
    .qt.qpipe.job.kafka_flow.push_later[2#0j;0 1j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.published;n;"a redelivery of the restarted run is dropped"]};

.qt.qpipe.job.kafka_flow.test_a_batch_straddling_the_mark_publishes_only_the_new_part:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;1 2 3j];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];0 1 2 3j;
        "offsets 0 1 2 then 3 - not 0 1 2 1 2 3 - in topic order, with no gap"]};

.qt.qpipe.job.kafka_flow.test_identical_partition_and_offset_on_different_topics_are_both_published:{[t]
    / The key is (topic;partition;offset). Keyed on (partition;offset) the second
    / topic's records would all be dropped as repeats of the first's.
    .qetl.kafka.topic.define[`uqf.client.flow.b;`mapping`output!(`kafka_client_flow;`client_flow)];
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0j;0 1j];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;.qt.qpipe.job.kafka_flow.batch_on[`uqf.client.flow.b;0 0j;0 1j;.qt.qpipe.job.kafka_flow.payload each 0 1j]];
    r:.qt.qpipe.job.kafka_flow.all_rows[];
    .qunit.assertEquals[(r`topic;r`partition;r`offset);(`uqf.client.flow`uqf.client.flow`uqf.client.flow.b`uqf.client.flow.b;0 0 0 0j;0 1 0 1j);
        "four records: offsets 0 and 1 on partition 0 of each topic"];
    .qunit.assertEquals[(.qt.qpipe.job.kafka_flow.mark[`uqf.client.flow;0j];.qt.qpipe.job.kafka_flow.mark[`uqf.client.flow.b;0j]);1 1j;"and a mark per topic"];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;.qt.qpipe.job.kafka_flow.batch_on[`uqf.client.flow.b;0 0j;0 1j;.qt.qpipe.job.kafka_flow.payload each 0 1j]];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];4;"and a redelivery of the second topic is dropped by ITS mark"]};

/ --- the ways of getting the mark wrong ------------------------------------

.qt.qpipe.job.kafka_flow.test_offset_zero_on_an_unseen_partition_is_published:{[t]
    / The `0^mark` trap: offset 0 is a REAL record, and `0 > 0` is false.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[enlist 7j;enlist 0j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];1;
        "offset 0 is the topic's first record, not a missing value"]};

.qt.qpipe.job.kafka_flow.test_partitions_are_marked_independently:{[t]
    / A replay on partition 0 must not shadow a genuine first record on
    / partition 1. A single global mark would drop it.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qt.qpipe.job.kafka_flow.push[0 1j;1 0j];
    .qunit.assertEquals[exec partition from .qt.qpipe.job.kafka_flow.all_rows[];0 0 0 1j;"partition 1's offset 0 survives partition 0's replay"]};

.qt.qpipe.job.kafka_flow.test_an_out_of_order_batch_does_not_lower_the_mark:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[6#0j;0 1 2 3 4 5j];
    .qt.qpipe.job.kafka_flow.push[0 0j;2 3j];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j];5j;"the mark stays at the highest offset handled"];
    .qt.qpipe.job.kafka_flow.push[0 0j;4 5j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];6;"and 4 and 5 are still refused"]};

.qt.qpipe.job.kafka_flow.test_a_coordinate_repeated_within_one_batch_is_published_once:{[t]
    / The mark cannot catch this one: both copies are above it.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    / a redelivered record is the same record: it carries the first copy's broker_time
    b:update broker_time:first broker_time by partition,offset from .qt.qpipe.job.kafka_flow.batch[0 0 0j;0 0 1j];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;b];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];0 1j;"the first copy kept, in batch order"]};

/ --- the dead letters ------------------------------------------------------

.qt.qpipe.job.kafka_flow.test_a_malformed_record_is_dead_lettered_and_marked_handled:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    b:.qt.qpipe.job.kafka_flow.batch_on[.qt.qpipe.job.kafka_flow.topic;0 0 0j;0 1 2j;(.qt.qpipe.job.kafka_flow.payload[0];"not json";.qt.qpipe.job.kafka_flow.payload[2])];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;b];
    d:.qt.qpipe.job.kafka_flow.rows_of[`kafka_dead_letter];
    .qunit.assertEquals[(d`offset;d`reason;d`payload);(enlist 1j;enlist `malformed;enlist "not json");"on record, with its payload as received"];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];0 2j;"the neighbours publish"];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;b];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.rows_of `kafka_dead_letter;1;"a redelivery of the bad record is not dead-lettered a second time"]};

.qt.qpipe.job.kafka_flow.test_a_record_on_an_undeclared_topic_is_dead_lettered:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;.qt.qpipe.job.kafka_flow.batch_on[`unknown.topic;enlist 0j;enlist 0j;enlist "{}"]];
    .qunit.assertEquals[(.qt.qpipe.job.kafka_flow.rows_of[`kafka_dead_letter])`reason;enlist `no_mapping;"never silently dropped"];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.published_to[];enlist `kafka_dead_letter;"and nothing else was published"]};

/ --- the shape ------------------------------------------------------------

.qt.qpipe.job.kafka_flow.test_the_plant_stamped_time_is_not_republished:{[t]
    / Invariant 1: .u.upd stamps its own.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0j;0 1j];
    .qunit.assertTrue[not `time in cols .qt.qpipe.job.kafka_flow.all_rows[];"the batch's own time is dropped before publishing"]};

.qt.qpipe.job.kafka_flow.test_the_output_carries_the_kafka_coordinates:{[t]
    / Not bookkeeping: they let a desk name the exact record a row came from,
    / and let a restart read back what was handled.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0j;0 1j];
    .qunit.assertTrue[all `topic`partition`offset`broker_time in cols .qt.qpipe.job.kafka_flow.all_rows[];
        "topic, partition and offset, and the broker's own clock - `time` alone cannot show the lag"]};

.qt.qpipe.job.kafka_flow.test_a_batch_on_another_table_is_ignored:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qpipe.job.kafka_flow.on_batch[`quote;.qt.qpipe.job.kafka_flow.batch[0 0j;0 1j]];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.calls[];0;"a job subscribed to one table acts on one table"]};

.qt.qpipe.job.kafka_flow.test_an_empty_batch_publishes_nothing:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qpipe.job.kafka_flow.on_batch[`kafka_message;0#.qt.qpipe.job.kafka_flow.batch[enlist 0j;enlist 0j]];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.calls[];0;"an empty batch is legal and produces no write"]};

.qt.qpipe.job.kafka_flow.test_the_job_publishes_every_declared_topics_output_and_the_dead_letters:{[t]
    p:(.qetl.job.stream.def `kafka_flow)`publishes;
    .qunit.assertTrue[all `client_flow`kafka_dead_letter in p;"client_flow for the client topic, and the dead letters"]};

/ --- the output contract ---------------------------------------------------

/ What test_job_output_contracts.q drives kafka_flow with, so client_flow and
/ kafka_dead_letter are held to their plant tables by name, order and type.
/ It does NOT wire publish - that suite owns the wiring - but it resets the
/ marks: a driver whose every record was a repeat would publish nothing.
.qt.qpipe.job.kafka_flow.contract_driver:{[]
    .qetl.job.stream.reset `kafka_flow;
    .qpipe.job.kafka_flow.on_batch[`kafka_message;.qt.qpipe.job.kafka_flow.batch_on[.qt.qpipe.job.kafka_flow.topic;0 0 0j;0 1 2j;(.qt.qpipe.job.kafka_flow.payload[0];"not json";.qt.qpipe.job.kafka_flow.payload[2])]];
    }

/ --- restarts: restoring what was handled and catching up ------------------

/ What the plant's log holds when kafka_flow1 handled offsets 0, 1 (a dead
/ letter) and 3, went down, and the consumer published 2, 3 and 4 meanwhile:
/ raw rows and the rows they produced, in the order they were logged. Offset 2
/ is a GAP below the highest handled offset, which a mark would call done.
.qt.qpipe.job.kafka_flow.restart_log:{[]
    pl:(.qt.qpipe.job.kafka_flow.payload[0];"not json";.qt.qpipe.job.kafka_flow.payload[2];.qt.qpipe.job.kafka_flow.payload[3];.qt.qpipe.job.kafka_flow.payload[4]);
    raw:.qt.qpipe.job.kafka_flow.batch_on[.qt.qpipe.job.kafka_flow.topic;5#0j;til 5;pl];
    out:.qetl.kafka.process .qetl.kafka.envelope_columns#raw 0 1 3;
    flow:out[`out]`client_flow;
    dead:out`dead;
    ((`kafka_message;raw 0 1 3);(`client_flow;flow);(`kafka_dead_letter;dead);
     (`kafka_message;raw 2 3 4))}

.qt.qpipe.job.kafka_flow.replay_transport:{[replayed]
    `connect`publisher`subscribe`timer!(
        {[] };
        {[] .qt.qpipe.job.kafka_flow.recorder};
        {[replayed;tbls;h;replay] if[replay; {[h;m] h . m}[h] each replayed];}[replayed];
        {[n;p;f] })}

/ The bug this guards: rows published while the job was down reached the
/ plant's log and nothing else, because the job subscribed without replay and
/ the consumer had already committed their offsets.
.qt.qpipe.job.kafka_flow.test_a_restart_handles_exactly_the_records_that_were_not_handled:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qetl.job.stream.start[`kafka_flow;.qt.qpipe.job.kafka_flow.replay_transport[.qt.qpipe.job.kafka_flow.restart_log[]]];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];2 4j;
        "offset 2 (the gap) and 4 (arrived while down); 0, 1 and 3 are not handled a second time"];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.rows_of `kafka_dead_letter;0;"the dead letter at 1 is not repeated"];
    .qunit.assertEquals[(.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j];count .qpipe.job.kafka_flow.held;count .qpipe.job.kafka_flow.done);(4j;0;0);
        "the mark is restored and moved on, and nothing is left held"]};

.qt.qpipe.job.kafka_flow.test_after_a_restart_a_redelivery_of_a_handled_record_is_dropped:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qetl.job.stream.start[`kafka_flow;.qt.qpipe.job.kafka_flow.replay_transport[.qt.qpipe.job.kafka_flow.restart_log[]]];
    n:count .qt.qpipe.job.kafka_flow.published;
    .qt.qpipe.job.kafka_flow.push[5#0j;til 5];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.published;n;"the broker resends 0-4 after the restart: nothing is published"]};

.qt.qpipe.job.kafka_flow.test_during_a_replay_raw_rows_are_held_not_published:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    `.qetl.job.stream.replaying set 1b;
    .qt.qpipe.job.kafka_flow.push[enlist 0j;enlist 5j];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[(.qt.qpipe.job.kafka_flow.calls[];count .qpipe.job.kafka_flow.held);(0;1);
        "a raw row is judged only once the whole log has said what was handled"]};

/ Raising the mark before publishing left it raised over rows that never went
/ out, so a redelivery of them was dropped as already seen.
.qt.qpipe.job.kafka_flow.test_a_publish_that_throws_does_not_move_the_mark:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qetl.job.stream.wire[`kafka_flow;{[t;x] '"plant down"}];
    r:@[.qt.qpipe.job.kafka_flow.push[enlist 0j;];enlist 0j;{x}];
    .qunit.assertEquals[(r;.qt.qpipe.job.kafka_flow.mark[.qt.qpipe.job.kafka_flow.topic;0j]);("plant down";0Nj);
        "the publish failed and the mark did not move"]};

/ --- the consumer and the job together --------------------------------------

/ The plant, as far as this file needs it: whatever the consumer publishes onto
/ kafka_message reaches kafka_flow's handler, as the subscription would.
.qt.qpipe.job.kafka_flow.plant:{[t;x] .qpipe.job.kafka_flow.on_batch[t;(([] time:(count x)#.qt.qpipe.job.kafka_flow.t0),'x)]; count x}

.qt.qpipe.job.kafka_flow.wired_transport:{[]
    `poll`publish`commit`assigned!(
        .qt.qetl.kafka.transport`poll;
        {[t;x] .qt.qetl.kafka.transport[`publish][t;x]; .qt.qpipe.job.kafka_flow.plant[t;x]};
        .qt.qetl.kafka.transport`commit;
        .qt.qetl.kafka.transport`assigned)}

/ Acceptance: failure between publication and offset commit means recoverable
/ redelivery, not duplicate normalized output.
.qt.qpipe.job.kafka_flow.test_a_crash_between_publish_and_commit_redelivers_without_duplicating_output:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qetl.kafka.reset[];
    ps:.qt.qpipe.job.kafka_flow.payload each 0 1 2j;
    m:.qt.qetl.kafka.msgs[.qt.qpipe.job.kafka_flow.topic;0 0 0;0 1 2;ps];
    tr:.qt.qpipe.job.kafka_flow.wired_transport[];
    / consumer 1: publishes, then its commit fails - the crash window
    `.qt.qetl.kafka.inbox set enlist m;
    `.qt.qetl.kafka.failing set enlist `commit;
    .qetl.kafka.cycle[tr;.qetl.kafka.state_new[]];
    / consumer 2: nothing was committed, so the broker sends the same records again
    `.qt.qetl.kafka.failing set `symbol$();
    `.qt.qetl.kafka.inbox set enlist m;
    .qetl.kafka.cycle[tr;.qetl.kafka.state_new[]];
    raw:(,/) .qt.qetl.kafka.of[`publish][;2];
    .qunit.assertEquals[count raw;6;"the raw table recorded BOTH deliveries - it is the audit trail"];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];0 1 2j;"and client_flow has each trade once"];
    .qunit.assertEquals[(last .qt.qetl.kafka.log) 2;(enlist 0i)!enlist 3j;"the second consumer committed"]};

/ Acceptance: a rebalance hands a partition's records to another member, which
/ re-reads from the last commit; nothing is lost and nothing doubled.
.qt.qpipe.job.kafka_flow.test_a_rebalance_re_read_is_absorbed:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qetl.kafka.reset[];
    tr:.qt.qpipe.job.kafka_flow.wired_transport[];
    / member A reads 0-2 of partition 0 and 0-1 of partition 1, then loses partition 1 before committing
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[.qt.qpipe.job.kafka_flow.topic;0 0 0 1 1;0 1 2 0 1;.qt.qpipe.job.kafka_flow.payload each 0 1 2 0 1j];
    `.qt.qetl.kafka.assignment set ([] topic:enlist .qt.qpipe.job.kafka_flow.topic; partition:enlist 0);
    .qetl.kafka.cycle[tr;.qetl.kafka.state_new[]];
    / member B, now owning partition 1, starts at its last committed offset (none): reads 0-3
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[.qt.qpipe.job.kafka_flow.topic;4#1;til 4;.qt.qpipe.job.kafka_flow.payload each til 4];
    `.qt.qetl.kafka.assignment set ([] topic:enlist .qt.qpipe.job.kafka_flow.topic; partition:enlist 1);
    .qetl.kafka.cycle[tr;.qetl.kafka.state_new[]];
    r:.qt.qpipe.job.kafka_flow.all_rows[];
    .qunit.assertEquals[asc (r`partition),'r`offset;asc ((0 0 0 1 1 1 1j),'0 1 2 0 1 2 3j);
        "partition 0: 0-2; partition 1: 0-3 - every record once, none missing"]};

.qt.qpipe.job.kafka_flow.test_a_topic_whose_output_is_not_published_is_found:{[t]
    .qunit.assertEquals[.qetl.kafka.decoder.unpublished `kafka_flow;`symbol$();"every declared topic's output is one the job publishes"];
    .qetl.kafka.topic.registry[`orphan.topic]:`mapping`output`format`owner!(`kafka_client_flow;`trades;`json;`kafka_flow);
    .qunit.assertEquals[.qetl.kafka.decoder.unpublished `kafka_flow;enlist `orphan.topic;"and one that is not is named"]};
