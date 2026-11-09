/ test_verify_complete.q - reconciling the source against the destination
/ (.qetl.verify.completeness, src/etl/core/verify_complete.q, #1851).
/ (.qt.qetl.verify.complete).
/ .
/ The destination is a reader function here: a table stands in for the combined
/ RDB+HDB, and the source is demo_deals' fixture, read through its own adapter.

.qt.qetl.verify.complete.from:2026.09.11D00:00:00.000000000
.qt.qetl.verify.complete.to:2026.09.20D00:00:00.000000000
.qt.qetl.verify.complete.scope:enlist[`sym]!enlist `EURUSD

.qt.qetl.verify.complete.fixture:{[] .qpipe.source.demo_deals.fixture[]}

/ completeness over the fixture window, the destination being `rows`
.qt.qetl.verify.complete.run:{[rows]
    .qetl.verify.completeness[`demo_deals;.qt.qetl.verify.complete.scope;.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[rows;d;f;t] rows}[rows]]}

.qt.qetl.verify.complete.test_a_destination_holding_every_record_in_scope_passes:{[t]
    r:.qt.qetl.verify.complete.run .qt.qetl.verify.complete.fixture[];
    .qunit.assertEquals[(r`status;r`counts);(`PASS;3 3);"the three EURUSD deals, found in the destination"];
    .qunit.assertEquals[r`basis;"demo_deals, fixture, complete through (no cutoff: closed data) (not declared: closed by default); scope ",.Q.s1[.qt.qetl.verify.complete.scope]," on deal_time in [2026.09.11D00:00:00.000000000; 2026.09.20D00:00:00.000000000); keyed on deal_id; destination as the reader returned it";
        "the basis names the source, its cutoff, scope and bounds"]};

.qt.qetl.verify.complete.test_records_of_other_scopes_neither_help_nor_hurt:{[t]
    r:.qt.qetl.verify.complete.run select from .qt.qetl.verify.complete.fixture[] where sym=`EURUSD;
    .qunit.assertEquals[r`status;`PASS;"the destination need hold only the scope"];
    r2:.qt.qetl.verify.complete.run .qt.qetl.verify.complete.fixture[],update deal_id:77 from select from .qt.qetl.verify.complete.fixture[] where sym=`USDJPY;
    .qunit.assertEquals[r2`status;`PASS;"and an extra deal of another scope is not judged"]};

.qt.qetl.verify.complete.test_a_record_both_producers_could_omit_is_found_missing:{[t]
    r:.qt.qetl.verify.complete.run select from .qt.qetl.verify.complete.fixture[] where deal_id<>3;
    .qunit.assertEquals[(r`status;r`counts);(`FAIL;3 2);"the destination lacks deal 3"];
    .qunit.assertEquals[((r`findings)`rowkey;(r`findings)`kind;(r`findings)`detail);(enlist "deal_id=3";enlist `missing;enlist "in the source only");"named"]};

.qt.qetl.verify.complete.test_a_record_the_source_does_not_hold_is_extra:{[t]
    d:.qt.qetl.verify.complete.fixture[];
    r:.qt.qetl.verify.complete.run d,update deal_id:99 from 1#select from d where deal_id=1;
    .qunit.assertEquals[(r`status;(r`findings)`rowkey;(r`findings)`kind);(`FAIL;enlist "deal_id=99";enlist `extra);"a ghost deal"]};

.qt.qetl.verify.complete.test_a_repeated_record_is_a_duplicate_for_an_events_dataset:{[t]
    d:.qt.qetl.verify.complete.fixture[];
    r:.qt.qetl.verify.complete.run d,select from d where deal_id=1;
    .qunit.assertEquals[(r`status;(r`findings)`kind;first (r`findings)`detail);(`FAIL;enlist `duplicated;"source 1 time(s), destination 2")]};

.qt.qetl.verify.complete.test_no_scope_is_unverified:{[t]
    r:.qetl.verify.completeness[`demo_deals;()!();.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[d;f;t] 1}];
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"no scope given - a completeness check reconciles an explicit scope (column=value) and range");"nothing reconciled"]};

.qt.qetl.verify.complete.test_no_reader_is_unverified:{[t]
    r:.qetl.verify.completeness[`demo_deals;.qt.qetl.verify.complete.scope;.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;::];
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"no destination reader - the destination's RDB and HDB were not read");"the destination was not read"]};

.qt.qetl.verify.complete.test_a_scope_column_the_source_lacks_is_unverified:{[t]
    r:.qetl.verify.completeness[`demo_deals;enlist[`account]!enlist `a1;.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[d;f;t] 1}];
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"the source does not carry account");"named"]};

.qt.qetl.verify.complete.test_an_empty_scope_on_both_sides_is_unverified:{[t]
    r:.qetl.verify.completeness[`demo_deals;enlist[`sym]!enlist `NOSUCH;.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[d;f;t] .qpipe.source.demo_deals.fixture[]}];
    .qunit.assertEquals[(r`status;r`counts);(`UNVERIFIED;0 0);"no record to reconcile is not a pass"];
    .qunit.assertTrue[(r`reason) like "empty sample*";"and says why"]};

.qt.qetl.verify.complete.test_a_reader_that_throws_is_unverified:{[t]
    r:.qetl.verify.completeness[`demo_deals;.qt.qetl.verify.complete.scope;.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[d;f;t] '"gateway down"}];
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"reading the destination failed: gateway down");"the reason, not a pass"]};

.qt.qetl.verify.complete.test_the_gateway_reader_asks_getdata_for_the_table_and_a_padded_range:{[t]
    / a lambda stands in for the gateway handle: it is applied to (function; request) as a handle is
    seen:enlist 0;
    fake:{[x] `.qt.qetl.verify.complete.asked set x; .qpipe.source.demo_deals.fixture[]};
    reader:.qetl.verify.getdata_reader[fake;1D];
    got:reader[`demo_deals;.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to];
    .qunit.assertEquals[.qt.qetl.verify.complete.asked 0;`.dataaccess.getdata;"through the gateway's policy-checked entry point"];
    .qunit.assertEquals[.qt.qetl.verify.complete.asked 1;`tablename`starttime`endtime!(`demo_deals;.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to+1D);
        "the table and the window, widened by the pad past its end"];
    .qunit.assertEquals[count got;5;"the rows it returned"]};

/ #2473: `uqs job check --scope col=value` sends each value as text, typed here by its
/ column - a numeric-looking symbol, a date or a float used to arrive as a long or a
/ symbol, match no row on either side, and read UNVERIFIED as an "empty sample".
.qt.qetl.verify.complete.test_a_text_scope_is_typed_by_its_column_and_passes:{[t]
    r:.qetl.verify.completeness[`demo_deals;enlist[`sym]!enlist "EURUSD";.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[d;f;t] .qpipe.source.demo_deals.fixture[]}];
    .qunit.assertEquals[(r`status;r`counts);(`PASS;3 3);"\"EURUSD\" is the symbol on a symbol column"];
    r2:.qetl.verify.completeness[`demo_deals;enlist[`rate]!enlist "1.0847";.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[d;f;t] .qpipe.source.demo_deals.fixture[]}];
    .qunit.assertEquals[(r2`status;r2`counts);(`PASS;1 1);"\"1.0847\" is the float on a float column"]};

.qt.qetl.verify.complete.test_a_text_scope_keeps_leading_zeros_and_reads_dates_and_ints:{[t]
    rows:([] time:2026.09.13D01:00 2026.09.13D02:00; sym:`0005`0006; venue:7 8i; day:2026.09.13 2026.09.14);
    f:{[rows;s] count .qetl.verify.in_scope[rows;.qetl.verify.typed_scope[meta rows;s];`time;2026.09.13D00:00;2026.09.14D00:00]}[rows];
    .qunit.assertEquals[f enlist[`sym]!enlist "0005";1;"`0005, not the long 5"];
    .qunit.assertEquals[f enlist[`day]!enlist "2026.09.13";1;"a date on a date column"];
    .qunit.assertEquals[f enlist[`venue]!enlist "7";1;"an int on an int column"];
    .qunit.assertEquals[f enlist[`sym]!enlist `0005;1;"a value already typed is left as it is"]};

.qt.qetl.verify.complete.test_a_text_scope_its_column_cannot_hold_is_refused:{[t]
    a:(`demo_deals;enlist[`deal_id]!enlist "abc";.qt.qetl.verify.complete.from;.qt.qetl.verify.complete.to;{[d;f;t] .qpipe.source.demo_deals.fixture[]});
    / the whole message, trapped: KDB-X's like refuses a pattern with an inner *
    .qunit.assertEquals[@[{(`ok;.qetl.verify.completeness . x)};a;{[e] e}];"scope deal_id: \"abc\" is not a long (j)";
        "refused, naming the column"]};
