/ test_endofday.q - end of day for streaming jobs (#943): the on_endofday
/ callback, the plant's roll, and position books that carry across days.
/ .
/ The property the books are held to: a process restarted on the new day,
/ replaying its log, holds the same book as one left running through the
/ day change. Before #943 the running one kept yesterday's positions and
/ the restarted one had only today's.

\d .eodtest

d0:2026.10.08
t1:2026.10.09D09:00:00

fill:{[s;side;size;px;bk;prod]
    ([] time:enlist t1; source_time:enlist t1; sym:enlist s; venue:enlist `v; side:enlist side;
        size:enlist size; price:enlist px; fee:enlist 0f; fee_ccy:enlist `USD; fill_id:enlist `f;
        book:enlist bk; product:enlist prod)}

sent:()
record:{[job] .qetl.job.stream.wire[job;{[t;x] .eodtest.sent,:enlist (t;x); count x}]; `.eodtest.sent set ();}
sent_on:{[t] raze last each sent where t=first each sent}

/ Replay `batches` - (table;rows) pairs, in log order - into a job's
/ on_batch as a restart does: .qetl.job.stream.replaying set.
replay:{[job;batches]
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

saved_running:`symbol$()
setUp_eod:{[] `.eodtest.saved_running set .qetl.job.stream.running;}
tearDown_eod:{[]
    `.qetl.job.stream.running set .eodtest.saved_running;
    `.qetl.job.stream.replaying set 0b;
    .qetl.job.stream.reset each `fx_positions`posbook;
    }

feed:{[nm;eod] `procname`subscribe_to`publishes`period`on_timer`on_endofday!(
    `$string[nm],"1";`symbol$();`symbol$();0D00:00:01;{[] };eod)}

test_on_endofday_must_be_a_function:{[t]
    .qunit.assertThrows[.qetl.job.stream.define[`eod_a;];@[feed[`eod_a;{[d] }];`on_endofday;:;1];
        "*on_endofday must be a function of the date that ended*";"refused at declaration"]};

test_end_of_day_calls_each_running_job_and_survives_one_that_throws:{[t]
    `.eodtest.seen set ();
    if[not `eod_b in key .qetl.job.stream.jobs; .qetl.job.stream.define[`eod_b;feed[`eod_b;{[d] '"boom"}]]];
    if[not `eod_c in key .qetl.job.stream.jobs; .qetl.job.stream.define[`eod_c;feed[`eod_c;{[d] .eodtest.seen,:d}]]];
    `.qetl.job.stream.running set `eod_b`eod_c;
    ok:.qetl.job.stream.end_of_day d0;
    .qunit.assertEquals[ok;enlist `eod_c;"the one that threw is not reported as done"];
    .qunit.assertEquals[seen;enlist d0;"and the other still ran, with the date that ended"]};

test_a_job_that_is_not_running_is_not_called:{[t]
    `.eodtest.seen set ();
    if[not `eod_c in key .qetl.job.stream.jobs; .qetl.job.stream.define[`eod_c;feed[`eod_c;{[d] .eodtest.seen,:d}]]];
    `.qetl.job.stream.running set `symbol$();
    .qetl.job.stream.end_of_day d0;
    .qunit.assertEquals[count seen;0;"declared but not started in this process"]};

/ --- the plant's roll -----------------------------------------------------

test_the_plant_rolls_its_log_to_the_new_day:{[t]
    dir:first system "mktemp -d";
    .qetl.tick.reset[];
    .qetl.tick.schema[`eodx;([] time:`timestamp$(); v:`long$())];
    .qetl.tick.open_log[dir;`eodlog;d0];
    .qetl.tick.publish[`eodx;([] v:enlist 1)];
    first_log:.qetl.tick.log_path;
    ended:.qetl.tick.roll d0+1;
    .qetl.tick.publish[`eodx;([] v:enlist 2)];
    .qunit.assertEquals[ended;d0;"it reports the day that ended"];
    .qunit.assertEquals[.qetl.tick.log_date;d0+1;"and is on the new one"];
    .qunit.assertEquals[(-11!(-2;first_log);-11!(-2;.qetl.tick.log_path));1 1;
        "yesterday's message stays in yesterday's log, today's opens today's"];
    .qetl.tick.reset[]};

test_a_plant_with_no_log_has_no_day_to_end:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.roll;d0;"roll: no log is open*";"refused by name"]};

/ --- fx_positions carries its book ------------------------------------------

test_fx_positions_carries_its_book_and_a_restart_agrees:{[t]
    record[`fx_positions];
    on:.qpipe.job.fx_positions.on_batch;
    on[`executions;fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    / day 1 ends: the book is published as day 2 opens
    n:.qetl.job.stream.snapshot `fx_positions;
    .qunit.assertEquals[n;1;"one position carried"];
    opening:sent_on `fx_position_open;
    .qunit.assertEquals[exec base_qty from opening;enlist 1e6;"the day-1 position"];
    / day 2: one more fill, in the process that kept running
    day2:fill[`EURUSD;1;5e5;1.12;`london;`spot];
    on[`executions;day2];
    running:.qpipe.job.fx_positions.positions;
    / a restart on day 2 replays day 2's log: the opening book, then the fill
    `.qpipe.job.fx_positions.positions set `sym`book`product xkey .qpipe.job.fx_positions.desk_book;
    replay[`fx_positions;((`fx_position_open;opening);(`executions;day2))];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec base_qty from .qpipe.job.fx_positions.positions;enlist 1.5e6;"1mm carried plus 500k today"]};

test_fx_positions_ignores_its_own_live_echo:{[t]
    record[`fx_positions];
    .qpipe.job.fx_positions.on_batch[`executions;fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    before:.qpipe.job.fx_positions.positions;
    (.qetl.job.stream.handler `fx_positions)[`fx_position_open;0#0!before];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;before;"live, the opening book changes nothing"]};

test_an_empty_book_carries_nothing:{[t]
    record[`fx_positions];
    .qunit.assertEquals[.qetl.job.stream.snapshot `fx_positions;0;"nothing to carry"];
    .qunit.assertEquals[count sent;0;"so nothing is published"]};

/ #960: the plant rolls and tells the job asynchronously, so a fill can be
/ logged BEFORE the job's opening row; live, the job applied it on top of
/ its snapshot.
test_fx_positions_keeps_a_fill_logged_before_the_opening_row:{[t]
    record[`fx_positions];
    on:.qpipe.job.fx_positions.on_batch;
    on[`executions;fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `fx_positions;
    opening:sent_on `fx_position_open;
    early:fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;early];
    running:.qpipe.job.fx_positions.positions;
    `.qpipe.job.fx_positions.positions set `sym`book`product xkey .qpipe.job.fx_positions.desk_book;
    replay[`fx_positions;((`executions;early);(`fx_position_open;opening))];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec base_qty, fill_count from .qpipe.job.fx_positions.positions;`base_qty`fill_count!(enlist 6e5;enlist 2);"600k net, two fills"]};

test_fx_positions_first_day_without_an_opening_row_replays_as_before:{[t]
    record[`fx_positions];
    on:.qpipe.job.fx_positions.on_batch;
    f:fill[`EURUSD;1;1e6;1.1;`london;`spot];
    on[`executions;f];
    running:.qpipe.job.fx_positions.positions;
    `.qpipe.job.fx_positions.positions set `sym`book`product xkey .qpipe.job.fx_positions.desk_book;
    replay[`fx_positions;enlist (`executions;f)];
    .qunit.assertEquals[.qpipe.job.fx_positions.positions;running;"no opening row: the log alone rebuilds the book"]};

/ --- posbook carries its book ------------------------------------------------

test_posbook_carries_its_book_and_a_restart_agrees:{[t]
    record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    on[`executions;fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `posbook;
    opening:sent_on `position_open;
    day2:fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;day2];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    replay[`posbook;((`position_open;opening);(`executions;day2))];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec qty from .qpipe.job.posbook.book;enlist 6e5;"1mm carried, 400k sold today"]};

/ #960: posbook's avg_price and realised P&L depend on lot order, so a fill
/ logged before the opening row is re-applied after it, not summed.
test_posbook_keeps_a_fill_logged_before_the_opening_row:{[t]
    record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    on[`executions;fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `posbook;
    opening:sent_on `position_open;
    early:fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;early];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    replay[`posbook;((`executions;early);(`position_open;opening))];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"restarted and kept-running books agree"];
    .qunit.assertEquals[exec qty, realized_pnl from .qpipe.job.posbook.book;`qty`realized_pnl!(enlist 6e5;enlist 2000f);"600k left, 400k*0.005 realised"];
    .qunit.assertFalse[`posbook in key .qetl.job.stream.carry_log;"the recorded batches are forgotten"]};

test_posbook_first_day_without_an_opening_row_replays_as_before:{[t]
    record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    f:fill[`EURUSD;1;1e6;1.1;`london;`spot];
    on[`executions;f];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    replay[`posbook;enlist (`executions;f)];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"no opening row: the log alone rebuilds the book"];
    .qunit.assertFalse[`posbook in key .qetl.job.stream.carry_log;"replay state is reset"]};

test_posbook_restart_with_the_opening_row_first_is_unchanged:{[t]
    record[`posbook];
    on:.qpipe.job.posbook.on_batch;
    on[`executions;fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    .qetl.job.stream.snapshot `posbook;
    opening:sent_on `position_open;
    day2:fill[`EURUSD;-1;4e5;1.105;`london;`spot];
    on[`executions;day2];
    running:.qpipe.job.posbook.book;
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.position_book;
    replay[`posbook;((`position_open;opening);(`executions;day2))];
    .qunit.assertEquals[.qpipe.job.posbook.book;running;"no early fill: same book"]};

/ --- the shell's carry (#963) ---------------------------------------------

test_a_malformed_carry_is_refused:{[t]
    d:`procname`subscribe_to`publishes`on_batch`replay`carry!(`eodcar1;enlist `executions;enlist `eodx_open;{[t;x]};1b;0);
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*carry must be a dict of state and table*";"not a dict"];
    d[`carry]:`state`table!(`book;`elsewhere);
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*must be one the job publishes*";"a table it does not publish"];
    d[`carry]:`state`table!(`book;`eodx_open); d[`replay]:0b;
    .qunit.assertThrows[.qetl.job.stream.define[`eod_car;];d;"*needs replay 1b*";"nothing would read it back"]};

test_a_batch_beyond_the_window_is_not_held:{[t]
    / recording is bounded: past the window, nothing more is kept
    record[`fx_positions];
    late:update time:.eodtest.t1+0D00:05 from fill[`EURUSD;1;1e6;1.1;`london;`spot];
    h:.qetl.job.stream.handler `fx_positions;
    `.qetl.job.stream.replaying set 1b;
    h[`executions;update time:.eodtest.t1 from fill[`EURUSD;1;1e6;1.1;`london;`spot]];
    h[`executions;late];
    held:count last .qetl.job.stream.carry_log `fx_positions;
    `.qetl.job.stream.replaying set 0b;
    .qetl.job.stream.replayed `fx_positions;
    .qunit.assertEquals[held;0;"the 5-minute-late batch closed the window, so nothing is held"]};

\d .
