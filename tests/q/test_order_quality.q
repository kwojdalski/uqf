/ test_order_quality.q - the rolling order-outcome job (#1224, #1396): fill and
/ reject ratios per book over the last minute, five minutes and hour, read from
/ the order-outcome contract.
/ .
/ The kind's rules (the window boundary, lateness, eviction, restart) are
/ test_rolling.q's; these are the statistic's: the ratios on a worked example,
/ their agreement with .qf.exec.hit_ratio_by and .qf.exec.reject_ratio_by, the identity
/ of a book that is quiet in a short window, and the counting policy of
/ order_quality.q - one request, once, at its latest terminal outcome.
/ The orders feed's own rows go through the REAL adapter (order_outcomes1), so the
/ original worked examples are still the proof that the metrics did not move.

\d .order_qualitytest

t0:2026.10.09D10:00:00

/ The clock the job reads, which the tests move.
clock:t0

sent:()

/ The order id the next order is given: the feed's ids are unique, and so are these.
nid:0

/ An order received at tm, in the orders shape, with an id of its own.
order:{[tm;bk;status;sz]
    `.order_qualitytest.nid set 1+nid;
    ([] time:enlist tm; order_id:enlist nid; sym:enlist `EURUSD; book:enlist bk;
        product:enlist `spot; side:enlist 1; size:enlist sz; price:enlist 1.1; order_status:enlist status)}

/ An orders batch as the normalizer publishes it: through the real adapter, stamped
/ by the plant at tm.
outcomes_of:{[tm;batch] update time:tm from .qetl.job.stream.normalizer.normalize[`order_outcomes;`orders;batch]}

/ The job's own clock as the tree defined it, taken the first time it is moved so
/ release can put the same function back - the suite measures that one.
was:()

grab:{[] if[()~.order_qualitytest.was; `.order_qualitytest.was set .qpipe.job.order_quality.now]}

/ The job, emptied, its clock moved by the tests and its publish recorded.
job:{[]
    grab[];
    .qetl.job.stream.reset `order_quality;
    .qpipe.job.order_quality.now:{[] .order_qualitytest.clock};
    `.order_qualitytest.clock set t0;
    `.order_qualitytest.sent set ();
    .qetl.job.stream.wire[`order_quality;{[t;x] .order_qualitytest.sent,:enlist (t;x); count x}];
    }

/ Put the job's own clock back, as the suite found it.
release:{[] .qpipe.job.order_quality.now:.order_qualitytest.was}

feed:{[tm;bk;status;sz] `.order_qualitytest.clock set tm; .qpipe.job.order_quality.on_batch[`order_outcomes;outcomes_of[tm;order[tm;bk;status;sz]]]}

published:{[] $[count sent; raze last each sent; 0#.qetl.plant.published `order_quality]}

row:{[x;bk;w] first select from x where book=bk, window=w}

/ What jobout drives it with: orders and a tick.
contract_driver:{[]
    / wired by the suite already: only the state and the clock are ours
    grab[];
    .qetl.job.stream.reset `order_quality;
    .qpipe.job.order_quality.now:{[] .order_qualitytest.clock};
    feed[t0+0D00:00:10;`london;`rejected;1e6];
    feed[t0+0D00:00:20;`london;`filled;1e6];
    .qpipe.job.order_quality.evaluate t0+0D00:00:30;
    release[];
    }

test_the_job_reads_the_process_clock_by_default:{[t]
    before:.z.p;
    at:.qpipe.job.order_quality.now[];
    .qunit.assertTrue[at within (before;.z.p);"the default clock is .z.p - .u.upd stamps rows in it"]};

test_it_is_declared_over_order_outcomes_by_book_at_three_windows:{[t]
    d:.qetl.job.stream.rolling.def `order_quality;
    .qunit.assertEquals[(d`windows;d`by;d`event_time);(0D00:01 0D00:05 0D01;enlist `book;`source_time);"the declaration: cut on the source's time, not the plant's"];
    .qunit.assertEquals[(.qetl.job.stream.def `order_quality)`subscribe_to;enlist `order_outcomes;"it reads the contract, not an order table"];
    .qunit.assertEquals[(.qetl.job.stream.def `order_quality)`publishes;enlist `order_quality;"and writes order_quality"]};

/ As of 10:10:00. london's orders: 10:00:00 filled, 10:01:00 cancelled, 10:02:00
/ rejected, 10:05:00 rejected, 10:06:00 filled, 10:09:30 filled, 10:09:45
/ rejected, 10:10:00 filled. The minute (10:09:00, 10:10:00] holds the last three;
/ the five minutes (10:05:00, ...] the last four - 10:05:00 is exactly 5m old and
/ out; the hour all eight. newyork has one order, rejected at 10:09:50.
test_ratios_on_a_worked_example:{[t]
    job[];
    as_of:t0+0D00:10:00;
    feed[t0;`london;`filled;1e6];
    feed[t0+0D00:01:00;`london;`cancelled;1e6];
    feed[t0+0D00:02:00;`london;`rejected;1e6];
    feed[t0+0D00:05:00;`london;`rejected;1e6];
    feed[t0+0D00:06:00;`london;`filled;1e6];
    feed[t0+0D00:09:30;`london;`filled;1e6];
    feed[t0+0D00:09:45;`london;`rejected;1e6];
    feed[t0+0D00:09:50;`newyork;`rejected;1e6];
    feed[as_of;`london;`filled;1e6];
    .qpipe.job.order_quality.evaluate as_of;
    p:published[];
    m:row[p;`london;0D00:01];
    .qunit.assertEquals[(m`n_orders;m`n_filled;m`n_rejected);3 2 1;"london 1m: filled, rejected, filled"];
    .testutil.assertApprox[m`fill_ratio;2%3;1e-12;"2 of 3 filled"];
    .testutil.assertApprox[m`reject_ratio;1%3;1e-12;"1 of 3 rejected"];
    f:row[p;`london;0D00:05];
    .qunit.assertEquals[(f`n_orders;f`n_filled;f`n_rejected);4 3 1;"london 5m: the 10:05:00 order is exactly 5m old and out"];
    .testutil.assertApprox[f`fill_ratio;0.75;1e-12;"3 of 4"];
    .testutil.assertApprox[f`reject_ratio;0.25;1e-12;"1 of 4"];
    h:row[p;`london;0D01];
    .qunit.assertEquals[(h`n_orders;h`n_filled;h`n_rejected);8 4 3;"london 1h: all eight, the 10:00:00 order is only 10m old"];
    n:row[p;`newyork;0D00:01];
    .qunit.assertEquals[(n`n_orders;n`n_rejected);1 1;"newyork 1m"];
    .testutil.assertApprox[n`reject_ratio;1f;1e-12;"its only order was rejected"];
    .qunit.assertEquals[count p;6;"two books by three windows"];
    release[]};

test_the_ratios_are_the_execution_modules_own:{[t]
    job[];
    as_of:t0+0D00:10:00;
    feed[as_of-0D00:00:50;`london;`filled;1e6];
    feed[as_of-0D00:00:40;`london;`rejected;2e6];
    feed[as_of-0D00:00:30;`london;`rejected;3e6];
    feed[as_of-0D00:00:20;`london;`cancelled;1e6];
    .qpipe.job.order_quality.evaluate as_of;
    m:row[published[];`london;0D00:01];
    / the same window through the library: [as_of-1m+1ns, as_of], as the half-open window is
    reqs:([] time:as_of-0D00:00:50 0D00:00:40 0D00:00:30 0D00:00:20; book:4#`london; size:1 2 3 1f;
        hit:1000b; reject:0110b);
    hit:first exec hit_ratio from .qf.exec.hit_ratio_by[reqs;as_of-0D00:00:59;as_of;0Nn;enlist `book;`count];
    rej:first exec reject_ratio from .qf.exec.reject_ratio_by[reqs;as_of-0D00:00:59;as_of;0Nn;enlist `book;`count];
    .testutil.assertApprox[m`fill_ratio;hit;1e-12;"fill ratio is hit_ratio_by in count mode"];
    .testutil.assertApprox[m`reject_ratio;rej;1e-12;"reject ratio is reject_ratio_by in count mode"];
    release[]};

test_a_book_quiet_in_the_minute_publishes_zero_orders_and_null_ratios:{[t]
    job[];
    as_of:t0+0D00:10:00;
    feed[as_of-0D00:03:00;`london;`rejected;1e6];
    .qpipe.job.order_quality.evaluate as_of;
    p:published[];
    m:row[p;`london;0D00:01];
    .qunit.assertEquals[(m`n_orders;m`n_filled;m`n_rejected);0 0 0;"nothing in the minute"];
    .qunit.assertTrue[(null m`fill_ratio) and null m`reject_ratio;"ratios are null, not zero: no order says nothing about the rate"];
    .qunit.assertEquals[(row[p;`london;0D00:05])`reject_ratio;1f;"the five minutes hold the reject"];
    release[]};

test_an_outcome_is_late_on_the_sources_stamp_not_the_plants:{[t]
    job[];
    / the plant receives it a day after the source stamped it
    `.order_qualitytest.clock set t0+0D01;
    .qpipe.job.order_quality.on_batch[`order_outcomes;outcomes_of[t0+0D01;order[t0;`london;`filled;1e6]]];
    .qunit.assertEquals[(count .qpipe.job.order_quality.dropped;first .qpipe.job.order_quality.dropped`reason);(1;`late);"dropped and recorded"];
    .qunit.assertEquals[count .qpipe.job.order_quality.pending;0;"and not buffered"];
    / three seconds is inside the 5s lateness
    job[];
    `.order_qualitytest.clock set t0+0D00:00:03;
    .qpipe.job.order_quality.on_batch[`order_outcomes;outcomes_of[t0+0D00:00:03;order[t0;`london;`filled;1e6]]];
    .qunit.assertEquals[(count .qpipe.job.order_quality.dropped;count .qpipe.job.order_quality.pending);0 1;"within the lateness it counts"];
    release[]};

test_a_book_with_no_order_in_the_hour_is_not_published:{[t]
    job[];
    as_of:t0+0D02:00:00;
    feed[t0;`gone;`filled;1e6];
    feed[as_of-0D00:00:10;`london;`filled;1e6];
    .qpipe.job.order_quality.evaluate as_of;
    .qunit.assertEquals[distinct published[]`book;enlist `london;"two hours quiet: its rows are evicted and it is no longer a key"];
    release[]};

\
/ ------------------------------------------------- THE COUNTING POLICY
/ These drive the statistic through the kind's own compute (the live job's path
/ and a backfill's), on outcome rows built directly in the contract's shape.

as_of:t0+0D00:10:00

/ Outcome rows: one per (second offset before as_of; outcome) for a request of a source.
out:{[src;rid;sec;oc;bk]
    ([] source_time:enlist as_of-0D00:00:01*sec; source:enlist src; request_id:enlist rid; outcome:enlist oc; book:enlist bk)}

/ The statistic of rows as of an instant, and one group's row for a window.
stat:{[rows;at] .qetl.job.stream.rolling.compute[`order_quality;rows;at;0Np]}

counts:{[x;bk;w] r:first select from x where book=bk, window=w; (r`n_orders;r`n_filled;r`n_rejected)}

test_a_redelivered_outcome_is_one_request:{[t]
    / the same row three times, and the same outcome re-sent with a later stamp
    r:raze (out[`a;`r1;30;`filled;`london];out[`a;`r1;30;`filled;`london];out[`a;`r1;30;`filled;`london];out[`a;`r1;20;`filled;`london]);
    x:stat[r;as_of];
    .qunit.assertEquals[counts[x;`london;0D00:01];1 1 0;"four deliveries, one request"];
    .qunit.assertEquals[counts[x;`london;0D01];1 1 0;"and in every window"]};

test_a_multi_update_lifecycle_is_counted_once_at_its_terminal_outcome:{[t]
    / new, partially filled twice, filled: four status events, one request
    r:raze (out[`a;`r1;50;`unresolved;`london];out[`a;`r1;40;`unresolved;`london];out[`a;`r1;30;`unresolved;`london];out[`a;`r1;20;`filled;`london]);
    x:stat[r;as_of];
    .qunit.assertEquals[counts[x;`london;0D00:01];1 1 0;"the denominator is 1, not 4"];
    .testutil.assertApprox[(first select from x where window=0D00:01)`fill_ratio;1f;1e-12;"fully filled"]};

test_a_request_is_counted_when_it_ends_not_when_it_opens:{[t]
    / opened 5m ago, filled 30s ago: in the 1m window as a fill, and in the 5m window once
    r:raze (out[`a;`r1;290;`unresolved;`london];out[`a;`r1;30;`filled;`london]);
    x:stat[r;as_of];
    .qunit.assertEquals[counts[x;`london;0D00:01];1 1 0;"in the minute its terminal event falls in"];
    .qunit.assertEquals[counts[x;`london;0D00:05];1 1 0;"and once in the five minutes"]};

test_a_request_still_open_is_in_no_count_and_its_group_has_null_ratios:{[t]
    x:stat[out[`a;`r1;20;`unresolved;`boston];as_of];
    .qunit.assertEquals[counts[x;`boston;0D00:01];0 0 0;"not in the denominator"];
    r:first select from x where window=0D00:01;
    .qunit.assertTrue[(null r`fill_ratio) and null r`reject_ratio;"null ratios, not a rate of zero"];
    .qunit.assertEquals[count x;3;"the group is still a row per window: it has an outcome in the hour"]};

test_an_unmapped_status_is_in_no_count:{[t]
    r:raze (out[`a;`r1;20;`unmapped;`london];out[`a;`r2;20;`filled;`london]);
    .qunit.assertEquals[counts[stat[r;as_of];`london;0D00:01];1 1 0;"the unmapped row is neither numerator nor denominator"]};

test_a_corrected_outcome_replaces_the_earlier_one:{[t]
    r:raze (out[`a;`r1;70;`rejected;`london];out[`a;`r1;20;`filled;`london]);
    .qunit.assertEquals[counts[stat[r;as_of];`london;0D00:05];1 1 0;"as of now: one request, filled"];
    / the series is not republished: as of before the correction it was a reject
    .qunit.assertEquals[counts[stat[r;as_of-0D00:00:30];`london;0D00:05];1 0 1;"as of 30s earlier the correction had not arrived"]};

test_a_reject_followed_by_a_retry_is_two_requests:{[t]
    r:raze (out[`a;`r1;50;`rejected;`london];out[`a;`r2;20;`filled;`london]);
    x:stat[r;as_of];
    .qunit.assertEquals[counts[x;`london;0D00:01];2 1 1;"a retry has its own id: two requests"];
    .testutil.assertApprox[(first select from x where window=0D00:01)`reject_ratio;0.5;1e-12;"half rejected"]};

test_the_same_id_from_two_sources_is_two_requests:{[t]
    r:raze (out[`a;`r1;50;`rejected;`london];out[`b;`r1;20;`filled;`london]);
    .qunit.assertEquals[counts[stat[r;as_of];`london;0D00:01];2 1 1;"identity is (source; request_id)"];
    r2:raze (out[`a;`r1;50;`rejected;`london];out[`a;`r1;20;`rejected;`london]);
    .qunit.assertEquals[counts[stat[r2;as_of];`london;0D00:01];1 0 1;"within one source it is one"]};

/ The policy on one population: filled, rejected, cancelled, unresolved, unmapped.
test_the_counting_policy_on_a_mixed_population:{[t]
    r:raze (out[`a;`r1;50;`filled;`london];out[`a;`r2;45;`filled;`london];out[`a;`r3;40;`rejected;`london];
        out[`a;`r4;35;`cancelled;`london];out[`a;`r5;30;`unresolved;`london];out[`a;`r6;25;`unmapped;`london]);
    x:stat[r;as_of];
    .qunit.assertEquals[counts[x;`london;0D00:01];4 2 1;"cancelled counts, open and unmapped do not"];
    m:first select from x where window=0D00:01;
    .testutil.assertApprox[m`fill_ratio;0.5;1e-12;"2 of 4"];
    .testutil.assertApprox[m`reject_ratio;0.25;1e-12;"1 of 4"]};

test_an_empty_input_publishes_nothing_and_an_empty_window_a_null_ratio:{[t]
    .qunit.assertEquals[count stat[0#out[`a;`r1;20;`filled;`london];as_of];0;"no outcomes, no rows"];
    e:.qpipe.transform.order_quality.stats 0#.qetl.plant.columns[`order_outcomes;`source_time`source`request_id`outcome`book];
    .qunit.assertEquals[count e;0;"the transform itself, on an empty table"];
    x:stat[out[`a;`r1;200;`filled;`london];as_of];
    r:first select from x where window=0D00:01;
    .qunit.assertTrue[(r`n_orders)=0;"nothing in the minute"];
    .qunit.assertTrue[null r`fill_ratio;"null, as .qf.exec.fill_ratio says no requests is"]};

/ ------------------------------------------------------ GROUPING

/ Six counted requests across book, product and venue, one without a venue.
grouped:{[]
    n:6;
    ([] source_time:as_of-0D00:00:01*10+til n; source:n#`a; request_id:`r1`r2`r3`r4`r5`r6;
        outcome:`filled`rejected`filled`filled`cancelled`rejected;
        book:`london`london`newyork`newyork`london`newyork; product:`spot`fwd`spot`spot`fwd`fwd;
        venue:`lp1`lp1`lp2``lp2`lp1; window:n#0D00:01; as_of:n#as_of; inside:n#1b)}

/ n_orders of the group whose column c is v.
n_of:{[t;c;v] first (select from t where (t c)=v)`n_orders}

test_it_groups_by_each_dimension_and_each_decomposes_the_total:{[t]
    g:grouped[];
    f:{[g;d] .qpipe.transform.order_quality.stats_by[d;g]}[g];
    b:f `book;
    .qunit.assertEquals[(n_of[b;`book;`london];n_of[b;`book;`newyork]);3 3;"book"];
    p:f `product;
    .qunit.assertEquals[(first exec n_filled from p where product=`spot;first exec n_filled from p where product=`fwd);3 0;"product: spot filled three, fwd none"];
    v:f `venue;
    .qunit.assertEquals[(n_of[v;`venue;`lp1];n_of[v;`venue;`lp2];n_of[v;`venue;`]);3 2 1;"venue: the missing one is a group of its own"];
    .qunit.assertEquals[sum each (b`n_orders;p`n_orders;v`n_orders);3#6;"each decomposes the six"];
    .qunit.assertEquals[count f `book`product;4;"two dimensions at once"]};

test_a_missing_venue_is_not_filled_in:{[t]
    v:.qpipe.transform.order_quality.stats_by[`venue;grouped[]];
    .qunit.assertEquals[asc v`venue;``lp1`lp2;"the request without a venue stays under the null venue"];
    .qunit.assertEquals[count select from v where null venue;1;"exactly one null-venue group"]};

test_grouping_by_something_the_contract_lacks_is_refused:{[t]
    .qunit.assertThrows[{.qpipe.transform.order_quality.stats_by[`sym;x]};grouped[];
        "*group by a subset of book, product, venue*";"a named refusal"];
    .qunit.assertThrows[{.qpipe.transform.order_quality.stats_by[`symbol$();x]};grouped[];
        "*group by a subset of*";"and no dimension at all"]};

/ ------------------------------------------------- THE CONSUMER IS CLEAN

/ The code lines of a file: comments are prose, and may name the history.
code_of:{[f] l:read0 hsym `$f; l where not (l like "/*") or 0=count each l}

test_the_consumer_names_no_provider_column_status_or_table:{[t]
    check:{[i]
        c:" " sv code_of i;
        .qunit.assertFalse[c like "*order_status*";i," reads a provider status column"];
        .qunit.assertFalse[c like "*order_id*";i," reads a provider id column"];
        .qunit.assertFalse[c like "*`orders*";i," names the orders table"]};
    check each ("src/etl/transforms/order_quality.q";"src/etl/streaming/order_quality.q");
    .qunit.assertTrue[1b;"both files read"]};

\d .
