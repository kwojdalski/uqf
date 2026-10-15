/ test_uptime.q - .qetl.uptime: when a streaming job was up, and the gaps
/ between (#630).


.qt.qetl.uptime.dir:"build/test-uptime"
.qt.qetl.uptime.t0:2026.09.13D00:00:00.000000000
.qt.qetl.uptime.h:{[n] .qt.qetl.uptime.t0+n*0D01:00}

/ A clean table and directory per test, and THIS process's sessions
/ forgotten, so no test beats another's rows.
.qt.qetl.uptime.setUp_fresh:{[]
    setenv[`UQF_STATUS_DIR;.qt.qetl.uptime.dir];
    system"rm -rf ",.qt.qetl.uptime.dir;
    system"mkdir -p ",.qt.qetl.uptime.dir;
    delete etl_stream_uptime from `.;
    `.qetl.uptime.mine set 0#0Ng;
    `.qetl.uptime.failed_seen set (`symbol$())!`long$();
    delete from `.qetl.stream_health.batches;
    }

.qt.qetl.uptime.tearDown_fresh:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    delete etl_stream_uptime from `.;
    `.qetl.uptime.mine set 0#0Ng;
    `.qetl.uptime.failed_seen set (`symbol$())!`long$();
    delete from `.qetl.stream_health.batches;
    }

/ A session that ran from hour a to hour b.
.qt.qetl.uptime.session:{[job;a;b]
    .qetl.uptime.init_table[];
    `etl_stream_uptime insert (first 1?0Ng;job;`proc1;`host1;1i;.qt.qetl.uptime.h[a];.qt.qetl.uptime.h[b]);}

.qt.qetl.uptime.gaps:{[job;a;b] .qetl.uptime.gaps[job;.qt.qetl.uptime.h[a];.qt.qetl.uptime.h[b]]}

/ --- recording -------------------------------------------------------------

.qt.qetl.uptime.test_a_session_records_who_and_when_and_is_on_disk:{[t]
    id:.qetl.uptime.begin[`demo_markout];
    r:first select from .qetl.uptime.sessions[] where session=id;
    .qunit.assertEquals[(r`job;r`host;r`pid;(r`started_at)=r`last_seen);(`demo_markout;.z.h;.z.i;1b);
        "the job, this host and pid, just started"];
    delete etl_stream_uptime from `.;
    .qetl.uptime.attach[];
    .qunit.assertEquals[count select from .qetl.uptime.sessions[] where session=id;1;
        "a fresh reader finds it - uqs job gaps reads after the process is gone"]};

.qt.qetl.uptime.test_a_beat_moves_only_this_processes_sessions:{[t]
    .qt.qetl.uptime.session[`posbook;0;1];
    .qetl.job.bounded.state.durable_set[.qetl.uptime.path[];.qetl.uptime.sessions[]];
    id:.qetl.uptime.begin[`demo_markout];
    before:exec first last_seen from .qetl.uptime.sessions[] where session=id;
    system"sleep 0.01";
    .qunit.assertEquals[.qetl.uptime.beat[];1;"one session is this process's"];
    s:.qetl.uptime.sessions[];
    .qunit.assertTrue[before<exec first last_seen from s where session=id;"its last_seen moved"];
    .qunit.assertEquals[exec last_seen from s where job=`posbook;enlist .qt.qetl.uptime.h 1;
        "another process's session is left alone"]};

.qt.qetl.uptime.test_a_beat_with_no_session_writes_nothing:{[t]
    .qunit.assertEquals[.qetl.uptime.beat[];0;"nothing opened here, nothing to beat"]};

/ --- gaps ------------------------------------------------------------------

.qt.qetl.uptime.test_a_job_never_up_is_one_gap_the_whole_range:{[t]
    .qunit.assertEquals[.qt.qetl.uptime.gaps[`demo_markout;0;24];([] range_from:enlist .qt.qetl.uptime.h 0; range_to:enlist .qt.qetl.uptime.h 24);
        "no session at all: everything is a gap"]};

.qt.qetl.uptime.test_the_hole_between_two_sessions_is_the_gap:{[t]
    .qt.qetl.uptime.session[`demo_markout;0;10];
    .qt.qetl.uptime.session[`demo_markout;11;24];
    .qunit.assertEquals[.qt.qetl.uptime.gaps[`demo_markout;0;24];([] range_from:enlist .qt.qetl.uptime.h 10; range_to:enlist .qt.qetl.uptime.h 11);
        "the hour it was down"]};

.qt.qetl.uptime.test_overlapping_and_touching_sessions_leave_no_gap:{[t]
    / a restart that overlapped the old process, and one that took over exactly at its last beat
    .qt.qetl.uptime.session[`demo_markout;0;12];
    .qt.qetl.uptime.session[`demo_markout;10;18];
    .qt.qetl.uptime.session[`demo_markout;18;24];
    .qunit.assertEquals[count .qt.qetl.uptime.gaps[`demo_markout;0;24];0;"up throughout"]};

.qt.qetl.uptime.test_the_edges_of_the_range_are_gaps_too:{[t]
    .qt.qetl.uptime.session[`demo_markout;2;20];
    .qunit.assertEquals[.qt.qetl.uptime.gaps[`demo_markout;0;24];([] range_from:.qt.qetl.uptime.h[0 20]; range_to:.qt.qetl.uptime.h[2 24]);
        "before it started, and after its last beat"]};

.qt.qetl.uptime.test_another_jobs_session_closes_nothing:{[t]
    .qt.qetl.uptime.session[`posbook;0;24];
    .qunit.assertEquals[count .qt.qetl.uptime.gaps[`demo_markout;0;24];1;"posbook being up says nothing about markout"]};

.qt.qetl.uptime.test_a_session_that_never_beat_covers_nothing:{[t]
    / started and died before its first beat: started_at = last_seen
    .qt.qetl.uptime.session[`demo_markout;5;5];
    .qunit.assertEquals[count .qt.qetl.uptime.gaps[`demo_markout;0;24];1;"a zero-length session is not uptime"]};

/ --- twins and wiring -------------------------------------------------------

.qt.qetl.uptime.test_a_jobs_twin_is_the_worker_filling_what_it_publishes:{[t]
    .qunit.assertEquals[.qetl.uptime.twins each `demo_markout`posbook;(enlist `hdb_demo_markouts_backfill;`symbol$());
        "markout's demo_execution_quality is hdb_demo_markouts_backfill's dataset; posbook has no twin"]};

.qt.qetl.uptime.test_starting_a_job_opens_a_session_and_its_beat_timer:{[t]

    tr:`connect`publisher`subscribe`timer!(
        {[] };
        {[] {[t;x] }};
        {[tbls;h;replay] };
        {[n;p;f] .qt.qetl.uptime.timers,:enlist (n;p)});
    `.qt.qetl.uptime.timers set ();
    .qetl.job.stream.start[`demo_markout;tr];
    .qunit.assertEquals[exec job from .qetl.uptime.sessions[];enlist `demo_markout;"a session, opened once it was subscribed"];
    .qunit.assertTrue[(`demo_markout_uptime;.qetl.uptime.period) in .qt.qetl.uptime.timers;"and the timer that beats it"]};

.qt.qetl.uptime.test_a_session_that_cannot_be_recorded_does_not_stop_the_job:{[t]
    keep:.qetl.uptime.begin;
    .qetl.uptime.begin:{[job] '"disk full"};
    tr:`connect`publisher`subscribe`timer!({[] };{[] {[t;x] }};{[tbls;h;replay] };{[n;p;f] });
    r:@[.qetl.job.stream.start[`demo_markout;];tr;{[e] `threw}];
    .qetl.uptime.begin:keep;
    .qunit.assertEquals[r;`demo_markout;"the job starts; the record is an addition, never a precondition"]};

/ --- the process a session names (#619) -------------------------------------

.qt.qetl.uptime.test_a_session_names_no_process_in_plain_q:{[t]
    id:.qt.support.with_procname[`;{.qetl.uptime.begin[`demo_markout]}];
    .qunit.assertEquals[exec first process from .qetl.uptime.sessions[] where session=id;`;
        "outside TorQ there is no process name to record"]};

.qt.qetl.uptime.test_a_session_names_torqs_process_under_torq:{[t]
    id:.qt.support.with_procname[`uptimetest_proc1;{.qetl.uptime.begin[`demo_markout]}];
    .qunit.assertEquals[exec first process from .qetl.uptime.sessions[] where session=id;`uptimetest_proc1;
        "under TorQ the session records the process TorQ named"]};

/ --- failing batches (#832) ------------------------------------------------

/ A batch handler that always throws, as a job with a stale input shape does.
.qt.qetl.uptime.throws:{[t;x] '"stale shape"}

.qt.qetl.uptime.test_the_guard_counts_every_batch_and_keeps_the_last_failure:{[t]
    g:.qetl.job.stream.guarded[`uptimetest_job];
    g[{[t;x] count x};`tbl;til 3];
    r:@[g[.qt.qetl.uptime.throws;`tbl];til 3;{x}];
    h:.qetl.stream_health.of `uptimetest_job;
    .qunit.assertEquals[r;"stale shape";"the failure still reaches the caller"];
    .qunit.assertEquals[(h`ok;h`failed;h`last_error);(1;1;`$"stale shape");
        "one handled, one failed, and why"];
    .qunit.assertFalse[null h`last_failure_at;"and when"]};

.qt.qetl.uptime.test_a_failed_batch_ends_the_session_and_becomes_a_gap:{[t]
    id:.qetl.uptime.begin[`demo_markout];
    system"sleep 0.01";
    .qetl.uptime.beat[];
    last_ok:exec first last_seen from .qetl.uptime.sessions[] where session=id;
    @[.qetl.job.stream.guarded[`demo_markout;.qt.qetl.uptime.throws;`tbl];();::];
    system"sleep 0.01";
    .qetl.uptime.beat[];
    failed_beat:max exec last_seen from .qetl.uptime.sessions[];
    system"sleep 0.01";
    .qetl.uptime.beat[];
    s:.qetl.uptime.sessions[];
    .qunit.assertEquals[exec first last_seen from s where session=id;last_ok;
        "the session that saw the failure stops at its last healthy beat"];
    holes:.qetl.uptime.gaps[`demo_markout;exec min started_at from s;exec max last_seen from s];
    .qunit.assertEquals[holes;([] range_from:enlist last_ok; range_to:enlist failed_beat);
        "the span with the failed batch is a gap, and the healthy beat after it is not"]};

.qt.qetl.uptime.test_a_job_that_keeps_failing_keeps_one_empty_session:{[t]
    .qetl.uptime.begin `demo_markout;
    do[3;
        @[.qetl.job.stream.guarded[`demo_markout;.qt.qetl.uptime.throws;`tbl];();::];
        system"sleep 0.01";
        .qetl.uptime.beat[]];
    s:select from .qetl.uptime.sessions[] where job=`demo_markout;
    .qunit.assertEquals[count s;1;"one row, moved on each beat - not one per failed beat"];
    .qunit.assertEquals[exec first last_seen=started_at from s;1b;"and it claims no time up"]};

.qt.qetl.uptime.test_each_beat_writes_whether_the_job_is_failing:{[t]
    .qetl.uptime.begin `demo_markout;
    @[.qetl.job.stream.guarded[`demo_markout;.qt.qetl.uptime.throws;`tbl];();::];
    .qetl.uptime.beat[];
    file:hsym `$.qt.qetl.uptime.dir,"/stream_health_demo_markout.txt";
    got:.j.k first read0 file;
    .qunit.assertEquals[(got`failing;got`failed;got`last_error;`$got`process);
        (1b;1f;"stale shape";.qetl.run.proc_name[]);"failing, with the count and why, for uqs summary"];
    .qetl.job.stream.guarded[`demo_markout;{[t;x] x};`tbl;()];
    .qetl.uptime.beat[];
    .qunit.assertFalse[(.j.k first read0 file)`failing;"a beat with no failure since clears it"]};

/ A process that starts with the same random seed as another - which is what
/ every fresh q does - must still mint a different session id (#974).
.qt.qetl.uptime.test_sessions_in_identically_seeded_processes_get_different_ids:{[t]
    system"S 7";
    a:.qetl.uptime.begin[`demo_markout];
    system"S 7";
    b:.qetl.uptime.begin[`posbook];
    .qunit.assertTrue[not a=b;"the same seed does not give the same session id"];
    .qunit.assertEquals[count distinct exec session from .qetl.uptime.sessions[];2;"two distinct rows"]};

.qt.qetl.uptime.test_renewed_sessions_get_distinct_ids_whatever_the_seed:{[t]
    system"S 7";
    a:.qetl.uptime.begin[`demo_markout];
    b:.qetl.uptime.begin[`posbook];
    @[.qetl.job.stream.guarded[`demo_markout;.qt.qetl.uptime.throws;`tbl];();::];
    @[.qetl.job.stream.guarded[`posbook;.qt.qetl.uptime.throws;`tbl];();::];
    system"sleep 0.01";
    system"S 7";
    .qetl.uptime.beat[];
    .qunit.assertEquals[count distinct .qetl.uptime.mine;2;"two renewed sessions, two ids"];
    .qunit.assertTrue[not any .qetl.uptime.mine in (a;b);"neither reuses an old id"]};
