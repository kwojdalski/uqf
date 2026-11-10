/ test_io_hdb_eod.q - a backfill must not write a date TorQ's end of day has
/ not yet moved into the HDB (#2186), against TorQ's REAL movetohdb.
/ .
/ movetohdb is read out of lib/torq/code/processes/wdb.q and defined verbatim,
/ so these tests fail the way the wdb does if the writer leaves an hdb/D
/ that clashes with the wdb's copy.

.qt.qetl.io.hdb_eod.D:2026.11.05

/ Private: a scratch pair of directories, the older end of day already in the HDB
/ and day D waiting in the wdb, as TorQ leaves them at midnight.
.qt.qetl.io.hdb_eod.stack:{[]
    s:first system"mktemp -d";
    root:hsym `$s,"/hdb"; wdb:hsym `$s,"/wdbhdb";
    D:.qt.qetl.io.hdb_eod.D;
    .Q.dpft[root;D-3;`sym;`trade] [`trade set ([] sym:`a`b; time:2#D-3+0D12; px:1 2f)];
    .Q.dpft[root;D-3;`sym;`quote] [`quote set ([] sym:`a`b; time:2#D-3+0D12; bid:1 2f)];
    .Q.dpft[wdb;D;`sym;`trade] [`trade set ([] sym:`a`b`c; time:3#D+0D12; px:1 2 3f)];
    .Q.dpft[wdb;D;`sym;`quote] [`quote set ([] sym:`a`b`c; time:3#D+0D12; bid:1 2 3f)];
    .qetl.io.watch_eod[root;wdb];
    `root`wdb!(root;wdb)}

/ Private: TorQ's movetohdb, verbatim, run for day D in a child q so TorQ's .os
/ and .lg never enter this process (other suites load their own); returns the
/ ERR lines it logged.
.qt.qetl.io.hdb_eod.torq_move:{[st]
    D:.qt.qetl.io.hdb_eod.D;
    f:(first system"mktemp -d"),"/move.q";
    hsym[`$f] 0: (
        "\\l lib/torq/code/common/os.q";
        ".lg.o:{[a;b] ()}; .lg.e:{[a;b] -1 \"ERR \",b};";
        "src:read0 `:lib/torq/code/processes/wdb.q;";
        "i:first where src like \"movetohdb:*\"; j:i+first where (i _ src) like \" }*\";";
        "`.wdb.movetohdb set value (count \"movetohdb:\")_\"\\n\" sv src i+til 1+j-i;";
        (".wdb.movetohdb[\"",(1_string ` sv st[`wdb],`$string D),"\";\"",(1_string ` sv st[`root],`$string D),"\";",string[D],"];");
        "exit 0");
    out:system "env ",.qt.support.q_interpreter[]," ",f," -q 2>&1";
    out where out like "ERR *"}

/ Private: what end of day does after the move - the wdb opens the next day.
.qt.qetl.io.hdb_eod.open_next:{[st]
    .Q.dpft[st`wdb;.qt.qetl.io.hdb_eod.D+1;`sym;`trade] [`trade set ([] sym:`symbol$(); time:`timestamp$(); px:`float$())]}

.qt.qetl.io.hdb_eod.setUp_clean:{[]
    `.qetl.io.eod_wdb set (`symbol$())!();
    `.qetl.io.touched set 0#.qetl.io.touched;
    `.qetl.io.default set .qetl.io.memory;
    }

.qt.qetl.io.hdb_eod.rows:{[] ([] time:enlist .qt.qetl.io.hdb_eod.D+0D10; sym:enlist `x; v:enlist 1f)}

.qt.qetl.io.hdb_eod.test_a_backfill_of_yesterday_before_eod_is_refused_and_eod_moves_the_live_day:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    st:.qt.qetl.io.hdb_eod.stack[];
    D:.qt.qetl.io.hdb_eod.D;
    m:.qetl.io.hdb[st`root;`time];
    .qunit.assertThrows[{x[`write][`upstream_trades;y]}[m];.qt.qetl.io.hdb_eod.rows[];
        "hdb: upstream_trades would write 2026.11.05 into the HDB before TorQ's end of day has moved that date there:*";
        "the refusal names end of day and the way out"];
    m[`finish][::];
    .qunit.assertEquals[key ` sv st[`root],`$string D;();"the refused backfill created no partition for end of day to clash with"];
    errs:.qt.qetl.io.hdb_eod.torq_move st;
    .qunit.assertEquals[count errs;0;"TorQ's movetohdb logged no error"];
    .qunit.assertEquals[count get ` sv st[`root],(`$string D),`trade`;3;"the live day reached the HDB intact"];
    .qunit.assertEquals[key ` sv st[`wdb],`$string D;();"the wdb handed the day over"]};

.qt.qetl.io.hdb_eod.test_after_eod_the_backfill_proceeds:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    st:.qt.qetl.io.hdb_eod.stack[];
    D:.qt.qetl.io.hdb_eod.D;
    .qt.qetl.io.hdb_eod.torq_move st;
    .qt.qetl.io.hdb_eod.open_next st;
    m:.qetl.io.hdb[st`root;`time];
    .qunit.assertEquals[m[`eod_waiting] D;`date$();"the day is no longer waiting"];
    .qunit.assertEquals[m[`write][`upstream_trades;.qt.qetl.io.hdb_eod.rows[]];1;"the backfill writes the handed-over day"];
    m[`finish][::];
    .qunit.assertEquals[count get ` sv st[`root],(`$string D),`upstream_trades`;1;"the backfilled rows are there"];
    .qunit.assertEquals[count get ` sv st[`root],(`$string D),`trade`;3;"and the live rows it sits beside are untouched"]};

.qt.qetl.io.hdb_eod.test_the_plan_waits_for_eod_and_says_so:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    st:.qt.qetl.io.hdb_eod.stack[];
    D:.qt.qetl.io.hdb_eod.D;
    `.qetl.io.default set .qetl.io.hdb[st`root;`time];
    f:`timestamp$D-1; to:`timestamp$D+1;
    .qunit.assertEquals[.qetl.io.eod_waiting_for[.qetl.job.bounded.def `demo_deals_backfill;f;to];enlist D;"the report names the waiting day"];
    .qunit.assertEquals[.qetl.io.clip_to_eod[`demo_deals_backfill;.qetl.job.bounded.def `demo_deals_backfill;f;to];`timestamp$D;
        "the plan stops at the start of the waiting day; the day itself stays uncovered"];
    .qt.qetl.io.hdb_eod.torq_move st;
    .qt.qetl.io.hdb_eod.open_next st;
    .qunit.assertEquals[.qetl.io.clip_to_eod[`demo_deals_backfill;.qetl.job.bounded.def `demo_deals_backfill;f;to];to;"once end of day has run the plan is whole"]};

.qt.qetl.io.hdb_eod.test_a_root_with_no_wdb_has_nothing_to_wait_for:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    .qunit.assertEquals[.qetl.io.eod_waiting[`:/no/such/hdb;enlist .qt.qetl.io.hdb_eod.D];`date$();
        "a root nobody registered a wdb for is not fenced"]};

.qt.qetl.io.hdb_eod.test_finish_does_not_fill_a_partition_that_waits:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    st:.qt.qetl.io.hdb_eod.stack[];
    D:.qt.qetl.io.hdb_eod.D;
    / a partition already in the HDB for a waiting day (made before this fence, or by hand)
    .Q.dpft[st`root;D;`sym;`other] [`other set ([] sym:enlist `a; time:enlist D+0D01; z:enlist 1f)];
    m:.qetl.io.hdb[st`root;`time];
    m[`write][`upstream_trades;([] time:enlist D-1+0D10; sym:enlist `x; v:enlist 1f)];
    m[`finish][::];
    .qunit.assertEquals[key ` sv st[`root],(`$string D),`trade;();"no empty trade table was made in the waiting day"]};

.qt.qetl.io.hdb_eod.test_handed_over_is_before_today_and_past_the_wdbs_directory:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    s:first system"mktemp -d";
    wdb:hsym `$s,"/wdbhdb";
    t0:.z.d-3; t1:.z.d-2; t2:.z.d-1;
    / the wdb holds t0, t1 and t2: nothing has been moved yet
    system "mkdir -p ",s,"/wdbhdb/",string t2;
    system "mkdir -p ",s,"/wdbhdb/",string t0;
    system "mkdir -p ",s,"/wdbhdb/",string t1;
    .qetl.io.watch_eod[`:/no/such/hdb;wdb];
    .qunit.assertEquals[.qetl.io.handed_over[`:/no/such/hdb;(t0;t1;t2;.z.d)];`date$();"every date the wdb still holds is waiting, and today is never handed over"];
    / end of day moved t0 and t1 out of the directory
    system "rm -r ",s,"/wdbhdb/",string t0;
    system "rm -r ",s,"/wdbhdb/",string t1;
    .qunit.assertEquals[.qetl.io.handed_over[`:/no/such/hdb;(t0;t1;t2;.z.d)];t0,t1;"the days the wdb has moved on from are handed over; the day it still holds, and today, are not"];
    .qetl.io.eod_wdb:(`symbol$())!();
    .qunit.assertEquals[.qetl.io.handed_over[`:/no/such/hdb;(t2;.z.d)];enlist t2;"with no wdb registered only today is excluded"]};

.qt.qetl.io.hdb_eod.test_require_handed_over_refuses_today_and_a_waiting_day:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    .qunit.assertTrue[.qetl.io.require_handed_over[`:/no/such/hdb;`replay;.z.d-1];"a past day with no wdb is fine"];
    r:@[.qetl.io.require_handed_over[`:/no/such/hdb;`replay];.z.d;{x}];
    .qunit.assertTrue[r like "*belong to the tickerplant and end-of-day*";"today is refused with the named error"]};

/ Leave nothing registered for the suites that run after this one.
.qt.qetl.io.hdb_eod.tearDown_clean:{[]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    }

.qt.qetl.io.hdb_eod.test_a_plan_clipped_for_eod_is_on_the_eod_channel:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    st:.qt.qetl.io.hdb_eod.stack[];
    D:.qt.qetl.io.hdb_eod.D;
    `.qetl.io.default set .qetl.io.hdb[st`root;`time];
    lines:.qt.support.captured_log[0b;{
        D:.qt.qetl.io.hdb_eod.D;
        .qetl.io.clip_to_eod[`demo_deals_backfill;.qetl.job.bounded.def `demo_deals_backfill;`timestamp$D-1;`timestamp$D+1]}];
    .qunit.assertEquals[(lines[;0];lines[;1]);(enlist `WARNING;enlist `eod);"the handover clip is an eod line"];
    .qunit.assertEquals[((first lines)[3])`worker;`demo_deals_backfill;"and the worker it clipped is a field"]};

/ Under a native calendar that rolls at 17:00 UTC, the waiting day D starts at
/ D-1 17:00 (#2397). A clip at D's UTC midnight left [D-1 17:00; D) in the plan:
/ rows of D, which the write refuses, failing the whole window.
.qt.qetl.io.hdb_eod.test_the_clip_stops_where_the_native_calendar_starts_the_waiting_day:{[t]
    .qt.qetl.io.hdb_eod.setUp_clean[];
    s:first system"mktemp -d";
    root:hsym `$s,"/hdb";
    D:.z.d-1;
    system "mkdir -p ",s,"/wdb/",string[D]," ",s,"/wdb/",string .z.d;
    .qetl.io.watch_eod[root;hsym `$s,"/wdb"];
    saved:(.qetl.io.scoped_roots;.qetl.io.calendar;.qetl.io.native_day_fn);
    `.qetl.io.scoped_roots set enlist `$string root;
    `.qetl.io.calendar set "off=x,adj=y";
    / a stand-in for attached_calendar.q's .eodtime.getday at a 17:00 UTC roll
    `.qetl.io.native_day_fn set {[times] `date$times+0D07:00};
    cfg:enlist[`io]!enlist .qetl.io.hdb[root;`time];
    r:@[{[cfg;D] .qetl.io.clip_to_eod[`w;cfg;`timestamp$D-1;`timestamp$D+1]}[cfg];D;{[e] e}];
    `.qetl.io.scoped_roots set saved 0;
    `.qetl.io.calendar set saved 1;
    `.qetl.io.native_day_fn set saved 2;
    .qunit.assertEquals[r;(`timestamp$D-1)+0D17:00;"the plan ends at the first instant the calendar files under D"];
    .qunit.assertEquals[{[D;r] `date$(r-1)+0D07:00}[D;r];D-1;"so its last planned instant is D-1's, which is handed over"]};
