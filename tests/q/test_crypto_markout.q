/ test_crypto_markout.q - the live crypto markout job and its dataset
/ (.qpipe.job.crypto_markout, the crypto_markout table, #1936).
/ .
/ The domain is tested on its own (test_crypto_domain.q) and the twin on its own
/ (test_hdb_crypto_markout_backfill.q); this file tests the LIFECYCLE the horizon kind gives
/ them and the two claims the dataset exists to make:
/ .
/   live output equals backfill output   one transform serves both, and the same rows come out
/   the bps match the retired job        crypto_markout used to score crypto_trades against
/                                        crypto_book by the library's best-mid-across-venues on
/                                        the plant's receipt time. On a fixture where fills and
/                                        books are on one clock, the new dataset's total bps are
/                                        that figure, row for row. The old computation is
/                                        restated here from the same library calls, since the job
/                                        that held it is gone.
/ Every expected number is the hand-worked one from src/etl/transforms/crypto_markout_outputs.q.


.qt.qpipe.job.crypto_markout.t:2026.09.17D10:00:00.000000000

/ The job's check holds a batch whose configuration snapshot is not durable (#1999), so the suite
/ has a status directory of its own and the job's start has pinned what it scores under.
.qt.qpipe.job.crypto_markout.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status-crypto-markout-job"];
    system"mkdir -p build/test-status-crypto-markout-job";
    .qt.support.reset_snapshots[];
    .qpipe.transform.crypto_markout_outputs.pin[];
    }
.qt.qpipe.job.crypto_markout.btc:`$"BTC-USDT"

.qt.qpipe.job.crypto_markout.sent:()

.qt.qpipe.job.crypto_markout.setUp_fresh:{[]
    `.qt.qpipe.job.crypto_markout.sent set ();
    .qetl.job.stream.reset `crypto_markout;
    .qetl.job.stream.wire[`crypto_markout;{[n;tb;x] .qt.qpipe.job.crypto_markout.sent,:enlist (n;tb;x); count x}[`crypto_markout]];
    }

.qt.qpipe.job.crypto_markout.tearDown_unwire:{[]
    (` sv `.qpipe.job,`crypto_markout,`publish) set .qetl.job.stream.unwired `crypto_markout;
    }

/ An execution as the plant carries it: stamped by the source at st on the clock clk, received at rv.
.qt.qpipe.job.crypto_markout.ex:{[st;rv;venue;sym;sd;px;id;clk]
    ([] time:enlist rv; source_time:enlist st; sym:enlist sym; venue:enlist venue; side:enlist sd; size:enlist 0.25;
        price:enlist px; fee:enlist 0f; fee_ccy:enlist `USDT; fill_id:enlist id; book:enlist `; product:enlist `; clock:enlist clk)}

/ A book snapshot as the plant carries it.
.qt.qpipe.job.crypto_markout.bk:{[st;sym;src;bid;ask;clk]
    ([] time:enlist st+0D00:00:00.1; sym:enlist sym; source:enlist src; market:enlist `crypto; source_time:enlist st;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1f; ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1f; clock:enlist clk; bid:enlist bid; ask:enlist ask)}

/ The worked fill of crypto_markout_outputs.q, as the plant carries it.
.qt.qpipe.job.crypto_markout.buy:{[]
    update time:source_time+0D00:00:00.2, book:`, product:` from .qpipe.transform.crypto_markout_outputs.sample_executions[]}

/ The worked books, as the plant carries them, and one closing book per (pair, source) after the furthest
/ horizon quoting what that series last quoted: the reference covers an instant only once a book at or after
/ it has arrived, and readiness waits for every source with a book inside max_age of the horizon.
.qt.qpipe.job.crypto_markout.books:{[] update time:source_time+0D00:00:00.1, market:`crypto from .qpipe.transform.crypto_markout_outputs.sample_books[]}

.qt.qpipe.job.crypto_markout.close_after:{[b;t]
    last_of:select by sym, source from b;
    b,update time:t+0D00:00:00.1, source_time:t from 0!last_of}

.qt.qpipe.job.crypto_markout.covered:{[] .qt.qpipe.job.crypto_markout.close_after[.qt.qpipe.job.crypto_markout.books[];.qt.qpipe.job.crypto_markout.t+0D00:00:11]}

.qt.qpipe.job.crypto_markout.feed:{[tb;x] .qpipe.job.crypto_markout.on_batch[tb;x]}
.qt.qpipe.job.crypto_markout.tick:{[now] .qpipe.job.crypto_markout.score_ready now}
.qt.qpipe.job.crypto_markout.published:{[] raze {x 2} each .qt.qpipe.job.crypto_markout.sent}

/ What the output-contract suite (test_job_output_contracts.q) drives the job with: one fill, the
/ books around it, and the timer once its horizons have passed.
.qt.qpipe.job.crypto_markout.contract_driver:{[]
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.covered[]];
    .qt.qpipe.job.crypto_markout.feed[`executions;.qt.qpipe.job.crypto_markout.buy[]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:00:10]}

/ --- the declaration --------------------------------------------------------------

.qt.qpipe.job.crypto_markout.test_the_worked_example_is_the_fill_these_tests_drive:{[tc]
    .qunit.assertEquals[.qpipe.transform.crypto_markout_outputs.sample_executions[];select source_time, venue, sym, side, size, price, fee, fee_ccy, fill_id, clock from .qt.qpipe.job.crypto_markout.buy[];
        "one example, hand-worked in crypto_markout_outputs.q, is the fill every lifecycle test below scores"]};

.qt.qpipe.job.crypto_markout.test_the_job_is_declared_over_the_canonical_tables_and_publishes_the_dataset:{[tc]
    d:.qetl.job.stream.horizons.def `crypto_markout;
    .qunit.assertEquals[(d`events;d`reference;first d`outputs);(`executions;`market_data;`crypto_markout);"the two canonical tables in, the dataset out - never crypto_trades or crypto_book"];
    .qunit.assertEquals[(d`event_time;d`reference_time;d`ready_on);(`source_time;`source_time;`both);"source_time on both clocks, wall and reference readiness"];
    .qunit.assertEquals[(d`identity;d`retain_by;d`by);(`venue`fill_id;`sym`source;enlist `sym);"identity, per-venue retention, readiness keyed by pair"];
    .qunit.assertEquals[(d`horizon;d`lookback;d`max_age);(0D00:00:10;0D;.qf.mkt.reference_max_age);"the furthest horizon, no reach back, the library's staleness limit"];
    .qunit.assertEquals[d`procname;`crypto_markout1;"the process keeps its name"];
    .qunit.assertTrue[d[`expire_after]>d`horizon;"and a fill is given up on afterwards"]};

.qt.qpipe.job.crypto_markout.test_the_job_and_its_twin_run_one_transform_not_a_copy:{[tc]
    .qunit.assertEquals[(.qetl.job.stream.horizons.def `crypto_markout)`transform;`crypto_markout_score;"the live job's"];
    f:.qpipe.source.hdb_crypto_markouts.fixture[];
    live:.qetl.transform.apply[`crypto_markout_score;f];
    .qunit.assertEquals[delete time from .qetl.transform.apply[`hdb_crypto_markout_long;f];live;"the twin is the live transform plus the core's time"];
    .qunit.assertTrue[0<count live;"and it scored something"]};

.qt.qpipe.job.crypto_markout.test_the_retired_table_is_gone_and_the_dataset_conforms:{[tc]
    .qunit.assertFalse[`crypto_execution_quality in .qetl.plant.names[];"crypto_execution_quality is retired"];
    .qunit.assertTrue[`crypto_markout in .qetl.plant.names[];"and crypto_markout is on the plant"];
    .qunit.assertEquals[cols .qetl.plant.shape `crypto_markout;(cols .qpipe.transform.markout_core.result),`markout_bps`fee_quote`fee_bps`markout_net_bps;
        "the core's long result, then the crypto measures"]};

.qt.qpipe.job.crypto_markout.test_the_job_keeps_only_what_scoring_can_use:{[tc]
    k:.qpipe.job.crypto_markout.keep;
    .qunit.assertEquals[k ([] sym:`$("BTC-USDT";"EURUSD";"ETH-USDT"); source:`a`a`a);101b;"books of BASE-QUOTE pairs"];
    e:.qt.qpipe.job.crypto_markout.ex[.qt.qpipe.job.crypto_markout.t;.qt.qpipe.job.crypto_markout.t;`a;.qt.qpipe.job.crypto_markout.btc;1;62000f;`a1;`venue],
        .qt.qpipe.job.crypto_markout.ex[.qt.qpipe.job.crypto_markout.t;.qt.qpipe.job.crypto_markout.t;`a;`EURUSD;1;1.1;`a2;`receipt],
        .qt.qpipe.job.crypto_markout.ex[.qt.qpipe.job.crypto_markout.t;.qt.qpipe.job.crypto_markout.t;`a;.qt.qpipe.job.crypto_markout.btc;1;62000f;`a3;`],
        .qt.qpipe.job.crypto_markout.ex[0Np;.qt.qpipe.job.crypto_markout.t;`a;.qt.qpipe.job.crypto_markout.btc;1;62000f;`a4;`venue];
    .qunit.assertEquals[k e;1000b;"a crypto pair with an identity, a time and a clock; not FX, not a null clock, not a null time"]};

/ #2574: the resolver prices any BASE-QUOTE symbol, so an equity ticker with a
/ dash - XNAS `BF-B` - was kept and scored as a pair quoted in `B`.
.qt.qpipe.job.crypto_markout.test_an_equity_venues_dashed_ticker_is_not_crypto:{[tc]
    k:.qpipe.job.crypto_markout.keep;
    bfb:`$"BF-B";
    .qunit.assertEquals[k ([] sym:(bfb;bfb;.qt.qpipe.job.crypto_markout.btc); source:`XNAS`a`a);011b;"an XNAS book is equity, whatever its ticker"];
    e:.qt.qpipe.job.crypto_markout.ex[.qt.qpipe.job.crypto_markout.t;.qt.qpipe.job.crypto_markout.t;`XNAS;bfb;1;45f;`x1;`venue],
        .qt.qpipe.job.crypto_markout.ex[.qt.qpipe.job.crypto_markout.t;.qt.qpipe.job.crypto_markout.t;`a;.qt.qpipe.job.crypto_markout.btc;1;62000f;`a1;`venue];
    .qunit.assertEquals[k e;01b;"and so is an XNAS fill"];
    md:([] source_time:.qt.qpipe.job.crypto_markout.t+0D00:00:00 0D00:00:10; sym:2#bfb; source:2#`XNAS; bid:45 46f; ask:45.1 46.1; clock:2#`venue);
    .qunit.assertEquals[count .qpipe.transform.crypto_markout_outputs.rows[1#e;md];0;"scoring an XNAS fill directly produces no crypto rows"]};

/ --- readiness: the wall AND the reference ------------------------------------------

.qt.qpipe.job.crypto_markout.test_a_fill_waits_for_the_wall_though_the_books_cover_it:{[tc]
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.covered[]];
    .qt.qpipe.job.crypto_markout.feed[`executions;.qt.qpipe.job.crypto_markout.buy[]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:00:05];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_markout.sent;0;"the books reach past +10s, but only 5s have elapsed"];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:00:10];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_markout.published[];4;"scored the instant the furthest horizon has elapsed: two horizons, two components"]};

.qt.qpipe.job.crypto_markout.test_a_fill_waits_for_the_books_though_the_wall_has_elapsed:{[tc]
    edge:.qt.qpipe.job.crypto_markout.t+0D00:00:06;
    .qt.qpipe.job.crypto_markout.feed[`market_data;select from .qt.qpipe.job.crypto_markout.covered[] where source_time<edge];
    .qt.qpipe.job.crypto_markout.feed[`executions;.qt.qpipe.job.crypto_markout.buy[]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:04];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_markout.sent;0;"four minutes on the wall clock, but no book reaches +10s"];
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.bk[.qt.qpipe.job.crypto_markout.t+0D00:00:10.5;.qt.qpipe.job.crypto_markout.btc;`a;62010f;62020f;`venue]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:04];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_markout.published[];4;"scored once the books have advanced through the horizon"]};

.qt.qpipe.job.crypto_markout.test_maturity_is_the_venue_time_not_the_plant_receipt_time:{[tc]
    / received an hour late: still due 10s after the venue printed it
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.covered[]];
    .qt.qpipe.job.crypto_markout.feed[`executions;update time:time+0D01 from .qt.qpipe.job.crypto_markout.buy[]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:00:10];
    .qunit.assertEquals[count .qt.qpipe.job.crypto_markout.published[];4;"due at source_time + 10s, not at receipt + 10s"]};

.qt.qpipe.job.crypto_markout.test_a_pair_nobody_quotes_holds_the_fill_then_expires_it_with_the_reason:{[tc]
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.covered[]];
    .qt.qpipe.job.crypto_markout.feed[`executions;update sym:`$"SOL-USDT", fill_id:`s1 from .qt.qpipe.job.crypto_markout.buy[]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:01];
    .qunit.assertEquals[(count .qt.qpipe.job.crypto_markout.sent;count .qpipe.job.crypto_markout.pending);0 1;"waiting: never scored with its pair unquoted"];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:06];
    gone:.qpipe.job.crypto_markout.expired;
    .qunit.assertEquals[(count gone;count .qpipe.job.crypto_markout.pending;count .qt.qpipe.job.crypto_markout.sent);1 0 0;"given up on after expire_after, nothing published"];
    .qunit.assertEquals[first gone`reason;`$"no reference for SOL-USDT";"and the diagnostic names the pair"]};

.qt.qpipe.job.crypto_markout.test_an_fx_execution_never_enters_the_queue:{[tc]
    .qt.qpipe.job.crypto_markout.feed[`executions;.qt.qpipe.job.crypto_markout.ex[.qt.qpipe.job.crypto_markout.t;.qt.qpipe.job.crypto_markout.t;`fx;`EURUSD;1;1.1;`x1;`receipt]];
    .qunit.assertEquals[count .qpipe.job.crypto_markout.pending;0;"the executions table carries FX fills too, and the crypto domain cannot route them"]};

/ --- the rows ---------------------------------------------------------------------------

.qt.qpipe.job.crypto_markout.test_the_published_rows_are_the_hand_worked_ones:{[tc]
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.covered[]];
    .qt.qpipe.job.crypto_markout.feed[`executions;.qt.qpipe.job.crypto_markout.buy[]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:00:10];
    p:.qt.qpipe.job.crypto_markout.published[];
    .qunit.assertEquals[cols p;cols .qpipe.transform.crypto_markout_outputs.out;"the plant table without time, which the plant stamps"];
    .qunit.assertEquals[p;delete time from .qpipe.transform.crypto_markout_outputs.sample_rows[];"the worked rows: 62006 at 1s (+6), 62015 at 10s (+15)"]};

.qt.qpipe.job.crypto_markout.test_a_receipt_clock_fill_is_refused_against_venue_books:{[tc]
    / the same fill, stamped by this stack on arrival: comparable with no venue's book
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.covered[]];
    .qt.qpipe.job.crypto_markout.feed[`executions;update clock:`receipt from .qt.qpipe.job.crypto_markout.buy[]];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:00:10];
    p:.qt.qpipe.job.crypto_markout.published[];
    .qunit.assertEquals[(count p;distinct p`status);(4;enlist `clock_mismatch);"every row says why it is unpriced"];
    .qunit.assertTrue[all null p`markout_bps;"and carries no bps from two clocks"]};

.qt.qpipe.job.crypto_markout.test_a_recorder_fill_with_a_venue_time_is_scored_and_one_without_falls_back:{[tc]
    / end to end through the executions normalizer: crypto_trades rows as the plant delivers them. A null
    / source_time is the receipt clock (the plant's time), and the venue-clock books cannot price it.
    two:([] time:2#.qt.qpipe.job.crypto_markout.t+0D00:00:00.2; source_time:(.qt.qpipe.job.crypto_markout.t;0Np);
        sym:2#.qt.qpipe.job.crypto_markout.btc; venue:2#`a; side:2#1; trade_price:2#62000f; size:2#0.25; fee:2#15.5; fee_currency:2#`USDT;
        exchange_fill_id:`v1`r1);
    n:.qetl.job.stream.normalizer.normalize[`executions;`crypto_trades;two];
    .qunit.assertEquals[n`clock;`venue`receipt;"the normalizer's rule"];
    .qt.qpipe.job.crypto_markout.feed[`market_data;.qt.qpipe.job.crypto_markout.covered[]];
    .qt.qpipe.job.crypto_markout.feed[`executions;update time:source_time+0D00:00:00.2, book:`, product:` from n];
    .qt.qpipe.job.crypto_markout.tick[.qt.qpipe.job.crypto_markout.t+0D00:00:20];
    p:.qt.qpipe.job.crypto_markout.published[];
    .qunit.assertEquals[exec distinct status from p where fill_id=`v1;enlist `ok;"the venue-stamped fill is scored"];
    .qunit.assertEquals[exec distinct status from p where fill_id=`r1;enlist `clock_mismatch;"the receipt-stamped one is refused, not guessed at"]};

/ --- a live row is the row a refill writes ------------------------------------------------

.qt.qpipe.job.crypto_markout.key_order:{[t] `venue`fill_id`horizon`kind`step xasc t}

.qt.qpipe.job.crypto_markout.test_live_output_equals_backfill_output:{[tc]
    f:.qpipe.source.hdb_crypto_markouts.fixture[];
    books:update time:source_time+0D00:00:00.1 from f`market_data;
    / the reference covers an instant only once a later row has arrived: one closing book per (pair, source)
    all_books:.qt.qpipe.job.crypto_markout.close_after[books;2026.09.17D11:00:00];
    .qt.qpipe.job.crypto_markout.feed[`market_data;all_books];
    .qt.qpipe.job.crypto_markout.feed[`executions;update time:source_time+0D00:00:00.2, book:`, product:` from f`executions];
    .qt.qpipe.job.crypto_markout.tick[2026.09.17D11:00:30];
    live:.qt.qpipe.job.crypto_markout.key_order[.qt.qpipe.job.crypto_markout.published[]];
    twin:.qt.qpipe.job.crypto_markout.key_order[delete time from .qetl.transform.apply[`hdb_crypto_markout_long;f]];
    .qunit.assertTrue[0<count live;"the live job scored"];
    .qunit.assertEquals[live;twin;"identical rows: the same calculation, the same inputs"];
    .qunit.assertEquals[asc exec distinct fill_id from live;`f1`f2`f3;"the EURUSD fill is excluded by both"]};

/ --- the bps are the retired job's, on one clock ---------------------------------------------

/ Books for a pair on a venue, one a second from 10:00:00 for n seconds, a spread of ask less bid around the mid.
.qt.qpipe.job.crypto_markout.grid:{[sym;src;mid;bidoff;askoff;n]
    ([] source_time:2026.09.17D10:00:00+1000000000*til n; sym:n#sym; source:n#src;
        bid:`float$mid+bidoff; ask:`float$mid+askoff; clock:n#`venue)}

/ Three venues quoting two pairs: a and b live throughout, with tops that differ so the best bid and the
/ best ask come from different venues at different seconds; c quotes better than both for three seconds and
/ then goes silent, the trap a naive "latest across venues" falls into. 45 books a venue and pair.
.qt.qpipe.job.crypto_markout.market:{[]
    i:til 45;
    btc_walk:(11*i mod 7)-25*i mod 4;
    eth_walk:(3*i mod 5)-4*i mod 3;
    pair:{[g;sym;mid;i]
        a:g[sym;`a;mid;neg 4+i mod 2;4+i mod 3;45];
        b:g[sym;`b;mid;-3+i mod 3;5-i mod 2;45];
        c:g[sym;`c;3#mid;3#1;3#9;3];
        a,b,c}[.qt.qpipe.job.crypto_markout.grid];
    pair[`$"BTC-USDT";62000+btc_walk;i],pair[`$"ETH-USDT";3000+eth_walk;i]}

/ Eleven fills: ten over 24 seconds, alternating pair, venue and side, at prices near the mid, and a late one at
/ 10:00:40 whose 10s horizon lies past the last book (10:00:44) by more than max_age.
.qt.qpipe.job.crypto_markout.fills:{[]
    k:til 11;
    ([] source_time:2026.09.17D10:00:00+?[k<10;3000000000+2700000000*k;40000000000]; venue:(`a`b) (k mod 2); sym:(`$"BTC-USDT";`$"ETH-USDT") (k mod 2);
        side:1 -1 1 1 -1 -1 1 -1 1 1 1; size:0.1+0.05*k; price:?[0=k mod 2;62000f+3*k;3000f+2*k];
        fee:(count k)#0f; fee_ccy:(count k)#`USDT; fill_id:`$"f",/:string k; clock:(count k)#`venue)}

/ The retired job's figure: its transform restated from the same library calls - the venues' tops of book,
/ the best mid across them within max_age of fill time + horizon, side*10000*(ref-price)%price.
.qt.qpipe.job.crypto_markout.retired_bps:{[fl;books;hs]
    tob:([] time:books`source_time; sym:books`sym; venue:books`source; bid:books`bid; ask:books`ask);
    n:count fl;
    m:count hs;
    f:fl "j"$raze m#'til n;
    h:hs (n*m)#til m;
    ref:.qf.mkt.best_mid_across_venues[tob;([] sym:f`sym; time:(f`source_time)+h);.qf.mkt.reference_max_age];
    ([] fill_id:f`fill_id; horizon:h; ref:ref; bps:(f`side)*10000*(ref-f`price)%f`price)}

.qt.qpipe.job.crypto_markout.test_the_bps_match_the_retired_job_on_a_clock_consistent_fixture:{[tc]
    books:.qt.qpipe.job.crypto_markout.market[];
    fl:.qt.qpipe.job.crypto_markout.fills[];
    hs:(.qpipe.transform.crypto_markout_outputs.job_spec)`horizons;
    was:.qt.qpipe.job.crypto_markout.retired_bps[fl;books;hs];
    got:select fill_id, horizon, bps:markout_bps, ref:ref_t1 from .qetl.transform.apply[`hdb_crypto_markout_long;`executions`market_data!(fl;books)] where kind=`total;
    .qunit.assertEquals[(select fill_id, horizon from got);(select fill_id, horizon from was);"the same (fill, horizon) rows, in the same order"];
    .qunit.assertEquals[count got;22;"eleven fills at two horizons"];
    .qunit.assertTrue[(any not null was`bps) and any null was`bps;"the fixture has both priced rows and rows no venue was live for"];
    .qunit.assertEquals[null got`bps;null was`bps;"unpriced where the retired job was"];
    priced:not null was`bps;
    .qunit.assertTrue[all 1e-9>=abs ((got`bps) where priced)-(was`bps) where priced;"the same bps wherever it was priced"];
    .qunit.assertEquals[got`ref;was`ref;"from the same reference mid"]};

.qt.qpipe.job.crypto_markout.test_the_fixtures_dead_venue_would_have_changed_the_figure:{[tc]
    / the trap is real: with venue c's three books treated as live, the figure differs. The sign of a working
    / test is that it can fail.
    books:.qt.qpipe.job.crypto_markout.market[];
    fl:.qt.qpipe.job.crypto_markout.fills[];
    hs:(.qpipe.transform.crypto_markout_outputs.job_spec)`horizons;
    with_c:.qt.qpipe.job.crypto_markout.retired_bps[fl;books;hs];
    without_c:.qt.qpipe.job.crypto_markout.retired_bps[fl;select from books where source<>`c;hs];
    .qunit.assertTrue[any (with_c`bps)<>without_c`bps;"c's early books set the best price for the first fills"]};
