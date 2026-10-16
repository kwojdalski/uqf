/ test_fx_markout.q - the live normalized FX markout job
/ (.qpipe.job.fx_markout and its wide twin .qpipe.job.fx_markout_wide, #1395).
/ .
/ The calculation is tested on its own (test_fx_markout_calc.q) and the adapters on
/ theirs; this file tests the LIFECYCLE the horizon kind gives them: when an execution
/ is ready, what a missing leg does, how a negative horizon keeps its history, what a
/ redelivery or a failure leaves behind, and that a live row is the row a refill
/ writes. Every expected number is the hand-worked one from
/ src/etl/transforms/fx_markout_outputs.q: a EURUSD buy at 1.1001 against two sources.


.qt.qpipe.job.fx_markout.t:2026.09.17D10:00:00.000000000

.qt.qpipe.job.fx_markout.sent:()

/ Both jobs, state emptied and publish pointing at a recorder.
.qt.qpipe.job.fx_markout.setUp_fresh:{[]
    `.qt.qpipe.job.fx_markout.sent set ();
    {[j] .qetl.job.stream.reset j; .qetl.job.stream.wire[j;{[n;tb;x] .qt.qpipe.job.fx_markout.sent,:enlist (n;tb;x); count x}[j]]} each `fx_markout`fx_markout_wide;
    }

.qt.qpipe.job.fx_markout.tearDown_unwire:{[]
    {[j] (` sv `.qpipe.job,j,`publish) set .qetl.job.stream.unwired j} each `fx_markout`fx_markout_wide;
    }

/ An execution, as the plant carries it: received at rv, stamped by the source at st.
.qt.qpipe.job.fx_markout.ex:{[st;rv;sym;sd;px;id]
    ([] time:enlist rv; source_time:enlist st; sym:enlist sym; venue:enlist `a; side:enlist sd; size:enlist 1e6;
        price:enlist px; fee:enlist 0f; fee_ccy:enlist `; fill_id:enlist id; book:enlist `; product:enlist `)}

/ A book snapshot, as the plant carries it.
.qt.qpipe.job.fx_markout.bk:{[st;sym;src;bid;ask]
    ([] time:enlist st+0D00:00:00.1; sym:enlist sym; source:enlist src; market:enlist `fx; source_time:enlist st;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1e6; ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6)}

/ The worked books of fx_markout_outputs.q, as the plant carries them
.qt.qpipe.job.fx_markout.books:{[] update time:source_time+0D00:00:00.1, market:`fx from .qpipe.transform.fx_markout_outputs.sample_books[]}

.qt.qpipe.job.fx_markout.buy:{[] .qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t+0D00:00:00.2;`EURUSD;1;1.1001;`x1]}

/ The worked books and one closing book after the furthest horizon: the reference
/ covers an instant only once a row at or after it has arrived, because a book that
/ has not been delivered yet could still be the one that supersedes the last.
.qt.qpipe.job.fx_markout.covered:{[] .qt.qpipe.job.fx_markout.books[],.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t+0D00:00:11;`EURUSD;`a;1.1012;1.1014]}

.qt.qpipe.job.fx_markout.feed:{[nm;tb;x] ((get ` sv `.qpipe.job,nm,`on_batch))[tb;x]}
.qt.qpipe.job.fx_markout.tick:{[nm;now] ((get ` sv `.qpipe.job,nm,`score_ready))[now]}
.qt.qpipe.job.fx_markout.state:{[nm;what] get ` sv `.qpipe.job,nm,what}

.qt.qpipe.job.fx_markout.published:{[nm] raze {[x] x 2} each .qt.qpipe.job.fx_markout.sent where nm=.qt.qpipe.job.fx_markout.sent[;0]}

.qt.qpipe.job.fx_markout.near:{[a;b] (all (null a)=null b) and all (null a) or 1e-9>=abs a-b}

/ What the output-contract suite (test_job_output_contracts.q) drives the job with: one
/ execution, the books around it, and the timer once its horizons have passed.
.qt.qpipe.job.fx_markout.contract_driver:{[]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10]}

.qt.qpipe.job.fx_markout.test_the_worked_example_is_the_execution_these_tests_drive:{[tc]
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.sample_executions[];select source_time, venue, sym, side, size, price, fill_id from .qt.qpipe.job.fx_markout.buy[];
        "one example, hand-worked in fx_markout_outputs.q, is the execution every lifecycle test below scores"]};

/ --- the declaration --------------------------------------------------------------

.qt.qpipe.job.fx_markout.test_the_job_is_declared_over_the_canonical_contracts_only:{[tc]
    d:.qetl.job.stream.horizons.def `fx_markout;
    .qunit.assertEquals[(d`events;d`reference);`executions`market_data;"the two canonical tables, no source's"];
    .qunit.assertEquals[(d`event_time;d`reference_time;d`ready_on);(`source_time;`source_time;`both);"source_time on both clocks, wall and reference readiness"];
    .qunit.assertEquals[(d`identity;d`retain_by;d`by);(`venue`fill_id;`sym`source;enlist `sym);"identity, per-source retention, readiness keyed by leg"];
    .qunit.assertEquals[d`lookback;0D00:00:01;"the one second the -1s horizon reaches back"];
    .qunit.assertEquals[d`horizon;0D00:00:10;"the furthest horizon"];
    .qunit.assertTrue[d[`expire_after]>d`horizon;"and an execution is given up on afterwards"]};

.qt.qpipe.job.fx_markout.test_neither_job_file_names_a_source:{[tc]
    forbidden:("marketwarehouse";"mkt_order_book";"FULL_AMOUNT";"security_code";"last_qty";"deal_tape";"book_levels");
    src:" " sv (read0 `:src/etl/streaming/fx_markout.q),read0 `:src/etl/streaming/fx_markout_wide.q;
    found:forbidden where {[s;w] 0<count ss[s;w]}[src] each forbidden;
    .qunit.assertEquals[found;();"the jobs are declared over executions and market_data alone"]};

.qt.qpipe.job.fx_markout.test_the_jobs_keep_only_what_scoring_can_use:{[tc]
    k:.qpipe.job.fx_markout.keep;
    .qunit.assertEquals[k ([] sym:`EURUSD`BTCUSDT`USDPLN);101b;"books of pairs the convention routes"];
    e:.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t;`EURUSD;1;1.1;`a1],.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t;`BTCUSDT;1;62000f;`a2],.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t;`EURUSD;0;1.1;`a3],.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t;`EURUSD;1;1.1;`];
    .qunit.assertEquals[k e;1000b;"a routable, well-formed, identified execution"]};

/ --- readiness: the wall AND the reference ------------------------------------------

.qt.qpipe.job.fx_markout.test_an_execution_waits_for_the_wall_though_the_books_cover_it:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:05];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.sent;0;"the books reach past +10s, but only 5s have elapsed"];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.published `fx_markout;16;"scored the instant the furthest horizon has elapsed"]};

.qt.qpipe.job.fx_markout.test_an_execution_waits_for_the_books_though_the_wall_has_elapsed:{[tc]
    edge:.qt.qpipe.job.fx_markout.t+0D00:00:06;
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;select from .qt.qpipe.job.fx_markout.covered[] where source_time<edge];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:04];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.sent;0;"four minutes on the wall clock, but no book reaches +10s"];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t+0D00:00:10.5;`EURUSD;`a;1.1010;1.1012]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:04];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.published `fx_markout;16;"scored once the books have advanced through the horizon"]};

.qt.qpipe.job.fx_markout.test_maturity_is_the_source_time_not_the_plant_receipt_time:{[tc]
    / received an hour late: still due 10s after it HAPPENED
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t+0D01;`EURUSD;1;1.1001;`x1]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.published `fx_markout;16;"due at source_time + 10s, not at receipt + 10s"]};

/ --- missing legs and expiry ----------------------------------------------------------

.qt.qpipe.job.fx_markout.test_a_missing_leg_holds_the_execution_then_expires_it_with_the_reason:{[tc]
    / USDPLN routes EURUSD (inverted) and EURPLN: EURPLN is never quoted
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t+0D00:00:00.2;`USDPLN;-1;3.99;`y1]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:01];
    .qunit.assertEquals[(count .qt.qpipe.job.fx_markout.sent;count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending]);0 1;"waiting: never scored with a leg missing"];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:06];
    gone:.qt.qpipe.job.fx_markout.state[`fx_markout;`expired];
    .qunit.assertEquals[(count gone;count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending];count .qt.qpipe.job.fx_markout.sent);1 0 0;"given up on after expire_after, nothing published"];
    .qunit.assertEquals[first gone`reason;`$"no reference for EURPLN";"and the diagnostic names the leg"]};

.qt.qpipe.job.fx_markout.test_the_legs_readiness_waits_on_are_the_legs_scoring_prices:{[tc]
    sp:.qpipe.transform.fx_markout_outputs.job_spec;
    e:.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t;`PLNTRY;1;12.4;`c1],.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t;`USDPLN;-1;3.99;`c2],.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t;`EURUSD;1;1.1;`c3];
    callback:.qpipe.job.fx_markout.legs e;
    m:raze (.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t-0D00:00:02;`EURPLN;`a;4f;4f];.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t-0D00:00:02;`EURUSD;`a;1.25;1.25];.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t-0D00:00:02;`USDTRY;`a;40f;40f]);
    scored:exec distinct leg by fill_id from .qpipe.transform.fx_markout.calc[sp;select source_time, venue, sym, side, size, price, fill_id from e;
        select source_time, sym, source, bid_prices, bid_sizes, ask_prices, ask_sizes from m] where kind=`leg;
    .qunit.assertEquals[callback 0;scored`c1;"PLNTRY: three legs"];
    .qunit.assertEquals[callback 1;scored`c2;"USDPLN: two"];
    .qunit.assertEquals[callback 2;scored`c3;"EURUSD: itself"]};

.qt.qpipe.job.fx_markout.test_an_execution_the_books_never_reach_expires_with_the_coverage_reason:{[tc]
    edge:.qt.qpipe.job.fx_markout.t+0D00:00:06;
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;select from .qt.qpipe.job.fx_markout.covered[] where source_time<edge];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:06];
    .qunit.assertTrue[(string first .qt.qpipe.job.fx_markout.state[`fx_markout;`expired]`reason) like "reference has not reached*";
        "the wall had elapsed, so the reason is the reference's"]};

/ --- signed and zero horizons, and the history they need -------------------------------

.qt.qpipe.job.fx_markout.test_the_published_rows_are_the_hand_worked_ones_at_every_horizon:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    r:.qt.qpipe.job.fx_markout.published `fx_markout;
    .qunit.assertEquals[r;.qpipe.transform.fx_markout_outputs.sample_long[];
        "-1s, 0, +1s and +10s: markout -1, -1, 4, 8.5; the execution residual -1; the leg 0, 0, 5, 9.5"]};

.qt.qpipe.job.fx_markout.test_a_negative_horizon_keeps_the_book_before_the_execution_across_a_tick:{[tc]
    / the -1s horizon reads the book at T-1s: the T-3s one. A tick before the execution is
    / ready must not evict it, nor the T-0.4s one that is newer than the anchor and not needed.
    pre:.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t-0D00:00:03;`EURUSD;`a;1.0899;1.0901],.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t-0D00:00:00.4;`EURUSD;`a;1.0999;1.1001];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;pre];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:01];
    held:exec source_time from .qt.qpipe.job.fx_markout.state[`fx_markout;`history];
    .qunit.assertTrue[(.qt.qpipe.job.fx_markout.t-0D00:00:03) in held;"the T-3s book is still the as-of answer for T-1s"];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t+0D00:00:10.5;`EURUSD;`a;1.1000;1.1002]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:11];
    r:.qt.qpipe.job.fx_markout.published `fx_markout;
    mid:first exec price_t1 from r where kind=`total, horizon=-0D00:00:01;
    .qunit.assertTrue[.qt.qpipe.job.fx_markout.near[mid;1.09];"scored against the T-3s book's mid 1.0900"];
    .qunit.assertTrue[.qt.qpipe.job.fx_markout.near[first exec pips from r where kind=`total, horizon=-0D00:00:01;10000*1.09-1.1001];"-1010 pips"]};

.qt.qpipe.job.fx_markout.test_a_zero_horizon_scores_the_execution_residual_alone:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    r:.qt.qpipe.job.fx_markout.published `fx_markout;
    atzero:select from r where horizon=0D00:00:00;
    .qunit.assertTrue[.qt.qpipe.job.fx_markout.near[first exec pips from atzero where kind=`total;first exec pips from atzero where kind=`execution];"at the execution the markout is the residual"];
    .qunit.assertTrue[.qt.qpipe.job.fx_markout.near[first exec pips from atzero where kind=`leg;0f];"and nothing moved"]};

/ --- out-of-order data and bounded history ------------------------------------------------

.qt.qpipe.job.fx_markout.test_books_and_executions_in_any_order_score_the_same:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    inorder:.qt.qpipe.job.fx_markout.published `fx_markout;
    .qt.qpipe.job.fx_markout.setUp_fresh[];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;reverse .qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    .qunit.assertEquals[.qt.qpipe.job.fx_markout.published `fx_markout;inorder;"the execution first, the books newest first"]};

.qt.qpipe.job.fx_markout.test_the_history_is_bounded_by_what_a_pending_execution_can_still_need:{[tc]
    / an hour of per-second books for each of two sources, with no execution waiting
    n:3600;
    stream:{[src;n] raze {[src;i] .qt.qpipe.job.fx_markout.bk[(2026.09.17D09:00:00)+0D00:00:01*i;`EURUSD;src;1.1+i*1e-6;1.1002+i*1e-6]}[src;] each til n};
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;stream[`a;n]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;stream[`b;n]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;2026.09.17D10:00:30];
    h:.qt.qpipe.job.fx_markout.state[`fx_markout;`history];
    .qunit.assertEquals[asc distinct h`source;`a`b;"both sources survive"];
    .qunit.assertTrue[(count h)<=2*(1+`long$(.qpipe.transform.fx_markout.wait[.qpipe.transform.fx_markout_outputs.job_spec;.qpipe.transform.fx_markout_outputs.settle]+.qpipe.transform.fx_markout_outputs.max_lateness+.qpipe.transform.fx_markout.lookback[.qpipe.transform.fx_markout_outputs.job_spec])%0D00:00:01);
        "a tick keeps the books a late execution could still need - the horizon, the lookback and the declared lateness - plus each source's anchor, not the hour"];
    .qunit.assertTrue[(count h)<2*n;"and far fewer than were sent"]};

/ --- identity, redelivery, failure ---------------------------------------------------------

.qt.qpipe.job.fx_markout.test_a_redelivered_execution_replaces_the_pending_one:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t+0D00:00:00.4;`EURUSD;1;1.1003;`x1]];
    .qunit.assertEquals[(count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending];first .qt.qpipe.job.fx_markout.state[`fx_markout;`pending]`price);(1;1.1003);"one pending, the last delivery"];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    .qunit.assertEquals[distinct exec price_t0 from .qt.qpipe.job.fx_markout.published[`fx_markout] where kind=`total;enlist 1.1003;"scored at the price of the last delivery"]};

.qt.qpipe.job.fx_markout.test_an_execution_already_scored_is_not_scored_again:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    n:count .qt.qpipe.job.fx_markout.sent;
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:20];
    .qunit.assertEquals[(count .qt.qpipe.job.fx_markout.sent;count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending]);(n;0);"the redelivery is dropped while the identity is remembered"]};

.qt.qpipe.job.fx_markout.test_the_same_fill_id_on_another_venue_is_another_execution:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;update venue:`b from .qt.qpipe.job.fx_markout.buy[]];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending];2;"identity is (venue; fill_id)"]};

.qt.qpipe.job.fx_markout.test_the_dedup_memory_is_bounded:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    later:.qt.qpipe.job.fx_markout.t+0D00:00:10+.qpipe.transform.fx_markout_outputs.remember+0D00:00:01;
    .qt.qpipe.job.fx_markout.tick[`fx_markout;later];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.state[`fx_markout;`completed];0;"forgotten once past remember"];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t+0D01;later;`EURUSD;1;1.1001;`x1]];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending];1;"after which a redelivery is a new execution: the bound, stated"]};

.qt.qpipe.job.fx_markout.test_a_publication_failure_leaves_the_execution_queued_for_retry:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qetl.job.stream.wire[`fx_markout;{[tb;x] '"plant down"}];
    .qunit.assertThrows[.qt.qpipe.job.fx_markout.tick[`fx_markout;];.qt.qpipe.job.fx_markout.t+0D00:00:10;"plant down";"the error is not swallowed here"];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending];1;"still queued"];
    .qetl.job.stream.wire[`fx_markout;{[tb;x] .qt.qpipe.job.fx_markout.sent,:enlist (`fx_markout;tb;x); count x}];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:11];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.published `fx_markout;16;"and the next tick publishes it"]};

.qt.qpipe.job.fx_markout.test_a_calculation_failure_leaves_the_execution_queued_for_retry:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    row:(enlist[`name]!enlist `fx_markout_score_long),.qetl.transform.registry `fx_markout_score_long;
    broken:@[row;`fn;:;{[e;m] '"calculation down"}];
    `.qetl.transform.registry upsert enlist broken;
    r:@[.qt.qpipe.job.fx_markout.tick[`fx_markout;];.qt.qpipe.job.fx_markout.t+0D00:00:10;{[e] `threw,e}];
    `.qetl.transform.registry upsert enlist row;
    .qunit.assertEquals[r;`threw,"calculation down";"the failure surfaces"];
    .qunit.assertEquals[(count .qt.qpipe.job.fx_markout.sent;count .qt.qpipe.job.fx_markout.state[`fx_markout;`pending]);0 1;"nothing published, the execution still queued"];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:11];
    .qunit.assertEquals[count .qt.qpipe.job.fx_markout.published `fx_markout;16;"scored on the retry"]};

/ --- long and wide: one result, two layouts ----------------------------------------------------

.qt.qpipe.job.fx_markout.both:{[x]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout_wide;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    {.qt.qpipe.job.fx_markout.feed[x;`executions;y]}[;x] each `fx_markout`fx_markout_wide;
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    .qt.qpipe.job.fx_markout.tick[`fx_markout_wide;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    }

.qt.qpipe.job.fx_markout.test_long_and_wide_carry_the_same_values_identities_and_alignment:{[tc]
    .qt.qpipe.job.fx_markout.both .qt.qpipe.job.fx_markout.buy[];
    long:.qt.qpipe.job.fx_markout.published `fx_markout;
    wide:.qt.qpipe.job.fx_markout.published `fx_markout_wide;
    .qunit.assertEquals[count wide;4;"a row per horizon"];
    .qunit.assertEquals[(distinct wide`fill_id;distinct wide`venue;wide`horizon);(enlist `x1;enlist `a;exec distinct horizon from long);"the same identities and horizons"];
    keyed:{[long;hz;knd] first exec pips from long where horizon=hz, kind=knd}[long];
    .qunit.assertTrue[all .qt.qpipe.job.fx_markout.near'[wide`total_pips;keyed[;`total] each wide`horizon];"total_pips is the long total"];
    .qunit.assertTrue[all .qt.qpipe.job.fx_markout.near'[wide`execution_pips;keyed[;`execution] each wide`horizon];"execution_pips is the long execution residual"];
    .qunit.assertTrue[all .qt.qpipe.job.fx_markout.near'[wide`residual_pips;keyed[;`residual] each wide`horizon];"residual_pips is the long residual"];
    .qunit.assertTrue[all .qt.qpipe.job.fx_markout.near'[wide`leg1_pips;{[long;hz] first exec pips from long where horizon=hz, kind=`leg, step=1}[long] each wide`horizon];"leg1_pips is the long leg 1"];
    .qunit.assertTrue[all .qt.qpipe.job.fx_markout.near'[wide`total_pips;(wide`execution_pips)+(wide`leg1_pips)+wide`residual_pips];"and the wide row reconciles by itself"]};

.qt.qpipe.job.fx_markout.test_a_cross_keeps_its_legs_aligned_in_both_layouts:{[tc]
    m:raze (.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t-0D00:00:01;`EURUSD;`a;1.25;1.25];.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t-0D00:00:01;`EURPLN;`a;5f;5f];
            .qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t+0D00:00:00.5;`EURUSD;`a;1f;1f];.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t+0D00:00:00.5;`EURPLN;`a;5.5;5.5];
            .qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t+0D00:00:11;`EURUSD;`a;1f;1f];.qt.qpipe.job.fx_markout.bk[.qt.qpipe.job.fx_markout.t+0D00:00:11;`EURPLN;`a;5.5;5.5]);
    {[m;nm] .qt.qpipe.job.fx_markout.feed[nm;`market_data;m]}[m] each `fx_markout`fx_markout_wide;
    e:.qt.qpipe.job.fx_markout.ex[.qt.qpipe.job.fx_markout.t;.qt.qpipe.job.fx_markout.t+0D00:00:00.2;`USDPLN;-1;3.99;`y1];
    {[e;nm] .qt.qpipe.job.fx_markout.feed[nm;`executions;e]}[e] each `fx_markout`fx_markout_wide;
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    .qt.qpipe.job.fx_markout.tick[`fx_markout_wide;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    long:.qt.qpipe.job.fx_markout.published `fx_markout;
    wide:.qt.qpipe.job.fx_markout.published `fx_markout_wide;
    w:first select from wide where horizon=0D00:00:01;
    / EURUSD (inverted, 1.25 -> 1.0) and EURPLN (5 -> 5.5): -10000 and -5000, markout -15100
    .qunit.assertEquals[(w`leg1;w`leg1_invert;w`leg2;w`leg2_invert);(`EURUSD;1b;`EURPLN;0b);"the legs in route order, with their inversion"];
    .qunit.assertTrue[.qt.qpipe.job.fx_markout.near[(w`leg1_pips;w`leg2_pips;w`total_pips;w`execution_pips);-10000 -5000 -15100 -100f];"the hand-worked allocation"];
    lg:select leg, pips from long where horizon=0D00:00:01, kind=`leg;
    .qunit.assertEquals[(lg`leg;.qt.qpipe.job.fx_markout.near[lg`pips;w[`leg1_pips],w`leg2_pips]);(`EURUSD`EURPLN;1b);"the same legs in the long layout"]};

/ --- a live row is the row a refill writes --------------------------------------------------------

.qt.qpipe.job.fx_markout.test_live_and_bounded_scoring_agree_on_identical_inputs_and_history:{[tc]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    / the reference covers an instant only once a later row has arrived: one closing book per pair, for both
    closing:raze (.qt.qpipe.job.fx_markout.bk[2026.09.17D11:00:00;`EURUSD;`a;1.2;1.2002];.qt.qpipe.job.fx_markout.bk[2026.09.17D11:00:00;`EURPLN;`a;5.2;5.2002]);
    f[`market_data]:f[`market_data],select source_time, sym, source, bid_prices, bid_sizes, ask_prices, ask_sizes from closing;
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;update time:source_time+0D00:00:00.1, market:`fx from f`market_data];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;update time:source_time+0D00:00:00.2, fee:0f, fee_ccy:`, book:`, product:` from f`executions];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;2026.09.17D11:00:30];
    live:.qt.qpipe.job.fx_markout.published `fx_markout;
    twin:.qetl.transform.apply[`hdb_fx_markouts_long;f];
    .qunit.assertTrue[0<count live;"the live job scored"];
    .qunit.assertEquals[live;delete time from twin;"identical rows: the same calculation, the same inputs"];
    .qunit.assertEquals[exec distinct fill_id from live;`x1`y1;"the crypto fill is excluded by both"]};

.qt.qpipe.job.fx_markout.test_the_live_job_and_its_twin_run_one_transform_not_a_copy:{[tc]
    .qunit.assertEquals[(.qetl.job.stream.horizons.def `fx_markout)`transform;`fx_markout_score_long;"the live job's"];
    .qunit.assertEquals[(.qetl.job.stream.horizons.def `fx_markout_wide)`transform;`fx_markout_score_wide;"and the wide job's"];
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    live:.qetl.transform.apply[`fx_markout_score_long;f];
    .qunit.assertEquals[delete time from .qetl.transform.apply[`hdb_fx_markouts_long;f];live;"the twin is the live transform plus a time"];
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.stamped[.qpipe.transform.fx_markout_outputs.job_spec;live];.qetl.transform.apply[`hdb_fx_markouts_long;f];"stamped as the header says"]};

/ --- a source that stopped quoting does not set the reference (#1410) --------------------------------

/ Source a quotes 1.1000/1.1010 (mid 1.1005) every few seconds, never more than 5s apart,
/ from T-2s to T+11s. Source s quoted a BETTER 1.1006/1.1008 (mid 1.1007) at T-20s and has
/ been silent since. Kept forever (the old max_age 0Wn) s sets both sides of the best-of.
.qt.qpipe.job.fx_markout.dead_source_books:{[]
    t:.qt.qpipe.job.fx_markout.t;
    raze (.qt.qpipe.job.fx_markout.bk[t-0D00:00:20;`EURUSD;`s;1.1006;1.1008];
        .qt.qpipe.job.fx_markout.bk[t-0D00:00:02;`EURUSD;`a;1.1000;1.1010];
        .qt.qpipe.job.fx_markout.bk[t+0D00:00:04;`EURUSD;`a;1.1000;1.1010];
        .qt.qpipe.job.fx_markout.bk[t+0D00:00:09;`EURUSD;`a;1.1000;1.1010];
        .qt.qpipe.job.fx_markout.bk[t+0D00:00:11;`EURUSD;`a;1.1000;1.1010])}

.qt.qpipe.job.fx_markout.test_a_source_that_stopped_quoting_does_not_set_the_live_reference:{[tc]
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.dead_source_books[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    r:.qt.qpipe.job.fx_markout.published `fx_markout;
    tot:select horizon, price_t1, status from r where kind=`total;
    .qunit.assertEquals[count tot;4;"scored at all four horizons"];
    .qunit.assertTrue[.qt.qpipe.job.fx_markout.near[tot`price_t1;4#1.1005];"the live source's mid 1.1005 at every horizon, not the dead source's 1.1007"];
    .qunit.assertTrue[not any `unavailable=tot`status;"a live source is quoting, so nothing is unavailable"]};

.qt.qpipe.job.fx_markout.test_the_bounded_twin_ignores_the_dead_source_as_the_live_job_does:{[tc]
    b:.qt.qpipe.job.fx_markout.dead_source_books[];
    f:`executions`market_data!(select source_time, venue, sym, side, size, price, fill_id from .qt.qpipe.job.fx_markout.buy[];select source_time, sym, source, bid_prices, bid_sizes, ask_prices, ask_sizes from b);
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;update time:source_time+0D00:00:00.1, market:`fx from b];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;.qt.qpipe.job.fx_markout.t+0D00:00:10];
    live:.qt.qpipe.job.fx_markout.published `fx_markout;
    twin:delete time from .qetl.transform.apply[`hdb_fx_markouts_long;f];
    .qunit.assertEquals[live;twin;"live and bounded agree with a dead source in the history"];
    .qunit.assertTrue[.qt.qpipe.job.fx_markout.near[exec price_t1 from twin where kind=`total;4#1.1005];"and both price the live source"]};

.qt.qpipe.job.fx_markout.test_the_history_drops_the_dead_sources_book_once_nothing_can_need_it:{[tc]
    t:.qt.qpipe.job.fx_markout.t;
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.dead_source_books[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;t+0D00:00:10];
    / a minute on: the queue reaches back max_lateness+lookback+max_age from the next execution, past s's book
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`market_data;.qt.qpipe.job.fx_markout.bk[t+0D00:01:00;`EURUSD;`a;1.1000;1.1010]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout;`executions;.qt.qpipe.job.fx_markout.ex[t+0D00:01:00;t+0D00:01:00.2;`EURUSD;1;1.1001;`x2]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout;t+0D00:01:01];
    held:exec source from .qt.qpipe.job.fx_markout.state[`fx_markout;`history];
    .qunit.assertTrue[not `s in held;"the book of a source silent past max_age is not kept for ever"];
    .qunit.assertTrue[`a in held;"while the live source's is"]};

/ --- one definition of the reference's staleness (#1410) ---------------------------------------------

.qt.qpipe.job.fx_markout.test_both_markout_families_take_the_reference_age_from_one_definition:{[tc]
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.job_spec`max_age;.qf.mkt.reference_max_age;"fx_markout scores with the shared reference age"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.default_spec`max_age;.qf.mkt.reference_max_age;"and so does its default spec"];
    .qunit.assertEquals[.qpipe.job.crypto_markout.max_age;.qf.mkt.reference_max_age;"as does crypto_markout"];
    .qunit.assertEquals[(.qetl.job.stream.horizons.def `fx_markout)`max_age;.qf.mkt.reference_max_age;"the long job's history ages out on it"];
    .qunit.assertEquals[(.qetl.job.stream.horizons.def `fx_markout_wide)`max_age;.qf.mkt.reference_max_age;"and the wide job's"];
    .qunit.assertEquals[(.qetl.job.stream.horizons.def `crypto_markout)`max_age;.qf.mkt.reference_max_age;"and crypto_markout's"]};
