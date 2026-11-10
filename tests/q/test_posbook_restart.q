/ test_posbook_restart.q - posbook after a restart marks as the running job does (#2460).
/ .
/ A running posbook marks a fill to the last mid it holds for the sym, however
/ old: the FX mark has no age limit. A restart replays the plant's log for the
/ current day alone, so a fill before its sym's first book of the day lost that
/ mid and was marked at its own price. The restarted job now seeds the history
/ from the HDB (posbook.q seed_mids, .qetl.mapped.last_tops), and these tests hold
/ the restarted job to the running one's answer.

/ The jobs this suite resets are process-global; leave the registry as found (#1375).
.qt.qpipe.job.posbook.beforeNamespace_save_jobs:{[] .qt.support.save_stream_jobs[`.qt.qpipe.job.posbook]}
.qt.qpipe.job.posbook.afterNamespace_restore_jobs:{[] .qt.support.restore_stream_jobs[`.qt.qpipe.job.posbook]}

/ The end of day D, and what was published by the job under test.
.qt.qpipe.job.posbook.d0:2026.10.09D23:59:00
.qt.qpipe.job.posbook.sent:()

.qt.qpipe.job.posbook.setUp_posbook:{[]
    .qetl.job.stream.reset `posbook;
    `.qt.qpipe.job.posbook.sent set ();
    .qetl.job.stream.wire[`posbook;{[t;x] .qt.qpipe.job.posbook.sent,:enlist (t;x); count x}];
    }
.qt.qpipe.job.posbook.tearDown_posbook:{[]
    .qetl.job.stream.reset `posbook;
    `.qetl.job.stream.replaying set 0b;
    `.qetl.job.stream.hdb_handle set {[] 0Ni};
    if[`market_data in key `.; ![`.;();0b;enlist `market_data]];
    }

/ A market_data batch of one FX book, as the plant delivers it.
.qt.qpipe.job.posbook.book:{[st;bid;ask]
    ([] time:enlist st; sym:enlist `EURUSD; market:enlist `fx; source:enlist `lp; source_time:enlist st;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1e6; ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6;
        clock:enlist `receipt; bid:enlist bid; ask:enlist ask)}

/ An executions batch of one buy of 1mm EURUSD at px.
.qt.qpipe.job.posbook.fill:{[st;px]
    ([] time:enlist st; source_time:enlist st; sym:enlist `EURUSD; venue:enlist `fx; side:enlist 1;
        size:enlist 1e6; price:enlist px; fee:enlist 0f; fee_ccy:enlist `; fill_id:enlist `f1)}

/ The position rows published so far.
.qt.qpipe.job.posbook.positions:{[]
    raze {[p] p 1} each .qt.qpipe.job.posbook.sent where `position=first each .qt.qpipe.job.posbook.sent}

/ Stand `rows` in for the HDB's market_data, reached through a handle that runs in this process.
.qt.qpipe.job.posbook.stand_in_hdb:{[rows]
    `market_data set .qt.support.stand_in_market_data rows;
    `.qetl.job.stream.hdb_handle set {[] {value x}};
    }

/ Day D's last book as the HDB holds it: EURUSD 1.1000/1.1002 at 23:59:00, so mid 1.1001.
.qt.qpipe.job.posbook.hdb_rows:{[]
    ([] sym:`EURUSD`EURUSD`BTCUSD; source:`lp`lp`bn; market:`fx`fx`crypto;
        source_time:2026.10.09D23:30:00 2026.10.09D23:59:00 2026.10.09D23:59:30;
        bid:1.0990 1.1000 61000f; ask:1.0992 1.1002 61002f; clock:3#`receipt)}

/ The restart: the replay of D+1's log (the snapshot only, nothing marked yet),
/ the seed from the HDB the runner does after it, and the fill.
.qt.qpipe.job.posbook.restart_then:{[fill]
    .qetl.job.stream.reset `posbook;
    .qetl.job.stream.wire[`posbook;{[t;x] .qt.qpipe.job.posbook.sent,:enlist (t;x); count x}];
    `.qt.qpipe.job.posbook.sent set ();
    .qpipe.job.posbook.seed_mids 2026.10.10D00:00:00;
    .qpipe.job.posbook.on_batch[`executions;fill]}

.qt.qpipe.job.posbook.test_a_restarted_posbook_marks_a_fill_before_the_first_book_as_the_running_one_does:{[t]
    fill:.qt.qpipe.job.posbook.fill[2026.10.10D00:01:30;1.1050];
    .qpipe.job.posbook.on_batch[`market_data;.qt.qpipe.job.posbook.book[.qt.qpipe.job.posbook.d0;1.1000;1.1002]];
    .qpipe.job.posbook.on_batch[`executions;fill];
    running:.qt.qpipe.job.posbook.positions[];
    .qt.qpipe.job.posbook.stand_in_hdb .qt.qpipe.job.posbook.hdb_rows[];
    .qt.qpipe.job.posbook.restart_then fill;
    restarted:.qt.qpipe.job.posbook.positions[];
    .qunit.assertEquals[first running`mark_price;1.1001;"the running job marks to day D's last mid"];
    .qunit.assertEquals[restarted;running;"the restarted job publishes the same position row"];
    .qunit.assertEquals[first restarted`unrealized_pnl;-4900f;"1mm bought at 1.1050 against 1.1001"]};

.qt.qpipe.job.posbook.test_a_book_of_today_overrides_the_seed_as_it_does_in_the_running_job:{[t]
    .qt.qpipe.job.posbook.stand_in_hdb .qt.qpipe.job.posbook.hdb_rows[];
    .qetl.job.stream.reset `posbook;
    .qetl.job.stream.wire[`posbook;{[t;x] .qt.qpipe.job.posbook.sent,:enlist (t;x); count x}];
    `.qt.qpipe.job.posbook.sent set ();
    .qpipe.job.posbook.on_batch[`market_data;.qt.qpipe.job.posbook.book[2026.10.10D00:00:10;1.2000;1.2002]];
    .qpipe.job.posbook.seed_mids 2026.10.10D00:00:00;
    / before today's first book: the seed; at and after it: today's book
    .qpipe.job.posbook.on_batch[`executions;.qt.qpipe.job.posbook.fill[2026.10.10D00:00:05;1.15]];
    .qpipe.job.posbook.on_batch[`executions;.qt.qpipe.job.posbook.fill[2026.10.10D00:00:20;1.15]];
    .qunit.assertEquals[(.qt.qpipe.job.posbook.positions[])`mark_price;1.1001 1.2001;"the fill ahead of today's first book marks to the seed, the one after it to today's"]};

.qt.qpipe.job.posbook.test_only_the_fx_book_with_both_sides_is_seeded:{[t]
    .qt.qpipe.job.posbook.stand_in_hdb .qt.qpipe.job.posbook.hdb_rows[];
    n:.qpipe.job.posbook.seed_mids 2026.10.10D00:00:00;
    .qunit.assertEquals[n;1;"one sym seeded: the crypto top is not an own-book mid"];
    .qunit.assertEquals[.qpipe.job.posbook.mid_history[`rows]`sym;enlist `EURUSD;"only EURUSD is in the history"];
    .qunit.assertEquals[.qpipe.job.posbook.mid_history[`rows]`source_time;enlist 2026.10.09D23:59:00;"at the book's own time"]};

.qt.qpipe.job.posbook.test_a_process_with_no_hdb_restarts_as_it_did:{[t]
    `.qetl.job.stream.hdb_handle set {[] 0Ni};
    n:.qpipe.job.posbook.seed_mids 2026.10.10D00:00:00;
    .qunit.assertEquals[n;0;"nothing to seed from"];
    .qpipe.job.posbook.on_batch[`executions;.qt.qpipe.job.posbook.fill[2026.10.10D00:01:30;1.1050]];
    .qunit.assertEquals[first (.qt.qpipe.job.posbook.positions[])`mark_price;1.105;"the fill-price fallback, as before the seed"]};

.qt.qpipe.job.posbook.test_the_declaration_seeds_after_the_replay:{[t]
    .qunit.assertEquals[.qetl.job.stream.def[`posbook][`on_replayed];.qpipe.job.posbook.on_replayed;"posbook's on_replayed is the seed"]};

/ --- the HDB side: .qetl.mapped.last_tops ---------------------------------

.qt.qpipe.job.posbook.test_last_tops_is_the_latest_book_of_each_sym_and_source_before_the_instant:{[t]
    rows:([] sym:`EURUSD`EURUSD`EURUSD`EURUSD`GBPUSD; source:`lp`lp`lp`ox`lp; market:5#`fx;
        source_time:2026.10.08D10:00:00 2026.10.09D12:00:00 2026.10.10D00:00:05 2026.10.09D13:00:00 2026.10.07D09:00:00;
        bid:1 2 3 4 5f; ask:2 3 4 5 6f; clock:5#`receipt);
    got:.qt.qpipe.job.posbook.last_tops[rows;2026.10.10D00:00:00;7D];
    .qunit.assertEquals[exec bid from `sym`source xasc got;2 4 5f;"EURUSD ox 4, EURUSD lp 2 (the 00:00:05 book is after the instant), GBPUSD lp 5"]};

.qt.qpipe.job.posbook.last_tops:{[rows;before;lookback]
    `market_data set .qt.support.stand_in_market_data rows;
    r:@[{.qetl.mapped.last_tops x};`before`read_from!(before;before-lookback);{[e] `threw,e}];
    ![`.;();0b;enlist `market_data];
    if[`threw~first r; 'last r];
    r}
