/ test_bars.q - .qetl.job.stream.bars: a job that aggregates a stream into
/ fixed-width time windows (#946), and exec_bars with its backfill twin.
/ .
/ The job under test uses the real exec_bars transform on a table of its own,
/ so the shell is tested through the aggregation that ships.

\d .barstest

d1:2026.10.09
t0:2026.10.09D10:00:00

/ The clock the job reads, which the tests move.
clock:t0

sent:()

/ Fills in the executions shape the job projects: source_time sym size price.
fill:{[tm;s;sz;px] ([] time:enlist tm; source_time:enlist tm; sym:enlist s; size:enlist sz; price:enlist px)}

/ A job named nm over the real transform, state emptied and publish recorded.
job:{[nm]
    if[not nm in .qetl.job.stream.bars.defined[];
        .qetl.job.stream.at_bars[nm;`procname`events`event_time`transform`publishes`width`lateness`period!(
            `$string[nm],"1";`bt_fills;`source_time;`exec_bars;`bt_bar;0D00:01:00;0D00:00:05;0D00:00:01)]];
    ns:.qetl.job.stream.namespace nm;
    .qetl.job.stream.reset nm;
    (` sv ns,`now) set {[] .barstest.clock};
    `.barstest.clock set t0;
    `.barstest.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .barstest.sent,:enlist (t;x); count x}];
    nm}

ns:{[nm;k] get ` sv (.qetl.job.stream.namespace nm),k}
feed:{[nm;x] (ns[nm;`on_batch]) [`bt_fills;x]}
published:{[] raze last each sent}

/ What jobout drives it with: a fill, and the day's end, which closes its window.
contract_driver:{[]
    `.barstest.clock set t0;
    .qetl.job.stream.reset `exec_bars;
    / the job's own clock is put back as it was, not redefined, so it stays
    / the function the suite measures
    was:.qpipe.job.exec_bars.now;
    .qpipe.job.exec_bars.now:{[] .barstest.clock};
    .qpipe.job.exec_bars.on_batch[`executions;fill[t0+0D00:00:30;`EURUSD;1f;1.1]];
    .qpipe.job.exec_bars.on_endofday d1;
    .qpipe.job.exec_bars.now:was;
    }

test_a_bars_job_reads_the_process_clock_by_default:{[t]
    before:.z.p;
    at:.qpipe.job.exec_bars.now[];
    .qunit.assertTrue[at within (before;.z.p);"the kind's default clock is .z.p - .u.upd stamps rows in it"]};

/ --- declaring -----------------------------------------------------------

decl:{[] `procname`events`event_time`transform`publishes`width`lateness`period!(
    `btd1;`bt_fills;`source_time;`exec_bars;`bt_bar;0D00:01:00;0D00:00:05;0D00:00:01)}

test_a_declaration_missing_a_key_is_refused_by_name:{[t]
    .qunit.assertThrows[.qetl.job.stream.at_bars[`bt_x;];`lateness _ decl[];
        "*bars job bt_x is missing lateness*";"the key it lacks"]};

test_a_zero_width_is_refused:{[t]
    .qunit.assertThrows[.qetl.job.stream.at_bars[`bt_x;];@[decl[];`width;:;0D];
        "*bt_x's width must be a positive timespan*";"a window has to have an extent"]};

test_a_transform_that_does_not_name_the_window_is_refused:{[t]
    .qunit.assertThrows[.qetl.job.stream.at_bars[`bt_x;];@[decl[];`transform;:;`hdb_exec_bars];
        "*bt_x's transform input carries no bar_start*";"the twin's transform reads raw fills"]};

test_an_unknown_key_is_refused:{[t]
    .qunit.assertThrows[.qetl.job.stream.at_bars[`bt_x;];decl[],enlist[`horizon]!enlist 0D00:00:01;
        "*declares horizon, which a bars job does not take*";"not a horizon job"]};

/ --- windows ---------------------------------------------------------------

test_windows_are_half_open_on_the_event_time:{[t]
    x:([] st:t0+0 -1 59999999999 60000000000 60000000001);
    .qunit.assertEquals[(.qetl.job.stream.bars.assign[0D00:01;`st;x])`bar_start;
        (t0;t0-0D00:01:00;t0;t0+0D00:01:00;t0+0D00:01:00);
        "a row exactly on a boundary opens the window that starts there"]};

test_an_empty_interval_has_no_bar:{[t]
    nm:job`bt_a;
    feed[nm;fill[t0+0D00:00:10;`EURUSD;1f;1.1]];
    feed[nm;fill[t0+0D00:02:10;`EURUSD;1f;1.3]];
    `.barstest.clock set t0+0D00:05:00;
    ns[nm;`close_ready][clock];
    .qunit.assertEquals[(published[])`bar_start;t0,t0+0D00:02:00;
        "the 10:01 minute held no fill and has no bar"]};

/ --- closing ---------------------------------------------------------------

test_a_window_closes_once_its_lateness_has_passed_and_publishes_once:{[t]
    nm:job`bt_b;
    feed[nm;fill[t0+0D00:00:30;`EURUSD;2f;1.1]];
    .qunit.assertEquals[ns[nm;`close_ready][t0+0D00:01:04.999999999];0;"one nanosecond inside the allowance: still open"];
    .qunit.assertEquals[ns[nm;`close_ready][t0+0D00:01:05];1;"end plus lateness: closed"];
    .qunit.assertEquals[ns[nm;`close_ready][t0+0D00:09:00];0;"never again"];
    .qunit.assertEquals[count sent;1;"one publish"];
    .qunit.assertEquals[first first sent;`bt_bar;"to the declared table"]};

test_a_published_bar_is_the_aggregation:{[t]
    nm:job`bt_c;
    {feed[x;y]}[nm] each (fill[t0+0D00:00:50;`EURUSD;3f;1.12];fill[t0+0D00:00:05;`EURUSD;1f;1.10];fill[t0+0D00:00:20;`EURUSD;2f;1.11]);
    ns[nm;`close_ready][t0+0D00:02:00];
    b:first published[];
    .qunit.assertEquals[b`open`high`low`close`volume`trades;(1.10;1.12;1.10;1.12;6f;3);"open and close by source_time, not arrival"];
    .qunit.assertEquals[b`vwap;6.68%6;"size-weighted"]};

test_a_late_row_within_the_allowance_amends_its_window:{[t]
    nm:job`bt_d;
    feed[nm;fill[t0+0D00:00:30;`EURUSD;1f;1.20]];
    `.barstest.clock set t0+0D00:01:03;
    feed[nm;fill[t0+0D00:00:10;`EURUSD;1f;1.10]];
    ns[nm;`close_ready][t0+0D00:01:05];
    b:first published[];
    .qunit.assertEquals[(b`open;b`volume;b`trades);(1.10;2f;2);"the late row opens the bar"];
    .qunit.assertEquals[count ns[nm;`dropped];0;"nothing dropped"]};

test_a_late_row_beyond_the_allowance_is_dropped_and_the_bar_is_not_republished:{[t]
    nm:job`bt_e;
    feed[nm;fill[t0+0D00:00:30;`EURUSD;1f;1.20]];
    `.barstest.clock set t0+0D00:01:05;
    ns[nm;`close_ready][clock];
    `.barstest.clock set t0+0D00:01:06;
    feed[nm;fill[t0+0D00:00:10;`EURUSD;1f;1.10]];
    ns[nm;`close_ready][clock];
    .qunit.assertEquals[count sent;1;"the bar went out once"];
    .qunit.assertEquals[(first published[])`open;1.20;"and without the late row"];
    .qunit.assertEquals[(ns[nm;`dropped])`reason;enlist `late;"the drop is on the ledger"];
    .qunit.assertEquals[count ns[nm;`pending];0;"and not buffered"]};

test_a_publish_that_throws_leaves_the_windows_for_the_next_tick:{[t]
    nm:job`bt_f;
    feed[nm;fill[t0+0D00:00:30;`EURUSD;1f;1.20]];
    .qetl.job.stream.wire[nm;{[t;x] '"plant down"}];
    .qunit.assertThrows[ns[nm;`close_ready];t0+0D00:02:00;"*plant down*";"the error reaches the timer"];
    .qunit.assertEquals[count ns[nm;`pending];1;"still buffered"];
    .qetl.job.stream.wire[nm;{[t;x] .barstest.sent,:enlist (t;x); count x}];
    .qunit.assertEquals[ns[nm;`close_ready][t0+0D00:02:00];1;"published on the next tick"]};

/ --- end of day ------------------------------------------------------------

test_end_of_day_closes_the_days_open_windows_and_no_others:{[t]
    nm:job`bt_g;
    last_min:2026.10.09D23:59:00;
    feed[nm;fill[last_min+0D00:00:30;`EURUSD;1f;1.20]];
    `.barstest.clock set 2026.10.10D00:00:03;
    feed[nm;fill[2026.10.10D00:00:01;`EURUSD;4f;1.30]];
    .qunit.assertEquals[ns[nm;`on_endofday][d1];1;"the 23:59 window"];
    .qunit.assertEquals[(published[])`bar_start;enlist last_min;"published before its lateness had passed"];
    .qunit.assertEquals[(ns[nm;`pending])`bar_start;enlist 2026.10.10D00:00:00;"the new day's window stays open"];
    feed[nm;fill[last_min+0D00:00:40;`EURUSD;9f;1.50]];
    .qunit.assertEquals[count ns[nm;`dropped];1;"a late row for the closed window is refused"];
    ns[nm;`close_ready][2026.10.10D00:02:00];
    .qunit.assertEquals[count sent;2;"the last window is not published twice"]};

test_the_kind_registers_its_end_of_day_hook:{[t]
    nm:job`bt_h;
    .qunit.assertTrue[`on_endofday in key .qetl.job.stream.def nm;"the runner calls it"];
    .qunit.assertTrue[(.qetl.job.stream.def nm)`replay;"and replays"];
    .qunit.assertEquals[(.qetl.job.stream.def nm)`restore_from;enlist `bt_bar;"from its own bars"]};

/ --- replay ----------------------------------------------------------------

/ A restart that replays the log holds the windows that were open and
/ publishes none that were closed - the same bars as a process left running.
test_a_replay_rebuilds_open_windows_and_does_not_republish_closed_ones:{[t]
    r1:fill[t0+0D00:00:10;`EURUSD;1f;1.10];
    r2:fill[t0+0D00:00:40;`EURUSD;1f;1.20];
    r3:fill[t0+0D00:01:20;`EURUSD;2f;1.30];
    / the process left running: rows, the first window closes, the second later
    nm:job`bt_i;
    feed[nm;r1]; feed[nm;r2]; feed[nm;r3];
    `.barstest.clock set t0+0D00:01:05;
    ns[nm;`close_ready][clock];
    first_bar:published[];
    `.barstest.clock set t0+0D00:02:05;
    ns[nm;`close_ready][clock];
    running:published[];
    / the restarted process, long after: replays the log, then ticks
    nm:job`bt_i;
    `.barstest.clock set t0+0D00:03:00;
    `.qetl.job.stream.replaying set 1b;
    r:@[{[nm;a;b;c;bar] feed[nm;a]; feed[nm;b]; feed[nm;c]; (ns[nm;`on_batch])[`bt_bar;bar]; 1b}[nm;r1;r2;r3];first_bar;{x}];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[r;1b;"the replay ran"];
    .qunit.assertEquals[(ns[nm;`pending])`price;enlist 1.30;"only the open window was rebuilt"];
    .qunit.assertEquals[count sent;0;"nothing published while replaying"];
    ns[nm;`close_ready][clock];
    after:published[];
    .qunit.assertEquals[(first_bar,after);running;"the closed bar from the log plus the rebuilt one are the running process's"]};

/ A row dropped live as late, in a window that never got a bar, stays dropped
/ across a restart: it is not buffered and no bar appears for it (#993).
test_a_row_dropped_live_as_late_stays_dropped_on_replay:{[t]
    / event 10:00:30, received (time) 10:05:00: its window [10:00,10:01) closed at 10:01:05
    late:update time:.barstest.t0+0D00:05:00 from fill[t0+0D00:00:30;`EURUSD;1f;1.10];
    nm:job`bt_l;
    `.barstest.clock set t0+0D00:05:00;
    feed[nm;late];
    .qunit.assertEquals[count ns[nm;`dropped];1;"live: dropped as late"];
    .qunit.assertEquals[count ns[nm;`pending];0;"live: not buffered"];
    nm:job`bt_l;
    `.barstest.clock set t0+0D00:06:00;
    `.qetl.job.stream.replaying set 1b;
    r:@[{[nm;x] feed[nm;x]; 1b}[nm];late;{x}];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[r;1b;"the replay ran"];
    .qunit.assertEquals[count ns[nm;`dropped];1;"replay: dropped again"];
    .qunit.assertEquals[count ns[nm;`pending];0;"replay: not buffered"];
    ns[nm;`close_ready][clock];
    .qunit.assertEquals[count published[];0;"no bar for a row the live process refused"]};

/ A row received in time is still buffered on replay: receipt time, not the
/ event time, decides, so an old event that arrived promptly is not lost.
test_a_row_received_in_time_is_buffered_on_replay:{[t]
    early:update time:.barstest.t0+0D00:01:02 from fill[t0+0D00:00:30;`EURUSD;1f;1.10];
    nm:job`bt_e;
    `.qetl.job.stream.replaying set 1b;
    r:@[{[nm;x] feed[nm;x]; 1b}[nm];early;{x}];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[r;1b;"the replay ran"];
    .qunit.assertEquals[count ns[nm;`pending];1;"within lateness at receipt: buffered"];
    .qunit.assertEquals[count ns[nm;`dropped];0;"and not dropped"]};

/ --- the twin --------------------------------------------------------------

test_the_backfill_twin_builds_the_live_bars_from_the_same_rows:{[t]
    nm:job`bt_j;
    rows:([] source_time:t0+0D00:00:01*7 33 59 60 61 119 130 185;
        sym:`EURUSD`USDJPY`EURUSD`EURUSD`USDJPY`EURUSD`EURUSD`USDJPY;
        size:1 2 3 4 5 6 7 8f; price:1.1 150 1.2 1.15 151 1.3 1.25 149);
    feed[nm;update time:source_time from rows];
    ns[nm;`close_ready][t0+0D00:10:00];
    live:`sym`bar_start xasc published[];
    twin:.qpipe.job.hdb_exec_bars_backfill.bars rows;
    .qunit.assertEquals[(cols live)#`sym`bar_start xasc twin;live;"one aggregation, two lifecycles"];
    .qunit.assertEquals[twin`time;twin[`bar_start]+0D00:01;"the twin stamps the window's end"];
    .qunit.assertEquals[count live;6;"EURUSD in minutes 0, 1, 2 and USDJPY in 0, 1, 3: no bar where a sym had no fill"]};

\d .
