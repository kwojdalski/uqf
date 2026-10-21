/ test_kafka_provenance.q - the Kafka provenance contract (.qt.qetl.kafka.provenance, #1511).
/ .
/ A decoded row names the record it came from by the same four columns whichever
/ decoder wrote it, and a mapping never builds them. The cases are the ways
/ that goes quietly wrong: a column missing or mistyped on an output table, a
/ broker time standing in for a payload time, a row index that depends on how a
/ batch happened to be split, a redelivery that republishes an expanded record.
/ .
/ The fixtures declare two throwaway plant tables, transforms and topics and
/ remove them afterwards: prov_one (a one-to-one output) and prov_many (an
/ output that expands a record into `legs` rows).


.qt.qetl.kafka.provenance.saved:()

.qt.qetl.kafka.provenance.t0:2026.10.20D09:00:00.000000000

/ One-to-one: a payload value and an optional payload time (0 = the payload has none).
.qt.qetl.kafka.provenance.one_map:{[m]
    ts:1970.01.01D00:00:00.000000000+1000000*m`t_ms;
    ([] message_index:til count m; a:m`a; event_time:@[ts;where 0=m`t_ms;:;0Np])}

/ A one-to-one mapping that drops a negative record instead of refusing it: it returns the wrong number of rows.
.qt.qetl.kafka.provenance.drop_map:{[m] k:where m[`a]>=0; update message_index:k from .qt.qetl.kafka.provenance.one_map select from m where a>=0}

/ A one-to-one mapping that returns one row per record, SORTED by the payload: the same count, a different order (#1520).
.qt.qetl.kafka.provenance.sort_map:{[m] `a xasc .qt.qetl.kafka.provenance.one_map m}

/ A one-to-one mapping that keeps the count but names record 0 twice: one record's coordinates would be copied onto another's row.
.qt.qetl.kafka.provenance.dup_map:{[m] update message_index:0 from .qt.qetl.kafka.provenance.one_map m}

/ One-to-many: `legs` rows for each record, numbered by the mapping only through message_index.
.qt.qetl.kafka.provenance.many_map:{[m]
    mi:`long$raze (m`legs)#'til count m;
    pos:`long$raze til each m`legs;
    ([] message_index:mi; leg:`symbol$ {`$"leg",string x} each pos; px:(m`px) mi)}

/ An expanding mapping that points past the batch.
.qt.qetl.kafka.provenance.bad_map:{[m] ([] message_index:(count m)#7j; leg:(count m)#`x; px:(count m)#1f)}

.qt.qetl.kafka.provenance.one_in:([] topic:`symbol$(); partition:`long$(); offset:`long$(); broker_time:`timestamp$(); a:`float$(); t_ms:`long$())
.qt.qetl.kafka.provenance.many_in:([] topic:`symbol$(); partition:`long$(); offset:`long$(); broker_time:`timestamp$(); legs:`long$(); px:`float$())

.qt.qetl.kafka.provenance.beforeNamespace_load:{[]
    `.qt.qetl.kafka.provenance.saved set (.qetl.kafka.topic.registry;.qetl.transform.registry);
    `.qetl.plant.prov_one set ([] time:`timestamp$(); a:`float$(); event_time:`timestamp$(); broker_time:`timestamp$(); topic:`symbol$(); partition:`long$(); offset:`long$());
    `.qetl.plant.prov_many set ([] time:`timestamp$(); leg:`symbol$(); px:`float$(); broker_time:`timestamp$(); topic:`symbol$(); partition:`long$(); offset:`long$(); row_index:`long$());
    / refusals: no broker_time, an int offset, a row_index on a one-to-one table, a float row_index
    `.qetl.plant.prov_nobt set ([] time:`timestamp$(); a:`float$(); event_time:`timestamp$(); topic:`symbol$(); partition:`long$(); offset:`long$());
    `.qetl.plant.prov_int set ([] time:`timestamp$(); a:`float$(); event_time:`timestamp$(); broker_time:`timestamp$(); topic:`symbol$(); partition:`long$(); offset:`int$());
    `.qetl.plant.prov_stray set ([] time:`timestamp$(); a:`float$(); event_time:`timestamp$(); broker_time:`timestamp$(); topic:`symbol$(); partition:`long$(); offset:`long$(); row_index:`long$());
    `.qetl.plant.prov_float set ([] time:`timestamp$(); leg:`symbol$(); px:`float$(); broker_time:`timestamp$(); topic:`symbol$(); partition:`long$(); offset:`long$(); row_index:`float$());
    one_ex:`inputs`expected!(
        enlist[`m]!enlist ([] topic:2#`prov.one; partition:0 0j; offset:1 2j; broker_time:2#.qt.qetl.kafka.provenance.t0; a:1 2f; t_ms:0 1000j);
        ([] message_index:0 1j; a:1 2f; event_time:(0Np;1970.01.01D00:00:01.000000000)));
    .qetl.transform.define[`prov_one_map;`inputs`output`fn`examples!(enlist[`m]!enlist .qt.qetl.kafka.provenance.one_in;
        .qetl.kafka.topic.payload_schema[`prov_one];.qt.qetl.kafka.provenance.one_map;enlist one_ex)];
    .qetl.transform.define[`prov_drop_map;`inputs`output`fn`examples!(enlist[`m]!enlist .qt.qetl.kafka.provenance.one_in;
        .qetl.kafka.topic.payload_schema[`prov_one];.qt.qetl.kafka.provenance.drop_map;enlist one_ex)];
    .qetl.transform.define[`prov_sort_map;`inputs`output`fn`examples!(enlist[`m]!enlist .qt.qetl.kafka.provenance.one_in;
        .qetl.kafka.topic.payload_schema[`prov_one];.qt.qetl.kafka.provenance.sort_map;enlist one_ex)];
    .qetl.transform.define[`prov_dup_map;`inputs`output`fn`examples!(enlist[`m]!enlist .qt.qetl.kafka.provenance.one_in;
        .qetl.kafka.topic.payload_schema[`prov_one];.qt.qetl.kafka.provenance.dup_map;enlist `inputs`expected!(one_ex`inputs;([] message_index:0 0j; a:1 2f; event_time:(0Np;1970.01.01D00:00:01.000000000))))];
    many_ex:`inputs`expected!(
        enlist[`m]!enlist ([] topic:enlist `prov.many; partition:enlist 0j; offset:enlist 1j; broker_time:enlist .qt.qetl.kafka.provenance.t0; legs:enlist 2j; px:enlist 1.5);
        ([] message_index:0 0j; leg:`leg0`leg1; px:1.5 1.5));
    .qetl.transform.define[`prov_many_map;`inputs`output`fn`examples!(enlist[`m]!enlist .qt.qetl.kafka.provenance.many_in;
        .qetl.kafka.topic.payload_schema[`prov_many];.qt.qetl.kafka.provenance.many_map;enlist many_ex)];
    .qetl.transform.define[`prov_bad_map;`inputs`output`fn`examples!(enlist[`m]!enlist .qt.qetl.kafka.provenance.many_in;
        .qetl.kafka.topic.payload_schema[`prov_many];.qt.qetl.kafka.provenance.bad_map;enlist `inputs`expected!(many_ex`inputs;([] message_index:enlist 7j; leg:enlist `x; px:enlist 1f)))];
    .qetl.kafka.topic.define[`prov.one;`mapping`output!(`prov_one_map;`prov_one)];
    .qetl.kafka.topic.define[`prov.drop;`mapping`output!(`prov_drop_map;`prov_one)];
    .qetl.kafka.topic.define[`prov.sorted;`mapping`output!(`prov_sort_map;`prov_one)];
    .qetl.kafka.topic.define[`prov.dup;`mapping`output!(`prov_dup_map;`prov_one)];
    .qetl.kafka.topic.define[`prov.many;`mapping`output`expand!(`prov_many_map;`prov_many;1b)];
    .qetl.kafka.topic.define[`prov.bad;`mapping`output`expand!(`prov_bad_map;`prov_many;1b)];
    }

.qt.qetl.kafka.provenance.afterNamespace_restore:{[]
    `.qetl.kafka.topic.registry set first .qt.qetl.kafka.provenance.saved;
    `.qetl.transform.registry set last .qt.qetl.kafka.provenance.saved;
    ![`.qetl.plant;();0b;`prov_one`prov_many`prov_nobt`prov_int`prov_stray`prov_float];
    }

/ Envelope rows: one per payload, offsets given, partition 0, the broker's time one second apart unless given.
.qt.qetl.kafka.provenance.env:{[tp;offs;payloads]
    n:count offs;
    ([] topic:n#tp; partition:n#0j; offset:offs; msg_key:n#enlist "k"; broker_time:.qt.qetl.kafka.provenance.t0+1000000000*til n; payload:payloads)}

.qt.qetl.kafka.provenance.one:{[a;t_ms] "{\"a\":",string[a],",\"t_ms\":",string[t_ms],"}"}
.qt.qetl.kafka.provenance.many:{[legs;px] "{\"legs\":",string[legs],",\"px\":",string[px],"}"}

/ --- the contract ------------------------------------------------------------

.qt.qetl.kafka.provenance.test_the_contract_names_four_columns_and_their_types:{[t]
    .qunit.assertEquals[key .qetl.kafka.provenance;`topic`partition`offset`broker_time;"the four columns, in order"];
    .qunit.assertEquals[value .qetl.kafka.provenance;"sjjp";"symbol, long, long, timestamp"];
    .qunit.assertEquals[.qetl.kafka.row_index;`row_index;"and the one extra column an expanded row carries"]};

/ Every declared topic's output, in this tree, meets the one contract - a decoder cannot have drifted.
.qt.qetl.kafka.provenance.test_every_declared_output_carries_the_contract_in_the_same_types:{[t]
    reg:0!select from .qetl.kafka.topic.registry where not topic in `prov.one`prov.drop`prov.sorted`prov.dup`prov.many`prov.bad;
    check:{[r]
        pub:.qetl.plant.published r`output;
        m:exec c!t from meta pub;
        ok:all (.qetl.kafka.coordinates in cols pub) and (m .qetl.kafka.coordinates)=.qetl.kafka.coordinate_types;
        ok and (.qetl.kafka.row_index in cols pub)=r`expand};
    .qunit.assertTrue[0<count reg;"the tree declares topics"];
    .qunit.assertTrue[all check each reg;"each output has topic, partition, offset, broker_time as symbol, long, long, timestamp"];
    };

.qt.qetl.kafka.provenance.test_a_declared_mapping_returns_the_payload_columns_only:{[t]
    reg:0!.qetl.kafka.topic.registry;
    got:{cols (.qetl.transform.def x)`output} each reg`mapping;
    want:{cols .qetl.kafka.topic.payload_schema[x]} each reg`output;
    .qunit.assertTrue[0<count reg;"there are mappings"];
    .qunit.assertTrue[got~want;"no mapping builds a provenance column"]};

/ --- refused when declared ---------------------------------------------------

.qt.qetl.kafka.provenance.test_an_output_without_broker_time_is_refused_at_declaration:{[t]
    .qunit.assertThrows[.qetl.kafka.topic.define[`prov.x];`mapping`output!(`prov_one_map;`prov_nobt);"*prov_nobt carries no broker_time*";
        "broker_time is part of the contract, not an option"]};

.qt.qetl.kafka.provenance.test_an_output_with_a_mistyped_provenance_column_is_refused:{[t]
    .qunit.assertThrows[.qetl.kafka.topic.define[`prov.x];`mapping`output!(`prov_one_map;`prov_int);"*prov_int's offset must be*";
        "an int offset would compare unequal to the long the broker gave"]};

.qt.qetl.kafka.provenance.test_row_index_belongs_to_the_expanding_topics_only:{[t]
    .qunit.assertThrows[.qetl.kafka.topic.define[`prov.x];`mapping`output!(`prov_one_map;`prov_stray);"*carries row_index but the topic does not expand*";
        "a one-to-one table stays minimal"];
    .qunit.assertThrows[.qetl.kafka.topic.define[`prov.x];`mapping`output`expand!(`prov_one_map;`prov_one;1b);"*carries no row_index*";
        "an expanding topic must number its rows"];
    .qunit.assertThrows[.qetl.kafka.topic.define[`prov.x];`mapping`output`expand!(`prov_many_map;`prov_float;1b);"*row_index must be a long*";
        "and in the right type"];
    .qunit.assertThrows[.qetl.kafka.topic.define[`prov.x];`mapping`output`expand!(`prov_many_map;`prov_many;1);"*expand must be a boolean*";
        "the flag is a boolean"]};

.qt.qetl.kafka.provenance.test_a_mapping_that_returns_provenance_columns_is_refused:{[t]
    / the OLD shape: coordinates built by the mapping
    old:`inputs`output`fn`examples!(enlist[`m]!enlist .qt.qetl.kafka.provenance.one_in;.qetl.plant.published `prov_one;{[m] m};
        enlist `inputs`expected!(enlist[`m]!enlist .qt.qetl.kafka.provenance.one_in;.qetl.plant.published `prov_one));
    d:@[old;`examples;:;enlist `inputs`expected!(enlist[`m]!enlist ([] topic:enlist `t; partition:enlist 0j; offset:enlist 0j; broker_time:enlist 0Np; a:enlist 1f; t_ms:enlist 0j);([] a:enlist 1f; event_time:enlist 0Np; broker_time:enlist 0Np; topic:enlist `t; partition:enlist 0j; offset:enlist 0j))];
    .qetl.transform.define[`prov_old_map;d];
    .qunit.assertThrows[.qetl.kafka.topic.define[`prov.x];`mapping`output!(`prov_old_map;`prov_one);"*does not return the payload columns of prov_one*";
        "the framework appends them; a mapping that also returns them is refused"]};

/ --- one-to-one --------------------------------------------------------------

.qt.qetl.kafka.provenance.test_a_one_to_one_row_keeps_the_records_coordinates_unchanged:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.one;7 9j;(.qt.qetl.kafka.provenance.one[1.5;0];.qt.qetl.kafka.provenance.one[2.5;0])];
    o:(.qetl.kafka.process e)[`out;`prov_one];
    .qunit.assertEquals[cols o;cols .qetl.plant.published `prov_one;"the table's own column order"];
    .qunit.assertEquals[.qetl.kafka.coordinates#o;.qetl.kafka.coordinates#e;"topic, partition, offset, broker_time exactly as the envelope had them"];
    .qunit.assertEquals[o`a;1.5 2.5;"and the payload's own columns"]};

.qt.qetl.kafka.provenance.test_a_missing_payload_time_stays_null_and_the_broker_time_is_not_substituted:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.one;1 2j;(.qt.qetl.kafka.provenance.one[1f;0];.qt.qetl.kafka.provenance.one[2f;5000])];
    o:(.qetl.kafka.process e)[`out;`prov_one];
    .qunit.assertTrue[null first o`event_time;"the payload had no time: null"];
    .qunit.assertEquals[last o`event_time;1970.01.01D00:00:05.000000000;"a payload time is the payload's"];
    .qunit.assertEquals[o`broker_time;e`broker_time;"the broker's time is its own column"]};

.qt.qetl.kafka.provenance.test_a_null_broker_time_is_carried_as_null:{[t]
    e:update broker_time:0Np from .qt.qetl.kafka.provenance.env[`prov.one;enlist 1j;enlist .qt.qetl.kafka.provenance.one[1f;0]];
    o:(.qetl.kafka.process e)[`out;`prov_one];
    .qunit.assertTrue[null first o`broker_time;"no record timestamp: null, not the receipt time"]};

.qt.qetl.kafka.provenance.test_a_one_to_one_mapping_that_changes_the_row_count_is_dead_lettered:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.drop;0 1 2j;.qt.qetl.kafka.provenance.one[;0] each 1 -1 3f];
    r:.qetl.kafka.process e;
    .qunit.assertEquals[r[`out;`prov_one]`offset;0 2j;"the records the mapping kept"];
    .qunit.assertEquals[(r[`dead]`offset;r[`dead]`reason);(enlist 1j;enlist `mapping);"the dropped one is on record"];
    .qunit.assertTrue[(first r[`dead]`detail) like "*declared with expand*";"saying what to do"]};

/ --- provenance follows message_index, not position (#1520) ---------------------

/ Offset 10 has the larger payload, so a sorting mapping puts it second: its row must still carry offset 10.
.qt.qetl.kafka.provenance.test_a_reordering_mapping_gets_each_rows_own_records_coordinates:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.sorted;10 11j;(.qt.qetl.kafka.provenance.one[2f;0];.qt.qetl.kafka.provenance.one[1f;0])];
    r:.qetl.kafka.process e;
    o:r[`out;`prov_one];
    .qunit.assertEquals[o`a;1 2f;"the mapping sorted its rows"];
    .qunit.assertEquals[o`offset;11 10j;"a=1 came from offset 11, a=2 from offset 10"];
    .qunit.assertEquals[o`broker_time;reverse e`broker_time;"and carries that record's broker_time"];
    .qunit.assertEquals[count r`dead;0;"nothing is refused"]};

.qt.qetl.kafka.provenance.test_a_one_to_one_mapping_must_name_each_record_exactly_once:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.dup;10 11j;(.qt.qetl.kafka.provenance.one[2f;0];.qt.qetl.kafka.provenance.one[1f;0])];
    r:.qetl.kafka.process e;
    / the batch is refused, so it is mapped record by record, where each is one row for one record
    .qunit.assertEquals[r[`out;`prov_one]`offset;10 11j;"a row never borrows another record's coordinates: the refused batch is mapped singly"];
    src:select topic,partition,offset,broker_time from e;
    .qunit.assertThrows[.qetl.kafka.propagate[`mapping`output`expand!(`prov_one_map;`prov_one;0b);src];([] message_index:0 0j; a:1 2f; event_time:2#0Np);"*message_index must name each of the 2 record(s) exactly once*";"a repeat is refused"];
    .qunit.assertThrows[.qetl.kafka.propagate[`mapping`output`expand!(`prov_one_map;`prov_one;0b);src];([] message_index:0 2j; a:1 2f; event_time:2#0Np);"*message_index must name each of the 2 record(s) exactly once*";"a position past the batch is refused"];
    .qunit.assertThrows[.qetl.kafka.propagate[`mapping`output`expand!(`prov_one_map;`prov_one;0b);src];([] a:1 2f; event_time:2#0Np);"*returned no message_index*";"a mapping that does not say is refused"]};

.qt.qetl.kafka.provenance.test_every_mapping_leads_with_message_index:{[t]
    reg:0!.qetl.kafka.topic.registry;
    got:{first cols (.qetl.transform.def x)`output} each reg`mapping;
    .qunit.assertTrue[0<count reg;"there are mappings"];
    .qunit.assertTrue[all got=\:`message_index;"one-to-one and expanding alike"];
    pub:.qetl.plant.published `client_flow;
    .qunit.assertEquals[cols pub;`broker_time`sym`side`qty`price`client`trade_id`topic`partition`offset;"and the published table does not carry it"]};

/ --- one-to-many ---------------------------------------------------------------

.qt.qetl.kafka.provenance.test_an_expanded_record_keeps_its_coordinates_on_every_row_and_numbers_them:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.many;4 5 6j;.qt.qetl.kafka.provenance.many'[2 0 3;1.5 2.5 3.5]];
    o:(.qetl.kafka.process e)[`out;`prov_many];
    .qunit.assertEquals[o`offset;4 4 6 6 6j;"each row carries its record's offset; the empty record has no rows"];
    .qunit.assertEquals[o`row_index;0 1 0 1 2j;"numbered within the record"];
    .qunit.assertEquals[o`broker_time;(2#e[`broker_time] 0),3#e[`broker_time] 2;"and its record's broker_time"];
    .qunit.assertEquals[count distinct flip (o`topic;o`partition;o`offset;o`row_index);count o;"(coordinates, row_index) is unique"];
    .qunit.assertEquals[count (.qetl.kafka.process e)`dead;0;"a record that produced no rows is not a dead letter"]};

.qt.qetl.kafka.provenance.test_row_indexes_do_not_depend_on_how_the_batch_is_split:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.many;4 5 6j;.qt.qetl.kafka.provenance.many'[2 1 3;1.5 2.5 3.5]];
    whole:(.qetl.kafka.process e)[`out;`prov_many];
    again:(.qetl.kafka.process e)[`out;`prov_many];
    single:(,/) {(.qetl.kafka.process enlist x)[`out;`prov_many]} each e;
    reversed:(.qetl.kafka.process reverse e)[`out;`prov_many];
    .qunit.assertEquals[again;whole;"a retry reproduces the rows"];
    .qunit.assertEquals[single;whole;"record by record gives the same indexes"];
    .qunit.assertEquals[asc exec row_index by offset from reversed;asc exec row_index by offset from whole;"and so does another delivery order"]};

.qt.qetl.kafka.provenance.test_a_refused_neighbour_does_not_change_an_expanded_records_indexes:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.many;1 2 3j;(.qt.qetl.kafka.provenance.many[2;1f];"not json";.qt.qetl.kafka.provenance.many[2;3f])];
    r:.qetl.kafka.process e;
    .qunit.assertEquals[r[`out;`prov_many]`row_index;0 1 0 1j;"the batch maps singly, the indexes stay per record"];
    .qunit.assertEquals[r[`dead]`offset;enlist 2j;"and the malformed record is on record"]};

.qt.qetl.kafka.provenance.test_an_expanding_mapping_that_points_past_the_batch_is_dead_lettered:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.bad;0 1j;2#enlist .qt.qetl.kafka.provenance.many[1;1f]];
    r:.qetl.kafka.process e;
    .qunit.assertEquals[(count r`out;r[`dead]`reason);(0;`mapping`mapping);"no row is attributed to a record it did not come from"];
    .qunit.assertTrue[all (r`dead)[`detail] like "*positions of the records*";"saying why"]};

/ --- empty outputs ---------------------------------------------------------------

.qt.qetl.kafka.provenance.test_an_empty_output_is_the_tables_published_shape:{[t]
    d:.qetl.kafka.topic.def `prov.many;
    src:([] topic:enlist `prov.many; partition:enlist 0j; offset:enlist 1j; broker_time:enlist .qt.qetl.kafka.provenance.t0; legs:enlist 0j; px:enlist 1f);
    r:.qetl.kafka.propagate[d;src;.qetl.kafka.topic.payload_schema[`prov_many]];
    .qunit.assertEquals[count r;0;"a record that expanded to nothing"];
    .qunit.assertEquals[(cols r;exec t from meta r);(cols pub;exec t from meta pub:.qetl.plant.published `prov_many);"and still the table's columns and types"];
    .qunit.assertEquals[count (.qetl.kafka.process .qt.qetl.kafka.provenance.env[`prov.many;`long$();()])`dead;0;"an empty batch dead-letters nothing"]};

/ --- malformed metadata ----------------------------------------------------------

.qt.qetl.kafka.provenance.test_a_record_without_its_coordinates_is_dead_lettered_not_decoded:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.one;1 2 3j;3#enlist .qt.qetl.kafka.provenance.one[1f;0]];
    e:update offset:0Nj from e where i=1;
    r:.qetl.kafka.process e;
    .qunit.assertEquals[r[`out;`prov_one]`offset;1 3j;"the others publish"];
    .qunit.assertEquals[(count r`dead;first r[`dead]`reason);(1;`malformed);"it cannot be keyed, so it is on record instead"];
    .qunit.assertTrue[(first r[`dead]`detail) like "*no topic, partition or offset*";"saying why"]};

/ --- replay and deduplication --------------------------------------------------------

.qt.qetl.kafka.provenance.fake_job:`prov_dec

.qt.qetl.kafka.provenance.fake_published:()

.qt.qetl.kafka.provenance.drive:{[]
    j:.qt.qetl.kafka.provenance.fake_job;
    `.qt.qetl.kafka.provenance.fake_published set ();
    (` sv `.qpipe.job,j,`publish_dead) set {[x] .qt.qetl.kafka.provenance.rec_dead x};
    (` sv `.qpipe.job,j,`publish_decoded) set {[t;x] `.qt.qetl.kafka.provenance.fake_published set .qt.qetl.kafka.provenance.fake_published,enlist (t;x)};
    .qetl.kafka.decoder.init j;
    j}

.qt.qetl.kafka.provenance.rec_dead:{[x] `.qt.qetl.kafka.provenance.fake_published set .qt.qetl.kafka.provenance.fake_published,enlist (`kafka_dead_letter;x)}

.qt.qetl.kafka.provenance.test_a_redelivered_expanded_record_is_not_republished:{[t]
    j:.qt.qetl.kafka.provenance.drive[];
    .qetl.kafka.topic.define[`prov.many;`mapping`output`job`expand!(`prov_many_map;`prov_many;j;1b)];
    e:.qt.qetl.kafka.provenance.env[`prov.many;4 5j;.qt.qetl.kafka.provenance.many'[2 3;1.5 2.5]];
    n:.qetl.kafka.decoder.handle[j;.qetl.kafka.decoder.fresh[j;e]];
    .qunit.assertEquals[n;5;"both records' rows went out"];
    again:.qetl.kafka.decoder.fresh[j;e];
    .qunit.assertEquals[count again;0;"the redelivery is judged by the record's coordinates, so none of its rows is published twice"];
    pub:(,/) .qt.qetl.kafka.provenance.fake_published[;1];
    .qunit.assertEquals[count distinct flip (pub`topic;pub`partition;pub`offset;pub`row_index);5;"every published row has its own key"];
    ![`.qpipe.job;();0b;enlist `prov_dec]};

.qt.qetl.kafka.provenance.test_a_restart_reads_the_handled_records_back_from_the_expanded_rows:{[t]
    e:.qt.qetl.kafka.provenance.env[`prov.many;4 5j;.qt.qetl.kafka.provenance.many'[2 3;1.5 2.5]];
    o:(.qetl.kafka.process e)[`out;`prov_many];
    s:.qetl.kafka.coordinate_set o;
    .qunit.assertEquals[count s;2;"five rows, two records"];
    .qunit.assertEquals[count .qetl.kafka.not_in[e;s];0;"and the replay finds both handled, so neither is decoded again"]};
