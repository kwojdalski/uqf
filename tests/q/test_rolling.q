/ test_rolling.q - .qetl.job.stream.rolling: a job that keeps a statistic over
/ the last N minutes (#1224).
/ .
/ The kind is tested through a statistic of its own - count, sum and mean of
/ a value per key - on a stream of its own, so the shell's rules (the window
/ boundary, lateness, eviction, restart) are measured apart from any instance.
/ The instance's ratios are test_order_quality.q's.


/ The jobs this suite defines are process-global; leave the registry as found (#1375).
.qt.qetl.job.stream.rolling.beforeNamespace_save_jobs:{[] .qt.support.save_stream_jobs[`.qt.qetl.job.stream.rolling]}
.qt.qetl.job.stream.rolling.afterNamespace_restore_jobs:{[] .qt.support.restore_stream_jobs[`.qt.qetl.job.stream.rolling]}

.qt.qetl.job.stream.rolling.t0:2026.10.09D10:00:00

/ The clock the jobs read, which the tests move.
.qt.qetl.job.stream.rolling.clock:.qt.qetl.job.stream.rolling.t0

.qt.qetl.job.stream.rolling.sent:()

/ The events: `time` is when the plant received the row, `t` when it happened.
.qt.qetl.job.stream.rolling.events:([] time:`timestamp$(); t:`timestamp$(); k:`symbol$(); v:`float$())

/ What the transform is offered, and what it returns.
.qt.qetl.job.stream.rolling.offered:update window:`timespan$(), as_of:`timestamp$(), inside:`boolean$() from .qt.qetl.job.stream.rolling.events
.qt.qetl.job.stream.rolling.stats:([] k:`symbol$(); window:`timespan$(); as_of:`timestamp$(); n:`long$(); total:`float$(); mean:`float$())

/ Count, sum and mean of the value over the rows inside the window. A key with
/ none inside publishes its identity: 0, 0 and a null mean.
/ @param x the offered rows
/ @return one row per key per window
.qt.qetl.job.stream.rolling.stat:{[x]
    if[0=count x; :0#.qt.qetl.job.stream.rolling.stats];
    x:update w:`float$inside, c:`long$inside from x;
    0!select n:sum c, total:sum v*w, mean:(sum v*w)%sum c by k, window, as_of from x}

.qt.qetl.job.stream.rolling.define_transform:{[]
    if[`rollingtest_stat in .qetl.transform.defined[]; :()];
    .qetl.transform.define[`rollingtest_stat;`inputs`output`fn`examples!(
        enlist[`ev]!enlist .qt.qetl.job.stream.rolling.offered;
        .qt.qetl.job.stream.rolling.stats;
        .qt.qetl.job.stream.rolling.stat;
        / a at 10:00:30 (1m: in) and 09:59:30 (1m: out), b only out of the 1m window
        enlist `inputs`expected!(
            enlist[`ev]!enlist ([] time:4#2026.10.09D10:01:00; t:2026.10.09D10:00:30 2026.10.09D09:59:30 2026.10.09D10:00:30 2026.10.09D09:59:30;
                k:`a`a`b`b; v:1 3 5 7f; window:4#0D00:01; as_of:4#2026.10.09D10:01:00; inside:1010b);
            ([] k:`a`b; window:2#0D00:01; as_of:2#2026.10.09D10:01:00; n:1 1; total:1 5f; mean:1 5f)))];
    }

.qt.qetl.job.stream.rolling.decl:{[] `procname`events`by`event_time`transform`publishes`windows`lateness`period!(
    `rtd1;`rt_events;`k;`t;`rollingtest_stat;`rt_out;0D00:01 0D00:05;0D00:00:10;0D00:00:01)}

/ A rolling job named nm over the test statistic: state emptied, publish recorded.
.qt.qetl.job.stream.rolling.job:{[nm] .qt.qetl.job.stream.rolling.jobwith[nm;.qt.qetl.job.stream.rolling.decl[]]}

/ job over a declaration of its own (a different lateness or period).
/ @param nm the job name
/ @param dc the declaration
/ @return the name
.qt.qetl.job.stream.rolling.jobwith:{[nm;dc]
    .qt.qetl.job.stream.rolling.define_transform[];
    if[not nm in .qetl.job.stream.rolling.defined[];
        .qetl.job.stream.at_rolling[nm;@[dc;`procname;:;`$string[nm],"1"]]];
    ns:.qetl.job.stream.namespace nm;
    .qetl.job.stream.reset nm;
    (` sv ns,`now) set {[] .qt.qetl.job.stream.rolling.clock};
    `.qt.qetl.job.stream.rolling.clock set .qt.qetl.job.stream.rolling.t0;
    `.qt.qetl.job.stream.rolling.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.rolling.sent,:enlist (t;x); count x}];
    nm}

.qt.qetl.job.stream.rolling.ns:{[nm;k] get ` sv (.qetl.job.stream.namespace nm),k}

/ An event at et for key k, received at rcv.
.qt.qetl.job.stream.rolling.row:{[et;rcv;k;v] ([] time:enlist rcv; t:enlist et; k:enlist k; v:enlist v)}

/ Feed an event that is received the instant it happens.
.qt.qetl.job.stream.rolling.feed:{[nm;et;k;v] `.qt.qetl.job.stream.rolling.clock set et; (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;.qt.qetl.job.stream.rolling.row[et;et;k;v]]}

/ Everything published, as one table.
.qt.qetl.job.stream.rolling.published:{[] $[count .qt.qetl.job.stream.rolling.sent; raze last each .qt.qetl.job.stream.rolling.sent; 0#.qt.qetl.job.stream.rolling.stats]}

/ One key's published row for a window.
.qt.qetl.job.stream.rolling.cell:{[x;key_;w] first select from x where k=key_, window=w}

/ --- declaring -----------------------------------------------------------

.qt.qetl.job.stream.rolling.test_a_declaration_missing_a_key_is_refused_by_name:{[t]
    .qt.qetl.job.stream.rolling.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_rolling[`rt_x;];`lateness _ .qt.qetl.job.stream.rolling.decl[];
        "*rolling job rt_x is missing lateness*";"the key it lacks"]};

.qt.qetl.job.stream.rolling.test_a_window_that_is_not_positive_is_refused:{[t]
    .qt.qetl.job.stream.rolling.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_rolling[`rt_x;];@[.qt.qetl.job.stream.rolling.decl[];`windows;:;0D00:01 0D];
        "*rt_x's windows must be timespans, each positive*";"a window has to have an extent"];
    .qunit.assertThrows[.qetl.job.stream.at_rolling[`rt_x;];@[.qt.qetl.job.stream.rolling.decl[];`windows;:;60 300];
        "*rt_x's windows must be timespans, each positive*";"longs are not seconds"]};

.qt.qetl.job.stream.rolling.test_an_unknown_key_is_refused:{[t]
    .qt.qetl.job.stream.rolling.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_rolling[`rt_x;];.qt.qetl.job.stream.rolling.decl[],enlist[`width]!enlist 0D00:01;
        "*declares width, which a rolling job does not take*";"a bars key"]};

.qt.qetl.job.stream.rolling.test_a_transform_without_the_shell_columns_is_refused:{[t]
    .qt.qetl.job.stream.rolling.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_rolling[`rt_x;];@[.qt.qetl.job.stream.rolling.decl[];`transform;:;`exec_bars];
        "*rt_x's transform input carries no window, as_of, inside - the shell adds them*";"a bars transform"]};

.qt.qetl.job.stream.rolling.test_a_grouping_the_shell_owns_is_refused:{[t]
    .qt.qetl.job.stream.rolling.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_rolling[`rt_x;];@[.qt.qetl.job.stream.rolling.decl[];`by;:;`window];
        "*rt_x's by names window, which the shell adds*";"window is the shell's"]};

.qt.qetl.job.stream.rolling.test_windows_are_kept_ascending_and_distinct:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_w;
    d:.qetl.job.stream.rolling.def nm;
    .qunit.assertEquals[d`windows;0D00:01 0D00:05;"declared order"];
    .qetl.job.stream.at_rolling[`rt_w2;@[@[.qt.qetl.job.stream.rolling.decl[];`procname;:;`rt_w21];`windows;:;0D00:05 0D00:01 0D00:05]];
    .qunit.assertEquals[(.qetl.job.stream.rolling.def `rt_w2)`windows;0D00:01 0D00:05;"sorted and distinct"]};

.qt.qetl.job.stream.rolling.test_the_kind_registers_replay_and_the_end_of_day_hook:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_h;
    d:.qetl.job.stream.def nm;
    .qunit.assertTrue[d`replay;"it replays"];
    .qunit.assertEquals[d`restore_from;enlist `rt_out;"from its own output"];
    .qunit.assertTrue[`on_endofday in key d;"and the runner can end its day"]};

/ --- the window -----------------------------------------------------------

.qt.qetl.job.stream.rolling.test_a_row_exactly_n_old_is_outside_and_one_at_the_instant_is_inside:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_a;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:05;`a;4f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:01;`a;1f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of;`a;2f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    p:.qt.qetl.job.stream.rolling.published[];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[p;`a;0D00:01])`n`total;(1;2f);"1m: the row at as_of-1m is out, the row at as_of is in"];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[p;`a;0D00:05])`n`total;(2;3f);"5m: the row at as_of-5m is out, the other two are in"]};

.qt.qetl.job.stream.rolling.test_a_row_after_the_instant_is_in_the_next_publication_not_this_one:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_b;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of;`a;1f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of+0D00:00:00.000000001;`a;10f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:01])`total;1f;"a nanosecond after is not yet in"];
    `.qt.qetl.job.stream.rolling.sent set ();
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of+0D00:00:01;
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:01])`total;11f;"and is in the next"]};

.qt.qetl.job.stream.rolling.test_every_window_length_is_published_for_each_key:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_c;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:04:00;`a;1f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:02:00;`a;2f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:00:30;`a;4f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:00:30;`b;8f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    p:.qt.qetl.job.stream.rolling.published[];
    .qunit.assertEquals[count p;4;"two keys by two lengths"];
    .qunit.assertEquals[(p`k;p`window);(`a`a`b`b;0D00:01 0D00:05 0D00:01 0D00:05);"sorted by key then window"];
    .qunit.assertEquals[p`n;1 3 1 1;"counts"];
    .qunit.assertEquals[p`total;4 7 8 8f;"sums"];
    .qunit.assertEquals[p`as_of;4#as_of;"each stamped with the instant"];
    .qunit.assertEquals[first first .qt.qetl.job.stream.rolling.sent;`rt_out;"to the declared table"]};

.qt.qetl.job.stream.rolling.test_the_windows_are_cut_on_a_grid_of_the_period:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_d;
    .qt.qetl.job.stream.rolling.feed[nm;.qt.qetl.job.stream.rolling.t0+0D00:09:59;`a;1f];
    `.qt.qetl.job.stream.rolling.clock set .qt.qetl.job.stream.rolling.t0+0D00:10:00.4;
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.clock;2;"published"];
    .qunit.assertEquals[(first .qt.qetl.job.stream.rolling.published[])`as_of;.qt.qetl.job.stream.rolling.t0+0D00:10:00;"at the period's grid point below the clock"];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.t0+0D00:10:00.9;0;"a second tick in the same period publishes nothing"];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.sent;1;"once"];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.t0+0D00:10:01;2;"the next grid point does"];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`last_as_of];.qt.qetl.job.stream.rolling.t0+0D00:10:01;"and is the newest published"]};

/ --- the identity ---------------------------------------------------------

.qt.qetl.job.stream.rolling.test_a_key_quiet_in_a_short_window_publishes_the_identity_for_it:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_e;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:03:00;`a;5f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    p:.qt.qetl.job.stream.rolling.published[];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[p;`a;0D00:01])`n`total;(0;0f);"1m: nothing inside: 0 and 0"];
    .qunit.assertTrue[null (.qt.qetl.job.stream.rolling.cell[p;`a;0D00:01])`mean;"and a null mean, not a mean of zero"];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[p;`a;0D00:05])`n`mean;(1;5f);"5m: the row"]};

.qt.qetl.job.stream.rolling.test_a_key_with_no_row_in_the_longest_window_publishes_nothing:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_f;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:06:00;`gone;5f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:00:10;`a;1f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    .qunit.assertEquals[distinct .qt.qetl.job.stream.rolling.published[]`k;enlist `a;"a key 6 minutes quiet is not published, so the key set cannot grow"]};

.qt.qetl.job.stream.rolling.test_nothing_buffered_publishes_nothing_but_advances:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_g;
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.t0+0D00:10:00;0;"no rows"];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.sent;0;"no publish"];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`last_as_of];.qt.qetl.job.stream.rolling.t0+0D00:10:00;"but the grid point is spent"]};

/ --- eviction -------------------------------------------------------------

.qt.qetl.job.stream.rolling.test_the_buffer_is_bounded_by_the_longest_window_plus_lateness:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_i;
    / one event a second for two hours, a publication every ten seconds
    sizes:{[nm;s]
        .qt.qetl.job.stream.rolling.feed[nm;.qt.qetl.job.stream.rolling.t0+0D00:00:01*s;`a;1f];
        n:0N;
        if[0=s mod 10; .qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.clock; n:count .qt.qetl.job.stream.rolling.ns[nm;`pending]];
        n}[nm] each 1+til 7200;
    biggest:max sizes where not null sizes;
    / 5m + 10s of lateness at one row a second, and a tick's worth
    .qunit.assertTrue[biggest<=320;"never more than the span of rows the longest window and lateness keep"];
    .qunit.assertTrue[biggest>=300;"and not fewer: the window's own rows are kept"];
    .qunit.assertEquals[count distinct (.qt.qetl.job.stream.rolling.published[])`as_of;720;"a publication every ten seconds"]};

.qt.qetl.job.stream.rolling.test_a_replay_is_bounded_too:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_j;
    `.qetl.job.stream.replaying set 1b;
    r:@[{[nm] {[nm;s] (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;.qt.qetl.job.stream.rolling.row[.qt.qetl.job.stream.rolling.t0+0D00:00:01*s;.qt.qetl.job.stream.rolling.t0+0D00:00:01*s;`a;1f]]}[nm] each 1+til 7200; 1b};nm;{x}];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[r;1b;"the replay ran"];
    .qunit.assertTrue[310>=count .qt.qetl.job.stream.rolling.ns[nm;`pending];"the log's own clock evicts: a day's log is not a day's buffer"]};

.qt.qetl.job.stream.rolling.test_a_batch_after_the_boundary_does_not_evict_what_the_next_grid_point_needs:{[t]
    / no lateness, a 10s grid: a batch lands 0.5s after the boundary, before the timer
    nm:.qt.qetl.job.stream.rolling.jobwith[`rt_m;@[.qt.qetl.job.stream.rolling.decl[];`lateness`period;:;(0D;0D00:00:10)]];
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:10;
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:04:59.8;`a;1f];
    .qt.qetl.job.stream.rolling.feed[nm;as_of+0D00:00:00.5;`b;1f];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.ns[nm;`pending];2;"the row still inside the 5m window of the grid point is kept"];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of+0D00:00:00.6;
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:05])`n`total;(1;1f);"and is published"]};

/ --- lateness -------------------------------------------------------------

.qt.qetl.job.stream.rolling.test_a_late_row_within_the_lateness_amends_the_next_publication:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_k;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:00:30;`a;1f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    / happened 3s before the publication, received 5s after it: 8s late of 10s allowed
    `.qt.qetl.job.stream.rolling.clock set as_of+0D00:00:05;
    (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;.qt.qetl.job.stream.rolling.row[as_of-0D00:00:03;.qt.qetl.job.stream.rolling.clock;`a;10f]];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.ns[nm;`dropped];0;"not dropped"];
    `.qt.qetl.job.stream.rolling.sent set ();
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of+0D00:00:10;
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:01])`n`total;(2;11f);"in the next publication"]};

.qt.qetl.job.stream.rolling.test_a_row_beyond_the_lateness_is_dropped_and_not_published:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_l;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    `.qt.qetl.job.stream.rolling.clock set as_of;
    / happened 11s before it was received: one second past the 10s allowed
    (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;.qt.qetl.job.stream.rolling.row[as_of-0D00:00:11;as_of;`a;10f]];
    (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;.qt.qetl.job.stream.rolling.row[as_of-0D00:00:10;as_of;`a;1f]];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.ns[nm;`dropped])`reason`v;(enlist `late;enlist 10f);"the late one is on the ledger, with why"];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.ns[nm;`pending];1;"and the other is buffered: exactly the lateness is not late"];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:01])`total;1f;"only the buffered row is published"]};

.qt.qetl.job.stream.rolling.test_a_row_without_an_event_time_is_dropped:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_m;
    `.qt.qetl.job.stream.rolling.clock set .qt.qetl.job.stream.rolling.t0;
    (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;.qt.qetl.job.stream.rolling.row[0Np;.qt.qetl.job.stream.rolling.t0;`a;1f]];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.ns[nm;`dropped])`reason;enlist `no_event_time;"it is in no window"];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.ns[nm;`pending];0;"and not buffered"]};

/ --- publish before advance -----------------------------------------------

.qt.qetl.job.stream.rolling.test_a_publish_that_throws_leaves_the_buffer_and_the_instant_for_the_next_tick:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_n;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of-0D00:00:30;`a;1f];
    .qetl.job.stream.wire[nm;{[t;x] '"plant down"}];
    .qunit.assertThrows[.qt.qetl.job.stream.rolling.ns[nm;`evaluate];as_of;"*plant down*";"the error reaches the timer"];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.ns[nm;`pending];1;"still buffered"];
    .qunit.assertTrue[null .qt.qetl.job.stream.rolling.ns[nm;`last_as_of];"the instant is not spent"];
    .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.rolling.sent,:enlist (t;x); count x}];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;2;"published on the next tick"]};

/ --- end of day -----------------------------------------------------------

.qt.qetl.job.stream.rolling.test_the_windows_roll_on_across_midnight:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_o;
    d1:2026.10.09;
    .qt.qetl.job.stream.rolling.feed[nm;2026.10.09D23:59:30;`a;1f];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`on_endofday] d1;1;"the end of day reports the rows it keeps"];
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.sent;0;"and publishes nothing: there is no day to close"];
    .qt.qetl.job.stream.rolling.feed[nm;2026.10.10D00:00:30;`a;2f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] 2026.10.10D00:01:00;
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:05])`n`total;(2;3f);"the 5m window at 00:01 holds both sides of the roll"];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:01])`n`total;(1;2f);"the 1m window only the new day's"]};

/ --- restart ----------------------------------------------------------------

/ The log the plant would hold for a run: the events and the job's own output.
.qt.qetl.job.stream.rolling.wal:()

/ Run events (time; rows) and ticks live, logging what the plant would.
.qt.qetl.job.stream.rolling.live:{[nm;script]
    `.qt.qetl.job.stream.rolling.wal set ();
    .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.rolling.sent,:enlist (t;x); .qt.qetl.job.stream.rolling.wal,:enlist (t;x); count x}];
    {[nm;e]
        `.qt.qetl.job.stream.rolling.clock set e 0;
        if[`tick=e 1; .qt.qetl.job.stream.rolling.ns[nm;`evaluate] e 0];
        if[not `tick=e 1;
            `.qt.qetl.job.stream.rolling.wal set .qt.qetl.job.stream.rolling.wal,enlist (`rt_events;e 2);
            (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;e 2]];
        }[nm] each script;
    }

/ A restart: empty the job, replay the log with publish muted and the process clock a day on.
.qt.qetl.job.stream.rolling.restart:{[nm;entries]
    .qetl.job.stream.reset nm;
    `.qt.qetl.job.stream.rolling.clock set .qt.qetl.job.stream.rolling.t0+1D;
    `.qetl.job.stream.replaying set 1b;
    h:.qetl.job.stream.handler nm;
    r:.[{[h;lg] {[h;b] h . b}[h] each lg; 1b};(h;entries);{x}];
    `.qetl.job.stream.replaying set 0b;
    if[not 1b~r; 'r]}

/ Events and ticks: arrivals with one late, ticks every ten seconds, a quiet key.
.qt.qetl.job.stream.rolling.script:{[]
    ev:{[et;rcv;k;v] (rcv;`rt_events;.qt.qetl.job.stream.rolling.row[et;rcv;k;v])};
    (ev[.qt.qetl.job.stream.rolling.t0+0D00:00:05;.qt.qetl.job.stream.rolling.t0+0D00:00:05;`a;1f];
     ev[.qt.qetl.job.stream.rolling.t0+0D00:00:20;.qt.qetl.job.stream.rolling.t0+0D00:00:20;`b;2f];
     (.qt.qetl.job.stream.rolling.t0+0D00:00:30;`tick;());
     ev[.qt.qetl.job.stream.rolling.t0+0D00:00:28;.qt.qetl.job.stream.rolling.t0+0D00:00:33;`a;4f];
     ev[.qt.qetl.job.stream.rolling.t0+0D00:00:01;.qt.qetl.job.stream.rolling.t0+0D00:00:34;`a;99f];
     ev[.qt.qetl.job.stream.rolling.t0+0D00:01:10;.qt.qetl.job.stream.rolling.t0+0D00:01:10;`a;8f];
     (.qt.qetl.job.stream.rolling.t0+0D00:01:30;`tick;());
     ev[.qt.qetl.job.stream.rolling.t0+0D00:04:50;.qt.qetl.job.stream.rolling.t0+0D00:04:50;`b;16f];
     (.qt.qetl.job.stream.rolling.t0+0D00:05:00;`tick;());
     (.qt.qetl.job.stream.rolling.t0+0D00:05:30;`tick;());
     (.qt.qetl.job.stream.rolling.t0+0D00:07:00;`tick;()))}

.qt.qetl.job.stream.rolling.test_a_restart_publishes_what_the_running_job_did:{[t]
    sc:.qt.qetl.job.stream.rolling.script[];
    nm:.qt.qetl.job.stream.rolling.job`rt_p;
    .qt.qetl.job.stream.rolling.live[nm;sc];
    running:.qt.qetl.job.stream.rolling.published[];
    / the same run, restarted after each step: replay the log so far, carry on
    bad:{[sc;running;k]
        nm:.qt.qetl.job.stream.rolling.job`rt_p;
        .qt.qetl.job.stream.rolling.live[nm;k#sc];
        before:.qt.qetl.job.stream.rolling.published[];
        lg:.qt.qetl.job.stream.rolling.wal;
        .qt.qetl.job.stream.rolling.restart[nm;lg];
        `.qt.qetl.job.stream.rolling.sent set ();
        `.qt.qetl.job.stream.rolling.wal set lg;
        .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.rolling.sent,:enlist (t;x); count x}];
        {[nm;e]
            `.qt.qetl.job.stream.rolling.clock set e 0;
            if[`tick=e 1; .qt.qetl.job.stream.rolling.ns[nm;`evaluate] e 0];
            if[not `tick=e 1; (.qt.qetl.job.stream.rolling.ns[nm;`on_batch])[`rt_events;e 2]];
            }[nm] each k _ sc;
        not running~before,.qt.qetl.job.stream.rolling.published[]}[sc;running] each 1+til count sc;
    .qunit.assertTrue[0<count running;"the script publishes"];
    .qunit.assertTrue[0=sum bad;"a restart at any step publishes the same series (restart points that differ)"]};

.qt.qetl.job.stream.rolling.test_a_restart_after_midnight_withholds_the_windows_it_cannot_cover:{[t]
    / the new day's log holds 00:02:30 onward; the job restarts at 00:03 and ticks
    / the roll is local midnight, so pin the zone to UTC: the test must not depend on the host's
    old:.qetl.job.stream.rolling.zone_offset;
    .qetl.job.stream.rolling.zone_offset:{0D00:00};
    nm:.qt.qetl.job.stream.rolling.job`rt_q;
    lg:enlist (`rt_events;.qt.qetl.job.stream.rolling.row[2026.10.10D00:02:30;2026.10.10D00:02:30;`a;1f]);
    r:.[{[nm;lg] .qt.qetl.job.stream.rolling.restart[nm;lg]; .qt.qetl.job.stream.rolling.ns[nm;`covered_from]};(nm;lg);{x}];
    .qetl.job.stream.rolling.zone_offset:old;
    .qunit.assertEquals[r;2026.10.10D00:00:00;"covered from the roll, which the log begins at"];
    `.qt.qetl.job.stream.rolling.clock set 2026.10.10D00:03:00;
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.clock;
    p:.qt.qetl.job.stream.rolling.published[];
    .qunit.assertEquals[distinct p`window;enlist 0D00:01;"the 5m window reaches before the roll: withheld, not published over part of its span"];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`withheld];enlist 0D00:05;"and said so"];
    `.qt.qetl.job.stream.rolling.clock set 2026.10.10D00:05:00;
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.clock;
    .qunit.assertTrue[0D00:05 in (.qt.qetl.job.stream.rolling.published[])`window;"covered from 00:05"];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`withheld];`timespan$();"and no longer withheld"]};

.qt.qetl.job.stream.rolling.test_a_restart_on_a_non_utc_host_covers_from_the_local_roll:{[t]
    / the plant rolls its log at LOCAL midnight (run_stream names it by .z.D);
    / on a host an hour ahead of UTC the 10-10 log begins at 10-09 23:00 UTC
    old:.qetl.job.stream.rolling.zone_offset;
    .qetl.job.stream.rolling.zone_offset:{0D01:00};
    nm:.qt.qetl.job.stream.rolling.job`rt_z;
    lg:enlist (`rt_events;.qt.qetl.job.stream.rolling.row[2026.10.09D23:02:30;2026.10.09D23:02:30;`a;1f]);
    r:.[{[nm;lg] .qt.qetl.job.stream.rolling.restart[nm;lg]; .qt.qetl.job.stream.rolling.ns[nm;`covered_from]};(nm;lg);{x}];
    .qetl.job.stream.rolling.zone_offset:old;
    .qunit.assertEquals[r;2026.10.09D23:00:00;"covered from the local roll in UTC, not from the UTC date's midnight"];
    `.qt.qetl.job.stream.rolling.clock set 2026.10.09D23:03:00;
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] .qt.qetl.job.stream.rolling.clock;
    .qunit.assertEquals[distinct (.qt.qetl.job.stream.rolling.published[])`window;enlist 0D00:01;"the 5m window reaches before the roll: withheld"]};

.qt.qetl.job.stream.rolling.test_a_replayed_publication_is_not_published_again:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_r;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    earlier:([] time:enlist as_of; k:enlist `a; window:enlist 0D00:01; as_of:enlist as_of; n:enlist 1; total:enlist 1f; mean:enlist 1f);
    .qt.qetl.job.stream.rolling.restart[nm;enlist (`rt_out;earlier)];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`last_as_of];as_of;"the log's own output marks the instant spent"];
    .qunit.assertEquals[.qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of+0D00:00:00.5;0;"so its grid point is not published again"]};

/ --- the twin -------------------------------------------------------------

.qt.qetl.job.stream.rolling.test_the_backfill_twin_computes_the_series_the_live_job_publishes:{[t]
    nm:.qt.qetl.job.stream.rolling.job`rt_s;
    sc:.qt.qetl.job.stream.rolling.script[];
    .qt.qetl.job.stream.rolling.live[nm;sc];
    rows:raze {x 2} each sc where not `tick=sc[;1];
    / A twin reads history, which holds every row, the one dropped as too late
    / included; the live job had only what had arrived by each instant and
    / accepted. Given the same input - the rows received by the instant and
    / within the lateness - the two are the same series.
    accepted:rows where (rows[`time]-rows`t)<=0D00:00:10;
    grid:.qt.qetl.job.stream.rolling.t0+0D00:00:30 0D00:01:30 0D00:05:00 0D00:05:30 0D00:07:00;
    twin:raze {[nm;accepted;a] .qetl.job.stream.rolling.series[nm;select from accepted where time<=a;enlist a]}[nm;accepted] each grid;
    .qunit.assertTrue[0<count twin;"the twin produces a series"];
    .qunit.assertEquals[twin;.qt.qetl.job.stream.rolling.published[];"the same input, one statistic, two lifecycles"];
    / and the full history differs from the live series exactly where a row came late:
    / the 10:00:30 publication could not hold the row that arrived at 10:00:33
    final:.qetl.job.stream.rolling.series[nm;accepted;enlist .qt.qetl.job.stream.rolling.t0+0D00:00:30];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[final;`a;0D00:01])`n;2;"history is complete: the late-arriving row is in"];
    .qunit.assertEquals[(.qt.qetl.job.stream.rolling.cell[.qt.qetl.job.stream.rolling.published[];`a;0D00:01])`n;1;"live, at the time, it was not - the next publication carries it"]};

/ --- the quality gate (#1232) -----------------------------------------------

/ A quality gate that names the whole batch as offending when flagged, and
/ passes when not (#1232: the kind forwards check/on_fail to define).
.qt.qetl.job.stream.rolling.gate:{[flag;x] n:$[flag;1;0]; ([] check:n#`always; status:n#`bad; detail:n#enlist "flagged")}

.qt.qetl.job.stream.rolling.test_a_rolling_job_can_declare_a_check_that_withholds_a_publication:{[t]
    .qt.qetl.job.stream.rolling.define_transform[];
    .qetl.job.stream.at_rolling[`rt_chk;@[.qt.qetl.job.stream.rolling.decl[];`procname;:;`rtchk1],`check`on_fail!(.qt.qetl.job.stream.rolling.gate[1b];`hold)];
    nm:.qt.qetl.job.stream.rolling.job`rt_chk;
    as_of:.qt.qetl.job.stream.rolling.t0+0D00:10:00;
    .qt.qetl.job.stream.rolling.feed[nm;as_of;`a;1f];
    .qt.qetl.job.stream.rolling.ns[nm;`evaluate] as_of;
    .qunit.assertEquals[count .qt.qetl.job.stream.rolling.sent;0;"the failing check withheld the publication"];
    .qunit.assertEquals[(.qetl.stream_health.of nm)`failed;1;"and counted it"];
    .qetl.job.stream.at_rolling[`rt_chk_ok;@[.qt.qetl.job.stream.rolling.decl[];`procname;:;`rtchkok1],`check`on_fail!(.qt.qetl.job.stream.rolling.gate[0b];`hold)];
    ok:.qt.qetl.job.stream.rolling.job`rt_chk_ok;
    .qt.qetl.job.stream.rolling.feed[ok;as_of;`a;1f];
    .qt.qetl.job.stream.rolling.ns[ok;`evaluate] as_of;
    .qunit.assertTrue[0<count .qt.qetl.job.stream.rolling.sent;"a passing check lets it through"]};
