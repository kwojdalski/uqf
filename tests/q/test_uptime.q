/ test_uptime.q - .qetl.uptime: when a streaming job was up, and the gaps
/ between (#630).

\d .uptimetest

dir:"build/test-uptime"
t0:2026.09.13D00:00:00.000000000
h:{[n] t0+n*0D01:00}

/ A clean table and directory per test, and THIS process's sessions
/ forgotten, so no test beats another's rows.
setUp_fresh:{[]
    setenv[`UQF_STATUS_DIR;.uptimetest.dir];
    system"rm -rf ",.uptimetest.dir;
    system"mkdir -p ",.uptimetest.dir;
    delete etl_stream_uptime from `.;
    `.qetl.uptime.mine set `guid$();
    }

tearDown_fresh:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    delete etl_stream_uptime from `.;
    `.qetl.uptime.mine set `guid$();
    }

/ A session that ran from hour a to hour b.
session:{[job;a;b]
    .qetl.uptime.init_table[];
    `etl_stream_uptime insert (first 1?0Ng;job;`proc1;`host1;1i;.uptimetest.h a;.uptimetest.h b);}

gaps:{[job;a;b] .qetl.uptime.gaps[job;.uptimetest.h a;.uptimetest.h b]}

/ --- recording -------------------------------------------------------------

test_a_session_records_who_and_when_and_is_on_disk:{[t]
    id:.qetl.uptime.begin `demo_markout;
    r:first select from .qetl.uptime.sessions[] where session=id;
    .qunit.assertEquals[(r`job;r`host;r`pid;(r`started_at)=r`last_seen);(`demo_markout;.z.h;.z.i;1b);
        "the job, this host and pid, just started"];
    delete etl_stream_uptime from `.;
    .qetl.uptime.attach[];
    .qunit.assertEquals[count select from .qetl.uptime.sessions[] where session=id;1;
        "a fresh reader finds it - uqs gaps reads after the process is gone"]};

test_a_beat_moves_only_this_processes_sessions:{[t]
    .uptimetest.session[`posbook;0;1];
    persisted:.qetl.job.bounded.state.durable_set[.qetl.uptime.path[];.qetl.uptime.sessions[]];
    id:.qetl.uptime.begin `demo_markout;
    before:exec first last_seen from .qetl.uptime.sessions[] where session=id;
    system"sleep 0.01";
    .qunit.assertEquals[.qetl.uptime.beat[];1;"one session is this process's"];
    s:.qetl.uptime.sessions[];
    .qunit.assertTrue[before<exec first last_seen from s where session=id;"its last_seen moved"];
    .qunit.assertEquals[exec last_seen from s where job=`posbook;enlist .uptimetest.h 1;
        "another process's session is left alone"]};

test_a_beat_with_no_session_writes_nothing:{[t]
    .qunit.assertEquals[.qetl.uptime.beat[];0;"nothing opened here, nothing to beat"]};

/ --- gaps ------------------------------------------------------------------

test_a_job_never_up_is_one_gap_the_whole_range:{[t]
    .qunit.assertEquals[.uptimetest.gaps[`demo_markout;0;24];([] range_from:enlist .uptimetest.h 0; range_to:enlist .uptimetest.h 24);
        "no session at all: everything is a gap"]};

test_the_hole_between_two_sessions_is_the_gap:{[t]
    .uptimetest.session[`demo_markout;0;10];
    .uptimetest.session[`demo_markout;11;24];
    .qunit.assertEquals[.uptimetest.gaps[`demo_markout;0;24];([] range_from:enlist .uptimetest.h 10; range_to:enlist .uptimetest.h 11);
        "the hour it was down"]};

test_overlapping_and_touching_sessions_leave_no_gap:{[t]
    / a restart that overlapped the old process, and one that took over exactly at its last beat
    .uptimetest.session[`demo_markout;0;12];
    .uptimetest.session[`demo_markout;10;18];
    .uptimetest.session[`demo_markout;18;24];
    .qunit.assertEquals[count .uptimetest.gaps[`demo_markout;0;24];0;"up throughout"]};

test_the_edges_of_the_range_are_gaps_too:{[t]
    .uptimetest.session[`demo_markout;2;20];
    .qunit.assertEquals[.uptimetest.gaps[`demo_markout;0;24];([] range_from:.uptimetest.h 0 20; range_to:.uptimetest.h 2 24);
        "before it started, and after its last beat"]};

test_another_jobs_session_closes_nothing:{[t]
    .uptimetest.session[`posbook;0;24];
    .qunit.assertEquals[count .uptimetest.gaps[`demo_markout;0;24];1;"posbook being up says nothing about markout"]};

test_a_session_that_never_beat_covers_nothing:{[t]
    / started and died before its first beat: started_at = last_seen
    .uptimetest.session[`demo_markout;5;5];
    .qunit.assertEquals[count .uptimetest.gaps[`demo_markout;0;24];1;"a zero-length session is not uptime"]};

/ --- twins and wiring -------------------------------------------------------

test_a_jobs_twin_is_the_worker_filling_what_it_publishes:{[t]
    .qunit.assertEquals[.qetl.uptime.twins each `demo_markout`posbook;(enlist `hdb_demo_markouts_backfill;`symbol$());
        "markout's demo_execution_quality is hdb_demo_markouts_backfill's dataset; posbook has no twin"]};

test_starting_a_job_opens_a_session_and_its_beat_timer:{[t]

    tr:`connect`publisher`subscribe`timer!(
        {[] };
        {[] {[t;x] }};
        {[tbls;h;replay] };
        {[n;p;f] .uptimetest.timers,:enlist (n;p)});
    `.uptimetest.timers set ();
    .qetl.job.stream.start[`demo_markout;tr];
    .qunit.assertEquals[exec job from .qetl.uptime.sessions[];enlist `demo_markout;"a session, opened once it was subscribed"];
    .qunit.assertTrue[(`demo_markout_uptime;.qetl.uptime.period) in .uptimetest.timers;"and the timer that beats it"]};

test_a_session_that_cannot_be_recorded_does_not_stop_the_job:{[t]
    keep:.qetl.uptime.begin;
    .qetl.uptime.begin:{[job] '"disk full"};
    tr:`connect`publisher`subscribe`timer!({[] };{[] {[t;x] }};{[tbls;h;replay] };{[n;p;f] });
    r:@[.qetl.job.stream.start[`demo_markout;];tr;{[e] `threw}];
    .qetl.uptime.begin:keep;
    .qunit.assertEquals[r;`demo_markout;"the job starts; the record is an addition, never a precondition"]};

\d .
