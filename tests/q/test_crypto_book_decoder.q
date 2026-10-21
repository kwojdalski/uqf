/ test_crypto_book_decoder.q - the crypto_book_decoder streaming job (.qt.qpipe.job.crypto_book_decoder).
/ .
/ Payloads here are written the way cryptorust's kafka-market-data-recorder
/ writes them (serde_json of snapshot_payload): integer epoch milliseconds,
/ floats with a point, [price,size] pairs, source_metadata when present. The
/ mapping's own examples are verified by test_transform.q; what these cases
/ prove is the path a record takes - decoder, mapping, dedupe, plant table.
/ .
/ Every definition names its whole path: this file never enters
/ .qt.qpipe.job.crypto_book_decoder with \d.

.qt.qpipe.job.crypto_book_decoder.published:()

.qt.qpipe.job.crypto_book_decoder.recorder:{[t;x] `.qt.qpipe.job.crypto_book_decoder.published set .qt.qpipe.job.crypto_book_decoder.published,enlist (t;x); count x}

/ Every row published onto one table since the last reset, as ONE table (`,/`, not raze).
.qt.qpipe.job.crypto_book_decoder.rows_of:{[tbl]
    p:.qt.qpipe.job.crypto_book_decoder.published;
    $[count s:p[;1] where tbl=p[;0]; (,/) s; ()]}

.qt.qpipe.job.crypto_book_decoder.drive_ready:{[]
    .qetl.job.stream.reset `crypto_book_decoder;
    `.qt.qpipe.job.crypto_book_decoder.published set ();
    .qetl.job.stream.wire[`crypto_book_decoder;.qt.qpipe.job.crypto_book_decoder.recorder];
    }

.qt.qpipe.job.crypto_book_decoder.t0:2025.10.20D09:00:00.000000000
.qt.qpipe.job.crypto_book_decoder.topic:`cryptorust.book_snapshots

/ A full five-level book, exactly as the recorder serialises it.
.qt.qpipe.job.crypto_book_decoder.full:"{\"schema_version\":1,\"kind\":\"book_snapshot\",\"sampled_at_ms\":1760950800100,\"exchange_timestamp_ms\":1760950800050,\"local_timestamp_ms\":1760950800060,\"venue\":\"binance\",\"symbol\":\"BTC-USDT\",\"bids\":[[65000.5,0.25],[64999.5,1.5],[64999.0,2.0],[64998.5,0.1],[64998.0,3.0]],\"asks\":[[65001.0,0.4],[65001.5,0.6],[65002.0,1.0],[65002.5,1.2],[65003.0,2.2]],\"source_metadata\":{\"update_id\":18446744073709551615,\"source_is_snapshot\":true}}"

/ Two bid levels and no asks, and no source_metadata (the producer omits it when the connector gave none... it serialises null).
.qt.qpipe.job.crypto_book_decoder.short:"{\"schema_version\":1,\"kind\":\"book_snapshot\",\"sampled_at_ms\":1760950801100,\"exchange_timestamp_ms\":1760950801050,\"local_timestamp_ms\":1760950801060,\"venue\":\"kraken\",\"symbol\":\"ETH-USD\",\"bids\":[[3200.5,1.0],[3200.0,2.0]],\"asks\":[],\"source_metadata\":null}"

/ Seven levels a side: a producer run with top_n_levels 7.
.qt.qpipe.job.crypto_book_decoder.deep:"{\"schema_version\":1,\"kind\":\"book_snapshot\",\"sampled_at_ms\":1760950802100,\"exchange_timestamp_ms\":1760950802050,\"local_timestamp_ms\":1760950802060,\"venue\":\"binance\",\"symbol\":\"BTC-USDT\",\"bids\":[[7.0,1.0],[6.0,1.0],[5.0,1.0],[4.0,1.0],[3.0,1.0],[2.0,1.0],[1.0,1.0]],\"asks\":[[8.0,1.0],[9.0,1.0],[10.0,1.0],[11.0,1.0],[12.0,1.0],[13.0,1.0],[14.0,1.0]]}"

/ A record with its parts given as JSON text, so a case can break exactly one. (Not ssr: its pattern language reads [ as a character class.)
.qt.qpipe.job.crypto_book_decoder.record:{[ver;kind;venue;bids;asks]
    "{\"schema_version\":",ver,",\"kind\":",kind,",\"sampled_at_ms\":1760950800100,\"exchange_timestamp_ms\":1760950800050,\"local_timestamp_ms\":1760950800060,",venue,"\"symbol\":\"BTC-USDT\",\"bids\":",bids,",\"asks\":",asks,"}"}

.qt.qpipe.job.crypto_book_decoder.batch:{[parts;offs;payloads]
    n:count offs;
    ([] time:.qt.qpipe.job.crypto_book_decoder.t0+1000000000*til n;
        topic:n#.qt.qpipe.job.crypto_book_decoder.topic; partition:parts; offset:offs; msg_key:n#enlist "binance:BTC-USDT";
        broker_time:(.qt.qpipe.job.crypto_book_decoder.t0+1000000000*til n)-0D00:00:00.002;
        payload:payloads)}

.qt.qpipe.job.crypto_book_decoder.push:{[parts;offs;payloads]
    .qpipe.job.crypto_book_decoder.on_batch[`kafka_message;.qt.qpipe.job.crypto_book_decoder.batch[parts;offs;payloads]]}

.qt.qpipe.job.crypto_book_decoder.test_real_shaped_books_become_wide_rows:{[t]
    .qt.qpipe.job.crypto_book_decoder.drive_ready[];
    .qt.qpipe.job.crypto_book_decoder.push[0 1j;10 5j;(.qt.qpipe.job.crypto_book_decoder.full;.qt.qpipe.job.crypto_book_decoder.short)];
    r:.qt.qpipe.job.crypto_book_decoder.rows_of[`crypto_book_snapshot];
    .qunit.assertEquals[count r;2;"a row per record"];
    f:r 0;
    .qunit.assertEquals[(f`sym;f`venue);(`$"BTC-USDT";`binance);"the market, case kept"];
    .qunit.assertEquals[(f`source_time;f`local_time;f`sampled_time);
        2025.10.20D09:00:00.050000000 2025.10.20D09:00:00.060000000 2025.10.20D09:00:00.100000000;"epoch milliseconds, as timestamps"];
    .qunit.assertEquals[f`bid_px0`bid_px1`bid_px2`bid_px3`bid_px4;65000.5 64999.5 64999 64998.5 64998f;"the bid prices, best first"];
    .qunit.assertEquals[f`bid_sz0`bid_sz1`bid_sz2`bid_sz3`bid_sz4;0.25 1.5 2 0.1 3f;"the bid sizes"];
    .qunit.assertEquals[f`ask_px0`ask_px1`ask_px2`ask_px3`ask_px4;65001 65001.5 65002 65002.5 65003f;"the ask prices"];
    .qunit.assertEquals[f`ask_sz0`ask_sz1`ask_sz2`ask_sz3`ask_sz4;0.4 0.6 1 1.2 2.2;"the ask sizes"];
    .qunit.assertEquals[(f`topic;f`partition;f`offset);(`cryptorust.book_snapshots;0j;10j);"the record's coordinates travel with the row"]};

.qt.qpipe.job.crypto_book_decoder.test_a_short_side_and_an_empty_side_are_null_floats:{[t]
    .qt.qpipe.job.crypto_book_decoder.drive_ready[];
    .qt.qpipe.job.crypto_book_decoder.push[enlist 1j;enlist 5j;enlist .qt.qpipe.job.crypto_book_decoder.short];
    s:first .qt.qpipe.job.crypto_book_decoder.rows_of `crypto_book_snapshot;
    .qunit.assertEquals[s`bid_px0`bid_px1;3200.5 3200f;"two bid levels"];
    .qunit.assertTrue[all null s`bid_px2`bid_px3`bid_px4`bid_sz2`bid_sz3`bid_sz4;"the rest of the bid side is null"];
    .qunit.assertTrue[all null s`ask_px0`ask_px1`ask_px2`ask_px3`ask_px4`ask_sz0`ask_sz1`ask_sz2`ask_sz3`ask_sz4;"an empty ask side is all null"];
    .qunit.assertEquals[type s`ask_px0;-9h;"and the nulls are floats"]};

.qt.qpipe.job.crypto_book_decoder.test_levels_past_five_are_cut:{[t]
    .qt.qpipe.job.crypto_book_decoder.drive_ready[];
    .qt.qpipe.job.crypto_book_decoder.push[enlist 0j;enlist 0j;enlist .qt.qpipe.job.crypto_book_decoder.deep];
    d:first .qt.qpipe.job.crypto_book_decoder.rows_of `crypto_book_snapshot;
    .qunit.assertEquals[d`bid_px0`bid_px4;7 3f;"the best five bids"];
    .qunit.assertEquals[d`ask_px0`ask_px4;8 12f;"the best five asks"]};

.qt.qpipe.job.crypto_book_decoder.test_a_redelivered_record_is_not_duplicated:{[t]
    .qt.qpipe.job.crypto_book_decoder.drive_ready[];
    .qt.qpipe.job.crypto_book_decoder.push[0 0j;10 11j;2#enlist .qt.qpipe.job.crypto_book_decoder.full];
    .qt.qpipe.job.crypto_book_decoder.push[0 0 0j;10 11 12j;3#enlist .qt.qpipe.job.crypto_book_decoder.full];
    r:.qt.qpipe.job.crypto_book_decoder.rows_of[`crypto_book_snapshot];
    .qunit.assertEquals[r`offset;10 11 12j;"offsets 10 and 11 once, and the new 12"]};

.qt.qpipe.job.crypto_book_decoder.test_what_it_cannot_read_is_dead_lettered:{[t]
    .qt.qpipe.job.crypto_book_decoder.drive_ready[];
    r:.qt.qpipe.job.crypto_book_decoder.record;
    v:"\"venue\":\"binance\",";
    k:"\"book_snapshot\"";
    ok:"[[65000.5,0.25]]";
    pl:(.qt.qpipe.job.crypto_book_decoder.full; "not json"; "";
        r["2";k;v;ok;ok]; r["1";"\"trade\"";v;ok;ok]; r["1";k;v;"[[65000.5,0.25,9.0]]";ok];
        r["1";k;"";ok;ok]; r["1";k;v;ok;"\"none\""]);
    .qt.qpipe.job.crypto_book_decoder.push[(count pl)#0j;til count pl;pl];
    r:.qt.qpipe.job.crypto_book_decoder.rows_of[`crypto_book_snapshot];
    d:`offset xasc .qt.qpipe.job.crypto_book_decoder.rows_of `kafka_dead_letter;
    .qunit.assertEquals[r`offset;enlist 0j;"only the good record is a book"];
    .qunit.assertEquals[d`offset;1 2 3 4 5 6 7j;"and every bad one is a dead letter, once"];
    .qunit.assertEquals[d`reason;`malformed`empty_payload`mapping`mapping`mapping`malformed`malformed;
        "malformed or empty by the decoder, the unknown version, kind and a 3-element level by the mapping"];
    .qunit.assertTrue[(d[`detail] 2) like "*offsets 3*";"the refusal names the record"]};

.qt.qpipe.job.crypto_book_decoder.test_a_null_in_a_level_or_a_unit_slip_in_a_time_is_dead_lettered:{[t]
    / #1503: serde_json writes NaN/inf as null; #1501: us overflow to 2239, seconds land in 1970
    .qt.qpipe.job.crypto_book_decoder.drive_ready[];
    r:.qt.qpipe.job.crypto_book_decoder.record;
    v:"\"venue\":\"binance\",";
    k:"\"book_snapshot\"";
    ok:"[[65000.5,0.25]]";
    us:ssr[.qt.qpipe.job.crypto_book_decoder.full;"1760950800050";"1760950800050000"];
    sec:ssr[.qt.qpipe.job.crypto_book_decoder.full;"1760950800100";"1760950800"];
    pl:(.qt.qpipe.job.crypto_book_decoder.full; r["1";k;v;"[[65000.5,0.25],[null,1.5],[64999.0,2.0]]";ok];
        r["1";k;v;"[[65001.0,null]]";ok]; r["1";k;v;ok;"[[null,null]]"]; us; sec);
    .qt.qpipe.job.crypto_book_decoder.push[(count pl)#0j;til count pl;pl];
    b:.qt.qpipe.job.crypto_book_decoder.rows_of[`crypto_book_snapshot];
    d:`offset xasc .qt.qpipe.job.crypto_book_decoder.rows_of `kafka_dead_letter;
    .qunit.assertEquals[b`offset;enlist 0j;"only the clean record is a book"];
    .qunit.assertEquals[d`offset;1 2 3 4 5j;"each bad one is a dead letter"];
    .qunit.assertEquals[d`reason;5#`mapping;"refused by the mapping"]};

.qt.qpipe.job.crypto_book_decoder.test_the_two_book_tables_are_distinct:{[t]
    .qunit.assertTrue[not `crypto_book_snapshot~`crypto_book;"declared apart"];
    .qunit.assertTrue[`bid_prices in cols .qetl.plant.published `crypto_book;"crypto_book keeps its vector shape"];
    .qunit.assertTrue[not `bids in cols .qetl.plant.published `crypto_book_snapshot;"and the new table is wide"]};

/ What the plant's log holds when the job handled offsets 0 and 1 (a dead letter) and 3, went down, and the consumer published 2, 3 and 4 meanwhile.
.qt.qpipe.job.crypto_book_decoder.restart_log:{[]
    f:.qt.qpipe.job.crypto_book_decoder.full;
    raw:.qt.qpipe.job.crypto_book_decoder.batch[5#0j;til 5;(f;"not json";f;f;f)];
    out:.qetl.kafka.process .qetl.kafka.envelope_columns#raw 0 1 3;
    ((`kafka_message;raw 0 1 3);(`crypto_book_snapshot;out[`out]`crypto_book_snapshot);(`kafka_dead_letter;out`dead);(`kafka_message;raw 2 3 4))}

.qt.qpipe.job.crypto_book_decoder.replay_transport:{[replayed]
    `connect`publisher`subscribe`timer!(
        {[] };
        {[] .qt.qpipe.job.crypto_book_decoder.recorder};
        {[replayed;tbls;h;replay] if[replay; {[h;m] h . m}[h] each replayed];}[replayed];
        {[n;p;f] })}

.qt.qpipe.job.crypto_book_decoder.test_a_restart_handles_exactly_the_records_that_were_not_handled:{[t]
    .qt.qpipe.job.crypto_book_decoder.drive_ready[];
    .qetl.job.stream.start[`crypto_book_decoder;.qt.qpipe.job.crypto_book_decoder.replay_transport[.qt.qpipe.job.crypto_book_decoder.restart_log[]]];
    .qunit.assertEquals[(.qt.qpipe.job.crypto_book_decoder.rows_of[`crypto_book_snapshot])`offset;2 4j;
        "offset 2 (the gap) and 4 (arrived while down); 0 and 3 are not handled a second time"];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_book_decoder.rows_of `kafka_dead_letter;0;"the dead letter at 1 is not repeated"]};

/ What test_job_output_contracts.q drives the job with: one good record, one bad.
.qt.qpipe.job.crypto_book_decoder.contract_driver:{[]
    .qetl.job.stream.reset `crypto_book_decoder;
    .qt.qpipe.job.crypto_book_decoder.push[0 0j;0 1j;(.qt.qpipe.job.crypto_book_decoder.full;"not json")];
    }
