/ test_kafka_crypto_example.q - the kafka_crypto_example bundle's decoder
/ (.qt.qpipe.job.crypto_trades_decoder, #1492).
/ .
/ The runnable example publishes a known batch with deliberately invalid
/ records and expects exact rows and dead letters; these cases are the same
/ expectations without a broker: what decodes, which record is dead-lettered
/ for which reason, that offset identity is (topic;partition;offset), and that a
/ redelivery or a restart never doubles the output.

.qt.qpipe.job.crypto_trades_decoder.published:()

.qt.qpipe.job.crypto_trades_decoder.recorder:{[t;x] `.qt.qpipe.job.crypto_trades_decoder.published set .qt.qpipe.job.crypto_trades_decoder.published,enlist (t;x); count x}

/ Every row published onto one table since the last reset, as one table.
.qt.qpipe.job.crypto_trades_decoder.rows_of:{[tbl]
    p:.qt.qpipe.job.crypto_trades_decoder.published;
    $[count s:p[;1] where tbl=p[;0]; (,/) s; ()]}

.qt.qpipe.job.crypto_trades_decoder.trades:{[] .qt.qpipe.job.crypto_trades_decoder.rows_of `crypto_trade}

/ Empty the job's state and point its publish at the recorder.
.qt.qpipe.job.crypto_trades_decoder.drive_ready:{[]
    .qetl.job.stream.reset `crypto_trades_decoder;
    `.qt.qpipe.job.crypto_trades_decoder.published set ();
    .qetl.job.stream.wire[`crypto_trades_decoder;.qt.qpipe.job.crypto_trades_decoder.recorder];
    }

/ The topic registry is shared: a case that declares a topic puts it back.
.qt.qpipe.job.crypto_trades_decoder.registry_saved:()
.qt.qpipe.job.crypto_trades_decoder.beforeNamespace_load:{[] `.qt.qpipe.job.crypto_trades_decoder.registry_saved set .qetl.kafka.topic.registry;}
.qt.qpipe.job.crypto_trades_decoder.afterNamespace_restore:{[] `.qetl.kafka.topic.registry set .qt.qpipe.job.crypto_trades_decoder.registry_saved; .qetl.job.stream.reset `crypto_trades_decoder;}

.qt.qpipe.job.crypto_trades_decoder.t0:2026.10.20D09:00:00.000000000

/ One trade as the publisher writes it; the price and the id vary with the offset so two rows are distinguishable.
.qt.qpipe.job.crypto_trades_decoder.payload:{[off]
    "{\"sym\":\"BTC-USD\",\"trade_id\":\"BTC-",(-6#"000000",string off),"\",\"ts\":\"2026-10-20T09:00:0",string[off mod 10],".250Z\",\"price\":",string[65000+0.5*off],",\"qty\":0.25,\"side\":\"buy\"}"}

/ A batch as it arrives from the plant: `time` prepended, which the job must drop.
.qt.qpipe.job.crypto_trades_decoder.batch_on:{[tp;parts;offs;payloads]
    n:count offs;
    ([] time:.qt.qpipe.job.crypto_trades_decoder.t0+1000000000*til n;
        topic:n#tp; partition:parts; offset:offs; msg_key:n#enlist "";
        broker_time:(.qt.qpipe.job.crypto_trades_decoder.t0+1000000000*til n)-0D00:00:00.002;
        payload:payloads)}

.qt.qpipe.job.crypto_trades_decoder.push_on:{[tp;parts;offs]
    .qpipe.job.crypto_trades_decoder.on_batch[`kafka_message;.qt.qpipe.job.crypto_trades_decoder.batch_on[tp;parts;offs;.qt.qpipe.job.crypto_trades_decoder.payload each offs]]}

.qt.qpipe.job.crypto_trades_decoder.push:{[parts;offs] .qt.qpipe.job.crypto_trades_decoder.push_on[`example.crypto.trades;parts;offs]}

/ The five deliberately invalid payloads the example's publisher sends, in the order it sends them: not JSON, an empty payload, a missing field, a numeric trade id, a refused trade (negative quantity).
.qt.qpipe.job.crypto_trades_decoder.invalid:{[]
    ("not json at all";
     "";
     "{\"sym\":\"BTC-USD\",\"trade_id\":\"BTC-X1\",\"ts\":\"2026-10-20T09:00:00.000Z\",\"qty\":0.25,\"side\":\"buy\"}";
     "{\"sym\":\"BTC-USD\",\"trade_id\":1234567,\"ts\":\"2026-10-20T09:00:00.000Z\",\"price\":65000.5,\"qty\":0.25,\"side\":\"buy\"}";
     "{\"sym\":\"BTC-USD\",\"trade_id\":\"BTC-X2\",\"ts\":\"2026-10-20T09:00:00.000Z\",\"price\":65000.5,\"qty\":-0.25,\"side\":\"buy\"}")}

/ --- it installs, and decodes ----------------------------------------------

.qt.qpipe.job.crypto_trades_decoder.test_the_bundle_s_topics_are_declared_and_owned_by_its_job:{[t]
    .qunit.assertEquals[asc .qetl.kafka.topic.owned `crypto_trades_decoder;asc `example.crypto.trades`example.crypto.trades.b;"both topics are this job's"];
    .qunit.assertEquals[.qetl.kafka.decoder.check `crypto_trades_decoder;`crypto_trades_decoder;"the job is whole"];
    .qunit.assertEquals[(.qetl.kafka.topic.def `example.crypto.trades.b)`output;`crypto_trade;"and decodes into the bundle's own table"]};

.qt.qpipe.job.crypto_trades_decoder.test_a_trade_decodes_with_its_types_values_and_coordinates:{[t]
    .qt.qpipe.job.crypto_trades_decoder.drive_ready[];
    .qt.qpipe.job.crypto_trades_decoder.push[enlist 1j;enlist 7j];
    r:first .qt.qpipe.job.crypto_trades_decoder.trades[];
    .qunit.assertEquals[(r`sym;r`trade_id;r`side);(`$"BTC-USD";`$"BTC-000007";`buy);"symbol, trade id as sent, side"];
    .qunit.assertEquals[(r`price;r`qty);65003.5 0.25;"the price and quantity the producer wrote"];
    .qunit.assertEquals[r`event_time;2026.10.20D09:00:07.250000000;"the trade's own clock, not the plant's or the broker's"];
    .qunit.assertEquals[(r`topic;r`partition;r`offset);(`example.crypto.trades;1j;7j);"the record's coordinates"];
    .qunit.assertEquals[type each (r`event_time;r`price;r`partition);-12 -9 -7h;"typed: timestamp, float, long"];
    .qunit.assertTrue[not `time in cols .qt.qpipe.job.crypto_trades_decoder.trades[];"the plant stamps time"]};

.qt.qpipe.job.crypto_trades_decoder.test_the_case_of_a_trade_id_is_kept_and_the_side_is_lowered:{[t]
    .qt.qpipe.job.crypto_trades_decoder.drive_ready[];
    p:"{\"sym\":\"ETH-USD\",\"trade_id\":\"eth-Ab9\",\"ts\":\"2026-10-20T09:00:00.000Z\",\"price\":3200.25,\"qty\":1.5,\"side\":\"SELL\"}";
    .qpipe.job.crypto_trades_decoder.on_batch[`kafka_message;.qt.qpipe.job.crypto_trades_decoder.batch_on[`example.crypto.trades;enlist 0j;enlist 0j;enlist p]];
    r:first .qt.qpipe.job.crypto_trades_decoder.trades[];
    .qunit.assertEquals[(r`trade_id;r`side);(`$"eth-Ab9";`sell);"an identifier is not case-folded; a side is normalised"]};

/ --- the dead letters ------------------------------------------------------

.qt.qpipe.job.crypto_trades_decoder.test_each_deliberately_invalid_record_is_dead_lettered_with_its_reason:{[t]
    .qt.qpipe.job.crypto_trades_decoder.drive_ready[];
    pl:(enlist .qt.qpipe.job.crypto_trades_decoder.payload[0]),.qt.qpipe.job.crypto_trades_decoder.invalid[];
    pl:pl,enlist .qt.qpipe.job.crypto_trades_decoder.payload[6];
    b:.qt.qpipe.job.crypto_trades_decoder.batch_on[`example.crypto.trades;7#0j;til 7;pl];
    .qpipe.job.crypto_trades_decoder.on_batch[`kafka_message;b];
    d:`offset xasc .qt.qpipe.job.crypto_trades_decoder.rows_of[`kafka_dead_letter];
    .qunit.assertEquals[(d`offset;d`reason);(1 2 3 4 5j;`malformed`empty_payload`malformed`malformed`mapping);"five dead letters: not json, empty, missing field, numeric id, refused"];
    .qunit.assertTrue[(d[`detail] 2) like "*price*";"the missing field is named"];
    .qunit.assertTrue[(d[`detail] 3) like "*trade_id*";"and so is the numeric id: a float would round it"];
    .qunit.assertTrue[(d[`detail] 4) like "*not a trade*";"and a refused trade says why"];
    .qunit.assertEquals[d`payload;.qt.qpipe.job.crypto_trades_decoder.invalid[];"each payload as received"];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.crypto_trades_decoder.trades[];0 6j;"the good neighbours published"];
    .qpipe.job.crypto_trades_decoder.on_batch[`kafka_message;b];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_trades_decoder.rows_of[`kafka_dead_letter];5;"a redelivery is not dead-lettered again"];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_trades_decoder.trades[];2;"nor published again"]};

/ --- offset identity: topic and partition, not offset alone -----------------

.qt.qpipe.job.crypto_trades_decoder.test_the_same_offset_on_two_partitions_and_two_topics_is_four_trades:{[t]
    .qt.qpipe.job.crypto_trades_decoder.drive_ready[];
    .qt.qpipe.job.crypto_trades_decoder.push[0 1j;0 0j];
    .qt.qpipe.job.crypto_trades_decoder.push_on[`example.crypto.trades.b;0 1j;0 0j];
    r:.qt.qpipe.job.crypto_trades_decoder.trades[];
    .qunit.assertEquals[(r`topic;r`partition;r`offset);(`example.crypto.trades`example.crypto.trades`example.crypto.trades.b`example.crypto.trades.b;0 1 0 1j;0 0 0 0j);"offset 0 of two partitions of two topics: four records, none dropped as another's repeat"];
    .qt.qpipe.job.crypto_trades_decoder.push_on[`example.crypto.trades.b;0 1j;0 0j];
    .qt.qpipe.job.crypto_trades_decoder.push[0 1j;0 0j];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_trades_decoder.trades[];4;"and a redelivery of any of them is dropped by its own (topic;partition) mark"]};

.qt.qpipe.job.crypto_trades_decoder.test_a_higher_offset_on_one_partition_does_not_hide_a_lower_one_on_another:{[t]
    .qt.qpipe.job.crypto_trades_decoder.drive_ready[];
    .qt.qpipe.job.crypto_trades_decoder.push[enlist 0j;enlist 9j];
    .qt.qpipe.job.crypto_trades_decoder.push[enlist 1j;enlist 3j];
    .qunit.assertEquals[exec partition!offset from .qt.qpipe.job.crypto_trades_decoder.trades[];0 1j!9 3j;"partition 1's offset 3 is below partition 0's mark of 9 and is still new"]};

/ --- the redelivery the commit order produces -------------------------------

.qt.qpipe.job.crypto_trades_decoder.test_a_redelivered_batch_is_published_once:{[t]
    .qt.qpipe.job.crypto_trades_decoder.drive_ready[];
    .qt.qpipe.job.crypto_trades_decoder.push[3#0j;0 1 2j];
    .qt.qpipe.job.crypto_trades_decoder.push[4#0j;0 1 2 3j];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.crypto_trades_decoder.trades[];0 1 2 3j;"offsets 0-2 delivered twice (the broker redelivered them: publish happened, commit did not), each one row"]};

/ --- the tree's decoder and this one never both answer ---------------------

.qt.qpipe.job.crypto_trades_decoder.test_the_trees_decoder_leaves_these_topics_to_their_owner:{[t]
    r:.qt.qpipe.job.crypto_trades_decoder.batch_on[`example.crypto.trades;enlist 0j;enlist 0j;enlist .qt.qpipe.job.crypto_trades_decoder.payload[0]];
    .qunit.assertEquals[count .qetl.kafka.decoder.mine[`kafka_flow;r];0;"kafka_flow neither decodes nor dead-letters it"];
    .qunit.assertEquals[count .qetl.kafka.decoder.mine[`crypto_trades_decoder;r];1;"this job does"]};

/ --- the tree's suites, which hold every job to a contract ----------------

/ What test_job_output_contracts.q drives the job with: a good, a malformed and a refused record, so each published table is held to its plant table's shape.
.qt.qpipe.job.crypto_trades_decoder.contract_driver:{[]
    .qetl.job.stream.reset `crypto_trades_decoder;
    pl:(.qt.qpipe.job.crypto_trades_decoder.payload[0];"not json"),enlist last .qt.qpipe.job.crypto_trades_decoder.invalid[];
    .qpipe.job.crypto_trades_decoder.on_batch[`kafka_message;.qt.qpipe.job.crypto_trades_decoder.batch_on[`example.crypto.trades;3#0j;til 3;pl]];
    }

/ test_replay_equivalence.q scripts the tree's replaying jobs or excludes them with a reason; a bundle's says so here.
.qt.qpipe.job.crypto_trades_decoder.replay_excluded:"no time-based rule; replayed raw rows are held for on_replayed (test_a_restart_handles_exactly_the_records_that_were_not_handled)"

/ --- restarts --------------------------------------------------------------

/ What the plant's log holds when this job handled offsets 0, 1 (a dead letter) and 3, went down, and the consumer published 2, 3 and 4 meanwhile.
.qt.qpipe.job.crypto_trades_decoder.restart_log:{[]
    pl:(.qt.qpipe.job.crypto_trades_decoder.payload[0];"not json";.qt.qpipe.job.crypto_trades_decoder.payload[2];.qt.qpipe.job.crypto_trades_decoder.payload[3];.qt.qpipe.job.crypto_trades_decoder.payload[4]);
    raw:.qt.qpipe.job.crypto_trades_decoder.batch_on[`example.crypto.trades;5#0j;til 5;pl];
    out:.qetl.kafka.process .qetl.kafka.envelope_columns#raw 0 1 3;
    ((`kafka_message;raw 0 1 3);(`crypto_trade;out[`out]`crypto_trade);(`kafka_dead_letter;out`dead);
     (`kafka_message;raw 2 3 4))}

.qt.qpipe.job.crypto_trades_decoder.replay_transport:{[replayed]
    `connect`publisher`subscribe`timer!(
        {[] };
        {[] .qt.qpipe.job.crypto_trades_decoder.recorder};
        {[replayed;tbls;h;replay] if[replay; {[h;m] h . m}[h] each replayed];}[replayed];
        {[n;p;f] })}

.qt.qpipe.job.crypto_trades_decoder.test_a_restart_handles_exactly_the_records_that_were_not_handled:{[t]
    .qt.qpipe.job.crypto_trades_decoder.drive_ready[];
    .qetl.job.stream.start[`crypto_trades_decoder;.qt.qpipe.job.crypto_trades_decoder.replay_transport[.qt.qpipe.job.crypto_trades_decoder.restart_log[]]];
    .qunit.assertEquals[exec offset from .qt.qpipe.job.crypto_trades_decoder.trades[];2 4j;
        "offset 2 (the gap) and 4 (arrived while down); 0, 1 and 3 are not output a second time"];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_trades_decoder.rows_of[`kafka_dead_letter];0;"the dead letter at 1 is not repeated"];
    n:count .qt.qpipe.job.crypto_trades_decoder.published;
    .qt.qpipe.job.crypto_trades_decoder.push[5#0j;til 5];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_trades_decoder.published;n;"and the broker resending 0-4 publishes nothing"]};
