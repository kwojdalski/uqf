/ test_order_quality.q - the rolling order-outcome job (#1224): fill and reject
/ ratios per book over the last minute, five minutes and hour.
/ .
/ The kind's rules (the window boundary, lateness, eviction, restart) are
/ test_rolling.q's; these are the statistic's: the ratios on a worked example,
/ their agreement with .qexec.hit_ratio_by and .qexec.reject_ratio_by, and the
/ identity of a book that is quiet in a short window.

\d .order_qualitytest

t0:2026.10.09D10:00:00

/ The clock the job reads, which the tests move.
clock:t0

sent:()

/ An order received at tm, in the orders shape.
order:{[tm;bk;status;sz] ([] time:enlist tm; order_id:enlist 1; sym:enlist `EURUSD; book:enlist bk;
    product:enlist `spot; side:enlist 1; size:enlist sz; price:enlist 1.1; order_status:enlist status)}

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

feed:{[tm;bk;status;sz] `.order_qualitytest.clock set tm; .qpipe.job.order_quality.on_batch[`orders;order[tm;bk;status;sz]]}

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

test_it_is_declared_over_orders_by_book_at_three_windows:{[t]
    d:.qetl.job.stream.rolling.def `order_quality;
    .qunit.assertEquals[(d`windows;d`by;d`event_time);(0D00:01 0D00:05 0D01;enlist `book;`time);"the declaration"];
    .qunit.assertEquals[(.qetl.job.stream.def `order_quality)`subscribe_to;enlist `orders;"it reads orders"];
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
    hit:first exec hit_ratio from .qexec.hit_ratio_by[reqs;as_of-0D00:00:59;as_of;0Nn;enlist `book;`count];
    rej:first exec reject_ratio from .qexec.reject_ratio_by[reqs;as_of-0D00:00:59;as_of;0Nn;enlist `book;`count];
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

test_an_order_is_never_late_on_the_plants_own_stamp:{[t]
    job[];
    / received long after its time: the time IS the receipt, so it cannot be late
    `.order_qualitytest.clock set t0+0D01;
    .qpipe.job.order_quality.on_batch[`orders;order[t0;`london;`filled;1e6]];
    .qunit.assertEquals[count .qpipe.job.order_quality.dropped;0;"not dropped"];
    release[]};

test_a_book_with_no_order_in_the_hour_is_not_published:{[t]
    job[];
    as_of:t0+0D02:00:00;
    feed[t0;`gone;`filled;1e6];
    feed[as_of-0D00:00:10;`london;`filled;1e6];
    .qpipe.job.order_quality.evaluate as_of;
    .qunit.assertEquals[distinct published[]`book;enlist `london;"two hours quiet: its rows are evicted and it is no longer a key"];
    release[]};

\d .
