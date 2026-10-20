/ test_kafka_ingest.q - the transport-agnostic Kafka core (.qt.qetl.kafka):
/ envelope, topic declarations, decode-and-route, marks, and the
/ publish-then-commit cycle, all driven by a fake transport. No broker, no
/ native library, no network.
/ .
/ EVERY CASE HERE IS A WAY OF LOSING OR DOUBLING A RECORD, because that is the
/ only thing this module is for. Lose one and the plant never has a row that
/ existed; double one and the desk sees a trade twice. Neither throws.


/ --- the fake transport ----------------------------------------------------

/ Everything the cycle did to the outside world, in order: (`publish;table;rows)
/ or (`commit;topic;offsets).
.qt.qetl.kafka.log:()

/ The poll results still to hand out, one table per poll.
.qt.qetl.kafka.inbox:()

/ Operations that throw while listed: `publish and/or `commit.
.qt.qetl.kafka.failing:`symbol$()

/ What assigned[] answers: a table of topic and partition, or ::.
.qt.qetl.kafka.assignment:(::)

/ The consumer's messages as librdkafka hands them over (kfk.q): partition is
/ an int, the payload is bytes, a record has a null mtype.
/ @param tp the topic
/ @param parts partitions
/ @param offs offsets
/ @param payloads one string each
.qt.qetl.kafka.msgs:{[tp;parts;offs;payloads]
    n:count offs;
    / flip a dictionary: `key` is a keyword, so it cannot be named in a table literal.
    flip `mtype`topic`client`partition`offset`msgtime`data`key!(n#`; n#tp; n#0i; `int$parts; `long$offs;
        2026.10.20D09:00:00.000000000+1000000000*til n; ("x"$) each payloads; n#enlist `byte$())}

.qt.qetl.kafka.none:{[] .qt.qetl.kafka.msgs[`t;`long$();`long$();()]}

/ One client trade as the producer writes it.
.qt.qetl.kafka.trade:{[px] "{\"sym\":\"EURUSD\",\"side\":\"buy\",\"qty\":1000000,\"price\":",string[px],",\"client\":\"ACME\",\"trade_id\":100}"}

.qt.qetl.kafka.transport:`poll`publish`commit`assigned!(
    {[n] b:.qt.qetl.kafka.inbox;
         if[0=count b; :.qt.qetl.kafka.none[]];
         `.qt.qetl.kafka.inbox set 1_b;
         first b};
    {[t;x] if[`publish in .qt.qetl.kafka.failing; '"tickerplant down"];
         `.qt.qetl.kafka.log set .qt.qetl.kafka.log,enlist (`publish;t;x); count x};
    {[t;o] if[`commit in .qt.qetl.kafka.failing; '"broker said no"];
         `.qt.qetl.kafka.log set .qt.qetl.kafka.log,enlist (`commit;t;o); 1b};
    {[u] .qt.qetl.kafka.assignment})

.qt.qetl.kafka.reset:{[]
    `.qt.qetl.kafka.log set ();
    `.qt.qetl.kafka.inbox set ();
    `.qt.qetl.kafka.failing set `symbol$();
    `.qt.qetl.kafka.assignment set (::);
    }

/ The log, one kind of entry only.
.qt.qetl.kafka.of:{[k] .qt.qetl.kafka.log where k=first each .qt.qetl.kafka.log}

/ Every envelope row published, as one table.
.qt.qetl.kafka.published:{[] $[count p:.qt.qetl.kafka.of`publish; (,/) p[;2]; .qetl.kafka.envelope_empty]}

/ Save and restore the topic registry around a test that adds to it.
.qt.qetl.kafka.registry_saved:()
.qt.qetl.kafka.beforeNamespace_load:{[] `.qt.qetl.kafka.registry_saved set .qetl.kafka.topic.registry;}
.qt.qetl.kafka.afterNamespace_restore:{[] `.qetl.kafka.topic.registry set .qt.qetl.kafka.registry_saved;}

/ A second topic with the client-trade wire shape, to prove two topics are two things.
.qt.qetl.kafka.second_topic:{[] .qetl.kafka.topic.define[`uqf.client.flow.b;`mapping`output!(`kafka_client_flow;`client_flow)]}

/ --- the envelope ----------------------------------------------------------

.qt.qetl.kafka.test_from_messages_keeps_the_brokers_coordinates_and_the_raw_payload:{[t]
    m:.qt.qetl.kafka.msgs[`uqf.client.flow;0 1;7 3;("{\"a\":1}";"plain text")];
    e:.qetl.kafka.from_messages m;
    .qunit.assertEquals[cols e;.qetl.kafka.envelope_columns;"the envelope's columns, in order"];
    .qunit.assertEquals[e`partition;0 1j;"partitions, widened to long"];
    .qunit.assertEquals[e`offset;7 3j;"offsets"];
    .qunit.assertEquals[e`topic;2#`uqf.client.flow;"topic"];
    .qunit.assertEquals[e`payload;("{\"a\":1}";"plain text");"the payload as the characters received"];
    .qunit.assertEquals[e`broker_time;m`msgtime;"the broker's timestamp"];
    .qunit.assertEquals[type each e`payload;10 10h;"each payload is a string"]};

.qt.qetl.kafka.test_a_one_character_payload_stays_a_string:{[t]
    / A list of char ATOMS collapses into one string; a payload of one byte
    / must stay a one-character string in its row.
    e:.qetl.kafka.from_messages .qt.qetl.kafka.msgs[`t;0 0;0 1;("a";"b")];
    .qunit.assertEquals[count e;2;"two rows"];
    .qunit.assertEquals[e`payload;(enlist "a";enlist "b");"each payload is its own string"]};

.qt.qetl.kafka.test_a_null_value_is_an_empty_payload_not_a_missing_row:{[t]
    m:.qt.qetl.kafka.msgs[`t;enlist 0;enlist 0;enlist ""];
    e:.qetl.kafka.from_messages m;
    .qunit.assertEquals[(count e;count first e`payload);(1;0);"a tombstone is a row with an empty payload"]};

.qt.qetl.kafka.test_the_clients_own_reports_are_not_records:{[t]
    m:.qt.qetl.kafka.msgs[`t;0 0;0 1;("a";"b")];
    m:update mtype:`PARTITION_EOF from m where offset=1;
    .qunit.assertEquals[(count .qetl.kafka.from_messages m;count .qetl.kafka.events m);(1;1);
        "a message with an mtype is a report about the consumer, kept out of the envelope"]};

/ --- declaring a topic -----------------------------------------------------

.qt.qetl.kafka.test_the_client_trade_topic_is_declared:{[t]
    .qunit.assertEquals[.qetl.kafka.topic.def[`uqf.client.flow]`output;`client_flow;"its output"];
    .qunit.assertTrue[`client_flow in .qetl.kafka.topic.outputs[];"and the outputs list names it"]};

.qt.qetl.kafka.test_a_topic_refuses_an_unregistered_mapping:{[t]
    .qunit.assertThrows[.qetl.kafka.topic.define[`x];`mapping`output!(`no_such_transform;`client_flow);"*not registered*";
        "a mapping is a declared transform"]};

.qt.qetl.kafka.test_a_topic_refuses_an_output_without_the_coordinates:{[t]
    / quote is a real plant table; it has no topic, partition or offset.
    .qunit.assertThrows[.qetl.kafka.topic.define[`x];`mapping`output!(`kafka_client_flow;`quote);"*carries no topic, partition, offset*";
        "a restart could not tell which records an output covers"]};

.qt.qetl.kafka.test_a_topic_refuses_an_output_the_plant_does_not_have:{[t]
    .qunit.assertThrows[.qetl.kafka.topic.define[`x];`mapping`output!(`kafka_client_flow;`no_such_table);"*plant does not define*";
        "the plant silently discards rows onto a table it lacks"]};

.qt.qetl.kafka.test_a_topic_refuses_a_format_with_no_decoder:{[t]
    .qunit.assertThrows[.qetl.kafka.topic.define[`x];`mapping`output`format!(`kafka_client_flow;`client_flow;`avro);"*no decoder*";
        "naming a format nothing decodes"]};

.qt.qetl.kafka.test_a_mapping_must_start_with_the_coordinates:{[t]
    / market_data_from_quote reads a plant table, not a decoded record.
    .qunit.assertThrows[.qetl.kafka.topic.define[`x];`mapping`output!(`market_data_from_quote;`client_flow);"*must begin with topic*";
        "an input that does not start with the coordinates is not a record"]};

/ --- decoding --------------------------------------------------------------

.qt.qetl.kafka.test_decode_json_reads_the_declared_fields_in_schema_order:{[t]
    r:.qetl.kafka.decode_json[`px`sym`id`at`ok!"fsjpb";"{\"id\":42,\"ok\":true,\"sym\":\"EURUSD\",\"px\":1.5,\"at\":\"2026-10-20T09:00:00Z\",\"extra\":1}"];
    .qunit.assertEquals[r;(1.5;`EURUSD;42j;2026.10.20D09:00:00.000000000;1b);"typed, in the schema's order; an extra field is ignored"]};

.qt.qetl.kafka.test_decode_json_refuses_each_way_a_payload_can_be_wrong:{[t]
    s:`px`sym!"fs";
    f:.qetl.kafka.decode_json[s;];
    .qunit.assertThrows[f;"";"empty payload";"empty"];
    .qunit.assertThrows[f;"hello";"not valid JSON*";"not JSON"];
    .qunit.assertThrows[f;"[1,2]";"the payload is not a JSON object";"an array"];
    .qunit.assertThrows[f;"{\"px\":1.5}";"missing field(s): sym";"a field absent"];
    .qunit.assertThrows[f;"{\"px\":null,\"sym\":\"A\"}";"px: expected a number";"a null is not a number"];
    .qunit.assertThrows[f;"{\"px\":\"1.5\",\"sym\":\"A\"}";"px: expected a number";"a string is not a number: no silent cast"];
    .qunit.assertThrows[f;"{\"px\":1.5,\"sym\":\"\"}";"sym: expected a non-empty string";"an empty symbol"];
    .qunit.assertThrows[f;"{\"px\":1.5,\"sym\":3}";"sym: expected a non-empty string";"a number is not a symbol"]};

.qt.qetl.kafka.test_a_long_past_two_to_the_fifty_third_is_refused_but_exact_as_a_string:{[t]
    / JSON numbers are floats: 2^53+1 reads back as 2^53, so a larger integer
    / would lose its low digits with nothing erroring.
    f:.qetl.kafka.decode_json[(enlist `id)!enlist "j";];
    .qunit.assertThrows[f;"{\"id\":9007199254740993}";"id: expected a whole number below 2^53*";"refused, not rounded"];
    .qunit.assertEquals[f "{\"id\":\"9007199254740993\"}";enlist 9007199254740993j;"the same value sent as a string is read exactly"];
    .qunit.assertThrows[f;"{\"id\":1.5}";"id: expected a whole number below 2^53*";"a fraction is not an id"]};

.qt.qetl.kafka.test_decode_json_reads_arrays_of_numbers_and_arrays_of_pairs:{[t]
    r:.qetl.kafka.decode_json[`v`m`e!"F F";"{\"v\":[1,2.5],\"m\":[[1,2],[3,4.5]],\"e\":[]}"];
    .qunit.assertEquals[r 0;1 2.5;"a flat array is a float list"];
    .qunit.assertEquals[r 1;(1 2f;3 4.5);"an array of pairs is a list of float lists"];
    .qunit.assertEquals[r 2;0#0f;"an empty array is an empty float list"];
    m:.qetl.kafka.decode_json[(enlist `m)!enlist " ";];
    .qunit.assertEquals[first m "{\"m\":[[1,2]]}";enlist 1 2f;"a single pair is still a list of pairs"];
    .qunit.assertEquals[count first m "{\"m\":[]}";0;"no pairs at all"];
    .qunit.assertThrows[m;"{\"m\":[1,2]}";"m: expected an array of arrays of numbers";"a flat array is not nested"];
    .qunit.assertThrows[m;"{\"m\":[[1,\"a\"]]}";"m: expected an array of arrays of numbers";"a string inside a pair"];
    .qunit.assertThrows[m;"{\"m\":3}";"m: expected an array of arrays of numbers";"a number is not an array"];
    f:.qetl.kafka.decode_json[(enlist `v)!enlist "F";];
    .qunit.assertThrows[f;"{\"v\":[1,\"a\"]}";"v: expected an array of numbers";"a string in a number array"];
    .qunit.assertThrows[f;"{\"v\":\"1\"}";"v: expected an array of numbers";"a string is not an array"]};

.qt.qetl.kafka.test_a_timestamp_with_only_a_date_is_refused:{[t]
    / The shared coercion layer's rule: a date where a timestamp belongs
    / collapses a whole day onto midnight.
    .qunit.assertThrows[.qetl.kafka.decode_json[(enlist `at)!enlist "p";];"{\"at\":\"2026-10-20\"}";"at: expected an ISO-8601*";"date-only"]};

/ --- decode and route ------------------------------------------------------

/ Envelope rows for the client-trade topic.
.qt.qetl.kafka.env:{[tp;parts;offs;payloads] .qetl.kafka.from_messages .qt.qetl.kafka.msgs[tp;parts;offs;payloads]}

.qt.qetl.kafka.test_process_maps_a_client_trade_and_keeps_its_coordinates:{[t]
    r:.qetl.kafka.process .qt.qetl.kafka.env[`uqf.client.flow;enlist 2;enlist 41;enlist .qt.qetl.kafka.trade[1.1]];
    o:r[`out;`client_flow];
    .qunit.assertEquals[cols o;cols .qetl.plant.published `client_flow;"exactly the plant table's published columns"];
    .qunit.assertEquals[first o`price;1.1;"the payload's price"];
    .qunit.assertEquals[(first o`topic;first o`partition;first o`offset);(`uqf.client.flow;2j;41j);"the record's coordinates, from the broker"];
    .qunit.assertEquals[count r`dead;0;"nothing dead"]};

.qt.qetl.kafka.test_every_malformed_record_is_dead_lettered_with_a_reason:{[t]
    ps:(.qt.qetl.kafka.trade[1.1];"";"not json";"{\"sym\":\"EURUSD\"}";.qt.qetl.kafka.trade[-1f]);
    r:.qetl.kafka.process .qt.qetl.kafka.env[`uqf.client.flow;5#0;til 5;ps];
    d:r`dead;
    .qunit.assertEquals[count r[`out;`client_flow];1;"only the good record is published"];
    .qunit.assertEquals[d`offset;1 2 3 4j;"the other four are on record, by offset"];
    .qunit.assertEquals[d`reason;`empty_payload`malformed`malformed`mapping;"empty, unparseable, missing field, refused by the mapping"];
    .qunit.assertEquals[d`payload;ps 1 2 3 4;"the payload exactly as received"];
    .qunit.assertTrue[all 0<count each d`detail;"and every one says why"];
    .qunit.assertEquals[(count r[`out;`client_flow])+count d;5;"nothing unaccounted for: published plus dead is everything"]};

.qt.qetl.kafka.test_a_topic_with_no_mapping_is_dead_lettered_not_dropped:{[t]
    r:.qetl.kafka.process .qt.qetl.kafka.env[`some.other.topic;enlist 0;enlist 9;enlist "{}"];
    .qunit.assertEquals[(count r`out;first r[`dead]`reason);(0;`no_mapping);"on record as having no mapping"];
    .qunit.assertTrue[(first r[`dead]`detail) like "*some.other.topic*";"naming the topic"]};

.qt.qetl.kafka.test_one_refused_row_does_not_take_its_neighbours:{[t]
    ps:(.qt.qetl.kafka.trade[1.1];.qt.qetl.kafka.trade[0f];.qt.qetl.kafka.trade[1.3]);
    r:.qetl.kafka.process .qt.qetl.kafka.env[`uqf.client.flow;3#0;0 1 2;ps];
    .qunit.assertEquals[r[`out;`client_flow]`offset;0 2j;"the good rows publish"];
    .qunit.assertEquals[(r[`dead]`offset;r[`dead]`reason);(enlist 1j;enlist `mapping);"the refused one is dead-lettered"];
    .qunit.assertTrue[(first r[`dead]`detail) like "*positive*";"with the mapping's own message"]};

.qt.qetl.kafka.test_process_ignores_the_time_the_plant_stamped:{[t]
    e:update time:2026.10.20D10:00:00.000000000 from .qt.qetl.kafka.env[`uqf.client.flow;enlist 0;enlist 0;enlist .qt.qetl.kafka.trade[1.1]];
    .qunit.assertEquals[count (.qetl.kafka.process e)[`out;`client_flow];1;"a batch off the plant carries time"]};

.qt.qetl.kafka.test_identical_partition_and_offset_on_different_topics_are_different_records:{[t]
    .qt.qetl.kafka.second_topic[];
    a:.qt.qetl.kafka.env[`uqf.client.flow;enlist 0;enlist 5;enlist .qt.qetl.kafka.trade[1.1]];
    b:.qt.qetl.kafka.env[`uqf.client.flow.b;enlist 0;enlist 5;enlist .qt.qetl.kafka.trade[1.2]];
    m:.qetl.kafka.advance[.qetl.kafka.marks_empty;a];
    .qunit.assertEquals[count .qetl.kafka.above_marks[m;b];1;"topic b's offset 5 is above topic b's mark, which is unset"];
    .qunit.assertEquals[count .qetl.kafka.above_marks[m;a];0;"topic a's offset 5 is a repeat"];
    .qunit.assertEquals[count .qetl.kafka.first_per_coordinate a,b;2;"and both survive the within-batch dedupe"];
    r:.qetl.kafka.process a,b;
    .qunit.assertEquals[(r[`out;`client_flow]`topic;r[`out;`client_flow]`price);(`uqf.client.flow`uqf.client.flow.b;1.1 1.2);
        "two rows, each carrying its own topic"]};

/ --- the marks -------------------------------------------------------------

.qt.qetl.kafka.test_offset_zero_on_an_unseen_partition_is_above_the_marks:{[t]
    / `0^mark` would read the unseen partition as 0, and 0>0 is false: the
    / first record of every partition would vanish.
    e:.qt.qetl.kafka.env[`uqf.client.flow;enlist 3;enlist 0;enlist "x"];
    .qunit.assertEquals[count .qetl.kafka.above_marks[.qetl.kafka.marks_empty;e];1;"offset 0 is a real record"]};

.qt.qetl.kafka.test_advance_never_lowers_a_mark:{[t]
    hi:.qt.qetl.kafka.env[`t;4#0;2 3 4 5;4#enlist "x"];
    lo:.qt.qetl.kafka.env[`t;2#0;2 3;2#enlist "x"];
    m:.qetl.kafka.advance[.qetl.kafka.advance[.qetl.kafka.marks_empty;hi];lo];
    .qunit.assertEquals[first exec offset from m;5j;"a late batch of old offsets leaves the mark at 5"]};

.qt.qetl.kafka.test_first_per_coordinate_keeps_the_first_in_batch_order:{[t]
    e:.qt.qetl.kafka.env[`t;0 0 0;1 0 1;("a";"b";"c")];
    f:.qetl.kafka.first_per_coordinate e;
    .qunit.assertEquals[(f`offset;f`payload);(1 0j;(enlist "a";enlist "b"));"offset 1 once, as its first copy, in batch order"]};

.qt.qetl.kafka.test_not_in_finds_a_gap_that_a_mark_would_have_hidden:{[t]
    / Handled: 1 and 3. The mark would be 3 and call 2 done; the set does not.
    raw:.qt.qetl.kafka.env[`t;4#0;1 2 3 4;4#enlist "x"];
    done:.qetl.kafka.coordinate_set select topic,partition,offset from raw where offset in 1 3;
    .qunit.assertEquals[(.qetl.kafka.not_in[raw;done])`offset;2 4j;"2 is owed, and so is 4"];
    .qunit.assertEquals[count .qetl.kafka.above_marks[.qetl.kafka.advance[.qetl.kafka.marks_empty;0!done];raw];1;
        "whereas the mark at 3 would have owed only 4"]};

/ --- the cycle: publish, THEN commit ---------------------------------------

.qt.qetl.kafka.test_the_commit_follows_the_publish:{[t]
    .qt.qetl.kafka.reset[];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;0 0 1;10 11 4;3#enlist "x"];
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    .qunit.assertEquals[first each .qt.qetl.kafka.log;`publish`commit;"the publish is first in the log, and the only commit comes after it"];
    c:last .qt.qetl.kafka.log;
    .qunit.assertEquals[(c 1;c 2);(`t;0 1i!12 5j);"commit the offset to RESUME at: the highest published plus one, per partition"];
    .qunit.assertEquals[count st`pending;0;"nothing left owing once the commit returned"];
    .qunit.assertEquals[st[`stats]`published;3;"counted"]};

.qt.qetl.kafka.test_a_publish_that_throws_commits_nothing_and_loses_nothing:{[t]
    .qt.qetl.kafka.reset[];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;0 0;0 1;2#enlist "x"];
    `.qt.qetl.kafka.failing set enlist `publish;
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    .qunit.assertEquals[count .qt.qetl.kafka.log;0;"no commit, and no publish got through"];
    .qunit.assertEquals[(count st`backlog;st[`stats]`publish_errors);(2;1);"the polled records are held, not forgotten"];
    `.qt.qetl.kafka.failing set `symbol$();
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;st];
    .qunit.assertEquals[.qt.qetl.kafka.published[]`offset;0 1j;"the next cycle publishes them - the broker will not resend what it already delivered"];
    .qunit.assertEquals[first each .qt.qetl.kafka.log;`publish`commit;"and only then commits"]};

.qt.qetl.kafka.test_a_commit_that_fails_is_retried_without_publishing_again:{[t]
    / The window the whole design is about: published, not committed.
    .qt.qetl.kafka.reset[];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;0 0;0 1;2#enlist "x"];
    `.qt.qetl.kafka.failing set enlist `commit;
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    .qunit.assertEquals[(count .qt.qetl.kafka.of`publish;count .qt.qetl.kafka.of`commit;count st`pending);(1;0;1);
        "published once, nothing committed, the offset still owed"];
    `.qt.qetl.kafka.failing set `symbol$();
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;st];
    .qunit.assertEquals[(count .qt.qetl.kafka.of`publish;count .qt.qetl.kafka.of`commit;count st`pending);(1;1;0);
        "the retry commits, and does not publish the records a second time"];
    .qunit.assertEquals[(last .qt.qetl.kafka.log) 2;(enlist 0i)!enlist 2j;"committing past both"]};

.qt.qetl.kafka.test_a_restart_after_publish_redelivers_and_the_raw_table_shows_both_copies:{[t]
    / Crash between publish and commit, simulated by a fresh state: the broker
    / sends offsets 0 and 1 again. The raw table records both deliveries -
    / that is its job - and kafka_flow is what stops the second reaching a
    / normalized table (test_kafka_flow.q).
    .qt.qetl.kafka.reset[];
    m:.qt.qetl.kafka.msgs[`t;0 0;0 1;2#enlist "x"];
    `.qt.qetl.kafka.inbox set enlist m;
    `.qt.qetl.kafka.failing set enlist `commit;
    .qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    `.qt.qetl.kafka.failing set `symbol$();
    `.qt.qetl.kafka.inbox set enlist m;
    .qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    .qunit.assertEquals[.qt.qetl.kafka.published[]`offset;0 1 0 1j;"both deliveries are on record"];
    .qunit.assertEquals[(last .qt.qetl.kafka.log) 2;(enlist 0i)!enlist 2j;"and the restarted consumer commits"]};

.qt.qetl.kafka.test_a_redelivery_within_a_session_is_not_republished_but_is_committed:{[t]
    / A rebalance hands the same offsets back. Not republished - they are
    / already in the plant - but the commit moves past them, or the broker
    / would keep redelivering.
    .qt.qetl.kafka.reset[];
    m:.qt.qetl.kafka.msgs[`t;0 0;0 1;2#enlist "x"];
    st:.qetl.kafka.state_new[];
    `.qt.qetl.kafka.inbox set enlist m;
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;st];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;0 0 0;0 1 2;3#enlist "x"];
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;st];
    .qunit.assertEquals[.qt.qetl.kafka.published[]`offset;0 1 2j;"0 and 1 once; 2 is new"];
    .qunit.assertEquals[st[`stats]`duplicates;2;"two repeats counted"];
    .qunit.assertEquals[(last .qt.qetl.kafka.log) 2;(enlist 0i)!enlist 3j;"the commit covers the repeats too"]};

.qt.qetl.kafka.test_the_same_offset_on_two_topics_is_published_twice:{[t]
    .qt.qetl.kafka.reset[];
    `.qt.qetl.kafka.inbox set (.qt.qetl.kafka.msgs[`a;enlist 0;enlist 5;enlist "x"];.qt.qetl.kafka.msgs[`b;enlist 0;enlist 5;enlist "y"]);
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;st];
    .qunit.assertEquals[.qt.qetl.kafka.published[]`topic;`a`b;"topic b's offset 5 is not a repeat of topic a's"];
    .qunit.assertEquals[(.qt.qetl.kafka.of`commit)[;1];`a`b;"and each topic is committed on its own"]};

.qt.qetl.kafka.test_a_revoked_partition_is_not_committed:{[t]
    .qt.qetl.kafka.reset[];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;0 1;3 8;2#enlist "x"];
    `.qt.qetl.kafka.assignment set ([] topic:enlist `t; partition:enlist 0);
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    .qunit.assertEquals[(last .qt.qetl.kafka.log) 2;(enlist 0i)!enlist 4j;
        "partition 1 was taken away: its commit would be refused, and the new owner re-reads it"];
    .qunit.assertEquals[(st[`stats]`revoked;exec partition from st`marks);(1;enlist 0j);"counted, and its mark forgotten"];
    .qunit.assertEquals[.qt.qetl.kafka.published[]`partition;0 1j;"the records were still published: the plant has them, and dedupe absorbs the re-read"]};

.qt.qetl.kafka.test_a_commit_given_up_on_is_dropped_after_its_tries:{[t]
    .qt.qetl.kafka.reset[];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;enlist 0;enlist 0;enlist "x"];
    `.qt.qetl.kafka.failing set enlist `commit;
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    st:(.qetl.kafka.commit_tries-1) .qetl.kafka.cycle[.qt.qetl.kafka.transport;]/ st;
    .qunit.assertEquals[(count st`pending;st[`stats]`commit_errors);(0;`long$.qetl.kafka.commit_tries);
        "after the limit it is dropped, not retried for ever: a redelivery is absorbed by the marks"]};

.qt.qetl.kafka.test_flush_publishes_the_backlog_and_commits_without_polling:{[t]
    / The orderly-shutdown path.
    .qt.qetl.kafka.reset[];
    st:.qetl.kafka.state_new[];
    st[`backlog]:.qt.qetl.kafka.env[`t;0 0;0 1;2#enlist "x"];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;enlist 0;enlist 9;enlist "z"];
    st:.qetl.kafka.flush[.qt.qetl.kafka.transport;st];
    .qunit.assertEquals[.qt.qetl.kafka.published[]`offset;0 1j;"what was held goes out; nothing new is polled"];
    .qunit.assertEquals[(last .qt.qetl.kafka.log) 2;(enlist 0i)!enlist 2j;"and is committed"];
    .qunit.assertEquals[count .qt.qetl.kafka.inbox;1;"the inbox was not touched"]};

.qt.qetl.kafka.test_an_overfull_backlog_stops_the_polling:{[t]
    .qt.qetl.kafka.reset[];
    `.qt.qetl.kafka.inbox set enlist .qt.qetl.kafka.msgs[`t;enlist 0;enlist 9;enlist "z"];
    st:.qetl.kafka.state_new[];
    st[`backlog]:.qt.qetl.kafka.env[`t;(.qetl.kafka.backlog_cap)#0;til .qetl.kafka.backlog_cap;(.qetl.kafka.backlog_cap)#enlist "x"];
    `.qt.qetl.kafka.failing set enlist `publish;
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;st];
    .qunit.assertEquals[count .qt.qetl.kafka.inbox;1;"back-pressure: a consumer that cannot publish does not keep reading"]};

.qt.qetl.kafka.test_client_reports_are_counted_and_not_published:{[t]
    .qt.qetl.kafka.reset[];
    m:.qt.qetl.kafka.msgs[`t;0 0;0 1;2#enlist "x"];
    `.qt.qetl.kafka.inbox set enlist update mtype:`UNKNOWN_TOPIC_OR_PART from m where offset=1;
    st:.qetl.kafka.cycle[.qt.qetl.kafka.transport;.qetl.kafka.state_new[]];
    .qunit.assertEquals[(st[`stats]`events;st[`stats]`published);(1;1);"one report, one record"]};

.qt.qetl.kafka.test_a_poll_that_throws_is_counted_and_the_cycle_survives:{[t]
    tr:.qt.qetl.kafka.transport;
    tr[`poll]:{[n] '"broker unreachable"};
    st:.qetl.kafka.cycle[tr;.qetl.kafka.state_new[]];
    .qunit.assertEquals[st[`stats]`poll_errors;1;"counted; the timer that called us is not killed"]};
