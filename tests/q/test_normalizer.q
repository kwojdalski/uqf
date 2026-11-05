// test_normalizer.q - tests for src/etl/core/normalizer.q. Load the ETL tree
// (src/etl/init.q), tests/lib/qunit.q and tests/lib/support.q before this
// file: the executions and market_data normalizers it exercises register as their
// files load.


/ A canonical table of this suite's own, and a mapping onto it, so the
/ shell's refusals can be tested without touching the real normalizers.
.qt.qetl.job.stream.normalizer.canon:([] a:`long$(); b:`symbol$())

/ Register a transform this suite controls. Re-registering replaces, so a
/ test that wants a different shape for the same name just defines again.
.qt.qetl.job.stream.normalizer.mk_xf:{[name;output;fn]
    .qetl.transform.define[name;`inputs`output`fn`examples!(
        (enlist `src)!enlist ([] time:`timestamp$(); x:`long$(); y:`symbol$());
        output;
        fn;
        enlist `inputs`expected!(
            (enlist `src)!enlist ([] time:enlist 2026.01.01D09:00:00; x:enlist 1; y:enlist `p);
            fn ([] time:enlist 2026.01.01D09:00:00; x:enlist 1; y:enlist `p)))]}

.qt.qetl.job.stream.normalizer.good:{[batch] select a:x, b:y from batch}

/ .qetl.job.stream.define refuses a second job on one procname, and a test that
/ defines the same normalizer twice would trip that - so each test uses a
/ name and procname of its own, and forgets them afterwards.
.qt.qetl.job.stream.normalizer.forget:{[name]
    `.qetl.job.stream.normalizer.registry set (enlist name) _ .qetl.job.stream.normalizer.registry;
    `.qetl.job.stream.jobs set (enlist name) _ .qetl.job.stream.jobs;
    pn:key[.qetl.job.stream.procnames] where name=value .qetl.job.stream.procnames;
    `.qetl.job.stream.procnames set pn _ .qetl.job.stream.procnames;
    }

.qt.qetl.job.stream.normalizer.define_ok:{[name;pn]
    .qt.qetl.job.stream.normalizer.mk_xf[`normtest_good;.qt.qetl.job.stream.normalizer.canon;.qt.qetl.job.stream.normalizer.good];
    (` sv `.qpipe.job,name,`publish) set .qetl.job.stream.unwired name;
    .qetl.job.stream.normalize[name;`procname`output`input!(pn;.qt.qetl.job.stream.normalizer.canon;(enlist `src)!enlist `normtest_good)]}

/ ------------------------------------------------------------- DEFINING

.qt.qetl.job.stream.normalizer.test_define_registers_the_job_with_its_edges_derived:{[t]
    .qt.qetl.job.stream.normalizer.define_ok[`nt_a;`nta1];
    d:.qetl.job.stream.def `nt_a;
    .qunit.assertEquals[(d`subscribe_to;d`outputs);(enlist `src;enlist `nt_a);
        "subscribe_to is the source list and publishes is the normalizer's own name - an instance cannot declare them differently from its mappings"];
    .qunit.assertTrue[`nt_a in .qetl.job.stream.normalizer.defined[];"and it is a defined normalizer"];
    .qt.qetl.job.stream.normalizer.forget[`nt_a]};

.qt.qetl.job.stream.normalizer.test_a_missing_key_is_named:{[t]
    .qunit.assertThrows[.qetl.job.stream.normalize[`nt_b;];`procname`output!(`ntb1;.qt.qetl.job.stream.normalizer.canon);
        "*is missing input*";"a normalizer with no sources is refused by the key it lacks"]};

.qt.qetl.job.stream.normalizer.test_an_output_with_time_is_refused:{[t]
    .qunit.assertThrows[.qetl.job.stream.normalize[`nt_c;];
        `procname`output`input!(`ntc1;([] time:`timestamp$(); a:`long$());(enlist `src)!enlist `normtest_good);
        "*carries `time`*";"the plant stamps time; a source's own stamp is a column named for what it is"]};

.qt.qetl.job.stream.normalizer.test_an_unregistered_transform_is_refused:{[t]
    .qunit.assertThrows[.qetl.job.stream.normalize[`nt_d;];
        `procname`output`input!(`ntd1;.qt.qetl.job.stream.normalizer.canon;(enlist `src)!enlist `normtest_nonesuch);
        "*is not registered*";"a mapping is a declared transform, so its examples are verified"]};

.qt.qetl.job.stream.normalizer.test_a_mapping_whose_output_drifts_is_refused:{[t]
    / The check this kind exists for. Same columns in the wrong order is
    / drift too: the output is published positionally.
    .qt.qetl.job.stream.normalizer.mk_xf[`normtest_drift;([] b:`symbol$(); a:`long$());{[batch] select b:y, a:x from batch}];
    .qunit.assertThrows[.qetl.job.stream.normalize[`nt_e;];
        `procname`output`input!(`nte1;.qt.qetl.job.stream.normalizer.canon;(enlist `src)!enlist `normtest_drift);
        "*does not produce the canonical table*";"a mapping that emits the columns in another order lands as a misaligned table"];
    .qt.qetl.job.stream.normalizer.mk_xf[`normtest_wider;([] a:`long$(); b:`symbol$(); c:`float$());{[batch] select a:x, b:y, c:0f from batch}];
    .qunit.assertThrows[.qetl.job.stream.normalize[`nt_f;];
        `procname`output`input!(`ntf1;.qt.qetl.job.stream.normalizer.canon;(enlist `src)!enlist `normtest_wider);
        "*unexpected column*";"and one that emits an extra column is named for it"]};

.qt.qetl.job.stream.normalizer.test_a_mapping_with_two_inputs_is_refused:{[t]
    .qetl.transform.define[`normtest_two;`inputs`output`fn`examples!(
        `p`q!(([] x:`long$());([] y:`long$()));
        ([] a:`long$(); b:`symbol$());
        {[p;q] ([] a:p`x; b:(count p)#`z)};
        enlist `inputs`expected!(`p`q!(([] x:enlist 1);([] y:enlist 2));([] a:enlist 1; b:enlist `z)))];
    .qunit.assertThrows[.qetl.job.stream.normalize[`nt_g;];
        `procname`output`input!(`ntg1;.qt.qetl.job.stream.normalizer.canon;(enlist `src)!enlist `normtest_two);
        "*takes 2 inputs*";"a mapping reads one source"]};

/ ---------------------------------------------------------- NORMALIZING

.qt.qetl.job.stream.normalizer.test_normalize_projects_the_batch_onto_what_the_mapping_declares:{[t]
    .qt.qetl.job.stream.normalizer.define_ok[`nt_h;`nth1];
    / The wire carries more than the mapping reads: an attribute, an extra
    / column the plant or a sibling added. The mapping never sees them.
    batch:([] time:enlist 2026.01.01D09:00:00; x:enlist 7; y:`g#enlist `k; extra:enlist 1f);
    .qunit.assertEquals[.qetl.job.stream.normalizer.normalize[`nt_h;`src;batch];([] a:enlist 7; b:enlist `k);
        "the canonical rows, from the declared columns only"];
    .qt.qetl.job.stream.normalizer.forget[`nt_h]};

.qt.qetl.job.stream.normalizer.test_normalize_names_a_column_the_batch_lacks:{[t]
    .qt.qetl.job.stream.normalizer.define_ok[`nt_i;`nti1];
    .qunit.assertThrows[.qetl.job.stream.normalizer.normalize[`nt_i;`src;];([] time:enlist 2026.01.01D09:00:00; x:enlist 7);
        "*missing required column(s) y*";"a batch without a declared column is refused by name, not silently mapped"];
    .qt.qetl.job.stream.normalizer.forget[`nt_i]};

.qt.qetl.job.stream.normalizer.test_normalize_refuses_a_table_that_is_not_a_source:{[t]
    .qt.qetl.job.stream.normalizer.define_ok[`nt_j;`ntj1];
    .qunit.assertThrows[.qetl.job.stream.normalizer.normalize[`nt_j;`other;];([] x:enlist 1);
        "*is not a source of nt_j*";"asking to normalize the wrong table is a wiring bug"];
    .qt.qetl.job.stream.normalizer.forget[`nt_j]};

.qt.qetl.job.stream.normalizer.test_an_undefined_normalizer_is_named:{[t]
    .qunit.assertThrows[.qetl.job.stream.normalizer.def;`nonesuch;"*is not a defined normalizer*";
        "looking one up that was never defined says so"]};

/ ----------------------------------------------------------- DISPATCHING

.qt.qetl.job.stream.normalizer.test_dispatch_publishes_through_the_instances_own_seam:{[t]
    .qt.qetl.job.stream.normalizer.define_ok[`nt_k;`ntk1];
    `.qt.qetl.job.stream.normalizer.got set ();
    .qetl.job.stream.wire[`nt_k;{[t;x] `.qt.qetl.job.stream.normalizer.got set .qt.qetl.job.stream.normalizer.got,enlist (t;x); count x}];
    .qetl.job.stream.normalizer.dispatch[`nt_k;`src;([] time:enlist 2026.01.01D09:00:00; x:enlist 3; y:enlist `m)];
    .qunit.assertEquals[.qt.qetl.job.stream.normalizer.got;enlist (`nt_k;([] a:enlist 3; b:enlist `m));
        "one publication, onto the canonical table, of the normalized rows"];
    .qt.qetl.job.stream.normalizer.forget[`nt_k]};

.qt.qetl.job.stream.normalizer.test_dispatch_publishes_nothing_for_an_empty_batch:{[t]
    .qt.qetl.job.stream.normalizer.define_ok[`nt_l;`ntl1];
    `.qt.qetl.job.stream.normalizer.got set ();
    .qetl.job.stream.wire[`nt_l;{[t;x] `.qt.qetl.job.stream.normalizer.got set .qt.qetl.job.stream.normalizer.got,enlist (t;x); count x}];
    .qetl.job.stream.normalizer.dispatch[`nt_l;`src;0#([] time:`timestamp$(); x:`long$(); y:`symbol$())];
    .qetl.job.stream.normalizer.dispatch[`nt_l;`not_a_source;([] x:enlist 1)];
    .qunit.assertEmpty[.qt.qetl.job.stream.normalizer.got;"an empty batch, and a batch on a foreign table, publish nothing"];
    .qt.qetl.job.stream.normalizer.forget[`nt_l]};

/ ------------------------------------------------------ THE REAL ONES

.qt.qetl.job.stream.normalizer.test_the_shipped_normalizers_are_defined_and_registered:{[t]
    .qunit.assertEquals[`executions`market_data in .qetl.job.stream.normalizer.defined[];11b;"executions and market_data are defined"];
    .qunit.assertEquals[.qetl.job.stream.def[`executions]`subscribe_to;`trades`crypto_trades`orders;
        "executions reads every fill table: FX trades, crypto fills and the desk's orders (#885)"];
    .qunit.assertEquals[.qetl.job.stream.def[`market_data]`subscribe_to;`quote`fx_orderbook`crypto_book;
        "market_data reads every book: the two FX ones and crypto"];
    .qunit.assertEquals[.qetl.dag.kinds;`bounded`continuous`stream`reaction`normalizer;
        "and normalizer is a kind the job graph knows"]};

.qt.qetl.job.stream.normalizer.test_a_crypto_book_carries_the_venues_clock_not_the_plants:{[t]
    / The two clocks made to disagree by four hours, so reading the wrong
    / one cannot pass. The retired marks normalizer published the PLANT's
    / `time` as `source_time` until 2026-09-25: for a live row the two are
    / milliseconds apart and it was invisible, but a replayed or backfilled
    / row would assert the venue quoted that book whenever the plant happened
    / to receive it - and posbook marks positions to it.
    row:([] time:enlist 2026.09.17D14:32:09.000000000;
        source_time:enlist 2026.09.17D10:00:01.000000000;
        venue:enlist `binance_spot; sym:enlist `$"BTC-USDT";
        bid_prices:enlist 61999 61998 61997f; bid_sizes:enlist 3#0.5;
        ask_prices:enlist 62001 62002 62003f; ask_sizes:enlist 3#0.5);
    m:.qpipe.job.market_data.from_crypto_book row;
    .qunit.assertEquals[first m`source_time;2026.09.17D10:00:01.000000000;
        "the venue's stamp, not the plant's 14:32:09"];
    .qunit.assertEquals[first m`source;`binance_spot;"the venue is the source"];
    / As the plant delivers it, `time` stamped in front.
    b:first .qf.mkt.venue_tops update time:2026.09.17D14:32:09.000000000 from m;
    .qunit.assertEquals[b`venue`bid`ask;(`binance_spot;61999f;62001f);
        "and posbook reads the venue's touch, to mark across venues"]};

.qt.qetl.job.stream.normalizer.test_market_data_never_carries_a_time_of_its_own:{[t]
    .qunit.assertEquals[`time in cols .qpipe.job.market_data.market_data;0b;
        "the plant stamps `time` on what market_data publishes - sending one makes the message a column too wide"]};

/ --- whose clock source_time is (#1925) --------------------------------------

/ One batch per shipped mapping, in the shape the plant delivers it. The
/ receipt-stamped sources carry only `time`; crypto_book and crypto_trades also carry
/ the venue's own source_time, four hours earlier so that reading the wrong one cannot pass.
.qt.qetl.job.stream.normalizer.clock_batches:{[]
    t:2026.09.17D10:00:00.000000000;
    lad:([] bid_prices:enlist 1.1 1.09; bid_sizes:enlist 1000 2000f; ask_prices:enlist 1.2 1.21; ask_sizes:enlist 3000 4000f);
    `trades`crypto_trades`orders`quote`fx_orderbook`crypto_book!(
        ([] time:enlist t; sym:enlist `EURUSD; side:enlist 1; trade_price:enlist 1.085; size:enlist 1e6; pip_factor:enlist 10000; fill_id:enlist `f1);
        ([] time:enlist t+0D04; source_time:enlist t; sym:enlist `$"BTC-USDT"; venue:enlist `binance_spot; side:enlist -1; trade_price:enlist 62000f; size:enlist 0.25; fee:enlist 15.5; fee_currency:enlist `USDT; exchange_fill_id:enlist `x1);
        ([] time:enlist t; order_id:enlist 1; sym:enlist `EURUSD; book:enlist `london; product:enlist `spot; side:enlist 1; size:enlist 1e6; price:enlist 1.085; order_status:enlist `filled);
        ([] time:enlist t; sym:enlist `EURUSD; bid:enlist 1.1; ask:enlist 1.2; bsize:enlist 1000; asize:enlist 2000; src:enlist `LP_A);
        ([] time:enlist t; sym:enlist `EURUSD),'lad;
        ([] time:enlist t+0D04; source_time:enlist t; venue:enlist `binance_spot; sym:enlist `$"BTC-USDT"),'lad)}

.qt.qetl.job.stream.normalizer.test_every_mapping_stamps_whose_clock_its_source_time_is:{[t]
    / Confirmed from each mapping's source table: trades, orders, quote and
    / fx_orderbook carry only the plant's receipt `time`, so their source_time is
    / receipt; crypto_book and crypto_trades (#1936) carry the venue's own source_time.
    b:.qt.qetl.job.stream.normalizer.clock_batches[];
    owner:`trades`crypto_trades`orders`quote`fx_orderbook`crypto_book!`executions`executions`executions`market_data`market_data`market_data;
    want:`trades`crypto_trades`orders`quote`fx_orderbook`crypto_book!`receipt`venue`receipt`receipt`receipt`venue;
    got:{[b;owner;src] first .qetl.job.stream.normalizer.normalize[owner src;src;b src]`clock}[b;owner] each key want;
    .qunit.assertEquals[got;value want;"each mapping's clock: trades, crypto_trades, orders, quote, fx_orderbook, crypto_book"]};

.qt.qetl.job.stream.normalizer.test_a_crypto_fill_without_a_venue_time_falls_back_to_receipt:{[t]
    / The rule (#1936), row by row: a fill with the venue's time keeps it on the
    / venue clock; one with a null source_time (an old partition replayed, an
    / older recorder, a venue that gave no time) is the plant's `time` on the receipt clock.
    b:.qt.qetl.job.stream.normalizer.clock_batches[]`crypto_trades;
    two:b,update source_time:0Np, exchange_fill_id:`x2 from b;
    r:.qetl.job.stream.normalizer.normalize[`executions;`crypto_trades;two];
    .qunit.assertEquals[r`clock;`venue`receipt;"the stamped row is venue, the null one receipt"];
    .qunit.assertEquals[r`source_time;(first b`source_time),first b`time;"venue time where there is one, else the plant's time"]};

.qt.qetl.job.stream.normalizer.test_the_checks_refuse_a_row_with_no_clock_or_an_unknown_one:{[t]
    ex:.qetl.job.stream.normalizer.normalize[`executions;`trades;.qt.qetl.job.stream.normalizer.clock_batches[]`trades];
    md:.qetl.job.stream.normalizer.normalize[`market_data;`quote;.qt.qetl.job.stream.normalizer.clock_batches[]`quote];
    .qunit.assertEquals[count .qpipe.job.executions.check ex;0;"a stamped execution passes"];
    .qunit.assertEquals[count .qpipe.job.market_data.check md;0;"a stamped book passes"];
    .qunit.assertThrows[.qpipe.job.executions.check;update clock:` from ex;
        "clock: executions has 1 row(s) whose clock is not `venue or `receipt (null, or `unknown) - first: null; a mapping must stamp whose clock source_time is";
        "executions: a null clock"];
    .qunit.assertThrows[.qpipe.job.market_data.check;update clock:` from md;
        "clock: market_data has 1 row(s) whose clock is not `venue or `receipt (null, or `unknown) - first: null; a mapping must stamp whose clock source_time is";
        "market_data: a null clock"];
    .qunit.assertThrows[.qpipe.job.executions.check;update clock:`unknown from ex;
        "clock: executions has 1 row(s) whose clock is not `venue or `receipt (null, or `unknown) - first: unknown; a mapping must stamp whose clock source_time is";
        "the history fill's `unknown is refused"];
    .qunit.assertThrows[.qpipe.job.market_data.check;update clock:`unknown from md;
        "clock: market_data has 1 row(s) whose clock is not `venue or `receipt (null, or `unknown) - first: unknown; a mapping must stamp whose clock source_time is";
        "the history fill's `unknown is refused"];
    .qunit.assertThrows[.qetl.plant.require_clock[`executions];delete clock from ex;
        "clock: executions rows carry no clock column - every mapping must stamp whose clock source_time is";
        "a table with no clock column at all"]};

.qt.qetl.job.stream.normalizer.test_a_batch_with_a_null_clock_is_withheld_whole_by_the_published_job:{[t]
    / The check THROWS, and .qetl.job.stream.checked treats a throwing check as a
    / failure of the whole batch: nothing reaches the publisher.
    good:.qetl.job.stream.normalizer.normalize[`executions;`trades;.qt.qetl.job.stream.normalizer.clock_batches[]`trades];
    pub:{[t;x] `.qt.qetl.job.stream.normalizer.seen set (.qt.qetl.job.stream.normalizer.seen,count x)};
    `.qt.qetl.job.stream.normalizer.seen set ();
    guarded:.qetl.job.stream.checked[`executions;.qpipe.job.executions.check;`hold;pub;`executions;];
    guarded good;
    guarded update clock:` from good;
    .qunit.assertEquals[.qt.qetl.job.stream.normalizer.seen;enlist 1;"the stamped batch published, the unstamped one did not"]};

.qt.qetl.job.stream.normalizer.test_every_shipped_mapping_verifies:{[t]
    / The examples in executions.q and market_data.q, run - test_transform.q
    / does this for every transform too, but a reader of THIS file should see
    / the five mappings pass here.
    xfs:raze value each (.qetl.job.stream.normalizer.def[`executions]`input;.qetl.job.stream.normalizer.def[`market_data]`input);
    failed:select from raze .qetl.transform.verify each xfs where not passed;
    .qunit.assertEmpty[failed;"every mapping's examples produce what they say"]};
