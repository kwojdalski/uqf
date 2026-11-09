/ test_compare.q - one table on two endpoints, compared (.qetl.compare,
/ src/etl/core/compare.q, #2033). (.qt.qetl.compare).
/ .
/ Each side is a FIXTURE TARGET: a function of one list standing in for a
/ gateway handle, answering the three reads compare.q makes - meta through
/ .gw.syncexec, .uqf.browse and .qetl.coverage.claims - from a table and a
/ coverage ledger it was given. So every outcome is reached the way a real
/ comparison reaches it, through .qetl.compare.run, with no process.

.qt.qetl.compare.from:2026.09.11D00:00:00.000000000
.qt.qetl.compare.to:2026.09.16D00:00:00.000000000

/ demo_deals as the plant holds it: its fixture deals, stamped on receipt.
.qt.qetl.compare.deals:{[]
    d:.qpipe.source.demo_deals.fixture[];
    (cols .qetl.plant.schema `demo_deals)#update time:deal_time+0D00:00:01 from d}

/ One claim covering the whole window, under source_version v1.
.qt.qetl.compare.whole:([] range_from:enlist .qt.qetl.compare.from; range_to:enlist .qt.qetl.compare.to)

/ A fixture target: `tabs` the tables it holds, `claims` its coverage. A read
/ it does not serve fails, as a gateway without the function would.
.qt.qetl.compare.target:{[tabs;claims]
    {[tabs;claims;x]
        f:first x;
        if[f~`.gw.syncexec;
            tn:x[1;1];
            :$[tn in key tabs; select c, t from 0!meta tabs tn; ()]];
        if[f~`.uqf.browse;
            rows:`time xasc tabs x 1;
            fc:x 2; fo:x 3; fv:x 4;
            keep:(rows[`time]>=fv 0) & rows[`time]<fv 1;
            keep:{[rows;k;c;o;v] k & $[o=`eq; rows[c]=v; o=`in; rows[c] in v; o=`ne; not rows[c] in v;
                o=`lt; rows[c]<v; o=`le; rows[c]<=v; o=`gt; rows[c]>v; rows[c]>=v]}[rows]/[keep;2_fc;2_fo;2_fv];
            :`rows`cap!((x 5) sublist rows where keep;x 5)];
        if[f~`.qetl.coverage.claims; :claims];
        '"fixture: no ",string f}[tabs;claims]}

/ The endpoint `run` takes for a fixture target.
.qt.qetl.compare.side:{[name;h] `name`h!(name;h)}

.qt.qetl.compare.req:{[given]
    (`range_from`range_to`filters`limit`source_version`partition`login!(
        .qt.qetl.compare.from;.qt.qetl.compare.to;();100j;`;`;"u:p")),given}

/ Compare demo_deals on two fixture targets.
.qt.qetl.compare.cmp:{[l;r;given]
    .qetl.compare.run[.qt.qetl.compare.side["local";l];.qt.qetl.compare.side["prod";r];`demo_deals;.qt.qetl.compare.req[given]]}

.qt.qetl.compare.same:{[] .qt.qetl.compare.target[enlist[`demo_deals]!enlist .qt.qetl.compare.deals[];.qt.qetl.compare.whole]}

/ --- the five outcomes ------------------------------------------------------

.qt.qetl.compare.test_equal_rows_are_equal_in_the_sample_and_say_every_row_was_read:{[t]
    r:.qt.qetl.compare.cmp[.qt.qetl.compare.same[];.qt.qetl.compare.same[];()!()];
    v:first r`tables;
    .qunit.assertEquals[(r`outcome;v`outcome;v`truncated;v`read);(`equal_in_sample;`equal_in_sample;0b;5 5j);"five deals each, all read"];
    .qunit.assertEquals[v`label;"equal in the sample - every row in the window was read on both sides and compared (5 rows)";"the label says the whole window was read"];
    .qunit.assertEquals[(v`key;v`basis);(enlist `deal_id;`events);"paired on the producers' key and basis"]};

.qt.qetl.compare.test_the_receipt_time_is_not_compared:{[t]
    later:.qt.qetl.compare.target[enlist[`demo_deals]!enlist update time:time+0D00:00:00.5 from .qt.qetl.compare.deals[];.qt.qetl.compare.whole];
    .qunit.assertEquals[(first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];later;()!()]`tables)`outcome;`equal_in_sample;
        "two environments receive a row at different times"]};

.qt.qetl.compare.test_a_truncated_sample_is_never_labelled_as_equality:{[t]
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];.qt.qetl.compare.same[];enlist[`limit]!enlist 3j]`tables;
    .qunit.assertEquals[(v`outcome;v`truncated;v`read);(`equal_in_sample;1b;3 3j);"cut at three rows each"];
    .qunit.assertEquals[v`label;"equal in a sample cut at 3 rows per side - NOT proof the tables are equal: rows past the cut were not compared";
        "and the label says so"]};

.qt.qetl.compare.test_a_sample_exactly_at_the_limit_is_not_truncated:{[t]
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];.qt.qetl.compare.same[];enlist[`limit]!enlist 5j]`tables;
    .qunit.assertEquals[(v`truncated;v`read);(0b;5 5j);"a sixth row was asked for, and there was none"]};

.qt.qetl.compare.test_a_truncated_sample_judges_only_paired_keys:{[t]
    d:.qt.qetl.compare.deals[];
    / prod's sample is cut at another place: deal 2 arrives last there
    late:.qt.qetl.compare.target[enlist[`demo_deals]!enlist update time:time+5D from d where deal_id=2;.qt.qetl.compare.whole];
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];late;`limit`range_to!(3j;.qt.qetl.compare.to+10D)]`tables;
    .qunit.assertEquals[(v`outcome;v`unpaired;count v`rows);(`equal_in_sample;2j;0);"deals 2 and 4 each in one sample only: not judged"];
    .qunit.assertTrue[v[`label] like "*(2 keys in one sample only, not judged)";"and counted in the label"]};

/ --- a table with no declared key (#2065) --------------------------------

/ Five ticks of a table no producer declares a key for, as the plant holds it.
.qt.qetl.compare.ticks:{[] ([] time:.qt.qetl.compare.from+0D01*1+til 5; sym:`a`b`c`d`e; px:1 2 3 4 5f)}

.qt.qetl.compare.nokey:{[cmp;t1;t2;given]
    a:.qt.qetl.compare.target[enlist[`nokey]!enlist t1;.qt.qetl.compare.whole];
    b:.qt.qetl.compare.target[enlist[`nokey]!enlist t2;.qt.qetl.compare.whole];
    .qetl.compare.run[.qt.qetl.compare.side["local";a];.qt.qetl.compare.side["prod";b];`nokey;.qt.qetl.compare.req[given]]}

.qt.qetl.compare.test_a_truncated_keyless_table_with_every_row_changed_is_not_equal:{[t]
    t1:.qt.qetl.compare.ticks[];
    r:.qt.qetl.compare.nokey[0;t1;update px:px+100 from t1;enlist[`limit]!enlist 3j];
    v:first r`tables;
    .qunit.assertTrue[not (r`outcome) in `equal_in_sample`incomplete_coverage;"every row differs, and no sample can say it does not"];
    .qunit.assertEquals[(r`outcome;v`outcome;v`truncated);(`not_comparable;`not_comparable;1b);"named as not comparable"];
    .qunit.assertTrue[v[`label] like "not comparable - nokey declares no row key and a sample was cut at 3 rows per side*";"the label says why"];
    .qunit.assertFalse[v[`label] like "*equal*";"and never says equal"]};

.qt.qetl.compare.test_an_untruncated_keyless_table_still_compares_whole_rows:{[t]
    t1:.qt.qetl.compare.ticks[];
    same:.qt.qetl.compare.nokey[0;t1;t1;()!()];
    .qunit.assertEquals[(same`outcome;(first same`tables)`truncated);(`equal_in_sample;0b);"identical, all read"];
    diff:.qt.qetl.compare.nokey[0;t1;update px:px+100 from t1;enlist[`limit]!enlist 10j];
    .qunit.assertEquals[(diff`outcome;count (first diff`tables)`rows);(`differs;10);"each changed row is one only_left and one only_right"]};

.qt.qetl.compare.test_a_truncated_keyless_table_with_a_schema_difference_still_differs:{[t]
    t1:.qt.qetl.compare.ticks[];
    r:.qt.qetl.compare.nokey[0;t1;update venue:`x from t1;enlist[`limit]!enlist 3j];
    .qunit.assertEquals[r`outcome;`differs;"a schema difference is a finding whatever the sample"]};

.qt.qetl.compare.test_a_truncated_keyed_table_is_unchanged_by_keyless_handling:{[t]
    other:.qt.qetl.compare.target[enlist[`demo_deals]!enlist update rate:1.2 from .qt.qetl.compare.deals[] where deal_id=1;.qt.qetl.compare.whole];
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];other;enlist[`limit]!enlist 3j]`tables;
    .qunit.assertEquals[(v`outcome;v`truncated;count v`rows);(`differs;1b;1);"a paired changed row is still judged"]};

.qt.qetl.compare.test_a_changed_value_differs_and_is_named_by_key:{[t]
    other:.qt.qetl.compare.target[enlist[`demo_deals]!enlist update rate:1.2 from .qt.qetl.compare.deals[] where deal_id=3;.qt.qetl.compare.whole];
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];other;()!()]`tables;
    .qunit.assertEquals[(v`outcome;v`label);(`differs;"differs - rows (1 difference)");"one deal changed"];
    .qunit.assertEquals[flip value flip v`rows;enlist ("deal_id=3";`changed;"rate: 1.0847 -> 1.2");"the deal, the column, both values"]};

.qt.qetl.compare.test_a_row_on_one_side_only_is_named_with_its_side:{[t]
    fewer:.qt.qetl.compare.target[enlist[`demo_deals]!enlist select from .qt.qetl.compare.deals[] where deal_id<>4;.qt.qetl.compare.whole];
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];fewer;()!()]`tables;
    .qunit.assertEquals[(v`outcome;flip value flip v`rows);(`differs;enlist ("deal_id=4";`only_left;"on local only"));"deal 4 is local's alone"]};

.qt.qetl.compare.test_a_schema_difference_differs_and_names_the_column:{[t]
    wider:.qt.qetl.compare.target[enlist[`demo_deals]!enlist update venue:`x from .qt.qetl.compare.deals[];.qt.qetl.compare.whole];
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];wider;()!()]`tables;
    .qunit.assertEquals[(v`outcome;v`label);(`differs;"differs - schema (1 column)");"prod has a column local lacks"];
    .qunit.assertEquals[flip value flip v`schema;enlist (`venue;"absent";"symbol (s)");"named, with each side's type"]};

.qt.qetl.compare.test_different_published_coverage_differs:{[t]
    part:([] range_from:enlist .qt.qetl.compare.from; range_to:enlist 2026.09.13D00:00:00.000000000);
    other:.qt.qetl.compare.target[enlist[`demo_deals]!enlist .qt.qetl.compare.deals[];part];
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];other;enlist[`source_version]!enlist `v1]`tables;
    .qunit.assertEquals[(v`outcome;v`label);(`differs;"differs - published coverage");"prod has not published from the 13th"];
    .qunit.assertEquals[((v`coverage)`right)`gaps;([] range_from:enlist 2026.09.13D00:00:00.000000000; range_to:enlist .qt.qetl.compare.to);"its gap, named"]};

.qt.qetl.compare.test_the_same_gaps_on_both_sides_are_incomplete_coverage:{[t]
    part:([] range_from:enlist .qt.qetl.compare.from; range_to:enlist 2026.09.13D00:00:00.000000000);
    both:.qt.qetl.compare.target[enlist[`demo_deals]!enlist .qt.qetl.compare.deals[];part];
    v:first .qt.qetl.compare.cmp[both;both;enlist[`source_version]!enlist `v1]`tables;
    .qunit.assertEquals[v`outcome;`incomplete_coverage;"neither side has published the whole window"];
    .qunit.assertEquals[v`label;"incomplete coverage - neither side has published the whole window (1 gap); the rows both have agree";"said as such"]};

.qt.qetl.compare.test_coverage_is_not_checked_without_a_source_version:{[t]
    none:.qt.qetl.compare.target[enlist[`demo_deals]!enlist .qt.qetl.compare.deals[];0#.qt.qetl.compare.whole];
    v:first .qt.qetl.compare.cmp[none;none;()!()]`tables;
    .qunit.assertEquals[(v`outcome;((v`coverage)`left)`checked);(`equal_in_sample;0b);"no ledger read, so no gap claimed"]};

.qt.qetl.compare.test_a_table_one_side_lacks_is_a_missing_table:{[t]
    bare:.qt.qetl.compare.target[(`symbol$())!();.qt.qetl.compare.whole];
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];bare;()!()]`tables;
    .qunit.assertEquals[(v`outcome;v`label);(`missing_table;"missing table - demo_deals is not on prod");"prod has no demo_deals"]};

.qt.qetl.compare.test_an_endpoint_not_reached_is_unavailable_with_its_reason:{[t]
    down:`name`address`error!("prod";"";"ssh: connect to host prod refused");
    r:.qetl.compare.run[.qt.qetl.compare.side["local";.qt.qetl.compare.same[]];down;`demo_deals;.qt.qetl.compare.req[()!()]];
    v:first r`tables;
    .qunit.assertEquals[(r`outcome;v`outcome);(`endpoint_unavailable;`endpoint_unavailable);"nothing to compare with"];
    .qunit.assertEquals[v`label;"endpoint unavailable - prod: ssh: connect to host prod refused";"and why"]};

.qt.qetl.compare.test_a_gateway_that_fails_a_read_is_unavailable:{[t]
    broken:{[x] '"gw: timeout"};
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];broken;()!()]`tables;
    .qunit.assertEquals[(v`outcome;v`label);(`endpoint_unavailable;"endpoint unavailable - prod: reading demo_deals's schema failed: gw: timeout");"its own message"]};

.qt.qetl.compare.test_a_closed_port_is_unavailable:{[t]
    closed:`name`address`error!("prod";"localhost:1";"");
    v:first .qetl.compare.run[.qt.qetl.compare.side["local";.qt.qetl.compare.same[]];closed;`demo_deals;.qt.qetl.compare.req[()!()]]`tables;
    .qunit.assertEquals[v`outcome;`endpoint_unavailable;"nothing listens on port 1"];
    .qunit.assertTrue[v[`label] like "endpoint unavailable - prod: could not connect to localhost:1*";"said so"]};

.qt.qetl.compare.test_the_run_reports_the_worst_outcome_of_its_tables:{[t]
    both:.qt.qetl.compare.target[`demo_deals`other!(.qt.qetl.compare.deals[];.qt.qetl.compare.deals[]);.qt.qetl.compare.whole];
    r:.qetl.compare.run[.qt.qetl.compare.side["local";both];.qt.qetl.compare.side["prod";.qt.qetl.compare.same[]];`demo_deals`other;.qt.qetl.compare.req[()!()]];
    .qunit.assertEquals[(r`outcome;r[`tables]`outcome);(`missing_table;`equal_in_sample`missing_table);"one equal, one missing"]};

/ --- filters ---------------------------------------------------------------

.qt.qetl.compare.test_a_filter_narrows_both_sides_by_a_typed_value:{[t]
    v:first .qt.qetl.compare.cmp[.qt.qetl.compare.same[];.qt.qetl.compare.same[];enlist[`filters]!enlist ("sym=EURUSD";"notional>=1000000")]`tables;
    .qunit.assertEquals[v`read;2 2j;"the EURUSD deals of a million or more"]};

.qt.qetl.compare.test_a_list_filter_is_in:{[t]
    f:.qetl.compare.parse_filter "sym=EURUSD,GBPUSD";
    .qunit.assertEquals[(f`column;f`op;f`values);(`sym;`in;("EURUSD";"GBPUSD"));"= with a list"];
    .qunit.assertEquals[.qetl.compare.parse_filter["side!=buy"]`op;`ne;"!= alone"]};

.qt.qetl.compare.test_a_filter_that_is_not_column_op_value_is_refused:{[t]
    .qunit.assertThrows[.qetl.compare.parse_filter;"sym";"filter sym: expected COLUMN OP VALUE*";"no operator"];
    .qunit.assertThrows[.qetl.compare.parse_filter;"sym!EURUSD";"filter sym!EURUSD: ! is not an operator*";"a bare !"];
    .qunit.assertThrows[.qetl.compare.parse_filter;"px<1,2";"filter px<1,2: only = and != take a list*";"a list on <"]};

.qt.qetl.compare.test_a_filter_on_a_column_the_table_lacks_or_a_mistyped_value_is_refused:{[t]
    m:([] c:`sym`px; t:"sf");
    .qunit.assertThrows[.qetl.compare.browse_filters[m];enlist .qetl.compare.parse_filter "venue=x";"filter on venue: no such column*";"no column"];
    .qunit.assertThrows[.qetl.compare.browse_filters[m];enlist .qetl.compare.parse_filter "px=abc";"filter on px: \"abc\" is not a float (f)*";"not a float"]};

/ #2472: the value a /query filter means - a timespan in nanoseconds, a
/ timestamp with its offset applied - not q's bare "N"$ / "P"$ parse.
.qt.qetl.compare.test_a_digit_timespan_is_nanoseconds:{[t]
    .qunit.assertEquals[.qetl.compare.typed["n";"1234"];0D00:00:00.000001234;"1234 ns, not 12:34"];
    .qunit.assertEquals[.qetl.compare.typed["n";"-5"];neg 0D00:00:00.000000005;"a negative count"];
    .qunit.assertEquals[.qetl.compare.typed["n";"0D00:01:00"];0D00:01:00;"a span with a D is q's"]};

.qt.qetl.compare.test_a_timestamp_has_its_offset_applied:{[t]
    .qunit.assertEquals[.qetl.compare.typed["p";"2026-09-13T06:00:00+02:00"];2026.09.13D04:00;"+02:00 applied"];
    .qunit.assertEquals[.qetl.compare.typed["p";"2026-09-13T06:00:00.000000001Z"];2026.09.13D06:00:00.000000001;"Z, to the ns"];
    .qunit.assertEquals[.qetl.compare.typed["p";"2026.09.13D06:00:00"];2026.09.13D06:00;"q's own spelling"]};

.qt.qetl.compare.test_a_char_value_of_more_than_one_character_is_refused:{[t]
    .qunit.assertEquals[.qetl.compare.typed["c";"A"];"A";"one character"];
    .qunit.assertThrows[.qetl.compare.typed["c"];"ABC";"\"ABC\" is not a char (c)";"never its first character"]};

/ demo_deals with a venue column the other side does not have (#2069).
.qt.qetl.compare.with_venue:{[] update venue:`X from .qt.qetl.compare.deals[]}

/ A second table, with a venue column, held identically by both fixture sides.
.qt.qetl.compare.venues:{[]
    ([] time:.qt.qetl.compare.from+0D01:00:00 0D02:00:00; venue:`X`Y; px:1.5 2.5)}

.qt.qetl.compare.test_a_filter_column_only_one_side_has_reports_the_schema_difference:{[t]
    lhs:.qt.qetl.compare.same[];
    r:.qt.qetl.compare.target[enlist[`demo_deals]!enlist .qt.qetl.compare.with_venue[];.qt.qetl.compare.whole];
    res:.qt.qetl.compare.cmp[lhs;r;enlist[`filters]!enlist enlist "venue=X"];
    v:first res`tables;
    .qunit.assertEquals[(res`outcome;v`outcome);(`differs;`differs);"differs, not a refusal"];
    .qunit.assertEquals[v`schema;([] column:enlist `venue; left:enlist "absent"; right:enlist "symbol (s)");"the schema names venue, absent on the left"];
    .qunit.assertEquals[count v`rows;0;"rows are not compared when the filter could not apply"];
    .qunit.assertEquals[v`reasons;`left`right!("lacks the filter column venue";"");"the side that lacks it is named"];
    .qunit.assertEquals[v`label;"differs - schema (1 column); rows not compared: local lacks the filter column venue";"the label names the side"]};

.qt.qetl.compare.test_a_filter_column_one_table_lacks_does_not_stop_the_other_tables:{[t]
    ts:{[d;v] `demo_deals`demo_venues!(d;v)};
    lhs:.qt.qetl.compare.target[ts[.qt.qetl.compare.deals[];.qt.qetl.compare.venues[]];.qt.qetl.compare.whole];
    r:.qt.qetl.compare.target[ts[.qt.qetl.compare.with_venue[];.qt.qetl.compare.venues[]];.qt.qetl.compare.whole];
    res:.qetl.compare.run[.qt.qetl.compare.side["local";lhs];.qt.qetl.compare.side["prod";r];`demo_deals`demo_venues;
        .qt.qetl.compare.req[enlist[`filters]!enlist enlist "venue=X"]];
    .qunit.assertEquals[(res[`tables]`table;res[`tables]`outcome);(`demo_deals`demo_venues;`differs`equal_in_sample);"the other table is still compared, on the filtered rows"];
    .qunit.assertEquals[(res[`tables] 1)`read;1 1j;"one venue X row each side"]};

.qt.qetl.compare.test_a_filter_column_absent_on_both_sides_is_still_refused:{[t]
    run:{[x] .qt.qetl.compare.cmp[.qt.qetl.compare.same[];.qt.qetl.compare.same[];enlist[`filters]!enlist enlist "venue=X"]};
    .qunit.assertThrows[run;::;"filter on venue: no such column*";"on neither side"]};

.qt.qetl.compare.test_an_empty_window_or_no_limit_is_refused:{[t]
    s:.qt.qetl.compare.side["a";.qt.qetl.compare.same[]];
    .qunit.assertThrows[{[s;r] .qetl.compare.run[s;s;`demo_deals;r]}[s];.qt.qetl.compare.req[enlist[`range_to]!enlist .qt.qetl.compare.from];"the window must be non-empty*";"from = to"];
    .qunit.assertThrows[{[s;r] .qetl.compare.run[s;s;`demo_deals;r]}[s];.qt.qetl.compare.req[enlist[`limit]!enlist 0j];"the row limit must be positive*";"no rows"]};

/ --- the key ---------------------------------------------------------------

.qt.qetl.compare.test_a_table_no_producer_keys_is_matched_whole:{[t]
    .qunit.assertEquals[.qetl.compare.basis[`compare_no_such_table];`key`basis!(`symbol$();`events);"every column, as events"]};
