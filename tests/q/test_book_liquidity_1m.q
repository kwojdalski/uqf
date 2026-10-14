/ test_book_liquidity_job.q - the registered one-minute liquidity job and its backfill twin (#1397).
/ .
/ Drives the REAL job, book_liquidity_1m1 (.qpipe.job.book_liquidity_1m), through its
/ bars-kind handlers with a moved clock and a recorder for publish, so the shell and
/ the shipped calculation are tested together - and proves the job path calls the
/ extracted transforms (tests/q/test_book_liquidity.q has those on their own). Its
/ backfill twin is hdb_book_liquidity_1m_backfill.

\d .book_liquidity_1mtest

t0:2026.10.09D10:00:00

/ The clock the job reads, which the tests move.
clock:t0

sent:()

/ One market_data row, as the plant delivers it (time is the receipt stamp).
/ @param st the book's source_time
/ @param src provider
/ @param mkt market
/ @param bp bid prices
/ @param bs bid sizes
/ @param ap ask prices
/ @param ak ask sizes
upd:{[st;src;mkt;bp;bs;ap;ak]
    ([] time:enlist st+0D00:00:00.040; sym:enlist `EURUSD; source:enlist src; market:enlist mkt;
        source_time:enlist st; bid_prices:enlist bp; bid_sizes:enlist bs; ask_prices:enlist ap; ask_sizes:enlist ak)}

/ LP_A's reference book: 1m at 1.1000 then 2m at 1.0999 on the bid, 1m at 1.1001
/ then 2m at 1.1003 on the ask.
lp_a:{[st] upd[st;`LP_A;`fx;1.1000 1.0999;1e6 2e6;1.1001 1.1003;1e6 2e6]}

fresh:{[]
    .qetl.job.stream.reset `book_liquidity_1m;
    `.book_liquidity_1mtest.clock set t0;
    `.book_liquidity_1mtest.sent set ();
    stand_in_clock[];
    .qetl.job.stream.wire[`book_liquidity_1m;{[t;x] .book_liquidity_1mtest.sent,:enlist (t;x); count x}];}

feed:{[rows] .qpipe.job.book_liquidity_1m.on_batch[`market_data;rows]}
published:{[] raze last each sent}

saved:()

stand_in_clock:{[]
    if[not 100h=type saved; `.book_liquidity_1mtest.saved set .qpipe.job.book_liquidity_1m.now];
    .qpipe.job.book_liquidity_1m.now:{[] .book_liquidity_1mtest.clock}}

restore_clock:{[] if[100h=type saved; .qpipe.job.book_liquidity_1m.now:saved]}

tearDown_clock:{[] .book_liquidity_1mtest.restore_clock[]; .qetl.job.stream.reset `book_liquidity_1m}

/ What jobout drives it with: one FX book, then the day's end, which closes its window.
contract_driver:{[]
    .qetl.job.stream.reset `book_liquidity_1m;
    `.book_liquidity_1mtest.clock set t0;
    stand_in_clock[];
    feed[lp_a[t0+0D00:00:10]];
    .qpipe.job.book_liquidity_1m.on_endofday 2026.10.09;
    restore_clock[];
    }

test_a_bars_job_reads_the_process_clock_by_default:{[t]
    before:.z.p;
    at:.qpipe.job.book_liquidity_1m.now[];
    .qunit.assertTrue[at within (before;.z.p);"the kind's default clock is .z.p - .u.upd stamps rows in it"]};

test_the_declaration_is_the_one_minute_source_time_job:{[t]
    d:.qetl.job.stream.bars.def `book_liquidity_1m;
    .qunit.assertEquals[(d`width;d`event_time;d`by;d`input);(0D00:01:00;`source_time;`sym`source;`books);
        "one-minute windows on the book's own clock, per provider"];
    .qunit.assertEquals[.qetl.job.stream.def[`book_liquidity_1m]`subscribe_to;enlist `market_data;"reads market_data"];
    .qunit.assertEquals[.qetl.job.stream.def[`book_liquidity_1m]`publishes;enlist `book_liquidity_1m;"publishes book_liquidity_1m"];
    .qunit.assertEquals[.qetl.job.stream.def[`book_liquidity_1m]`start_with_all;0b;"on demand, not in start-all"]};

test_the_job_is_the_twins_stream_job_and_shares_its_key_and_calculation:{[t]
    .qunit.assertEquals[.qetl.uptime.twins `book_liquidity_1m;enlist `hdb_book_liquidity_1m_backfill;"the backfill refills it"];
    w:.qetl.job.bounded.def `hdb_book_liquidity_1m_backfill;
    .qunit.assertEquals[(w`dataset;w`window_column;w`align);(`book_liquidity_1m;`bar_start;0D00:01:00);"same table, same window, same width"];
    .qunit.assertEquals[w`target_key;`sym`source`settlement`category`req_size`bar_start;"one key"]};

test_a_known_book_publishes_its_hand_worked_minute:{[t]
    fresh[];
    feed[lp_a[t0+0D00:00:10]];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:01:05;
    b:`req_size xasc published[];
    .qunit.assertEquals[count b;3;"one row per requested size"];
    .qunit.assertEquals[b`req_size;1e6 5e6 10e6;"the declared sizes"];
    .qunit.assertEquals[(b`bar_start;b`snapshots);(3#t0;3#1);"one snapshot in the minute of its source_time"];
    .qunit.assertEquals[(b`settlement;b`category);(3#`unspecified;3#`unspecified);"market_data cannot say"];
    / 1m: the best level. 5m: the book holds 3m, so it is partial. 10m: likewise
    .testutil.assertApprox[b`bid_price;1.1000,2#(1.1000+2*1.0999)%3;1e-9;"bid prices: the best level, then the blend of everything the book holds"];
    .qunit.assertEquals[b`bid_qty;1e6 3e6 3e6;"quantity actually available"];
    .qunit.assertEquals[b`bid_full_ratio;1 0 0f;"only the 1m request is filled in full"];
    .qunit.assertEquals[b`ask_full_ratio;1 0 0f;"and on the ask side"]};

/ Record a call by name, then run the original.
spy1:{[orig;n;a] `.book_liquidity_1mtest.hits set .book_liquidity_1mtest.hits,n; orig a}
spy2:{[orig;n;a;b] `.book_liquidity_1mtest.hits set .book_liquidity_1mtest.hits,n; orig[a;b]}

test_the_job_path_invokes_the_extracted_transforms:{[t]
    fresh[];
    `.book_liquidity_1mtest.hits set `symbol$();
    o1:.qpipe.transform.book_liquidity.from_market_data;
    o2:.qpipe.transform.book_liquidity.snapshot;
    o3:.qpipe.transform.book_liquidity.minute;
    .qpipe.transform.book_liquidity.from_market_data:spy1[o1;`from_market_data;];
    .qpipe.transform.book_liquidity.snapshot:spy2[o2;`snapshot;;];
    .qpipe.transform.book_liquidity.minute:spy1[o3;`minute;];
    r:@[{feed[lp_a[t0+0D00:00:10]]; .qpipe.job.book_liquidity_1m.close_ready t0+0D00:01:05; 1b};::;{[e] e}];
    .qpipe.transform.book_liquidity.from_market_data:o1;
    .qpipe.transform.book_liquidity.snapshot:o2;
    .qpipe.transform.book_liquidity.minute:o3;
    .qunit.assertEquals[r;1b;"the job ran"];
    .qunit.assertEquals[.book_liquidity_1mtest.hits;`from_market_data`snapshot`minute;"the registered job's batch went through the market_data adapter, the snapshot and the minute average, in that order"];
    .qunit.assertTrue[0<count published[];"and published what they returned"]};

test_a_crypto_book_and_a_malformed_book_leave_no_row:{[t]
    fresh[];
    feed[upd[t0+0D00:00:10;`binance;`crypto;enlist 61999f;enlist 1f;enlist 62001f;enlist 1f]];
    feed[upd[t0+0D00:00:20;`LP_B;`fx;1.1 1.09;enlist 1e6;enlist 1.2;enlist 1e6]];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:01:05;
    .qunit.assertEquals[count published[];0;"neither is an eligible FX book"]};

test_two_providers_in_one_minute_are_two_rows_per_size:{[t]
    fresh[];
    feed[lp_a[t0+0D00:00:10]];
    feed[upd[t0+0D00:00:20;`LP_B;`fx;enlist 1.1000;enlist 9e6;enlist 1.1001;enlist 9e6]];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:01:05;
    b:published[];
    .qunit.assertEquals[count b;6;"two providers, three sizes"];
    .qunit.assertEquals[asc distinct b`source;`LP_A`LP_B;"never merged into one book"]};

test_a_book_exactly_on_the_minute_opens_the_next_window:{[t]
    fresh[];
    feed[lp_a[t0+0D00:00:59.999999999]];
    feed[lp_a[t0+0D00:01:00]];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:03:00;
    b:published[];
    .qunit.assertEquals[asc distinct b`bar_start;t0,t0+0D00:01:00;"[from;to): the boundary book belongs to the minute that starts there"]};

test_a_book_within_the_lateness_amends_its_minute:{[t]
    fresh[];
    feed[lp_a[t0+0D00:00:10]];
    `.book_liquidity_1mtest.clock set t0+0D00:01:03;
    feed[upd[t0+0D00:00:50;`LP_A;`fx;enlist 1.2000;enlist 1e6;enlist 1.2001;enlist 1e6]];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:01:05;
    b:`req_size xasc published[];
    .qunit.assertEquals[first b`snapshots;2;"the late book is in the average"];
    .qunit.assertEquals[count .qpipe.job.book_liquidity_1m.dropped;0;"nothing dropped"]};

test_a_book_after_its_minute_is_out_is_dropped_live_but_counted_by_the_backfill:{[t]
    fresh[];
    late:upd[t0+0D00:00:50;`LP_A;`fx;enlist 1.2000;enlist 1e6;enlist 1.2001;enlist 1e6];
    feed[lp_a[t0+0D00:00:10]];
    `.book_liquidity_1mtest.clock set t0+0D00:01:06;
    .qpipe.job.book_liquidity_1m.close_ready clock;
    feed[late];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:09:00;
    live:`req_size xasc published[];
    .qunit.assertEquals[first live`snapshots;1;"the published minute stands without it"];
    d:.qpipe.job.book_liquidity_1m.dropped;
    .qunit.assertEquals[(count d;first d`reason);(1;`late);"and the late book is in the dropped ledger"];
    / the backfill reads the whole window, so it counts the book the live job dropped
    both:lp_a[t0+0D00:00:10],late;
    redone:`req_size xasc twin[both];
    .qunit.assertEquals[first redone`snapshots;2;"the backfill replaces the live minute with one that includes it"]};

/ The twin's calculation on market_data rows: the columns its source reads, and no bar_start.
twin:{[rows]
    r:.qetl.transform.apply[`hdb_book_liquidity_1m;enlist[`books]!enlist `source_time`sym`source`market`bid_prices`bid_sizes`ask_prices`ask_sizes#rows];
    (cols .qetl.plant.published `book_liquidity_1m) # r}

test_streaming_and_backfill_agree_on_the_same_books_and_windows:{[t]
    fresh[];
    rows:raze (lp_a[t0+0D00:00:10];
        upd[t0+0D00:00:20;`LP_B;`fx;1.1005 1.1004;3e6 3e6;1.1007 1.1008;3e6 3e6];
        upd[t0+0D00:00:35;`LP_A;`fx;enlist 1.0998;enlist 5e5;`float$();`float$()];
        lp_a[t0+0D00:01:10];
        upd[t0+0D00:01:20;`LP_B;`fx;`float$();`float$();`float$();`float$()]);
    / the stream sees them in two arrival orders; the backfill sees them whole
    feed[rows];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:09:00;
    live:`bar_start`sym`source`req_size xasc published[];
    redone:`bar_start`sym`source`req_size xasc twin[rows];
    .qunit.assertTrue[0<count live;"there is something to compare"];
    .qunit.assertEquals[count live;count redone;"the same rows"];
    .qunit.assertEquals[0;count .qetl.transform.differences[redone;live];"the same numbers, within the transform float tolerance"];
    fresh[];
    feed[reverse rows];
    .qpipe.job.book_liquidity_1m.close_ready t0+0D00:09:00;
    .qunit.assertEquals[0;count .qetl.transform.differences[redone;`bar_start`sym`source`req_size xasc published[]];"and in any arrival order"]};

test_the_transform_output_is_the_published_plant_shape:{[t]
    out:.qetl.transform.apply[`book_liquidity_1m;first[(.qetl.transform.def `book_liquidity_1m)`examples]`inputs];
    .qunit.assertEquals[0#out;.qetl.plant.published `book_liquidity_1m;"the transform's output is the plant table, minus time"]};

\d .
