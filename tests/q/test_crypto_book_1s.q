/ test_crypto_book_1s.q - the one-second crypto book snapshot job (#1222).
/ .
/ Drives the REAL job, crypto_book_1s1 (.qpipe.job.crypto_book_1s), through its
/ bars-kind handlers with a moved clock and a recorder for publish, so the
/ shell and the shipped pick are tested together. Its backfill twin is in
/ test_hdb_crypto_book_backfill.q.

\d .crypto_book_1stest

t0:2026.10.09D10:00:05

/ The clock the job reads, which the tests move.
clock:t0

sent:()

/ One crypto_book row, as the plant delivers it (time is the receipt stamp).
/ @param st the venue's source_time
/ @param v venue
/ @param s sym
/ @param bid a bid price, the ladder's top
upd:{[st;v;s;bid]
    ([] time:enlist st+0D00:00:00.040; source_time:enlist st; venue:enlist v; sym:enlist s;
        bid_prices:enlist bid-0 1f; bid_sizes:enlist 1 2f; ask_prices:enlist bid+10 11f; ask_sizes:enlist 3 4f)}

/ The job emptied, its clock stood in and publish recorded.
fresh:{[]
    .qetl.job.stream.reset `crypto_book_1s;
    `.crypto_book_1stest.clock set t0;
    `.crypto_book_1stest.sent set ();
    stand_in_clock[];
    .qetl.job.stream.wire[`crypto_book_1s;{[t;x] .crypto_book_1stest.sent,:enlist (t;x); count x}];}

feed:{[x] .qpipe.job.crypto_book_1s.on_batch[`crypto_book;x]}
published:{[] raze last each sent}

/ What jobout drives it with: one update, then the day's end, which closes its window.
contract_driver:{[]
    .qetl.job.stream.reset `crypto_book_1s;
    `.crypto_book_1stest.clock set t0;
    stand_in_clock[];
    feed[upd[t0+0D00:00:00.300;`a;`BTC;62000f]];
    .qpipe.job.crypto_book_1s.on_endofday 2026.10.09;
    restore_clock[];
    }

/ The job's own clock as declared, kept the first time a test stands one in so it
/ can be put back as it was (not redefined, so it stays the function the suite measures).
saved:()

stand_in_clock:{[]
    if[not 100h=type saved; `.crypto_book_1stest.saved set .qpipe.job.crypto_book_1s.now];
    .qpipe.job.crypto_book_1s.now:{[] .crypto_book_1stest.clock}}

restore_clock:{[] if[100h=type saved; .qpipe.job.crypto_book_1s.now:saved]}

tearDown_clock:{[] .crypto_book_1stest.restore_clock[]; .qetl.job.stream.reset `crypto_book_1s}

test_a_bars_job_reads_the_process_clock_by_default:{[t]
    before:.z.p;
    at:.qpipe.job.crypto_book_1s.now[];
    .qunit.assertTrue[at within (before;.z.p);"the kind's default clock is .z.p - .u.upd stamps rows in it"]};

test_the_declaration_is_the_one_second_source_time_job:{[t]
    d:.qetl.job.stream.bars.def `crypto_book_1s;
    .qunit.assertEquals[(d`width;d`event_time;d`by;d`input);(0D00:00:01;`source_time;`venue`sym;`book);
        "one-second windows on the venue's clock, per venue and sym"];
    .qunit.assertEquals[.qetl.job.stream.def[`crypto_book_1s]`subscribe_to;enlist `crypto_book;"reads crypto_book"];
    .qunit.assertEquals[.qetl.job.stream.def[`crypto_book_1s]`start_with_all;0b;"on demand"]};

test_out_of_order_updates_in_one_second_keep_the_latest_by_source_time:{[t]
    fresh[];
    feed[upd[t0+0D00:00:00.900;`a;`BTC;62001f]];
    feed[upd[t0+0D00:00:00.100;`a;`BTC;61990f]];
    feed[upd[t0+0D00:00:00.500;`a;`BTC;62000f]];
    .qpipe.job.crypto_book_1s.close_ready t0+0D00:00:01.5;
    b:published[];
    .qunit.assertEquals[count b;1;"one snapshot for one second"];
    .qunit.assertEquals[(first b`source_time;first b`bar_start);(t0+0D00:00:00.900;t0);"the latest update by source_time, in its second"];
    .qunit.assertEquals[first b`bid_prices;62001 62000f;"its whole ladder, not the last to arrive"];
    .qunit.assertEquals[first b`ask_sizes;3 4f;"every ladder travels with it"]};

test_an_update_exactly_on_a_second_boundary_opens_the_next_window:{[t]
    fresh[];
    feed[upd[t0+0D00:00:00.999999999;`a;`BTC;62000f]];
    feed[upd[t0+0D00:00:01;`a;`BTC;62010f]];
    .qpipe.job.crypto_book_1s.close_ready t0+0D00:00:09;
    b:`bar_start xasc published[];
    .qunit.assertEquals[b`bar_start;t0,t0+0D00:00:01;"two windows"];
    .qunit.assertEquals[b`source_time;(t0+0D00:00:00.999999999;t0+0D00:00:01);"[from;to): the boundary row belongs to the window that starts there"]};

test_two_venues_for_one_sym_stay_separate:{[t]
    fresh[];
    feed[upd[t0+0D00:00:00.200;`a;`BTC;62000f]];
    feed[upd[t0+0D00:00:00.800;`b;`BTC;61000f]];
    feed[upd[t0+0D00:00:00.300;`a;`ETH;3000f]];
    .qpipe.job.crypto_book_1s.close_ready t0+0D00:00:09;
    b:`venue`sym xasc published[];
    .qunit.assertEquals[(b`venue;b`sym);(`a`a`b;`BTC`ETH`BTC);"a snapshot per (venue, sym)"];
    .qunit.assertEquals[first each b`bid_prices;62000 3000 61000f;"each its own book"]};

test_a_second_with_no_update_has_no_row_and_nothing_is_carried_forward:{[t]
    fresh[];
    feed[upd[t0+0D00:00:00.500;`a;`BTC;62000f]];
    feed[upd[t0+0D00:00:03.500;`a;`BTC;62003f]];
    .qpipe.job.crypto_book_1s.close_ready t0+0D00:00:09;
    .qunit.assertEquals[(published[])`bar_start;t0,t0+0D00:00:03;"seconds 1 and 2 held no update: no row, no forward fill"]};

test_a_window_closes_after_its_lateness_and_publishes_once:{[t]
    fresh[];
    feed[upd[t0+0D00:00:00.500;`a;`BTC;62000f]];
    .qunit.assertEquals[.qpipe.job.crypto_book_1s.close_ready t0+0D00:00:01.499999999;0;"one nanosecond inside the allowance: still open"];
    .qunit.assertEquals[.qpipe.job.crypto_book_1s.close_ready t0+0D00:00:01.5;1;"end plus 0.5s: closed"];
    .qunit.assertEquals[.qpipe.job.crypto_book_1s.close_ready t0+0D00:00:09;0;"never again"];
    .qunit.assertEquals[(count sent;first first sent);(1;`crypto_book_1s);"one publish, to the declared table"];
    .qunit.assertFalse[`time in cols published[];"time is the plant's, not published"]};

test_a_late_update_within_the_allowance_amends_its_snapshot:{[t]
    fresh[];
    feed[upd[t0+0D00:00:00.200;`a;`BTC;62000f]];
    `.crypto_book_1stest.clock set t0+0D00:00:01.3;
    feed[upd[t0+0D00:00:00.700;`a;`BTC;62005f]];
    .qpipe.job.crypto_book_1s.close_ready t0+0D00:00:01.5;
    b:published[];
    .qunit.assertEquals[(first b`source_time;first first b`bid_prices);(t0+0D00:00:00.700;62005f);"the late update is the latest, and is folded in"];
    .qunit.assertEquals[count .qpipe.job.crypto_book_1s.dropped;0;"nothing dropped"]};

test_an_update_after_its_snapshot_is_out_is_dropped_and_logged:{[t]
    fresh[];
    feed[upd[t0+0D00:00:00.200;`a;`BTC;62000f]];
    `.crypto_book_1stest.clock set t0+0D00:00:02;
    .qpipe.job.crypto_book_1s.close_ready clock;
    feed[upd[t0+0D00:00:00.700;`a;`BTC;62005f]];
    .qpipe.job.crypto_book_1s.close_ready t0+0D00:00:09;
    .qunit.assertEquals[count published[];1;"the published snapshot stands, not republished"];
    d:.qpipe.job.crypto_book_1s.dropped;
    .qunit.assertEquals[(count d;first d`reason);(1;`late);"the late update is in the dropped ledger"]};

test_the_pick_does_not_depend_on_arrival_order:{[t]
    / four updates, including two at one source_time: every arrival order gives the one snapshot
    rows:raze (upd[t0+0D00:00:00.200;`a;`BTC;62000f];upd[t0+0D00:00:00.800;`a;`BTC;62001f];
        upd[t0+0D00:00:00.800;`a;`BTC;62002f];upd[t0+0D00:00:00.100;`a;`BTC;61999f]);
    / bound first: a bare name inside a q-SQL template resolves from the ROOT
    / context at run time, not this namespace (#1325)
    anchor:t0;
    rows:update bar_start:anchor from rows;
    shapes:{[rows;p] .qpipe.transform.crypto_book_1s.snapshots rows p}[rows] each (0 1 2 3;3 2 1 0;2 0 3 1;1 3 0 2);
    .qunit.assertTrue[all 1_(first shapes)~/:shapes;"the same snapshot under four arrival orders"];
    .qunit.assertEquals[first first (first shapes)`bid_prices;62002f;"the tie at one source_time goes to the greater content"]};

test_the_live_example_runs_through_the_published_shape:{[t]
    out:.qetl.transform.apply[`crypto_book_1s;first[(.qetl.transform.def `crypto_book_1s)`examples]`inputs];
    .qunit.assertEquals[0#out;.qetl.plant.published `crypto_book_1s;"the transform's output is the plant table, minus time"]};

\d .
