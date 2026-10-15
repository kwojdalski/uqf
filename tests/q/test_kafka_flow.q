/ test_kafka_flow.q - the kafka_flow streaming job (.qt.qpipe.job.kafka_flow).
/ .
/ EVERY CASE HERE IS A DELIVERY ANOMALY, because that is the only thing this
/ job does. The transform is a column projection and could not be wrong in an
/ interesting way; what can be wrong is which rows survive it, and every way
/ of getting that wrong is SILENT. Admit a replay and the desk sees a client
/ trade twice; drop a genuine record and it never existed. Neither throws.
/ .
/ The `0^` trap has its own test for that reason: seeding an unseen
/ partition's mark with zero instead of null reads as harmless and quietly
/ eats the first record of every partition.


/ --- a recorder, standing in for the tickerplant ---------------------------

/ One (table; rows) pair per publish call, in call order.
/ .
/ A plain list rather than a table of enlisted batches: the batches are the
/ thing under test and a column holding them needs unwrapping at every use,
/ which is one more place for a helper to be wrong than this suite can afford.
.qt.qpipe.job.kafka_flow.published:()

.qt.qpipe.job.kafka_flow.recorder:{[t;x] `.qt.qpipe.job.kafka_flow.published set .qt.qpipe.job.kafka_flow.published,enlist (t;x); count x}

/ Every row published since the last reset, as ONE table.
/ .
/ `,/` and not `raze`: raze flattens one level, so over a list of tables it
/ gives back a list of tables and `count` then answers how many BATCHES
/ there were - which is 1 exactly when the assertion wanted 3, and reads as
/ a job bug rather than a helper bug.
.qt.qpipe.job.kafka_flow.all_rows:{[] $[0=count .qt.qpipe.job.kafka_flow.published; (); (,/) last each .qt.qpipe.job.kafka_flow.published]}

/ The table name of each publish call, in order.
/ .
/ NOT `tables`, which is a q builtin - assigning over it throws 'assign at
/ load time, which is the good outcome. qlinter QF-checks for exactly this.
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
/ unwired - and this suite has more of those than most, since half its cases
/ assert that nothing is published.
.qt.qpipe.job.kafka_flow.drive_ready:{[]
    `.qpipe.job.kafka_flow.high_water set (`long$())!`long$();
    `.qt.qpipe.job.kafka_flow.published set ();
    .qetl.job.stream.wire[`kafka_flow;.qt.qpipe.job.kafka_flow.recorder];
    }

.qt.qpipe.job.kafka_flow.beforeNamespace_load:{[] `.qt.qpipe.job.kafka_flow.saved set .qpipe.job.kafka_flow.high_water;}
.qt.qpipe.job.kafka_flow.afterNamespace_restore:{[] `.qpipe.job.kafka_flow.high_water set .qt.qpipe.job.kafka_flow.saved;}

.qt.qpipe.job.kafka_flow.t0:2026.09.25D09:00:00.000000000

/ A batch as it arrives FROM THE PLANT: `time` prepended, because .u.upd
/ stamped it on the way in. The job has to drop it, and a fixture without it
/ would never prove that.
/ .
/ The payload varies with the row index only so two rows are distinguishable;
/ nothing here is a market.
.qt.qpipe.job.kafka_flow.batch:{[parts;offs]
    n:count parts;
    ([] time:.qt.qpipe.job.kafka_flow.t0+1000000000*til n;
        broker_time:(.qt.qpipe.job.kafka_flow.t0+1000000000*til n)-0D00:00:00.002;
        partition:parts;
        offset:offs;
        sym:n#`EURUSD`GBPUSD;
        side:n#`buy`sell;
        qty:n#1000000 2000000f;
        price:n#1.1000 1.2700;
        client:n#`ACME`BETA;
        trade_id:100+til n)}

.qt.qpipe.job.kafka_flow.push:{[parts;offs] .qpipe.job.kafka_flow.on_batch[`kafka_client_flow;.qt.qpipe.job.kafka_flow.batch[parts;offs]]}

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

/ --- the redelivery, which is the whole point ------------------------------

.qt.qpipe.job.kafka_flow.test_the_same_batch_delivered_twice_publishes_once:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.calls[];1;
        "the replay publishes NOTHING - not an empty batch, no call at all"];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];3;"the three original rows, once"]};

.qt.qpipe.job.kafka_flow.test_a_batch_straddling_the_mark_publishes_only_the_new_part:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;1 2 3j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];4;"offsets 0 1 2 then 3 - not 0 1 2 1 2 3"];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];0 1 2 3j;
        "and in topic order, with no gap"]};

/ --- the ways of getting the mark wrong ------------------------------------

.qt.qpipe.job.kafka_flow.test_offset_zero_on_an_unseen_partition_is_published:{[t]
    / The `0^high_water[p]` trap, which reads as a harmless default: offset 0
    / is a REAL record, and `0 > 0` is false, so seeding with zero silently
    / eats the first record of every partition. Null is the right seed
    / because `0 > 0N` is true.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[enlist 7j;enlist 0j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];1;
        "offset 0 is the topic's first record, not a missing value"]};

.qt.qpipe.job.kafka_flow.test_partitions_are_marked_independently:{[t]
    / A replay on partition 0 must not shadow a genuine first record on
    / partition 1. A single global mark - the obvious simplification - would
    / drop it, because 0 is below partition 0's mark of 2.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0j;0 1 2j];
    .qt.qpipe.job.kafka_flow.push[0 1j;1 0j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];4;"partition 1's offset 0 survives partition 0's replay"];
    .qunit.assertEquals[exec partition from .qt.qpipe.job.kafka_flow.all_rows[];0 0 0 1j;"and it is the one that survived"]};

.qt.qpipe.job.kafka_flow.test_an_out_of_order_batch_does_not_lower_the_mark:{[t]
    / `|` rather than a plain upsert in advance. Lowering the mark to 3 would
    / re-admit offsets 4 and 5 on the next delivery that carried them.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0 0 0 0 0j;0 1 2 3 4 5j];
    .qt.qpipe.job.kafka_flow.push[0 0j;2 3j];
    .qunit.assertEquals[.qpipe.job.kafka_flow.high_water 0j;5j;"the mark stays at the highest offset ever published"];
    .qt.qpipe.job.kafka_flow.push[0 0j;4 5j];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];6;"and 4 and 5 are still refused"]};

.qt.qpipe.job.kafka_flow.test_a_coordinate_repeated_within_one_batch_is_published_once:{[t]
    / The high-water mark cannot catch this one: both copies are above it.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qpipe.job.kafka_flow.on_batch[`kafka_client_flow;
        .qt.qpipe.job.kafka_flow.batch[0 0 0j;0 0 1j]];
    .qunit.assertEquals[count .qt.qpipe.job.kafka_flow.all_rows[];2;"(0;0) twice in one batch is one record"];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];0 1j;"the first copy kept, in batch order"]};

/ --- the shape ------------------------------------------------------------

.qt.qpipe.job.kafka_flow.test_the_plant_stamped_time_is_not_republished:{[t]
    / Invariant 1: .u.upd stamps its own. Publishing the incoming `time`
    / would make the column one wider than the plant's table and be refused
    / at the tickerplant, in a process the test suite never starts.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0j;0 1j];
    .qunit.assertTrue[not `time in cols .qt.qpipe.job.kafka_flow.all_rows[];
        "the batch's own time is dropped before publishing"]};

.qt.qpipe.job.kafka_flow.test_the_output_carries_the_kafka_coordinates:{[t]
    / Not bookkeeping to be stripped on the way out: they are what lets a
    / desk name the exact record a client_flow row came from, and what a
    / restart-seeding fix would read back.
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qt.qpipe.job.kafka_flow.push[0 0j;0 1j];
    .qunit.assertTrue[all `partition`offset in cols .qt.qpipe.job.kafka_flow.all_rows[];
        "partition and offset survive onto client_flow"];
    .qunit.assertTrue[`broker_time in cols .qt.qpipe.job.kafka_flow.all_rows[];
        "as does the broker's own clock - `time` alone cannot show the lag"]};

.qt.qpipe.job.kafka_flow.test_a_batch_on_another_table_is_ignored:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qpipe.job.kafka_flow.on_batch[`quote;.qt.qpipe.job.kafka_flow.batch[0 0j;0 1j]];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.calls[];0;"a job subscribed to one table acts on one table"]};

.qt.qpipe.job.kafka_flow.test_an_empty_batch_publishes_nothing:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qpipe.job.kafka_flow.on_batch[`kafka_client_flow;0#.qt.qpipe.job.kafka_flow.batch[enlist 0j;enlist 0j]];
    .qunit.assertEquals[.qt.qpipe.job.kafka_flow.calls[];0;"an empty batch is legal and produces no write"]};

/ --- the output contract ---------------------------------------------------

/ What test_job_output_contracts.q drives kafka_flow with, so client_flow is
/ held to its plant table by name, order and type.
/ .
/ Resets the mark first, and must: this suite leaves marks behind, and a
/ driver whose every row was a replay would publish nothing and leave the
/ contract suite with no output to check. It does NOT wire publish - that
/ suite owns the wiring.
.qt.qpipe.job.kafka_flow.contract_driver:{[]
    `.qpipe.job.kafka_flow.high_water set (`long$())!`long$();
    .qpipe.job.kafka_flow.on_batch[`kafka_client_flow;.qt.qpipe.job.kafka_flow.batch[0 0j;0 1j]];
    }

/ --- restarts: restoring the marks and catching up -------------------------

/ What the plant's log holds after kafka_flow1 published offsets 0 and 1,
/ went down, and the consumer published 2 and 3 meanwhile: each raw row,
/ and the client_flow row it produced, interleaved as they were logged.
.qt.qpipe.job.kafka_flow.restart_log:{[]
    raw:.qt.qpipe.job.kafka_flow.batch[0 0 0 0j;0 1 2 3j];
    flow:{[r] select time, broker_time, sym, side, qty, price, client, trade_id, partition, offset from r};
    ((`kafka_client_flow;1#raw);(`client_flow;flow 1#raw);
     (`kafka_client_flow;1_2#raw);(`client_flow;flow 1_2#raw);
     (`kafka_client_flow;2_raw))}

.qt.qpipe.job.kafka_flow.replay_transport:{[replayed]
    `connect`publisher`subscribe`timer!(
        {[] };
        {[] .qt.qpipe.job.kafka_flow.recorder};
        {[replayed;tbls;h;replay] if[replay; {[h;m] h . m}[h] each replayed];}[replayed];
        {[n;p;f] })}

/ The bug: rows published while the job was down reached the plant's log and
/ nothing else, because the job subscribed without replay and the consumer
/ had already committed their offsets.
.qt.qpipe.job.kafka_flow.test_a_restart_publishes_exactly_the_rows_that_arrived_while_it_was_down:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qetl.job.stream.reset `kafka_flow;
    .qetl.job.stream.start[`kafka_flow;.qt.qpipe.job.kafka_flow.replay_transport[.qt.qpipe.job.kafka_flow.restart_log[]]];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.kafka_flow.all_rows[];2 3j;"offsets 2 and 3, and not 0 and 1 a second time"];
    .qunit.assertEquals[(.qpipe.job.kafka_flow.high_water 0j;count .qpipe.job.kafka_flow.held);(3j;0);
        "the mark is restored and moved on, and nothing is left held"]};

.qt.qpipe.job.kafka_flow.test_during_a_replay_raw_rows_are_held_not_published:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qetl.job.stream.reset `kafka_flow;
    `.qetl.job.stream.replaying set 1b;
    @[.qt.qpipe.job.kafka_flow.push[0j;];enlist 5j;{x}];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[(.qt.qpipe.job.kafka_flow.calls[];count .qpipe.job.kafka_flow.held);(0;1);
        "a raw row is judged only once the whole log has restored the marks"];
    `.qpipe.job.kafka_flow.held set 0#.qpipe.job.kafka_flow.held};

/ Raising the mark before publishing left it raised over rows that never
/ went out, so a redelivery of them was dropped as already seen.
.qt.qpipe.job.kafka_flow.test_a_publish_that_throws_does_not_move_the_mark:{[t]
    .qt.qpipe.job.kafka_flow.drive_ready[];
    .qetl.job.stream.wire[`kafka_flow;{[t;x] '"plant down"}];
    r:@[.qt.qpipe.job.kafka_flow.push[0j;];enlist 0j;{x}];
    .qunit.assertEquals[(r;.qpipe.job.kafka_flow.high_water 0j);("plant down";0Nj);
        "the publish failed and the mark did not move"]};
