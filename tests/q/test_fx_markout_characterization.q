/ test_fx_markout_characterization.q - what the FX markout calculation does TODAY, pinned
/ before it is refactored (#1924, part of #1923).
/ .
/ These are characterization tests: they assert the behaviour of
/ .qpipe.transform.fx_markout.calc and .qpipe.transform.fx_markout_outputs as it is,
/ oddities included, not as it ought to be. A refactor that keeps every test here green
/ has kept the behaviour; one that turns a test red has changed it and must say so. The
/ suite goes through the public entry points only (calc, score_with, the layouts, the
/ declared transforms, the two jobs and the bounded worker), so a refactored module
/ that keeps those names reruns the whole suite unchanged (#1933).
/ .
/ The hand-worked numbers of test_fx_markout_calc.q and fx_markout_outputs.q are the
/ reference where a case can use them; the cases they do not cover are worked by hand
/ in the test, never read back from the function. Where the behaviour is odd rather
/ than right the test says so with "ODDITY", so the next reader does not "fix" it by
/ accident.


.qt.qpipe.transform.fx_markout.characterization.t:2026.09.17D10:00:00.000000000

/ calc under the default spec, with some of it changed
.qt.qpipe.transform.fx_markout.characterization.spec_of:{[changes] .qpipe.transform.fx_markout.default_spec,changes}

/ the default spec with these horizons
.qt.qpipe.transform.fx_markout.characterization.with_horizons:{[hs] .qt.qpipe.transform.fx_markout.characterization.spec_of[(enlist `horizons)!enlist hs]}

/ one execution of venue a
.qt.qpipe.transform.fx_markout.characterization.fill:{[tm;sym;side;px;id]
    ([] source_time:enlist tm; venue:enlist `a; sym:enlist sym; side:enlist side; size:enlist 1e6; price:enlist px; fill_id:enlist id)}

/ one source's book for one pair at one instant: a single level each side
.qt.qpipe.transform.fx_markout.characterization.bk:{[tm;sym;src;bid;ask]
    ([] source_time:enlist tm; sym:enlist sym; source:enlist src;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1e6;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6)}

/ equal where both are null, within a tolerance elsewhere
.qt.qpipe.transform.fx_markout.characterization.near:{[a;b] (all (null a)=null b) and all (null a) or 1e-9>=abs a-b}

/ the pips of one component of a result at one horizon
.qt.qpipe.transform.fx_markout.characterization.pips_of:{[res;hz;knd;stp]
    first exec pips from res where horizon=hz, kind=knd, step=stp}

/ ------------------------------------------------------------------ live and bounded

.qt.qpipe.transform.fx_markout.characterization.sent:()

/ Both live jobs, state emptied and publish pointing at a recorder.
.qt.qpipe.transform.fx_markout.characterization.setUp_fresh:{[]
    `.qt.qpipe.transform.fx_markout.characterization.sent set ();
    {[j] .qetl.job.stream.reset j; .qetl.job.stream.wire[j;{[n;tb;x] .qt.qpipe.transform.fx_markout.characterization.sent,:enlist (n;tb;x); count x}[j]]} each `fx_markout`fx_markout_wide;
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    `fx_markout_wide set 0#.qetl.plant.shape `fx_markout_wide;
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_fx_markouts_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_fx_markouts_backfill;
    }

.qt.qpipe.transform.fx_markout.characterization.tearDown_unwire:{[]
    {[j] (` sv `.qpipe.job,j,`publish) set .qetl.job.stream.unwired j} each `fx_markout`fx_markout_wide;
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    }

.qt.qpipe.transform.fx_markout.characterization.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ What a job published, in order.
.qt.qpipe.transform.fx_markout.characterization.published:{[nm]
    raze {[x] x 2} each .qt.qpipe.transform.fx_markout.characterization.sent where nm=.qt.qpipe.transform.fx_markout.characterization.sent[;0]}

/ The hdb_fx_markouts fixture, plus one closing book per (pair, source) after the furthest horizon: the
/ live reference covers an instant only once a later book has arrived. Both live jobs are fed it
/ and ticked past every horizon; returns the fixture, closing books included.
.qt.qpipe.transform.fx_markout.characterization.drive_live:{[]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    md:f`market_data;
    last_of:0!select by sym, source from md;
    closing:update source_time:2026.09.17D11:00:00 from last_of;
    f[`market_data]:md,closing;
    books:update time:source_time+0D00:00:00.1, market:`fx from f`market_data;
    exe:update time:source_time+0D00:00:00.2, fee:0f, fee_ccy:`, book:`, product:` from f`executions;
    {[books;exe;nm]
        ((get ` sv `.qpipe.job,nm,`on_batch))[`market_data;books];
        ((get ` sv `.qpipe.job,nm,`on_batch))[`executions;exe];
        ((get ` sv `.qpipe.job,nm,`score_ready))[2026.09.17D11:00:30]}[books;exe] each `fx_markout`fx_markout_wide;
    f}

.qt.qpipe.transform.fx_markout.characterization.test_live_and_bounded_long_output_are_equal:{[tc]
    f:.qt.qpipe.transform.fx_markout.characterization.drive_live[];
    live:.qt.qpipe.transform.fx_markout.characterization.published[`fx_markout];
    twin:.qetl.transform.apply[`hdb_fx_markouts_long;f];
    direct:.qpipe.transform.fx_markout_outputs.long .qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout_outputs.job_spec;f`executions;f`market_data];
    .qunit.assertEquals[count live;36;"the live job scored x1 (4 horizons x 4 components) and y1 (4 x 5); the crypto fill is excluded"];
    .qunit.assertEquals[delete time from twin;live;"the bounded twin gives the live rows, bar the time it stamps"];
    .qunit.assertEquals[direct;live;"and the bare calculation under the job spec gives them too"]};

.qt.qpipe.transform.fx_markout.characterization.test_live_and_bounded_wide_output_are_equal:{[tc]
    f:.qt.qpipe.transform.fx_markout.characterization.drive_live[];
    live:.qt.qpipe.transform.fx_markout.characterization.published[`fx_markout_wide];
    twin:.qetl.transform.apply[`hdb_fx_markouts_wide;f];
    direct:.qpipe.transform.fx_markout_outputs.wide .qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout_outputs.job_spec;f`executions;f`market_data];
    .qunit.assertEquals[count live;8;"one row per (execution, horizon): two executions, four horizons"];
    .qunit.assertEquals[delete time from twin;live;"the bounded twin gives the live rows, bar the time it stamps"];
    .qunit.assertEquals[direct;live;"and the bare calculation under the job spec gives them too"]};

.qt.qpipe.transform.fx_markout.characterization.test_the_twin_stamps_time_as_source_time_plus_the_furthest_horizon:{[tc]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    r:.qetl.transform.apply[`hdb_fx_markouts_long;f];
    .qunit.assertEquals[r`time;(r`source_time)+0D00:00:10;"the earliest a live row could have been stamped"]};

/ ------------------------------------------------------------------ idempotent rerun

.qt.qpipe.transform.fx_markout.characterization.test_calc_is_a_pure_function_of_its_inputs:{[tc]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    a:.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout_outputs.job_spec;f`executions;f`market_data];
    b:.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout_outputs.job_spec;f`executions;f`market_data];
    .qunit.assertEquals[a;b;"the same call twice gives the same rows"];
    .qunit.assertEquals[a;.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout_outputs.job_spec;f`executions;reverse f`market_data];
        "and the order the books arrive in does not matter"]};

.qt.qpipe.transform.fx_markout.characterization.test_writing_a_window_twice_under_replace_leaves_one_copy:{[tc]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    rows:.qetl.transform.apply[`hdb_fx_markouts_long;f];
    d:.qetl.job.bounded.def `hdb_fx_markouts_backfill;
    o:`on_conflict`row_key`time_column`range_from`range_to!(d`on_conflict;d`target_key;d`window_column;2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000);
    `fx_markout set 0#rows;
    .qetl.io.write_keyed[.qetl.io.memory;`fx_markout;rows;o];
    once:get `fx_markout;
    .qetl.io.write_keyed[.qetl.io.memory;`fx_markout;rows;o];
    .qunit.assertEquals[(count once;count get `fx_markout);(36;36);"36 rows after the first write and after the second"];
    .qunit.assertEquals[get `fx_markout;once;"the second write changes nothing"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_worker_run_twice_over_one_window_and_version_leaves_one_copy:{[tc]
    day:2026.09.17D00:00:00.000000000 2026.09.18D00:00:00.000000000;
    sp:`source_version`range_from`range_to!(`v1;day 0;day 1);
    .qpipe.job.hdb_fx_markouts_backfill.init sp;
    first_run:.qpipe.job.hdb_fx_markouts_backfill.run[];
    once:get `fx_markout;
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    .qpipe.job.hdb_fx_markouts_backfill.init sp;
    second_run:.qpipe.job.hdb_fx_markouts_backfill.run[];
    .qunit.assertEquals[(first_run`state;second_run`state);`completed`idle;"the first run completes; the second finds every window covered at this version and does nothing"];
    .qunit.assertEquals[(count once;count get `fx_markout);(36;36);"36 rows, still, after the rerun"];
    .qunit.assertEquals[get `fx_markout;once;"and the same rows"]};

/ ------------------------------------------------------------------ replace across midnight

/ One HDB partition's table, read back with its symbols as symbols.
.qt.qpipe.transform.fx_markout.characterization.part:{[root;d]
    dir:hsym `$(string .Q.par[root;d;`fx_markout]),"/";
    if[()~key dir; :([] venue:`symbol$(); fill_id:`symbol$(); horizon:`timespan$(); kind:`symbol$(); step:`long$())];
    @[`.;`sym;:;get ` sv root,`sym];
    p:get dir;
    select venue:`$string venue, fill_id:`$string fill_id, horizon, kind:`$string kind, step from p}

/ The bounded twin's rows for an execution at 23:59:45, and the live job's copy stamped after midnight.
.qt.qpipe.transform.fx_markout.characterization.midnight_rows:{[]
    tm:2026.09.17D23:59:45.000000000;
    exe:.qt.qpipe.transform.fx_markout.characterization.fill[tm;`EURUSD;1;1.1001;`m1];
    md:raze (.qt.qpipe.transform.fx_markout.characterization.bk[tm-0D00:00:01;`EURUSD;`a;1.0999;1.1001];
        .qt.qpipe.transform.fx_markout.characterization.bk[tm+0D00:00:00.5;`EURUSD;`a;1.1004;1.1006];
        .qt.qpipe.transform.fx_markout.characterization.bk[tm+0D00:00:04;`EURUSD;`a;1.1004;1.1006];
        .qt.qpipe.transform.fx_markout.characterization.bk[tm+0D00:00:08;`EURUSD;`a;1.1008;1.1010]);
    twin:.qetl.transform.apply[`hdb_fx_markouts_long;`executions`market_data!(exe;md)];
    live:update time:2026.09.18D00:00:03.000000000 from delete time from twin;
    (twin;live)}

/ An execution in the last seconds of a day: the live job stamps its rows when it
/ receives them, after the furthest horizon, which is the next day; the bounded twin stamps
/ them at source_time plus the furthest horizon, which is still this day. One key,
/ two date partitions, and only `replace clears across that boundary.
.qt.qpipe.transform.fx_markout.characterization.test_replace_across_midnight_leaves_no_duplicates:{[tc]
    rows:.qt.qpipe.transform.fx_markout.characterization.midnight_rows[];
    twin:rows 0;
    live:rows 1;
    .qunit.assertEquals[(distinct `date$twin`time;distinct `date$live`time);(enlist 2026.09.17;enlist 2026.09.18);"the premise: the twin's rows and the live rows fall in different date partitions"];
    d:.qetl.job.bounded.def `hdb_fx_markouts_backfill;
    .qunit.assertEquals[(d`on_conflict;d`window_column);(`replace;`source_time);"the worker declares replace on the execution's time"];
    o:`on_conflict`row_key`time_column`range_from`range_to!(`replace;d`target_key;d`window_column;2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000);
    root:`$":",first system"mktemp -d";
    m:.qetl.io.hdb[root;`time];
    .qetl.io.write_keyed[m;`fx_markout;live;o];
    / The live copy's place is checked on a root of its own: re-reading a partition in the process that
    / is about to replace it by rename is not something every interpreter supports.
    probe:`$":",first system"mktemp -d";
    .qetl.io.write_keyed[.qetl.io.hdb[probe;`time];`fx_markout;live;o];
    .qunit.assertEquals[(count .qt.qpipe.transform.fx_markout.characterization.part[probe;2026.09.18];count .qt.qpipe.transform.fx_markout.characterization.part[probe;2026.09.17]);16 0;"the live copy sits in the next day"];
    system "rm -rf ",1_string probe;
    .qetl.io.write_keyed[m;`fx_markout;live;o];
    .qetl.io.write_keyed[m;`fx_markout;twin;o];
    p17:.qt.qpipe.transform.fx_markout.characterization.part[root;2026.09.17];
    p18:.qt.qpipe.transform.fx_markout.characterization.part[root;2026.09.18];
    all_rows:p17,p18;
    .qunit.assertEquals[(count p17;count p18);(16;0);"the live copy in the next day is cleared and the twin's rows are the only ones"];
    .qunit.assertEquals[(count all_rows;count distinct `venue`fill_id`horizon`kind`step#all_rows);(16;16);"one row per key across the partitions"];
    system "rm -rf ",1_string root}

/ ------------------------------------------------------------------ empty window

.qt.qpipe.transform.fx_markout.characterization.test_an_empty_window_gives_no_rows:{[tc]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    none:0#f`executions;
    .qunit.assertEquals[.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;none;f`market_data];0#.qpipe.transform.fx_markout.result;"no executions, books or not: no rows, in the result's shape"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;none;0#f`market_data];0#.qpipe.transform.fx_markout.result;"and without books"];
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`long;none;f`market_data];0#.qetl.plant.published `fx_markout;"the long layout is empty in the plant's columns"];
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`wide;none;f`market_data];0#.qetl.plant.published `fx_markout_wide;"and so is the wide"];
    .qunit.assertEquals[.qetl.transform.apply[`hdb_fx_markouts_long;`executions`market_data!(none;f`market_data)];0#.qetl.plant.shape `fx_markout;"and the bounded twin's"]};

.qt.qpipe.transform.fx_markout.characterization.test_executions_that_cannot_be_scored_give_no_rows_either:{[tc]
    md:.qt.qpipe.transform.fx_markout.characterization.bk[.qt.qpipe.transform.fx_markout.characterization.t;`EURUSD;`a;1.0999;1.1001];
    crypto:.qt.qpipe.transform.fx_markout.characterization.fill[.qt.qpipe.transform.fx_markout.characterization.t;`$"BTC-USDT";1;62000f;`c1];
    bad_side:.qt.qpipe.transform.fx_markout.characterization.fill[.qt.qpipe.transform.fx_markout.characterization.t;`EURUSD;0;1.1001;`c2];
    bad_price:.qt.qpipe.transform.fx_markout.characterization.fill[.qt.qpipe.transform.fx_markout.characterization.t;`EURUSD;1;0f;`c3];
    no_id:.qt.qpipe.transform.fx_markout.characterization.fill[.qt.qpipe.transform.fx_markout.characterization.t;`EURUSD;1;1.1001;`];
    all_bad:crypto,bad_side,bad_price,no_id;
    .qunit.assertEquals[.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;all_bad;md];0#.qpipe.transform.fx_markout.result;"each is excluded, so nothing is scored"];
    .qunit.assertEquals[(.qpipe.transform.fx_markout.unscoreable[.qpipe.transform.fx_markout.default_spec;all_bad])`reason;`no_route`bad_side`bad_price`no_identity;"and unscoreable names why, in order"]};

/ ------------------------------------------------------------------ a missing reference

/ A USDPLN sell at 3.99. EURUSD and EURPLN each quoted once, at T-1s, bid = ask.
.qt.qpipe.transform.fx_markout.characterization.cross_books:{[]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    raze (.qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:01;`EURUSD;`a;1.25;1.25];
        .qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:01;`EURPLN;`a;5f;5f])}

.qt.qpipe.transform.fx_markout.characterization.test_a_horizon_beyond_the_books_age_is_unavailable_and_the_execution_residual_survives:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    r:.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;.qt.qpipe.transform.fx_markout.characterization.fill[t;`USDPLN;-1;3.99;`y1];.qt.qpipe.transform.fx_markout.characterization.cross_books[]];
    / +1s: the T-1s books are 2s old, within the 5s max_age; nothing moved. +10s: 11s old - no reference.
    one:select from r where horizon=0D00:00:01;
    ten:select from r where horizon=0D00:00:10;
    .qunit.assertEquals[one`status;`ok`ok`ok`ok`reconciled;"at +1s every component is priced"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[one`pips;-100 -100 0 0 0f];"and nothing moved: the markout is the execution residual, 10000*(4.0-3.99)*-1"];
    .qunit.assertEquals[ten`status;`unavailable`ok`unavailable`unavailable`unavailable;"at +10s the execution residual stays ok and the rest (total, two legs, residual) is unavailable"];
    .qunit.assertEquals[ten`kind;`total`execution`leg`leg`residual;"in the result's order"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[ten`pips;0n -100 0n 0n 0n];"the execution residual survives a missing horizon; the markout, legs and residual are null"];
    .qunit.assertEquals[(select kind, price_t1 from ten where kind=`total)`price_t1;enlist 0n;"the reference at the horizon is null"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_pair_with_no_book_for_a_leg_is_unavailable_in_every_row:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    md:select from .qt.qpipe.transform.fx_markout.characterization.cross_books[] where sym=`EURUSD;
    r:.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;.qt.qpipe.transform.fx_markout.characterization.fill[t;`USDPLN;-1;3.99;`y1];md];
    .qunit.assertEquals[count r;10;"the rows are kept so the gap shows: two horizons of five components"];
    .qunit.assertEquals[distinct r`status;enlist `unavailable;"every one unavailable"];
    .qunit.assertTrue[all null r`pips;"with no pips"];
    / ODDITY: the leg that HAS a book is unavailable too, and so is the execution residual - the
    / reference is the whole chain's, so one missing leg takes the execution's own row with it.
    .qunit.assertEquals[(first exec price_t0 from r where kind=`leg, step=1, horizon=0D00:00:01;first exec price_t0 from r where kind=`leg, step=2, horizon=0D00:00:01);1.25 0n;"the leg with a book still shows its mid; the other has none"];
    w:.qpipe.transform.fx_markout_outputs.wide r;
    .qunit.assertEquals[(w`status;w`horizon);(2#`unavailable;0D00:00:01 0D00:00:10);"the wide layout says unavailable"];
    .qunit.assertTrue[all null w`total_pips;"with no markout"]};

.qt.qpipe.transform.fx_markout.characterization.test_no_book_at_all_gives_unavailable_rows_not_an_error:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    r:.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;1;1.1001;`x1];0#.qpipe.transform.fx_markout.market_data_in];
    .qunit.assertEquals[(count r;distinct r`status);(8;enlist `unavailable);"two horizons of four components, all unavailable"];
    .qunit.assertTrue[all null r`pips;"no pips anywhere"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_book_that_starts_after_the_execution_prices_the_markout_but_not_the_execution:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    / The only book is at T+0.5s: 1.1004/1.1006, mid 1.1005. At the execution there is none.
    md:.qt.qpipe.transform.fx_markout.characterization.bk[t+0D00:00:00.5;`EURUSD;`a;1.1004;1.1006];
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[enlist 0D00:00:01];.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;1;1.1001;`x1];md];
    .qunit.assertEquals[r`kind;`total`execution`leg`residual;"the four components"];
    .qunit.assertEquals[r`status;`ok`unavailable`unavailable`unavailable;"the markout is priced; the rest, which need the book at the execution, are not"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[r`pips;4 0n 0n 0n];"4 pips: 10000*(1.1005-1.1001)"];
    / ODDITY: the wide layout then says `partial - a markout that is priced but not decomposed.
    .qunit.assertEquals[(.qpipe.transform.fx_markout_outputs.wide r)`status;enlist `partial;"the wide row is partial"]};

/ ------------------------------------------------------------------ an inverted multi-leg route reconciles

/ Every (execution, horizon) of a result: total = execution + sum of legs + residual, the residual is the
/ float noise and its status reconciled.
.qt.qpipe.transform.fx_markout.characterization.reconciles:{[res]
    per:0!select total:sum pips where kind=`total, execution:sum pips where kind=`execution, legs:sum pips where kind=`leg,
        residual:sum pips where kind=`residual by venue, fill_id, horizon from res;
    gap:(per`total)-(per`execution)+(per`legs)+per`residual;
    (all 1e-6>=abs gap;all (exec status from res where kind=`residual)=`reconciled)}

.qt.qpipe.transform.fx_markout.characterization.test_an_inverted_two_leg_route_reconciles:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    / USDPLN = EURUSD (inverted) then EURPLN. Before: EURUSD 1.25, EURPLN 5.0 -> 4.0. After: EURUSD 1.0, EURPLN 5.5 -> 5.5.
    md:raze (.qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:01;`EURUSD;`a;1.25;1.25];.qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:01;`EURPLN;`a;5f;5f];
        .qt.qpipe.transform.fx_markout.characterization.bk[t+0D00:00:00.5;`EURUSD;`a;1f;1f];.qt.qpipe.transform.fx_markout.characterization.bk[t+0D00:00:00.5;`EURPLN;`a;5.5;5.5]);
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[enlist 0D00:00:01];.qt.qpipe.transform.fx_markout.characterization.fill[t;`USDPLN;-1;3.99;`y1];md];
    .qunit.assertEquals[r`kind;`total`execution`leg`leg`residual;"total, execution, the two legs in route order, residual"];
    .qunit.assertEquals[(r`leg;r`invert);(``,`EURUSD`EURPLN,`;00100b);"EURUSD is inverted, EURPLN is not"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[r`pips;-15100 -100 -10000 -5000 0f];"a sell loses 10000*(5.5-3.99); -100 at the execution, -10000 to EURUSD (4.0 -> 5.0), -5000 to EURPLN (5.0 -> 5.5)"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[r`price_t0;3.99 3.99 1.25 5 0n];"price_t0: the fill, the fill, and each leg's own mid at the execution"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[r`price_t1;5.5 4 1 5.5 0n];"price_t1: the horizon's reference, the execution's, and each leg's own mid at the horizon"];
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.characterization.reconciles[r];11b;"it reconciles"];
    .qunit.assertEquals[r`status;`ok`ok`ok`ok`reconciled;"and says so"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_three_leg_route_with_an_inverted_first_leg_reconciles:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    / PLNMXN = EURPLN (inverted), EURUSD, USDMXN.
    / EURPLN 4 -> 5 (inverted: 0.25 -> 0.2), EURUSD 1.25 -> 1.5, USDMXN 20 -> 20.
    md:raze (.qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:01;`EURPLN;`a;4f;4f];.qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:01;`EURUSD;`a;1.25;1.25];.qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:01;`USDMXN;`a;20f;20f];
        .qt.qpipe.transform.fx_markout.characterization.bk[t+0D00:00:00.5;`EURPLN;`a;5f;5f];.qt.qpipe.transform.fx_markout.characterization.bk[t+0D00:00:00.5;`EURUSD;`a;1.5;1.5];.qt.qpipe.transform.fx_markout.characterization.bk[t+0D00:00:00.5;`USDMXN;`a;20f;20f]);
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[enlist 0D00:00:01];.qt.qpipe.transform.fx_markout.characterization.fill[t;`PLNMXN;1;6f;`z1];md];
    / The pair is (1/4)*1.25*20 = 6.25 before and (1/5)*1.5*20 = 6.0 after. A buy at 6.0: execution 10000*(6.25-6.0) = 2500; markout 0.
    / Waterfall in route order: EURPLN alone moves the chain to (1/5)*1.25*20 = 5.0 (-12500); EURUSD then to 6.0 (+10000); USDMXN nothing.
    .qunit.assertEquals[r`kind;`total`execution`leg`leg`leg`residual;"three legs"];
    .qunit.assertEquals[r`invert;001000b;"only the first is inverted"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[r`pips;0 2500 -12500 10000 0 0f];"the hand-worked waterfall"];
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.characterization.reconciles[r];11b;"it reconciles"];
    w:.qpipe.transform.fx_markout_outputs.wide r;
    .qunit.assertEquals[(first w`leg1;first w`leg2;first w`leg3;first w`leg1_invert);(`EURPLN;`EURUSD;`USDMXN;1b);"and fits the wide layout's three leg columns"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_multi_leg_route_reconciles_at_every_horizon_the_jobs_score:{[tc]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    r:.qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout_outputs.job_spec;f`executions;f`market_data];
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.characterization.reconciles[r];11b;"the fixture's EURUSD buy and USDPLN sell, at -1s, 0, 1s and 10s"];
    w:.qpipe.transform.fx_markout_outputs.wide r;
    .qunit.assertTrue[all (w`status) in `ok`unavailable`partial;"and the wide status is one of the documented ones"]};

/ ------------------------------------------------------------------ signed horizons

/ A EURUSD buy at 1.1001 at T. Source a quotes 1.0999/1.1001 (mid 1.1000) at T-3s, 1.1001/1.1003 (mid 1.1002)
/ at T-0.5s and 1.1004/1.1006 (mid 1.1005) at T+0.5s.
.qt.qpipe.transform.fx_markout.characterization.signed_books:{[]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    raze (.qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:03;`EURUSD;`a;1.0999;1.1001];
        .qt.qpipe.transform.fx_markout.characterization.bk[t-0D00:00:00.5;`EURUSD;`a;1.1001;1.1003];
        .qt.qpipe.transform.fx_markout.characterization.bk[t+0D00:00:00.5;`EURUSD;`a;1.1004;1.1006])}

.qt.qpipe.transform.fx_markout.characterization.test_signed_horizons_negative_zero_and_positive:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    hs:-0D00:00:01 0D00:00:00 0D00:00:01;
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[hs];.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;1;1.1001;`x1];.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    .qunit.assertEquals[distinct r`horizon;hs;"one block of rows per horizon, in the spec's order"];
    / The reference at T is the T-0.5s book (mid 1.1002): the execution residual is 10000*(1.1002-1.1001) = +1 at every horizon.
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[r[`pips] where r[`kind]=`execution;3#1f];"the execution residual is +1 pip at every horizon"];
    / -1s: the book at or before T-1s is the T-3s one (mid 1.1000), so the reference moved BACK 2 pips from the execution's: total -1, leg -2.
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[.qt.qpipe.transform.fx_markout.characterization.pips_of[r;-0D00:00:01;`total;0];-1f];"negative: total 10000*(1.1000-1.1001)"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[.qt.qpipe.transform.fx_markout.characterization.pips_of[r;-0D00:00:01;`leg;1];-2f];"negative: the leg is the move from the execution's reference back to the earlier one"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[(select price_t1 from r where horizon=-0D00:00:01, kind=`total)`price_t1;enlist 1.1];"negative: the reference is the earlier book's mid"];
    / zero: the reference is the execution's own, so the markout is the execution residual and nothing moved.
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[.qt.qpipe.transform.fx_markout.characterization.pips_of[r;0D00:00:00;`total;0];1f];"zero: the markout is the execution residual"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[.qt.qpipe.transform.fx_markout.characterization.pips_of[r;0D00:00:00;`leg;1];0f];"zero: no leg moved"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[.qt.qpipe.transform.fx_markout.characterization.pips_of[r;0D00:00:00;`residual;0];0f];"zero: nothing left over"];
    / +1s: the T+0.5s book (mid 1.1005): total 10000*(1.1005-1.1001) = 4, leg 10000*(1.1005-1.1002) = 3.
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[.qt.qpipe.transform.fx_markout.characterization.pips_of[r;0D00:00:01;`total;0];4f];"positive: total 4"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[.qt.qpipe.transform.fx_markout.characterization.pips_of[r;0D00:00:01;`leg;1];3f];"positive: leg 3"];
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.characterization.reconciles[r];11b;"and each horizon reconciles"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_sell_has_the_signs_of_a_buy_reversed:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    hs:-0D00:00:01 0D00:00:00 0D00:00:01;
    sp:.qt.qpipe.transform.fx_markout.characterization.with_horizons[hs];
    buy:.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;1;1.1001;`x1];.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    sell:.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;-1;1.1001;`x1];.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[sell`pips;neg buy`pips];"every component's pips is negated"];
    .qunit.assertEquals[(sell`side;buy`side);(12#-1;12#1);"and the side is carried"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_negative_horizon_before_the_first_book_is_unavailable_and_the_execution_is_not:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[enlist -0D00:00:04];.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;1;1.1001;`x1];.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    .qunit.assertEquals[r`status;`unavailable`ok`unavailable`unavailable;"no book at or before T-4s: the horizon's reference is missing, the execution residual is not"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.characterization.near[r`pips;0n 1 0n 0n];"the execution residual is +1 pip"]};

.qt.qpipe.transform.fx_markout.characterization.test_horizons_come_out_in_the_order_the_spec_lists_them:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    hs:0D00:00:01 -0D00:00:01 0D00:00:00;
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[hs];.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;1;1.1001;`x1];.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    .qunit.assertEquals[distinct r`horizon;hs;"not sorted: the spec's order"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_spec_with_a_null_or_repeated_horizon_is_refused:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    e:.qt.qpipe.transform.fx_markout.characterization.fill[t;`EURUSD;1;1.1001;`x1];
    md:.qt.qpipe.transform.fx_markout.characterization.signed_books[];
    .qunit.assertThrows[{[e;md] .qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[0D00:00:01 0D00:00:01];e;md]}[e];md;
        "fx_markout: horizons must be distinct";"a repeated horizon"];
    .qunit.assertThrows[{[e;md] .qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[0D00:00:01,0Nn];e;md]}[e];md;
        "fx_markout: horizons must not contain a null timespan";"a null horizon"];
    .qunit.assertThrows[{[e;md] .qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[`timespan$()];e;md]}[e];md;
        "fx_markout: horizons must be a non-empty list of timespans*";"no horizons"]};

.qt.qpipe.transform.fx_markout.characterization.test_the_wait_and_the_lookback_follow_the_signs_of_the_horizons:{[tc]
    sp:{.qt.qpipe.transform.fx_markout.characterization.with_horizons[x]};
    .qunit.assertEquals[.qpipe.transform.fx_markout.wait[sp -0D00:00:30 -0D00:00:01;0D00:00:01];0D00:00:01;"only negative horizons: the wait is the settle"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.wait[sp 0D00:00:01 0D00:00:10;0D00:00:01];0D00:00:10;"the furthest horizon ahead"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.lookback sp -0D00:00:30 0D00:00:10;0D00:00:30;"the lookback is the most negative horizon"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.lookback sp 0D00:00:00 0D00:00:10;0D00:00:00;"and none when no horizon is negative"]};

/ ------------------------------------------------------------------ last delivery

.qt.qpipe.transform.fx_markout.characterization.test_the_last_delivery_of_an_identity_is_the_one_scored:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    f:.qt.qpipe.transform.fx_markout.characterization.fill;
    exe:f[t;`EURUSD;1;1.1001;`x1],f[t;`EURUSD;1;1.1002;`y1],f[t;`EURUSD;1;1.1003;`x1];
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[enlist 0D00:00:00];exe;.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    .qunit.assertEquals[exec distinct fill_id from r;`y1`x1;"two executions, not three"];
    .qunit.assertEquals[(first exec price_t0 from r where fill_id=`x1, kind=`total;first exec price_t0 from r where fill_id=`y1, kind=`total);1.1003 1.1002;"x1 is scored at its LAST delivery's price"];
    / ODDITY: the result is ordered by where each identity's last delivery arrived, not by the order of first arrival or by time.
    .qunit.assertEquals[(.qpipe.transform.fx_markout.last_delivery exe)`price;1.1002 1.1003;"y1 (delivered second) before x1 (last delivered third)"]};

.qt.qpipe.transform.fx_markout.characterization.test_the_same_fill_id_on_another_venue_is_another_execution:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    f:.qt.qpipe.transform.fx_markout.characterization.fill;
    exe:f[t;`EURUSD;1;1.1001;`x1],update venue:`b from f[t;`EURUSD;1;1.1002;`x1];
    .qunit.assertEquals[count .qpipe.transform.fx_markout.last_delivery exe;2;"identity is (venue; fill_id)"];
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[enlist 0D00:00:00];exe;.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    .qunit.assertEquals[exec distinct venue from r;`a`b;"both are scored"]};

.qt.qpipe.transform.fx_markout.characterization.test_a_later_delivery_that_cannot_be_scored_does_not_displace_the_earlier_one:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    f:.qt.qpipe.transform.fx_markout.characterization.fill;
    / ODDITY: unscoreable rows are dropped BEFORE the last delivery is chosen, so a bad redelivery
    / (here a price of zero) leaves the earlier good one standing instead of replacing it.
    exe:f[t;`EURUSD;1;1.1001;`x1],f[t;`EURUSD;1;0f;`x1];
    r:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.characterization.with_horizons[enlist 0D00:00:00];exe;.qt.qpipe.transform.fx_markout.characterization.signed_books[]];
    .qunit.assertEquals[(count r;exec distinct price_t0 from r where kind=`total);(4;enlist 1.1001);"scored at the first delivery's price"]};

.qt.qpipe.transform.fx_markout.characterization.test_identities_that_join_to_the_same_text_are_two_executions:{[tc]
    t:.qt.qpipe.transform.fx_markout.characterization.t;
    f:.qt.qpipe.transform.fx_markout.characterization.fill;
    / Was an ODDITY: the identity was the venue and fill_id joined with "|", so venue `a|b with fill c
    / and venue `a with fill `b|c collapsed into one execution. The identity is now the pair (#1946).
    one:update venue:`$"a|b" from f[t;`EURUSD;1;1.1001;`c];
    two:update fill_id:`$"b|c" from f[t;`EURUSD;1;1.1002;`ignored];
    exe:one,two;
    got:.qpipe.transform.fx_markout.last_delivery exe;
    .qunit.assertEquals[(count got;got`price);(2;1.1001 1.1002);"two executions, each its own delivery"]};
