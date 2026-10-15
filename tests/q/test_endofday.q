/ test_endofday.q - end of day for streaming jobs (#943): the on_endofday
/ callback, the plant's roll, and position books that carry across days.
/ .
/ The property the books are held to: a process restarted on the new day,
/ replaying its log, holds the same book as one left running through the
/ day change. Before #943 the running one kept yesterday's positions and
/ the restarted one had only today's.


/ The jobs this suite defines are process-global; leave the registry as found (#1375).
.qt.qetl.job.stream.endofday.beforeNamespace_save_jobs:{[] .qt.support.save_stream_jobs[`.qt.qetl.job.stream.endofday]}
.qt.qetl.job.stream.endofday.afterNamespace_restore_jobs:{[] .qt.support.restore_stream_jobs[`.qt.qetl.job.stream.endofday]}

.qt.qetl.job.stream.endofday.d0:2026.10.08
.qt.qetl.job.stream.endofday.t1:2026.10.09D09:00:00

.qt.qetl.job.stream.endofday.fill:{[s;side;size;px;bk;prod]
    ([] time:enlist .qt.qetl.job.stream.endofday.t1; source_time:enlist .qt.qetl.job.stream.endofday.t1; sym:enlist s; venue:enlist `v; side:enlist side;
        size:enlist size; price:enlist px; fee:enlist 0f; fee_ccy:enlist `USD; fill_id:enlist `f;
        book:enlist bk; product:enlist prod)}

.qt.qetl.job.stream.endofday.sent:()
.qt.qetl.job.stream.endofday.record:{[job] .qetl.job.stream.wire[job;{[t;x] .qt.qetl.job.stream.endofday.sent,:enlist (t;x); count x}]; `.qt.qetl.job.stream.endofday.sent set ();}
.qt.qetl.job.stream.endofday.sent_on:{[t] raze last each .qt.qetl.job.stream.endofday.sent where t=first each .qt.qetl.job.stream.endofday.sent}

/ Replay `batches` - (table;rows) pairs, in log order - into a job's
/ on_batch as a restart does: .qetl.job.stream.replaying set.
.qt.qetl.job.stream.endofday.replay:{[job;batches]
    / through the shell's handler, as start subscribes it - the carry is
    / the shell's (#963), not the job's on_batch
    h:.qetl.job.stream.handler job;
    `.qetl.job.stream.replaying set 1b;
    r:@[{[h;b] h ./: b; 1b}[h];batches;{x}];
    `.qetl.job.stream.replaying set 0b;
    .qetl.job.stream.replayed job;
    if[not 1b~r; 'r];
    }

/ --- the callback ---------------------------------------------------------

.qt.qetl.job.stream.endofday.saved_running:`symbol$()
.qt.qetl.job.stream.endofday.setUp_eod:{[] `.qt.qetl.job.stream.endofday.saved_running set .qetl.job.stream.running;}
.qt.qetl.job.stream.endofday.tearDown_eod:{[]
    `.qetl.job.stream.running set .qt.qetl.job.stream.endofday.saved_running;
    `.qetl.job.stream.replaying set 0b;
    .qetl.job.stream.reset each `fx_positions`posbook;
    }

.qt.qetl.job.stream.endofday.feed:{[nm;eod] `procname`subscribe_to`publishes`period`on_timer`on_endofday!(
    `$string[nm],"1";`symbol$();`symbol$();0D00:00:01;{[] };eod)}

.qt.qetl.job.stream.endofday.test_on_endofday_must_be_a_function:{[t]
    .qunit.assertThrows[.qetl.job.stream.define[`eod_a;];@[.qt.qetl.job.stream.endofday.feed[`eod_a;{[d] }];`on_endofday;:;1];
        "*on_endofday must be a function of the date that ended*";"refused at declaration"]};

.qt.qetl.job.stream.endofday.test_end_of_day_calls_each_running_job_and_survives_one_that_throws:{[t]
    `.qt.qetl.job.stream.endofday.seen set ();
    if[not `eod_b in key .qetl.job.stream.jobs; .qetl.job.stream.define[`eod_b;.qt.qetl.job.stream.endofday.feed[`eod_b;{[d] '"boom"}]]];
    if[not `eod_c in key .qetl.job.stream.jobs; .qetl.job.stream.define[`eod_c;.qt.qetl.job.stream.endofday.feed[`eod_c;{[d] .qt.qetl.job.stream.endofday.seen,:d}]]];
    `.qetl.job.stream.running set `eod_b`eod_c;
    ok:.qetl.job.stream.end_of_day .qt.qetl.job.stream.endofday.d0;
    .qunit.assertEquals[ok;enlist `eod_c;"the one that threw is not reported as done"];
    .qunit.assertEquals[.qt.qetl.job.stream.endofday.seen;enlist .qt.qetl.job.stream.endofday.d0;"and the other still ran, with the date that ended"]};

.qt.qetl.job.stream.endofday.test_a_job_that_is_not_running_is_not_called:{[t]
    `.qt.qetl.job.stream.endofday.seen set ();
    if[not `eod_c in key .qetl.job.stream.jobs; .qetl.job.stream.define[`eod_c;.qt.qetl.job.stream.endofday.feed[`eod_c;{[d] .qt.qetl.job.stream.endofday.seen,:d}]]];
    `.qetl.job.stream.running set `symbol$();
    .qetl.job.stream.end_of_day .qt.qetl.job.stream.endofday.d0;
    .qunit.assertEquals[count .qt.qetl.job.stream.endofday.seen;0;"declared but not started in this process"]};

/ --- the plant's roll -----------------------------------------------------

.qt.qetl.job.stream.endofday.test_the_plant_rolls_its_log_to_the_new_day:{[t]
    dir:first system "mktemp -d";
    .qetl.tick.reset[];
    .qetl.tick.schema[`eodx;([] time:`timestamp$(); v:`long$())];
    .qetl.tick.open_log[dir;`eodlog;.qt.qetl.job.stream.endofday.d0];
    .qetl.tick.publish[`eodx;([] v:enlist 1)];
    first_log:.qetl.tick.log_path;
    ended:.qetl.tick.roll .qt.qetl.job.stream.endofday.d0+1;
    .qetl.tick.publish[`eodx;([] v:enlist 2)];
    .qunit.assertEquals[ended;.qt.qetl.job.stream.endofday.d0;"it reports the day that ended"];
    .qunit.assertEquals[.qetl.tick.log_date;.qt.qetl.job.stream.endofday.d0+1;"and is on the new one"];
    .qunit.assertEquals[(-11!(-2;first_log);-11!(-2;.qetl.tick.log_path));1 1;
        "yesterday's message stays in yesterday's log, today's opens today's"];
    .qetl.tick.reset[]};

.qt.qetl.job.stream.endofday.test_a_plant_with_no_log_has_no_day_to_end:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.roll;.qt.qetl.job.stream.endofday.d0;"roll: no log is open*";"refused by name"]};

/ --- fx_positions carries its book ------------------------------------------

.qt.qetl.job.stream.endofday.test_fx_positions_carries_its_book_and_a_restart_agrees:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    on:.qpipe.job.fx_positions.on_batch;
    on[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    / day 1 ends: the book is published as day 2 opens
    n:.qetl.job.stream.snapshot `fx_positions;
    .qunit.assertEquals[n;1;"one position carried"];
    opening:.qt.qetl.job.stream.endofday.sent_on[`fx_position_open];
    .qunit.assertEquals[exec base_qty from opening;enlist 1e6;"the day-1 position"];
    / day 2: one more fill, in the process that kept running
    day2:.qt.qetl.job.stream.endofday.fill[`EURUSD;1;5e5;1.12;`london;`spot];
    on[`executions;day2];
    running:.qpipe.job.fx_positions.positions;
    / a restart on day 2 replays day 2's log: the opening book, then the fill
    `.qpipe.job.fx_positions.positions set `sym`book`product xkey .qpipe.job.fx_positions.desk_book;
    .qt.qetl.job.stream.endofday.replay[`fx_positions;((`fx_position_open;opening);(`executions;day2))];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec base_qty from .qpipe.job.fx_positions.positions;enlist 1.5e6;"1mm carried plus 500k today"]};

.qt.qetl.job.stream.endofday.test_fx_positions_ignores_its_own_live_echo:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    .qpipe.job.fx_positions.on_batch[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    before:.qpipe.job.fx_positions.positions;
    (.qetl.job.stream.handler `fx_positions)[`fx_position_open;0#0!before];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;before;"live, the opening book changes nothing"]};

.qt.qetl.job.stream.endofday.test_an_empty_book_carries_nothing:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    .qunit.assertEquals[.qetl.job.stream.snapshot `fx_positions;0;"nothing to carry"];
    .qunit.assertEquals[count .qt.qetl.job.stream.endofday.sent;0;"so nothing is published"]};

/ #960: the plant rolls and tells the job asynchronously, so a fill can be
/ logged BEFORE the job's opening row; live, the job applied it on top of
/ its snapshot.
.qt.qetl.job.stream.endofday.test_fx_positions_keeps_a_fill_logged_before_the_opening_row:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    on:.qpipe.job.fx_positions.on_batch;
    on[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `fx_positions;
    opening:.qt.qetl.job.stream.endofday.sent_on[`fx_position_open];
    / logged after the roll, so stamped later than anything the snapshot holds
    early:update time:.qt.qetl.job.stream.endofday.t1+0D00:00:01 from .qt.qetl.job.stream.endofday.fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;early];
    running:.qpipe.job.fx_positions.positions;
    `.qpipe.job.fx_positions.positions set `sym`book`product xkey .qpipe.job.fx_positions.desk_book;
    .qt.qetl.job.stream.endofday.replay[`fx_positions;((`executions;early);(`fx_position_open;opening))];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec base_qty, fill_count from .qpipe.job.fx_positions.positions;`base_qty`fill_count!(enlist 6e5;enlist 2);"600k net, two fills"]};

.qt.qetl.job.stream.endofday.test_fx_positions_first_day_without_an_opening_row_replays_as_before:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    on:.qpipe.job.fx_positions.on_batch;
    f:.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot];
    on[`executions;f];
    running:.qpipe.job.fx_positions.positions;
    `.qpipe.job.fx_positions.positions set `sym`book`product xkey .qpipe.job.fx_positions.desk_book;
    .qt.qetl.job.stream.endofday.replay[`fx_positions;enlist (`executions;f)];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;running;"no opening row: the log alone rebuilds the book"]};

/ --- posbook carries its book ------------------------------------------------

.qt.qetl.job.stream.endofday.test_posbook_carries_its_book_and_a_restart_agrees:{[t]
    .qt.qetl.job.stream.endofday.record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    on[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `posbook;
    opening:.qt.qetl.job.stream.endofday.sent_on[`position_open];
    day2:.qt.qetl.job.stream.endofday.fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;day2];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    .qt.qetl.job.stream.endofday.replay[`posbook;((`position_open;opening);(`executions;day2))];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec qty from .qpipe.job.posbook.book;enlist 6e5;"1mm carried, 400k sold today"]};

/ #960: posbook's avg_price and realised P&L depend on lot order, so a fill
/ logged before the opening row is re-applied after it, not summed.
.qt.qetl.job.stream.endofday.test_posbook_keeps_a_fill_logged_before_the_opening_row:{[t]
    .qt.qetl.job.stream.endofday.record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    on[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `posbook;
    opening:.qt.qetl.job.stream.endofday.sent_on[`position_open];
    / logged after the roll, so stamped later than anything the snapshot holds
    early:update time:.qt.qetl.job.stream.endofday.t1+0D00:00:01 from .qt.qetl.job.stream.endofday.fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;early];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    .qt.qetl.job.stream.endofday.replay[`posbook;((`executions;early);(`position_open;opening))];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec qty, realized_pnl from .qpipe.job.posbook.book;`qty`realized_pnl!(enlist 6e5;enlist 2000f);"600k left, 400k*0.005 realised"];
    .qunit.assertFalse[`posbook in key .qetl.job.stream.carry_log;"the recorded batches are forgotten"]};

.qt.qetl.job.stream.endofday.test_posbook_first_day_without_an_opening_row_replays_as_before:{[t]
    .qt.qetl.job.stream.endofday.record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    f:.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot];
    on[`executions;f];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    .qt.qetl.job.stream.endofday.replay[`posbook;enlist (`executions;f)];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"no opening row: the log alone rebuilds the book"];
    .qunit.assertFalse[`posbook in key .qetl.job.stream.carry_log;"replay state is reset"]};

.qt.qetl.job.stream.endofday.test_posbook_restart_with_the_opening_row_first_is_unchanged:{[t]
    .qt.qetl.job.stream.endofday.record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    on[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `posbook;
    opening:.qt.qetl.job.stream.endofday.sent_on[`position_open];
    day2:.qt.qetl.job.stream.endofday.fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;day2];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    .qt.qetl.job.stream.endofday.replay[`posbook;((`position_open;opening);(`executions;day2))];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"no early fill: same book"]};

/ --- the shell's carry (#963) ---------------------------------------------

.qt.qetl.job.stream.endofday.test_a_malformed_carry_is_refused:{[t]
    d:`procname`subscribe_to`publishes`on_batch`replay`carry!(`eodcar1;enlist `executions;enlist `eodx_open;{[t;x]};1b;0);
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*carry must be a dict of state and table*";"not a dict"];
    d[`carry]:`state`table!(`book;`elsewhere);
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*must be one the job publishes*";"a table it does not publish"];
    d[`carry]:`state`table!(`book;`eodx_open); d[`replay]:0b;
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*needs replay 1b*";"nothing would read it back"]};

.qt.qetl.job.stream.endofday.test_a_carry_takes_only_state_table_and_a_positive_cap:{[t]
    d:`procname`subscribe_to`publishes`on_batch`replay`carry!(`eodcar1;enlist `executions;enlist `eodx_open;{[t;x]};1b;`state`table`window!(`book;`eodx_open;0D00:01));
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*takes state, table and cap, not window*";"the old time window is gone"];
    d[`carry]:`state`table`cap!(`book;`eodx_open;0);
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*cap must be a positive long*";"a cap that holds nothing"]};

/ #1015: the snapshot says how far its state had got, so a replay re-applies
/ exactly the rows it lacks, however long after the log's start it lands.
.qt.qetl.job.stream.endofday.test_a_snapshot_names_the_last_batch_its_state_applied:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    (.qetl.job.stream.handler `fx_positions)[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `fx_positions;
    .qunit.assertEquals[exec carried_to from .qt.qetl.job.stream.endofday.sent_on `fx_position_open;enlist .qt.qetl.job.stream.endofday.t1;"the day-1 fill's plant time"]};

.qt.qetl.job.stream.endofday.late_snapshot_replay:{[]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    h:.qetl.job.stream.handler `fx_positions;
    h[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `fx_positions;
    / the snapshot reaches the log an hour after the roll
    opening:update time:.qt.qetl.job.stream.endofday.t1+0D01 from .qt.qetl.job.stream.endofday.sent_on `fx_position_open;
    early:update time:.qt.qetl.job.stream.endofday.t1+0D00:00:01 from .qt.qetl.job.stream.endofday.fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    later:update time:.qt.qetl.job.stream.endofday.t1+0D00:30 from .qt.qetl.job.stream.endofday.fill[`GBPUSD;1;1e6;1.27;`london;`spot];
    h[`executions;early]; h[`executions;later];
    `.qt.qetl.job.stream.endofday.running_book set .qpipe.job.fx_positions.positions;
    .qetl.job.stream.reset `fx_positions;
    .qt.qetl.job.stream.endofday.replay[`fx_positions;((`executions;early);(`executions;later);(`fx_position_open;opening))];
    }

.qt.qetl.job.stream.endofday.errors:{[lines] lines where `ERROR=first each lines}

.qt.qetl.job.stream.endofday.test_a_late_snapshot_still_restores_the_whole_book:{[t]
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] .qt.qetl.job.stream.endofday.late_snapshot_replay];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;.qt.qetl.job.stream.endofday.running_book;"restarted and kept-running books agree"];
    .qunit.assertEquals[count e;0;"and nothing is reported lost"]};

/ A batch logged before the snapshot but already IN it - one the job applied
/ before taking it - is not applied twice.
.qt.qetl.job.stream.endofday.test_a_batch_the_snapshot_holds_is_not_applied_again:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    h:.qetl.job.stream.handler `fx_positions;
    h[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    held:update time:.qt.qetl.job.stream.endofday.t1+0D00:00:01 from .qt.qetl.job.stream.endofday.fill[`EURUSD;1;2e5;1.1;`london;`spot];
    h[`executions;held];
    .qetl.job.stream.snapshot `fx_positions;
    opening:.qt.qetl.job.stream.endofday.sent_on[`fx_position_open];
    running:.qpipe.job.fx_positions.positions;
    .qetl.job.stream.reset `fx_positions;
    .qt.qetl.job.stream.endofday.replay[`fx_positions;((`executions;held);(`fx_position_open;opening))];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;running;"1.2mm, not 1.4mm"]};

/ A recording is capped; a snapshot that arrives after the cap cannot have the
/ dropped batches re-applied, and says so rather than leaving the book short.
.qt.qetl.job.stream.endofday.capped:{[]
    if[not `eod_cap in key .qetl.job.stream.jobs;
        `.qpipe.job.eod_cap.book set ([] sym:`symbol$(); qty:`float$());
        .qetl.job.stream.define[`eod_cap;`procname`subscribe_to`publishes`on_batch`replay`carry!(
            `eodcap1;enlist `executions;enlist `eodx_open;{[t;x] `.qpipe.job.eod_cap.book upsert select sym, qty:size from x};1b;
            `state`table`cap!(`book;`eodx_open;1))]];
    `.qpipe.job.eod_cap.book set ([] sym:`symbol$(); qty:`float$());
    f:.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot];
    .qt.qetl.job.stream.endofday.replay[`eod_cap;((`executions;f);(`executions;f);(`eodx_open;([] time:enlist .qt.qetl.job.stream.endofday.t1; sym:`EURUSD; qty:3e6; carried_to:enlist 0Np)))];
    }

.qt.qetl.job.stream.endofday.test_a_snapshot_after_the_cap_is_logged_as_an_error:{[t]
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] .qt.qetl.job.stream.endofday.capped];
    .qunit.assertEquals[count e;1;"one error for the abandoned recording"];
    .qunit.assertEquals[e[0;1];`eod_cap;"naming the job"];
    .qunit.assertEquals[e[0;3]`cap;1;"and the cap it overran"]};

/ #1372: a recording that overflows between two snapshots has lost only the
/ batches the later snapshot does not already hold. The job caps at 2 rows;
/ replay s1, fills at t1+01:00..t1+03:00 (the third is dropped), then s2 with
/ carried_to t1+03:00, after `extra` more fills later than that (log order
/ before s2, as when the snapshot is published late).
.qt.qetl.job.stream.endofday.capped2:{[extra]
    if[not `eod_cap2 in key .qetl.job.stream.jobs;
        `.qpipe.job.eod_cap2.book set ([] sym:`symbol$(); qty:`float$());
        .qetl.job.stream.define[`eod_cap2;`procname`subscribe_to`publishes`on_batch`replay`carry!(
            `eodcap2;enlist `executions;enlist `eodx_open;{[t;x] `.qpipe.job.eod_cap2.book upsert select sym, qty:size from x};1b;
            `state`table`cap!(`book;`eodx_open;2))]];
    `.qpipe.job.eod_cap2.book set ([] sym:`symbol$(); qty:`float$());
    fl:{[s;h] update time:.qt.qetl.job.stream.endofday.t1+h from .qt.qetl.job.stream.endofday.fill[s;1;1e6;1.1;`london;`spot]};
    s1:([] time:enlist .qt.qetl.job.stream.endofday.t1; sym:enlist `AAA; qty:enlist 1e6; carried_to:enlist 0Np);
    s2:([] time:4#.qt.qetl.job.stream.endofday.t1+0D04:00:00; sym:`AAA`BBB`CCC`DDD; qty:1e6 1e6 1e6 1e6; carried_to:4#.qt.qetl.job.stream.endofday.t1+0D03:00:00);
    seq:((`executions;fl[`BBB;0D01:00:00]);(`executions;fl[`CCC;0D02:00:00]);(`executions;fl[`DDD;0D03:00:00]));
    if[extra; seq,:enlist (`executions;fl[`EEE;0D04:00:00])];
    .qt.qetl.job.stream.endofday.replay[`eod_cap2;(enlist (`eodx_open;s1)),seq,enlist (`eodx_open;s2)];
    }

.qt.qetl.job.stream.endofday.test_a_snapshot_covering_every_dropped_batch_is_not_an_error:{[t]
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {.qt.qetl.job.stream.endofday.capped2[0b]}];
    .qunit.assertEquals[count e;0;"the snapshot holds the dropped batch, so nothing is missing"];
    .qunit.assertEquals[select from .qpipe.job.eod_cap2.book;([] sym:`AAA`BBB`CCC`DDD; qty:4#1e6);"and the book is the snapshot's"]};

.qt.qetl.job.stream.endofday.test_a_dropped_batch_later_than_the_snapshot_is_still_an_error:{[t]
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {.qt.qetl.job.stream.endofday.capped2[1b]}];
    .qunit.assertEquals[count e;1;"a batch after the snapshot's carried_to was dropped: really missing"];
    .qunit.assertEquals[e[0;1];`eod_cap2;"naming the job"]};

/ --- a missed end of day (#1072) -------------------------------------------

/ The errors missed_end_of_day logs for fx_positions with `marker` as the
/ last end of day it saw (0Nd: none recorded), and `status` its carry's.
.qt.qetl.job.stream.endofday.missed:{[marker;status]
    p:.qetl.job.stream.carry_marker_path `fx_positions;
    system "mkdir -p ",.qetl.job.bounded.state.lock_dir[];
    system "rm -f ",p,"*";
    if[not null marker; .qetl.job.bounded.state.durable_set[p;([] last_end_of_day:enlist marker)]];
    if[not null status; .qetl.job.stream.carry_log[`fx_positions]:(status;0;();0Np)];
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {.qetl.job.stream.missed_end_of_day[`fx_positions;.z.D]}];
    .qetl.job.stream.replayed `fx_positions;
    system "rm -f ",p,"*";
    e}

.qt.qetl.job.stream.endofday.test_a_restart_after_a_missed_end_of_day_says_so:{[t]
    e:.qt.qetl.job.stream.endofday.missed[.z.D-3;`recording];
    .qunit.assertEquals[count e;1;"one error: the book lacks what was carried from before today"];
    .qunit.assertEquals[e[0;3]`last_end_of_day;.z.D-3;"naming the last end of day the job saw"]};

.qt.qetl.job.stream.endofday.test_a_flat_end_of_day_a_first_start_and_a_found_snapshot_are_quiet:{[t]
    .qunit.assertEquals[count .qt.qetl.job.stream.endofday.missed[.z.D-1;`recording];0;"yesterday's end of day was seen - its book was just flat"];
    .qunit.assertEquals[count .qt.qetl.job.stream.endofday.missed[0Nd;`recording];0;"a first start has no end of day to have missed"];
    .qunit.assertEquals[count .qt.qetl.job.stream.endofday.missed[.z.D-3;`restored];0;"a snapshot was found, so nothing is missing"]};

.qt.qetl.job.stream.endofday.test_end_of_day_records_the_day_a_carrying_job_saw_end:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    p:.qetl.job.stream.carry_marker_path `fx_positions;
    system "mkdir -p ",.qetl.job.bounded.state.lock_dir[];
    `.qetl.job.stream.running set enlist `fx_positions;
    .qetl.job.stream.end_of_day .qt.qetl.job.stream.endofday.d0;
    m:first (.qetl.job.bounded.state.durable_get p)`last_end_of_day;
    system "rm -f ",p,"*";
    .qunit.assertEquals[m;.qt.qetl.job.stream.endofday.d0;"the date that ended, kept beside the ledgers"]};

/ The carry marker of fx_positions after `end_of_day` with a seeded marker
/ (d0-1) and a publisher that does `pub`; 0Nd when none was recorded.
.qt.qetl.job.stream.endofday.marker_after:{[pub]
    p:.qetl.job.stream.carry_marker_path `fx_positions;
    system "mkdir -p ",.qetl.job.bounded.state.lock_dir[];
    system "rm -f ",p,"*";
    .qetl.job.bounded.state.durable_set[p;([] last_end_of_day:enlist .qt.qetl.job.stream.endofday.d0-1)];
    .qetl.job.stream.wire[`fx_positions;pub];
    (.qetl.job.stream.handler `fx_positions)[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    `.qetl.job.stream.running set enlist `fx_positions;
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {.qetl.job.stream.end_of_day .qt.qetl.job.stream.endofday.d0}];
    m:.qetl.job.stream.last_end_seen `fx_positions;
    system "rm -f ",p,"*";
    (m;e)}

.qt.qetl.job.stream.endofday.test_a_snapshot_the_plant_refused_leaves_the_end_of_day_marker_alone:{[t]
    r:.qt.qetl.job.stream.endofday.marker_after {[t;x] '"plant refused: ",string t};
    .qunit.assertEquals[r 0;.qt.qetl.job.stream.endofday.d0-1;"the end of day was not carried, so it is not recorded"];
    .qunit.assertEquals[count r 1;1;"and the failure is logged once"]};

.qt.qetl.job.stream.endofday.test_a_snapshot_the_publish_check_withheld_leaves_the_end_of_day_marker_alone:{[t]
    r:.qt.qetl.job.stream.endofday.marker_after {[t;x] .qetl.job.stream.withhold[`fx_positions;t;x;"test withhold";();count x]};
    .qunit.assertEquals[r 0;.qt.qetl.job.stream.endofday.d0-1;"a withheld snapshot did not carry the book across"];
    .qunit.assertTrue[any {(x[3]`error) like "*withheld by the publish check*"} each r 1;"and the failure says it was withheld"]};

.qt.qetl.job.stream.endofday.test_a_withheld_snapshot_is_an_error_not_a_count:{[t]
    .qetl.job.stream.wire[`fx_positions;{[t;x] .qetl.job.stream.withhold[`fx_positions;t;x;"test withhold";();count x]}];
    (.qetl.job.stream.handler `fx_positions)[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qunit.assertThrows[{[j] .qetl.job.stream.snapshot j};`fx_positions;"*carry snapshot withheld by the publish check*";"snapshot refuses to report a withheld batch as published"]};

/ Two snapshots in one replay (catch_up, then today's log, #1308): each is
/ amended by the fills logged before it but not in it.
.qt.qetl.job.stream.endofday.test_every_snapshot_in_a_replay_is_amended_by_the_fills_it_lacks:{[t]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    h:.qetl.job.stream.handler `fx_positions;
    f1:.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot];
    f2:update time:.qt.qetl.job.stream.endofday.t1+0D01 from .qt.qetl.job.stream.endofday.fill[`GBPUSD;1;1e6;1.27;`london;`spot];
    g:update time:.qt.qetl.job.stream.endofday.t1+0D02 from .qt.qetl.job.stream.endofday.fill[`USDJPY;1;1e6;150f;`london;`spot];
    h[`executions;f1]; .qetl.job.stream.snapshot `fx_positions;
    h[`executions;f2]; .qetl.job.stream.snapshot `fx_positions;
    h[`executions;g];
    s:.qt.qetl.job.stream.endofday.sent_on[`fx_position_open];
    s1:select from s where carried_to=.qt.qetl.job.stream.endofday.t1;
    s2:select from s where carried_to=.qt.qetl.job.stream.endofday.t1+0D01;
    live:.qpipe.job.fx_positions.positions;
    .qetl.job.stream.reset `fx_positions;
    .qt.qetl.job.stream.endofday.replay[`fx_positions;((`fx_position_open;s1);(`executions;f2);(`executions;g);(`fx_position_open;s2))];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;live;"the replayed book is the live one: the second snapshot did not drop g"];
    .qunit.assertEquals[count .qpipe.job.fx_positions.positions;3;"three positions"]};

/ --- the remedy: replay the days it was down across (#1072) ----------------

/ A plant whose logs run from the day after `marker` to today. The first log
/ opens with the snapshot of a book carrying 1mm EURUSD (the job was running
/ at that roll); each log then holds one 100k fill, and no later roll wrote a
/ snapshot - the job was down. Returns the marker's date.
.qt.qetl.job.stream.endofday.missed_logs:{[marker] .qt.qetl.job.stream.endofday.missed_logs_to[marker;1b;.z.D]}

/ The same, carrying a book (`seeded` 1b) or flat at the marker (0b - no
/ snapshot is written then), with the plant's last log being `through`.
.qt.qetl.job.stream.endofday.missed_logs_to:{[marker;seeded;through]
    .qt.qetl.job.stream.endofday.record[`fx_positions];
    if[seeded; .qpipe.job.fx_positions.on_batch[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e6;1.1;`london;`spot]]];
    .qetl.job.stream.snapshot `fx_positions;
    opening:.qt.qetl.job.stream.endofday.sent_on[`fx_position_open];
    dir:first system "mktemp -d";
    .qetl.tick.reset[];
    .qetl.tick.open_log[dir;`eodgap;marker+1];
    if[seeded; .qetl.tick.publish[`fx_position_open;opening]];
    days:(marker+1)+til through-marker;
    {[dt] if[not dt=.qetl.tick.log_date; .qetl.tick.roll dt]; .qetl.tick.publish[`executions;.qt.qetl.job.stream.endofday.fill[`EURUSD;1;1e5;1.1;`london;`spot]]} each days;
    p:.qetl.job.stream.carry_marker_path `fx_positions;
    system "mkdir -p ",.qetl.job.bounded.state.lock_dir[];
    system "rm -f ",p,"*";
    .qetl.job.bounded.state.durable_set[p;([] last_end_of_day:enlist marker)];
    marker}

/ The transport a local restart uses: today's log through the handler, and
/ the earlier days from the plant's logs.
.qt.qetl.job.stream.endofday.gap_transport:{[]
    `connect`publisher`subscribe`timer`earlier_days`plant_day`has_log!(
        {[] };
        {[] {[t;x] count x}};
        {[tbls;h;replay] .qetl.tick.replay[.qetl.tick.log_path;{[tbls;h;t;r] if[t in tbls; h[t;r]]}[tbls;h]]};
        {[n;p;f] };
        {[tbls;a;b] .qetl.tick.earlier_days[tbls;a;b]};
        {[] .qetl.tick.plant_day[]};
        {[dt] .qetl.tick.has_log dt})}

/ Restart fx_positions after `missed_logs[marker]`; the errors it logged, and
/ the book it holds.
.qt.qetl.job.stream.endofday.restart_after:{[marker]
    .qt.qetl.job.stream.endofday.missed_logs marker;
    .qetl.job.stream.reset `fx_positions;
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {.qetl.job.stream.start[`fx_positions;.qt.qetl.job.stream.endofday.gap_transport[]]}];
    book:exec base_qty from .qpipe.job.fx_positions.positions;
    system "rm -f ",.qetl.job.stream.carry_marker_path `fx_positions;
    .qetl.tick.reset[];
    (e;book)}

.qt.qetl.job.stream.endofday.test_a_job_down_across_one_end_of_day_restarts_with_its_carried_book:{[t]
    r:.qt.qetl.job.stream.endofday.restart_after[.z.D-2];
    .qunit.assertEquals[r 1;enlist 1.2e6;"1mm carried, plus yesterday's and today's 100k - not flat"];
    .qunit.assertEquals[count r 0;0;"and nothing is reported missing"]};

.qt.qetl.job.stream.endofday.test_a_job_down_across_two_end_of_days_restarts_with_its_carried_book:{[t]
    r:.qt.qetl.job.stream.endofday.restart_after[.z.D-3];
    .qunit.assertEquals[r 1;enlist 1.3e6;"1mm carried, plus three days' 100k fills"];
    .qunit.assertEquals[count r 0;0;"and nothing is reported missing"]};

/ Restart fx_positions after the given logs; (errors; book).
.qt.qetl.job.stream.endofday.restart_flat:{[marker;seeded;through]
    .qt.qetl.job.stream.endofday.missed_logs_to[marker;seeded;through];
    .qetl.job.stream.reset `fx_positions;
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {.qetl.job.stream.start[`fx_positions;.qt.qetl.job.stream.endofday.gap_transport[]]}];
    book:exec base_qty from .qpipe.job.fx_positions.positions;
    system "rm -f ",.qetl.job.stream.carry_marker_path `fx_positions;
    .qetl.tick.reset[];
    (e;book)}

.qt.qetl.job.stream.endofday.test_a_job_flat_at_its_last_end_of_day_catches_up_without_an_error:{[t]
    r:.qt.qetl.job.stream.endofday.restart_flat[.z.D-3;0b;.z.D];
    .qunit.assertEquals[r 1;enlist 3e5;"no snapshot anywhere: the three days' fills are the whole book"];
    .qunit.assertEquals[count r 0;0;"and nothing is reported lost (#1189)"]};

.qt.qetl.job.stream.endofday.test_a_restart_before_the_plant_rolls_replays_each_log_once:{[t]
    / the job's clock is on .z.D but the plant is still on yesterday's log
    / flat at the marker: no snapshot at the head of the log to mask a double replay
    r:.qt.qetl.job.stream.endofday.restart_flat[.z.D-2;0b;.z.D-1];
    .qunit.assertEquals[r 1;enlist 1e5;"yesterday's one 100k fill, not applied twice (#1190)"];
    .qunit.assertEquals[count r 0;0;"and no end of day is judged missed"]};

.qt.qetl.job.stream.endofday.test_a_transport_without_earlier_days_still_reports_the_gap:{[t]
    .qt.qetl.job.stream.endofday.missed_logs .z.D-3;
    .qetl.job.stream.reset `fx_positions;
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {.qetl.job.stream.start[`fx_positions;(`connect`publisher`subscribe`timer#.qt.qetl.job.stream.endofday.gap_transport[]),enlist[`recovery]!enlist 0b]}];
    system "rm -f ",.qetl.job.stream.carry_marker_path `fx_positions;
    .qetl.tick.reset[];
    .qunit.assertEquals[count e;1;"the existing detection still speaks"];
    .qunit.assertEquals[exec base_qty from .qpipe.job.fx_positions.positions;enlist 1e5;"and the book is today's fill alone"]};

/ --- the plant's day, not the job's (#1212) ---------------------------------

/ Start fx_positions after logs from `marker`, on a transport whose plant is on
/ `plant`; the errors logged, and the days catch_up asked earlier_days for.
.qt.qetl.job.stream.endofday.plant_on:{[marker;plant]
    .qt.qetl.job.stream.endofday.missed_logs marker;
    .qetl.job.stream.reset `fx_positions;
    `.qt.qetl.job.stream.endofday.asked set ();
    tr:.qt.qetl.job.stream.endofday.gap_transport[];
    tr[`plant_day]:{[p;unused] p}[plant];
    tr[`earlier_days]:{[tbls;a;b] `.qt.qetl.job.stream.endofday.asked set (a;b); ()};
    tr[`has_log]:{[dt] 0b};
    tr[`subscribe]:{[tbls;h;replay] };
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {[tr;unused] .qetl.job.stream.start[`fx_positions;tr]}[tr]];
    system "rm -f ",.qetl.job.stream.carry_marker_path `fx_positions;
    .qetl.tick.reset[];
    (e;.qt.qetl.job.stream.endofday.asked)}

.qt.qetl.job.stream.endofday.test_a_plant_a_day_ahead_of_the_job_replays_and_judges_by_the_plants_day:{[t]
    / the job's clock says the marker (yesterday) is current; the plant, a day on, says it missed a day
    r:.qt.qetl.job.stream.endofday.plant_on[.z.D-1;.z.D+1];
    .qunit.assertEquals[r 1;(.z.D;.z.D);"catch_up replays the plant's yesterday, which is the job's today"];
    .qunit.assertEquals[count r 0;1;"missed_end_of_day judges it against the plant's day and reports the lost state"];
    .qunit.assertEquals[r[0;0;3]`today;.z.D+1;"naming the plant's day"]};

.qt.qetl.job.stream.endofday.test_a_plant_a_day_behind_the_job_replays_nothing_and_reports_nothing:{[t]
    / the job's clock would call the marker two days stale; the plant, a day back, has seen it through yesterday
    r:.qt.qetl.job.stream.endofday.plant_on[.z.D-2;.z.D-1];
    .qunit.assertEquals[r 1;();"no earlier day is the plant's to replay"];
    .qunit.assertEquals[count r 0;0;"and nothing is judged missed"]};

/ --- the recovery contract (#1220) -----------------------------------------

/ Start fx_positions on gap_transport[] with `tr` altering it; the errors logged.
.qt.qetl.job.stream.endofday.start_with:{[alter]
    .qt.qetl.job.stream.endofday.missed_logs .z.D-2;
    .qetl.job.stream.reset `fx_positions;
    e:.qt.qetl.job.stream.endofday.errors[.qt.support.captured_log[0b] {[alter;unused] .qetl.job.stream.start[`fx_positions;alter .qt.qetl.job.stream.endofday.gap_transport[]]}[alter]];
    system "rm -f ",.qetl.job.stream.carry_marker_path `fx_positions;
    .qetl.tick.reset[];
    e}

.qt.qetl.job.stream.endofday.test_a_carrying_transport_with_all_three_recovery_keys_starts_quietly:{[t]
    .qunit.assertEquals[count .qt.qetl.job.stream.endofday.start_with[::];0;"all of earlier_days, plant_day and has_log: nothing to say"]};

.qt.qetl.job.stream.endofday.test_a_carrying_transport_with_some_recovery_keys_is_refused_by_name:{[t]
    .qt.qetl.job.stream.endofday.missed_logs .z.D-2;
    .qetl.job.stream.reset `fx_positions;
    tr:`earlier_days`plant_day _ .qt.qetl.job.stream.endofday.gap_transport[];
    .qunit.assertThrows[.qetl.job.stream.start[`fx_positions;];tr;"start: the transport for fx_positions has some recovery keys but not all - missing earlier_days, plant_day";"half a recovery contract"];
    .qetl.job.stream.reset `fx_positions;
    system "rm -f ",.qetl.job.stream.carry_marker_path `fx_positions;
    .qetl.tick.reset[]};

.qt.qetl.job.stream.endofday.test_a_carrying_transport_with_no_recovery_keys_logs_an_error_naming_the_job:{[t]
    e:.qt.qetl.job.stream.endofday.start_with {[tr] `earlier_days`plant_day`has_log _ tr};
    errs:e where e[;1]=`fx_positions;
    .qunit.assertEquals[1<=count errs;1b;"an ERROR, not a WARN"];
    .qunit.assertEquals[any errs[;2] like "*restart across an end of day will lose the state this job carries*";1b;"saying what is lost"]};

.qt.qetl.job.stream.endofday.test_a_transport_that_declares_recovery_off_is_quiet:{[t]
    e:.qt.qetl.job.stream.endofday.start_with {[tr] (`earlier_days`plant_day`has_log _ tr),enlist[`recovery]!enlist 0b};
    .qunit.assertEquals[any e[;2] like "*restart across an end of day*";0b;"declared, so not an error"]};
