/ test_half_open_windows.q - one contract for every bounded read: [start; end) (#2055).
/ .
/ A request for [a;b) and a request for [b;c) must tile [a;c): the event AT b is in
/ the second and not the first, an event at c is in neither, and the two results
/ concatenated are the one combined request. The same fixture is used everywhere:
/ an event at a, THREE events sharing the instant b, and one at c - with b at
/ midnight, so the boundary is also a date-partition boundary, and a, c a tick
/ either side of it. The tick is a millisecond, a microsecond and a nanosecond in
/ turn: a bound written as "the previous millisecond" is right at one precision
/ and wrong at the others, and these are the cases that tell.

.qt.qetl.half_open.b:2026.10.10D00:00:00.000000000
.qt.qetl.half_open.ticks:0D00:00:00.001000000 0D00:00:00.000001000 0D00:00:00.000000001

/ a, b, b, b, c - the fixture's event times for a tick u
.qt.qetl.half_open.events:{[u] b:.qt.qetl.half_open.b; (b-u),b,b,b,b+u}

/ --- the shipped HDB sources' queries ----------------------------------------

/ A handle that evaluates the query where it stands, as a stand-in for the HDB.
.qt.qetl.half_open.hdb:{[msg] value msg}

/ source -> the table its query reads
.qt.qetl.half_open.tables:`hdb_executions`hdb_market_data`hdb_crypto_book!`executions`market_data`crypto_book

/ Put a date-partitioned stand-in for `tbl` in the root, built from the source's
/ own fixture row (so every column the query selects is there) with the event
/ times given; run f; put the root back as it was.
.qt.qetl.half_open.with_table:{[source;ts;f]
    tbl:.qt.qetl.half_open.tables source;
    tc:(.qetl.source.def source)`time_column;
    row:1#.qetl.source.def[source][`fixture][];
    t:![(count ts)#row;();0b;(enlist tc)!enlist ts];
    had:$[tbl in key `.; (1b;get tbl); (0b;::)];
    tbl set update date:`date$ts from t;
    r:@[f;::;{[e] `threw,e}];
    $[first had; tbl set last had; ![`.;();0b;enlist tbl]];
    r}

/ the event times a source's query returns for [lo;hi), over a table holding ts
.qt.qetl.half_open.read:{[source;ts;lo;hi]
    tc:(.qetl.source.def source)`time_column;
    q:.qetl.source.def[source]`query;
    r:.qt.qetl.half_open.with_table[source;ts;{[q;lo;hi;x] q[.qt.qetl.half_open.hdb;lo;hi]}[q;lo;hi]];
    if[`threw~first r; 'r 1];
    r tc}

.qt.qetl.half_open.check_source:{[source;u]
    ts:.qt.qetl.half_open.events[u];
    a:ts 0; b:ts 1; c:ts 4;
    left:.qt.qetl.half_open.read[source;ts;a;b];
    right:.qt.qetl.half_open.read[source;ts;b;c];
    whole:.qt.qetl.half_open.read[source;ts;a;c];
    tag:string[source]," at tick ",string u;
    .qunit.assertEquals[left;enlist a;"[a;b) has a and none of the events at b: ",tag];
    .qunit.assertEquals[right;3#b;"[b;c) has all three events at b, once each, and not c's: ",tag];
    .qunit.assertEquals[left,right;whole;"the adjacent requests concatenate to the combined one: ",tag]}

.qt.qetl.half_open.test_every_hdb_source_returns_the_shared_instant_exactly_once:{[t]
    {[s] .qt.qetl.half_open.check_source[s;] each .qt.qetl.half_open.ticks} each key .qt.qetl.half_open.tables;
    }

.qt.qetl.half_open.test_an_empty_table_and_a_window_between_events_return_nothing:{[t]
    ts:.qt.qetl.half_open.events[0D00:00:00.001000000];
    .qunit.assertEquals[count .qt.qetl.half_open.read[`hdb_market_data;ts;ts[1]+0D00:00:00.000000001;ts 4];0;
        "(b;c) holds no event: nothing at b+1ns and c is excluded"]};

/ --- the fixture path and the window helper ----------------------------------

.qt.qetl.half_open.test_window_rows_is_half_open_at_every_precision:{[t]
    decl:`source`time_column!(`ho_unit;`time);
    {[decl;u]
        ts:.qt.qetl.half_open.events[u];
        tbl:([] time:ts; id:til count ts);
        w:{[decl;tbl;lo;hi] .qetl.source.window_rows[decl;tbl;lo;hi]}[decl;tbl;;];
        left:w[ts 0;ts 1]; right:w[ts 1;ts 4]; whole:w[ts 0;ts 4];
        .qunit.assertEquals[left`id;enlist 0;"[a;b) is a only"];
        .qunit.assertEquals[right`id;1 2 3;"[b;c) is the three events at b, and not c"];
        .qunit.assertEquals[(left,right)`id;whole`id;"concatenation is the combined window"];
        }[decl;] each .qt.qetl.half_open.ticks;
    }

/ --- a local HDB, partitioned at the midnight boundary ------------------------

.qt.qetl.half_open.root:"build/test-half-open-hdb"

/ a on the day before b's midnight; b (three events) and c on b's day - one
/ splayed `trades per date partition, as an HDB holds them
.qt.qetl.half_open.build_hdb:{[u]
    d:.qt.qetl.half_open.root;
    system"rm -rf ",d; system"mkdir -p ",d;
    ts:.qt.qetl.half_open.events[u];
    (hsym `$d,"/",string[`date$ts 0],"/trades/") set ([] time:enlist ts 0; id:enlist 0; px:enlist 1f);
    (hsym `$d,"/",string[`date$ts 1],"/trades/") set ([] time:1_ts; id:1 2 3 4; px:4#1f);
    hsym `$d}

/ the shipped sources' windowing, over a local read: partitions chosen, then filtered on time
.qt.qetl.half_open.local_rows:{[root;lo;hi]
    select time, id from .qetl.source.local_read[root;`trades;lo;hi] where time>=lo, time<hi}

.qt.qetl.half_open.check_local:{[u]
    root:.qt.qetl.half_open.build_hdb[u];
    ts:.qt.qetl.half_open.events[u];
    left:.qt.qetl.half_open.local_rows[root;ts 0;ts 1];
    right:.qt.qetl.half_open.local_rows[root;ts 1;ts 4];
    whole:.qt.qetl.half_open.local_rows[root;ts 0;ts 4];
    .qunit.assertEquals[left`id;enlist 0;"[a;b) is a, read from the day before midnight: ",string u];
    .qunit.assertEquals[right`id;1 2 3;"[b;c) is b's three events once each, c excluded: ",string u];
    .qunit.assertEquals[(left,right)~whole;1b;"concatenation is the combined request: ",string u]}

.qt.qetl.half_open.test_a_local_hdb_read_tiles_across_the_date_boundary_at_every_precision:{[t]
    .qt.qetl.half_open.check_local each .qt.qetl.half_open.ticks;
    }

.qt.qetl.half_open.test_a_window_ending_at_midnight_reads_no_part_of_the_next_day:{[t]
    root:.qt.qetl.half_open.build_hdb[0D00:00:00.000000001];
    b:.qt.qetl.half_open.b;
    r:.qetl.source.local_read[root;`trades;b-1D;b];
    .qunit.assertEquals[distinct r`date;enlist `date$b-1;"[..;midnight) opens only the day before"];
    r:.qetl.source.local_read[root;`trades;b-1D;b+0D00:00:00.000000001];
    .qunit.assertEquals[distinct r`date;(`date$b-1;`date$b);"a window one tick past midnight opens the next day too"]};

/ --- a bounded worker: cursor = the exclusive end of the completed window ------

.qt.qetl.half_open.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .qetl.source.define[`ho_src;
        `source`table_name`time_column`row_key`columns`types`query`fixture`tz`transport!
        (`ho_src;`trades;`time;`time`id;`time`id`px;"pjf";
         {[h;range_from;range_to]
            .qetl.source.local[h;{[read;from_ts;to_ts]
                select time, id, px from read[`trades;from_ts;to_ts] where time>=from_ts, time<to_ts
              };range_from;range_to]};
         {[] ([] time:enlist 2026.10.09D10:00; id:enlist 99; px:enlist 0f)};
         `UTC;`local)];
    .qetl.transform.define[`ho_passthrough;`inputs`output`fn`examples!(
        enlist[`batch]!enlist ([] time:`timestamp$(); id:`long$(); px:`float$());
        ([] time:`timestamp$(); id:`long$(); px:`float$());
        {[batch] batch};
        enlist `inputs`expected!(
            enlist[`batch]!enlist ([] time:enlist 2026.10.09D10:00; id:enlist 1; px:enlist 1f);
            ([] time:enlist 2026.10.09D10:00; id:enlist 1; px:enlist 1f)))];
    .qetl.job.bounded.define[`ho_backfill;
        `source`dataset`width`transform!(`ho_src;`ho_out;1D;`ho_passthrough)];
    }

.qt.qetl.half_open.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    .qetl.job.bounded.state.release_lock `ho_backfill;
    .qetl.job.bounded.state.clear_checkpoint `ho_backfill;
    `ho_out set ([] time:`timestamp$(); id:`long$(); px:`float$());
    .qt.qetl.half_open.build_hdb[0D00:00:00.001000000];
    setenv[`UQF_SOURCE_CRED_HO_SRC;.qt.qetl.half_open.root];
    }

.qt.qetl.half_open.tearDown_release:{[] .qpipe.job.ho_backfill.cleanup[]; setenv[`UQF_SOURCE_CRED_HO_SRC;""];}

.qt.qetl.half_open.spec:{[lo;hi] `source_version`range_from`range_to!(`v1;lo;hi)}

.qt.qetl.half_open.day1:2026.10.09D00:00:00.000000000

.qt.qetl.half_open.run:{[lo;hi]
    .qpipe.job.ho_backfill.init[.qt.qetl.half_open.spec[lo;hi]];
    r:.qpipe.job.ho_backfill.run[];
    .qpipe.job.ho_backfill.cleanup[];
    r}

.qt.qetl.half_open.test_the_checkpoint_is_the_exclusive_end_of_the_completed_window:{[t]
    d1:.qt.qetl.half_open.day1; b:.qt.qetl.half_open.b;
    .qt.qetl.half_open.run[d1;b];
    .qunit.assertEquals[.qetl.job.bounded.state.load_checkpoint[`ho_backfill;.qt.qetl.half_open.spec[d1;b]];b;
        "the cursor is range_to - not the time of the last event, which is a tick earlier"];
    .qunit.assertEquals[exec id from get `ho_out;enlist 0;"and the events AT the cursor were not published by that window"]};

.qt.qetl.half_open.test_a_restart_over_a_wider_range_neither_skips_nor_repeats_the_boundary_events:{[t]
    d1:.qt.qetl.half_open.day1; b:.qt.qetl.half_open.b;
    .qt.qetl.half_open.run[d1;b];
    first_run:count get `ho_out;
    r:.qt.qetl.half_open.run[d1;b+1D];
    resumed:`time xasc get `ho_out;
    / the same range in one run, on a clean ledger and table
    .qt.qetl.half_open.setUp_fresh[];
    .qt.qetl.half_open.run[d1;b+1D];
    once:`time xasc get `ho_out;
    .qunit.assertEquals[(first_run;r`windows_completed);(1;1);"the first window was skipped as covered; only [b;b+1D) ran"];
    .qunit.assertEquals[resumed`id;0 1 2 3 4;"all five events, the three sharing b exactly once"];
    .qunit.assertEquals[resumed~once;1b;"the resumed result is the single combined run"]};

.qt.qetl.half_open.test_adjacent_runs_concatenate_to_the_combined_run:{[t]
    d1:.qt.qetl.half_open.day1; b:.qt.qetl.half_open.b;
    .qt.qetl.half_open.run[d1;b]; .qt.qetl.half_open.run[b;b+1D];
    pieces:`time xasc get `ho_out;
    .qt.qetl.half_open.setUp_fresh[];
    .qt.qetl.half_open.run[d1;b+1D];
    .qunit.assertEquals[pieces~`time xasc get `ho_out;1b;"[a;b) then [b;c) is [a;c)"];
    .qunit.assertEquals[count pieces;5;"with no event twice"]};

/ --- empty and reversed windows are refused, by name ---------------------------

.qt.qetl.half_open.test_an_empty_or_reversed_work_window_is_refused_by_the_interval_contract:{[t]
    msg:"require_interval: must be non-empty and forward-going, got *";
    b:.qt.qetl.half_open.b;
    .qunit.assertThrows[{.qetl.coverage.require_interval[x;x]};b;msg;"zero-width"];
    .qunit.assertThrows[{.qetl.coverage.require_interval[x 0;x 1]};(b;b-1D);msg;"reversed"];
    .qunit.assertThrows[{.qpipe.job.ho_backfill.init .qt.qetl.half_open.spec[x;x]};b;msg;"a worker refuses a zero-width run"];
    .qunit.assertThrows[{.qpipe.job.ho_backfill.init .qt.qetl.half_open.spec[x;x-1D]};b;msg;"and a reversed one"];
    .qunit.assertThrows[{.qetl.coverage.stage_completion[`ho_out;`;`v1;x;x-0D00:00:00.000000001;1]};b;msg;
        "and the ledger will not stage a claim over one"]};

/ --- the continuous cursor is the last event observed ---------------------------

.qt.qetl.half_open.upstream:{[u] ([] ts:.qt.qetl.half_open.events[u]; id:til 5)}

/ the rows strictly after a cursor, as a polling feed asks its source
.qt.qetl.half_open.after:{[u;cursor] select from .qt.qetl.half_open.upstream[u] where ts>cursor}

.qt.qetl.half_open.test_a_restart_resumes_strictly_after_the_last_event_observed:{[t]
    {[u]
        w:`ho_tail;
        .qetl.job.continuous.clear_cursor w;
        ts:.qt.qetl.half_open.events[u];
        / the feed has seen a and all three events at b, so its cursor is b
        seen:select from .qt.qetl.half_open.upstream[u] where ts<=ts 1;
        .qetl.job.continuous.advance[w;.qetl.job.continuous.load_cursor w;last seen`ts];
        / a restart: nothing in memory, the cursor read back from disk
        cursor:.qetl.job.continuous.load_cursor w;
        following:.qt.qetl.half_open.after[u;cursor];
        .qunit.assertEquals[cursor;ts 1;"the cursor is the last event observed: ",string u];
        .qunit.assertEquals[following`id;enlist 4;"only c is fetched - b's events are neither skipped nor re-read: ",string u];
        .qunit.assertEquals[(seen`id),following`id;0 1 2 3 4;"before and after the restart, every event once: ",string u];
        .qetl.job.continuous.clear_cursor w;
        } each .qt.qetl.half_open.ticks;
    }

.qt.qetl.half_open.test_a_cursor_that_would_stand_still_is_refused:{[t]
    w:`ho_tail;
    .qetl.job.continuous.clear_cursor w;
    b:.qt.qetl.half_open.b;
    .qetl.job.continuous.advance[w;0Np;b];
    .qunit.assertThrows[{.qetl.job.continuous.advance[`ho_tail;x;x]};b;
        "advance: refusing to move ho_tail's cursor*";"re-reading the same boundary is refused"];
    .qetl.job.continuous.clear_cursor w};

/ --- a preview window is [from;to) ------------------------------------------------

.qt.qetl.half_open.beforeNamespace_save_jobs:{[] .qt.support.save_stream_jobs[`.qt.qetl.half_open]}
.qt.qetl.half_open.afterNamespace_restore_jobs:{[] .qt.support.restore_stream_jobs[`.qt.qetl.half_open]}

.qt.qetl.half_open.preview_counts:{[u]
    ts:.qt.qetl.half_open.events[u];
    `.qetl.job.stream.jobs set `ho_feed _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set `ho_feed1 _ .qetl.job.stream.procnames;
    .qetl.job.stream.define[`ho_feed;`procname`subscribe_to`publishes`period`poll!(
        `ho_feed1;`symbol$();enlist `ho_feed_out;0D00:00:01;
        `fetch`normalize`next_cursor!(.qt.qetl.half_open.after[u;];{[page] select ts, id from page};{[page] last page`ts}))];
    kept:{[ts;lo;hi] .qetl.job.stream.preview_window[`ho_feed;10;lo;hi]`kept}[ts;;];
    kept[ts 0;ts 1],kept[ts 1;ts 4],kept[ts 0;ts 4]}

.qt.qetl.half_open.test_a_preview_window_returns_the_shared_instant_once:{[t]
    {[u]
        .qunit.assertEquals[.qt.qetl.half_open.preview_counts[u];1 3 4;
            "[a;b) is a, [b;c) is the three at b, [a;c) is the four: ",string u]} each .qt.qetl.half_open.ticks;
    }
