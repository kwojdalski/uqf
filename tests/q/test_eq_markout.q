/ test_eq_markout.q - the live equity markout job and its dataset
/ (.qpipe.job.eq_markout, the eq_markout table, #1981).
/ .
/ The domain is tested on its own (test_eq_domain.q) and the twin on its own
/ (test_hdb_eq_markout_backfill.q); this file tests the LIFECYCLE the horizon kind gives
/ them and the claims the dataset exists to make:
/ .
/   live output equals backfill output   one transform serves both, and the same rows come out
/   a horizon across the close           is `unavailable, with no figure, however many books
/                                        the next session has already published
/   the shared executions table          the FX fills of the same tape never enter the queue
/ Every expected number is the hand-worked one from src/etl/transforms/eq_markout_outputs.q.


.qt.qpipe.job.eq_markout.t:2026.02.25D15:00:00.000000000

/ The job's check holds a batch whose configuration snapshot is not durable (#1999), so the suite
/ has a status directory of its own and the job's start has pinned what it scores under.
.qt.qpipe.job.eq_markout.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status-eq-markout-job"];
    system"mkdir -p build/test-status-eq-markout-job";
    .qt.support.reset_snapshots[];
    .qpipe.transform.eq_markout_outputs.pin[];
    }

.qt.qpipe.job.eq_markout.sent:()

.qt.qpipe.job.eq_markout.setUp_fresh:{[]
    `.qt.qpipe.job.eq_markout.sent set ();
    .qetl.job.stream.reset `eq_markout;
    .qetl.job.stream.wire[`eq_markout;{[n;tb;x] .qt.qpipe.job.eq_markout.sent,:enlist (n;tb;x); count x}[`eq_markout]];
    }

.qt.qpipe.job.eq_markout.tearDown_unwire:{[]
    (` sv `.qpipe.job,`eq_markout,`publish) set .qetl.job.stream.unwired `eq_markout;
    }

/ An execution as the plant carries it: stamped by the source at st on the clock clk, received at rv.
.qt.qpipe.job.eq_markout.ex:{[st;rv;venue;sym;sd;px;id;clk]
    ([] time:enlist rv; source_time:enlist st; sym:enlist sym; venue:enlist venue; side:enlist sd; size:enlist 100f;
        price:enlist px; fee:enlist 0f; fee_ccy:enlist `USD; fill_id:enlist id; book:enlist `; product:enlist `; clock:enlist clk)}

/ A book snapshot as the plant carries it.
.qt.qpipe.job.eq_markout.bk:{[st;sym;src;bid;ask;clk]
    ([] time:enlist st+0D00:00:00.1; sym:enlist sym; source:enlist src; market:enlist `equity; source_time:enlist st;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 100f; ask_prices:enlist enlist ask; ask_sizes:enlist enlist 100f; clock:enlist clk; bid:enlist bid; ask:enlist ask)}

/ The worked fill of eq_markout_outputs.q, as the plant carries it.
.qt.qpipe.job.eq_markout.buy:{[]
    update time:source_time+0D00:00:00.2, fee:0f, fee_ccy:`USD, book:`, product:` from .qpipe.transform.eq_markout_outputs.sample_executions[]}

/ The worked books, as the plant carries them, and one closing book per (symbol, source) after the furthest
/ horizon quoting what that series last quoted: the reference covers an instant only once a book at or after
/ it has arrived, and readiness waits for every source held (#2455).
.qt.qpipe.job.eq_markout.books:{[] update time:source_time+0D00:00:00.1, market:`equity from .qpipe.transform.eq_markout_outputs.sample_books[]}

.qt.qpipe.job.eq_markout.close_after:{[b;t]
    last_of:select by sym, source from b;
    b,update time:t+0D00:00:00.1, source_time:t from 0!last_of}

.qt.qpipe.job.eq_markout.covered:{[] .qt.qpipe.job.eq_markout.close_after[.qt.qpipe.job.eq_markout.books[];.qt.qpipe.job.eq_markout.t+0D00:01:10]}

.qt.qpipe.job.eq_markout.feed:{[tb;x] .qpipe.job.eq_markout.on_batch[tb;x]}
.qt.qpipe.job.eq_markout.tick:{[now] .qpipe.job.eq_markout.score_ready now}
.qt.qpipe.job.eq_markout.published:{[] raze {x 2} each .qt.qpipe.job.eq_markout.sent}

/ What the output-contract suite (test_job_output_contracts.q) drives the job with: one fill, the
/ books around it, and the timer once its horizons have passed.
.qt.qpipe.job.eq_markout.contract_driver:{[]
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.covered[]];
    .qt.qpipe.job.eq_markout.feed[`executions;.qt.qpipe.job.eq_markout.buy[]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:01:00]}

/ --- the declaration --------------------------------------------------------------

.qt.qpipe.job.eq_markout.test_the_worked_example_is_the_fill_these_tests_drive:{[tc]
    .qunit.assertEquals[.qpipe.transform.eq_markout_outputs.sample_executions[];select source_time, venue, sym, side, size, price, fill_id, clock from .qt.qpipe.job.eq_markout.buy[];
        "one example, hand-worked in eq_markout_outputs.q, is the fill every lifecycle test below scores"]};

.qt.qpipe.job.eq_markout.test_the_job_is_declared_over_the_canonical_tables_and_publishes_the_dataset:{[tc]
    d:.qetl.job.stream.horizons.def `eq_markout;
    .qunit.assertEquals[(d`events;d`reference;first d`outputs);(`executions;`market_data;`eq_markout);"the two canonical tables in, the dataset out"];
    .qunit.assertEquals[(d`event_time;d`reference_time;d`ready_on);(`source_time;`source_time;`both);"source_time on both clocks, wall and reference readiness"];
    .qunit.assertEquals[(d`identity;d`retain_by;d`by);(`venue`fill_id;`sym`source;enlist `sym);"identity, per-source retention, readiness keyed by symbol"];
    .qunit.assertEquals[(d`horizon;d`lookback;d`max_age);(0D00:01:00;0D;0D00:01:00);"the furthest horizon, no reach back, a minute of staleness"];
    .qunit.assertEquals[d`procname;`eq_markout1;"the process is named for the job"];
    .qunit.assertTrue[d[`expire_after]>d`horizon;"and a fill is given up on afterwards"]};

.qt.qpipe.job.eq_markout.test_the_job_and_its_twin_run_one_transform_not_a_copy:{[tc]
    .qunit.assertEquals[(.qetl.job.stream.horizons.def `eq_markout)`transform;`eq_markout_score;"the live job's"];
    f:.qpipe.source.hdb_eq_markouts.fixture[];
    live:.qetl.transform.apply[`eq_markout_score;f];
    .qunit.assertEquals[delete time from .qetl.transform.apply[`hdb_eq_markout_long;f];live;"the twin is the live transform plus the core's time"];
    .qunit.assertTrue[0<count live;"and it scored something"]};

.qt.qpipe.job.eq_markout.test_the_dataset_is_on_the_plant_in_the_contracts_shape:{[tc]
    .qunit.assertTrue[`eq_markout in .qetl.plant.names[];"eq_markout is on the plant"];
    .qunit.assertEquals[cols .qetl.plant.shape `eq_markout;(cols .qpipe.transform.markout_core.result),enlist `markout_bps;
        "the core's long result, then the equity measure"]};

.qt.qpipe.job.eq_markout.test_the_job_keeps_only_the_equity_venues_fills_and_books:{[tc]
    k:.qpipe.job.eq_markout.keep;
    .qunit.assertEquals[k ([] sym:`AAPL`AAPL`EURUSD; source:`XNAS`a`a);100b;"books of an equity venue"];
    e:.qt.qpipe.job.eq_markout.ex[.qt.qpipe.job.eq_markout.t;.qt.qpipe.job.eq_markout.t;`XNAS;`AAPL;1;271.5;`a1;`venue],
        .qt.qpipe.job.eq_markout.ex[.qt.qpipe.job.eq_markout.t;.qt.qpipe.job.eq_markout.t;`a;`EURUSD;1;1.1;`a2;`receipt],
        .qt.qpipe.job.eq_markout.ex[.qt.qpipe.job.eq_markout.t;.qt.qpipe.job.eq_markout.t;`XNAS;`AAPL;1;271.5;`a3;`],
        .qt.qpipe.job.eq_markout.ex[0Np;.qt.qpipe.job.eq_markout.t;`XNAS;`AAPL;1;271.5;`a4;`venue];
    .qunit.assertEquals[k e;1000b;"an XNAS fill with an identity, a time and a clock; not another venue's, not a null clock, not a null time"]};

/ --- readiness: the wall AND the reference ------------------------------------------

.qt.qpipe.job.eq_markout.test_a_fill_waits_for_the_wall_though_the_books_cover_it:{[tc]
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.covered[]];
    .qt.qpipe.job.eq_markout.feed[`executions;.qt.qpipe.job.eq_markout.buy[]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:00:30];
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.sent;0;"the books reach past +1min, but only 30s have elapsed"];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:01:00];
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.published[];6;"scored the instant the furthest horizon has elapsed: three horizons, two components"]};

.qt.qpipe.job.eq_markout.test_a_fill_waits_for_the_books_though_the_wall_has_elapsed:{[tc]
    edge:.qt.qpipe.job.eq_markout.t+0D00:00:40;
    .qt.qpipe.job.eq_markout.feed[`market_data;select from .qt.qpipe.job.eq_markout.covered[] where source_time<edge];
    .qt.qpipe.job.eq_markout.feed[`executions;.qt.qpipe.job.eq_markout.buy[]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:04];
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.sent;0;"four minutes on the wall clock, but no book reaches +1min"];
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.bk[.qt.qpipe.job.eq_markout.t+0D00:01:05;`AAPL;`XNAS;272f;272.25;`venue]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:04];
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.published[];6;"scored once the books have advanced through the horizon"]};

.qt.qpipe.job.eq_markout.test_maturity_is_the_source_time_not_the_plant_receipt_time:{[tc]
    / received an hour late: still due a minute after the venue printed it
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.covered[]];
    .qt.qpipe.job.eq_markout.feed[`executions;update time:time+0D01 from .qt.qpipe.job.eq_markout.buy[]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:01:00];
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.published[];6;"due at source_time + 1min, not at receipt + 1min"]};

.qt.qpipe.job.eq_markout.test_a_symbol_nobody_quotes_holds_the_fill_then_expires_it_with_the_reason:{[tc]
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.covered[]];
    .qt.qpipe.job.eq_markout.feed[`executions;update sym:`MSFT, fill_id:`s1 from .qt.qpipe.job.eq_markout.buy[]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:02];
    .qunit.assertEquals[(count .qt.qpipe.job.eq_markout.sent;count .qpipe.job.eq_markout.pending);0 1;"waiting: never scored with its symbol unquoted"];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:08];
    gone:.qpipe.job.eq_markout.expired;
    .qunit.assertEquals[(count gone;count .qpipe.job.eq_markout.pending;count .qt.qpipe.job.eq_markout.sent);1 0 0;"given up on after expire_after, nothing published"];
    .qunit.assertEquals[first gone`reason;`$"no reference for MSFT";"and the diagnostic names the symbol"]};

.qt.qpipe.job.eq_markout.test_an_fx_execution_never_enters_the_queue:{[tc]
    .qt.qpipe.job.eq_markout.feed[`executions;.qt.qpipe.job.eq_markout.ex[.qt.qpipe.job.eq_markout.t;.qt.qpipe.job.eq_markout.t;`a;`EURUSD;1;1.1;`x1;`receipt]];
    .qunit.assertEquals[count .qpipe.job.eq_markout.pending;0;"the executions table carries FX fills too; the equity domain would price their symbol, so the venue tells them apart"];
    .qt.qpipe.job.eq_markout.feed[`executions;.qt.qpipe.job.eq_markout.ex[.qt.qpipe.job.eq_markout.t;.qt.qpipe.job.eq_markout.t;`a;`AAPL;1;271.5;`x2;`venue]];
    .qunit.assertEquals[count .qpipe.job.eq_markout.pending;0;"and a fill of an equity-looking symbol on a venue that is not an equity one stays out"]};

/ --- the rows ---------------------------------------------------------------------------

.qt.qpipe.job.eq_markout.test_the_published_rows_are_the_hand_worked_ones:{[tc]
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.covered[]];
    .qt.qpipe.job.eq_markout.feed[`executions;.qt.qpipe.job.eq_markout.buy[]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:01:00];
    p:.qt.qpipe.job.eq_markout.published[];
    .qunit.assertEquals[cols p;cols .qpipe.transform.eq_markout_outputs.out;"the plant table without time, which the plant stamps"];
    .qunit.assertEquals[p;delete time from .qpipe.transform.eq_markout_outputs.sample_rows[];"the worked rows: 271.625 at 5s (+0.125), 271.875 at 30s (+0.375), 272.125 at 1min (+0.625)"]};

.qt.qpipe.job.eq_markout.test_a_receipt_clock_fill_is_refused_against_venue_books:{[tc]
    / the same fill, stamped by this stack on arrival: comparable with no venue's book
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.covered[]];
    .qt.qpipe.job.eq_markout.feed[`executions;update clock:`receipt from .qt.qpipe.job.eq_markout.buy[]];
    .qt.qpipe.job.eq_markout.tick[.qt.qpipe.job.eq_markout.t+0D00:01:00];
    p:.qt.qpipe.job.eq_markout.published[];
    .qunit.assertEquals[(count p;distinct p`status);(6;enlist `clock_mismatch);"every row says why it is unpriced"];
    .qunit.assertTrue[all null p`markout_bps;"and carries no bps from two clocks"]};

/ --- a horizon across the session close ----------------------------------------------------

/ A META sell of 10 at 643.25 at 20:59:50, ten seconds before the close, on the venue clock.
.qt.qpipe.job.eq_markout.close_fill:{[]
    update time:source_time+0D00:00:00.2, book:`, product:` from select source_time, sym, venue, side, size, price, fee, fee_ccy, fill_id, clock from
        ([] source_time:enlist 2026.02.25D20:59:50; sym:enlist `META; venue:enlist `XNAS; side:enlist -1; size:enlist 10f; price:enlist 643.25; fee:enlist 0f;
            fee_ccy:enlist `USD; fill_id:enlist `c1; clock:enlist `venue)}

.qt.qpipe.job.eq_markout.close_books:{[]
    b:.qpipe.source.hdb_eq_markouts.raw_market_data[];
    update time:source_time+0D00:00:00.1 from select source_time, sym, source, market, bid, ask, clock from b where sym=`META}

.qt.qpipe.job.eq_markout.test_a_horizon_across_the_close_is_unavailable_whatever_the_next_books_say:{[tc]
    / the 21:00:20 book (after the close) is published and would price +30s and +1min; the calendar rules them out
    .qt.qpipe.job.eq_markout.feed[`market_data;.qt.qpipe.job.eq_markout.close_after[.qt.qpipe.job.eq_markout.close_books[];2026.02.25D21:01:30]];
    .qt.qpipe.job.eq_markout.feed[`executions;.qt.qpipe.job.eq_markout.close_fill[]];
    .qt.qpipe.job.eq_markout.tick[2026.02.25D21:02:00];
    p:.qt.qpipe.job.eq_markout.published[];
    t:select from p where kind=`total;
    .qunit.assertEquals[t`horizon;0D00:00:05 0D00:00:30 0D00:01:00;"three horizons"];
    .qunit.assertEquals[t`status;`ok`unavailable`unavailable;"+5s is 20:59:55, inside the session; +30s and +1min are after the close"];
    .qunit.assertEquals[null t`move;011b;"and the pairs that cross the close have no move"];
    .qunit.assertEquals[null t`markout_bps;011b;"and so no bps - no overnight gap is scored as execution quality"];
    .qunit.assertEquals[first t`move;0.75;"the +5s sell: -(642.50 - 643.25), the 20:59:54 mid against the fill"];
    .qunit.assertEquals[first t`markout_bps;10000*0.75%643.25;"in bps of the price"];
    e:select from p where kind=`execution, horizon=0D00:00:05;
    .qunit.assertEquals[first e`move;0.25;"and the execution component: -(643.00 - 643.25), the 20:59:49 mid at the fill"]};

/ --- the calendar reaches any date (#2183) ---------------------------------------------
/ The declared table ends 2028.12.31; the rule that built it is applied on demand beyond it.

/ The worked example (a fill at 15:00 on 2026.02.25) moved to the instant at, status of its total rows.
.qt.qpipe.job.eq_markout.shifted_status:{[at]
    d:at-2026.02.25D15:00:00;
    e:update source_time:source_time+d from .qpipe.transform.eq_markout_outputs.sample_executions[];
    b:update source_time:source_time+d from .qpipe.transform.eq_markout_outputs.sample_books[];
    b:b,update source_time:source_time+0D00:01:10 from -1#b;
    exec status from .qpipe.transform.eq_markout_outputs.rows[e;b] where kind=`total}

/ A fill at 13:45 UTC is inside the session in daylight time (opens 13:30 UTC) and before it in
/ standard time (opens 14:30 UTC); one at 20:30 UTC is the reverse (closes 20:00 / 21:00 UTC).
.qt.qpipe.job.eq_markout.test_fills_in_2029_and_2035_are_in_session_at_the_right_utc_times_across_both_switches:{[tc]
    f:.qt.qpipe.job.eq_markout.shifted_status;
    ok:3#`ok;
    un:3#`unavailable;
    / 2029: daylight time begins Sunday 03-11 and ends Sunday 11-04
    .qunit.assertEquals[f 2029.03.09D13:45:00;un;"2029-03-09 is standard time: 13:45 UTC is before the 14:30 open"];
    .qunit.assertEquals[f 2029.03.09D20:30:00;ok;"2029-03-09 closes 21:00 UTC"];
    .qunit.assertEquals[f 2029.03.12D13:45:00;ok;"2029-03-12 is daylight time: opens 13:30 UTC"];
    .qunit.assertEquals[f 2029.03.12D20:30:00;un;"2029-03-12 closes 20:00 UTC"];
    .qunit.assertEquals[f 2029.11.02D20:30:00;un;"2029-11-02 is still daylight time: closes 20:00 UTC"];
    .qunit.assertEquals[f 2029.11.05D13:45:00;un;"2029-11-05 is standard time again: opens 14:30 UTC"];
    .qunit.assertEquals[f 2029.11.05D20:30:00;ok;"2029-11-05 closes 21:00 UTC"];
    / 2035: second Sunday of March is 03-11, first Sunday of November is 11-04
    .qunit.assertEquals[f 2035.03.09D13:45:00;un;"2035-03-09 standard time"];
    .qunit.assertEquals[f 2035.03.12D13:45:00;ok;"2035-03-12 daylight time"];
    .qunit.assertEquals[f 2035.11.02D20:30:00;un;"2035-11-02 daylight time"];
    .qunit.assertEquals[f 2035.11.05D20:30:00;ok;"2035-11-05 standard time"];
    .qunit.assertEquals[f 2035.03.10D15:00:00;un;"a Saturday has no session"]};

.qt.qpipe.job.eq_markout.test_the_extended_calendar_is_the_declared_rule_and_2026_to_2028_is_unchanged:{[tc]
    s:.qpipe.transform.eq_markout_outputs.regular_sessions[2026.01.01;2028.12.31];
    .qunit.assertEquals[count s;753;"every trading day of 2026-2028"];
    hol:2026.01.01 2026.01.19 2026.02.16 2026.04.03 2026.05.25 2026.06.19 2026.07.03 2026.09.07 2026.11.26 2026.12.25,2027.01.01 2027.01.18 2027.02.15 2027.03.26 2027.05.31 2027.06.18 2027.07.05 2027.09.06 2027.11.25 2027.12.24,2028.01.17 2028.02.21 2028.04.14 2028.05.29 2028.06.19 2028.07.04 2028.09.04 2028.11.23 2028.12.25;
    wk:{x where 1<(`long$x) mod 7} 2026.01.01+til 1096;
    .qunit.assertEquals[`date$s`open_time;wk except hol;"every weekday but the 29 NYSE holidays of 2026-2028"];
    .qunit.assertEquals[`date$exec open_time from s where (close_time-open_time)<0D06:30:00;2026.11.27 2026.12.24 2027.11.26 2028.07.03 2028.11.24;"the early closes (2027-12-24 is the observed Christmas, 2028-12-24 a Sunday)"];
    .qunit.assertEquals[.qpipe.transform.eq_markout_outputs.job_spec`sessions;s;"the spec's table is unchanged, so is its snapshot id"];
    / inside the declared table the extended rule is the domain's own
    sp:.qpipe.transform.markout_core.validate[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec];
    fr:([] sym:4#`AAPL; t0:2026.03.06D14:00:00 2026.03.09D13:45:00 2028.12.29D20:30:00 2028.12.29D21:30:00; t1:(2026.03.06D14:00:05 2026.03.09D13:45:05 2028.12.29D20:30:05 2028.12.29D21:30:05));
    .qunit.assertEquals[.qpipe.transform.eq_markout_outputs.available[sp;fr];.qpipe.transform.eq_domain.available[sp;fr];"identical to the domain's rule for 2026-2028"];
    / and a generated 2035 day is the rule's output
    .qunit.assertEquals[exec open_time from .qpipe.transform.eq_markout_outputs.regular_sessions[2035.03.12;2035.03.12];enlist 2035.03.12D13:30:00.000000000;"daylight open"]};

/ --- US holidays and early closes (#2572) ------------------------------------------------
/ The rule knows the NYSE calendar: no session on a holiday, a 13:00 New York close on an early-close day.

/ The session instants of one date, or an empty table.
.qt.qpipe.job.eq_markout.day:{[d] .qpipe.transform.eq_markout_outputs.regular_sessions[d;d]}

.qt.qpipe.job.eq_markout.test_the_day_after_thanksgiving_closes_at_1300_new_york:{[tc]
    / 2026-11-27: standard time, so 13:00 New York is 18:00 UTC
    .qunit.assertEquals[exec close_time from .qt.qpipe.job.eq_markout.day 2026.11.27;enlist 2026.11.27D18:00:00.000000000;"early close at 13:00 ET"];
    .qunit.assertEquals[exec open_time from .qt.qpipe.job.eq_markout.day 2026.11.27;enlist 2026.11.27D14:30:00.000000000;"the open is the regular one"];
    f:.qt.qpipe.job.eq_markout.shifted_status;
    / the issue's fill: 12:59:30 ET = 17:59:30 UTC. +5s is in session, +30s lands on the close, +1min is after it
    .qunit.assertEquals[f 2026.11.27D17:59:30;`ok`unavailable`unavailable;"a horizon across the early close is unavailable"];
    / after hours: 14:00 ET is after the 13:00 close, so the fill itself is outside every session
    .qunit.assertEquals[f 2026.11.27D19:00:00;3#`unavailable;"an after-hours fill is not in session"];
    / the ordinary Friday a week earlier closes at 16:00 ET
    .qunit.assertEquals[f 2026.11.20D19:00:00;3#`ok;"the same instant a week earlier is in session"]};

.qt.qpipe.job.eq_markout.test_a_holiday_has_no_session_so_a_fill_on_it_is_unavailable:{[tc]
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.day 2026.11.26;0;"Thanksgiving"];
    .qunit.assertEquals[.qt.qpipe.job.eq_markout.shifted_status[2026.11.26D15:00:00];3#`unavailable;"a fill on Thanksgiving is unavailable"];
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.day 2026.07.03;0;"Independence Day 2026 is a Saturday, observed on Friday"];
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.day 2026.04.03;0;"Good Friday 2026 (Easter is 04-05)"]};

.qt.qpipe.job.eq_markout.test_the_holiday_count_of_a_year_is_the_nyse_one:{[tc]
    n:{[y] count .qpipe.transform.eq_markout_outputs.regular_sessions[y;y+364]};
    .qunit.assertEquals[n 2026.01.01;251;"2026: 261 weekdays less 10 holidays"];
    .qunit.assertEquals[n 2027.01.01;251;"2027: 261 weekdays less 10 holidays"];
    / 2027: Independence Day Sunday -> Monday, Christmas Saturday -> Friday, Juneteenth Saturday -> Friday
    .qunit.assertEquals[(count .qt.qpipe.job.eq_markout.day 2027.07.05;count .qt.qpipe.job.eq_markout.day 2027.12.24;count .qt.qpipe.job.eq_markout.day 2027.06.18);0 0 0;"observed on the nearest weekday"];
    / New Year's Day on a Saturday (2028-01-01) is not observed on the Friday before
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.day 2027.12.31;1;"2027-12-31 trades"];
    / Juneteenth is a holiday from 2022 only
    .qunit.assertEquals[(count .qt.qpipe.job.eq_markout.day 2021.06.18;count .qt.qpipe.job.eq_markout.day 2022.06.20);1 0;"Juneteenth from 2022"];
    / Good Friday across Easters, early and late
    gf:2008.03.21 2011.04.22 2013.03.29 2024.03.29 2038.04.23;
    .qunit.assertEquals[{count .qt.qpipe.job.eq_markout.day x} each gf;5#0;"Good Friday"];
    / the rule is not a 2026 table: a 2035 Thanksgiving (11-22) is a holiday
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.day 2035.11.22;0;"2035 Thanksgiving"]};

.qt.qpipe.job.eq_markout.test_the_three_early_closes_and_the_ad_hoc_closures:{[tc]
    early:{[d] first exec close_time-`timestamp$d from .qt.qpipe.job.eq_markout.day d};
    .qunit.assertEquals[early 2025.07.03;0D17:00:00;"3 July 2025 closes 13:00 EDT = 17:00 UTC"];
    .qunit.assertEquals[early 2025.12.24;0D18:00:00;"Christmas Eve 2025"];
    .qunit.assertEquals[early 2025.11.28;0D18:00:00;"day after Thanksgiving 2025"];
    / Christmas Eve on a Friday (2027 is a Friday-observed Christmas) and on a weekend have no early close
    .qunit.assertEquals[count .qt.qpipe.job.eq_markout.day 2022.12.24;0;"a Saturday"];
    / ad hoc closures no rule produces
    .qunit.assertEquals[(count .qt.qpipe.job.eq_markout.day 2025.01.09;count .qt.qpipe.job.eq_markout.day 2018.12.05;count .qt.qpipe.job.eq_markout.day 2012.10.29);0 0 0;"national days of mourning and Sandy"]};

.qt.qpipe.job.eq_markout.test_a_fill_past_the_calendar_horizon_is_refused_not_marked_unavailable:{[tc]
    sp:.qpipe.transform.markout_core.validate[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec];
    fr:([] sym:enlist `AAPL; t0:enlist 2100.01.04D15:00:00; t1:enlist 2100.01.04D15:00:05);
    f:{[a] .qpipe.transform.eq_markout_outputs.available[a 0;a 1]};
    / like is not implemented on KDB-X for "prefix*mid*": one anchor each
    .qunit.assertThrows[f;(sp;fr);"eq_markout_outputs: a fill at*";"named refusal"];
    .qunit.assertThrows[f;(sp;fr);"*calendar horizon 2099.12.31*";"naming the horizon"]};

/ #2419: the rule reaches backwards too, so a backfilled fill before calendar_from is scored.
.qt.qpipe.job.eq_markout.test_fills_before_the_declared_calendar_are_in_session:{[tc]
    sp:.qpipe.transform.markout_core.validate[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec];
    / the same weekday 15:00 UTC (mid-session) in 2025, 2026 and 2035
    fr:([] sym:3#`AAPL; t0:2025.06.16D15:00:00 2026.06.15D15:00:00 2035.06.18D15:00:00; t1:2025.06.16D15:00:05 2026.06.15D15:00:05 2035.06.18D15:00:05);
    .qunit.assertEquals[.qpipe.transform.eq_markout_outputs.available[sp;fr];111b;"2025 is in session as 2026 and 2035 are"];
    f:.qt.qpipe.job.eq_markout.shifted_status;
    .qunit.assertEquals[f 2025.03.07D13:45:00;3#`unavailable;"2025-03-07 is standard time: 13:45 UTC is before the 14:30 open"];
    .qunit.assertEquals[f 2025.03.10D13:45:00;3#`ok;"2025-03-10 is daylight time: opens 13:30 UTC"];
    e:([] source_time:enlist 2025.06.16D15:00:00; venue:enlist `XNAS; sym:enlist `AAPL; side:enlist 1; size:enlist 100f; price:enlist 271.5; fill_id:enlist `e1; clock:enlist `venue);
    m:([] source_time:2025.06.16D15:00:00 2025.06.16D15:00:03 2025.06.16D15:00:20 2025.06.16D15:00:50; sym:4#`AAPL; source:4#`XNAS; bid:271.25 271.5 271.75 272f; ask:271.5 271.75 272 272.25f; clock:4#`venue);
    r:.qpipe.transform.eq_markout_outputs.live[e;m];
    .qunit.assertEquals[distinct r`status;enlist `ok;"every row of the 2025 fill is scored"];
    .qunit.assertEquals[exec move from r where kind=`total, horizon=0D00:00:05;enlist 0.125;"+5s: 271.625 against the 271.5 fill"]};

.qt.qpipe.job.eq_markout.test_a_fill_before_the_calendar_floor_is_refused_not_marked_unavailable:{[tc]
    sp:.qpipe.transform.markout_core.validate[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec];
    fr:([] sym:enlist `AAPL; t0:enlist 2006.06.15D15:00:00; t1:enlist 2006.06.15D15:00:05);
    f:{[a] .qpipe.transform.eq_markout_outputs.available[a 0;a 1]};
    .qunit.assertThrows[f;(sp;fr);"eq_markout_outputs: a fill at*";"named refusal"];
    .qunit.assertThrows[f;(sp;fr);"*calendar floor 2007.01.01*";"naming the floor"]};

/ --- a live row is the row a refill writes ------------------------------------------------

.qt.qpipe.job.eq_markout.key_order:{[t] `venue`fill_id`horizon`kind`step xasc t}

.qt.qpipe.job.eq_markout.test_live_output_equals_backfill_output:{[tc]
    f:.qpipe.source.hdb_eq_markouts.fixture[];
    books:update time:source_time+0D00:00:00.1, market:`equity from f`market_data;
    / the reference covers an instant only once a later row has arrived: one closing book per (symbol, source)
    all_books:.qt.qpipe.job.eq_markout.close_after[books;2026.02.25D21:05:00];
    .qt.qpipe.job.eq_markout.feed[`market_data;all_books];
    .qt.qpipe.job.eq_markout.feed[`executions;update time:source_time+0D00:00:00.2, fee:0f, fee_ccy:`USD, book:`, product:` from f`executions];
    .qt.qpipe.job.eq_markout.tick[2026.02.25D21:10:00];
    live:.qt.qpipe.job.eq_markout.key_order[.qt.qpipe.job.eq_markout.published[]];
    twin:.qt.qpipe.job.eq_markout.key_order[delete time from .qetl.transform.apply[`hdb_eq_markout_long;f]];
    .qunit.assertTrue[0<count live;"the live job scored"];
    .qunit.assertEquals[live;twin;"identical rows: the same calculation, the same inputs"];
    .qunit.assertEquals[asc exec distinct fill_id from live;`e1`e2`e3;"the EURUSD fill is excluded by both"];
    .qunit.assertTrue[any (live`status)=`unavailable;"and the rows across the close are among the ones that agree"]};
