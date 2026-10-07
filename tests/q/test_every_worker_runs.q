// test_every_worker_runs.q - every declared bounded worker, driven end to end
// on its own fixture (.wruntest).
//
// WHY THIS FILE EXISTS. coverage_baseline.txt carried eighteen `.qpipe.job` names -
// the largest single cluster in it - under the heading that the two workers
// concerned reach "a real database, a real ODBC driver". They do not. A source
// takes the FIXTURE path whenever no credential is set, which is exactly what
// `.qetl.source.has_credentials` decides and what `fetch_window` branches on: a null
// handle reads the fixture. Both workers run to `completed` on it.
//
// So the gap was never external. demo_deals_backfill and demo_events_backfill
// each had a hand-written lifecycle test; eq_orderbook_backfill and
// upstream_trades_backfill had none, and the baseline recorded a reason that
// was not the real one.
//
// GENERIC ON PURPOSE. This drives every worker the tree declares rather than
// naming any, so the fifth worker is covered the day its file lands. Nothing
// here is worker-specific: the window comes from the source's own fixture, the
// target shape from the transform's declared output, and the credential is
// cleared by the source's own variable name.
//
// WHAT IT DOES NOT PROVE. Anything about a real source's schema - the fixtures
// are synthetic by design, and only `.qetl.source.validate_live` against the real
// thing settles that. What it proves is that the FRAMEWORK carries each
// declared worker from init to completed: contract, windowing, transform,
// publication, coverage and cursor.
//
// Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .wruntest

beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ Every worker the tree declares, from its file rather than from the registry:
/ the tests register fixture workers into `.qetl.job.bounded.state` at run time, and those
/ have no declaration, no transform and nothing to drive.
workers:{[] .testutil.etl_declaration_names["src/etl/workers"]}

/ Put one worker in a state where a run can start, and return its spec.
/ .
/ Every step is derived, so adding a worker needs no edit here:
/ .
/   the credential is cleared through the SOURCE's own variable name, which
/   is what selects the fixture path (a set credential would try to connect)
/   the target table is the TRANSFORM's declared output shape, not the
/   fixture's - a transform that reshapes (upstream_trades_to_local) would
/   otherwise publish into a table of the wrong shape and throw `mismatch
/   the window is the fixture's own span, widened by one worker width so the
/   last row falls inside a window rather than on its boundary
prepare:{[w]
    cfg:.qetl.job.bounded.def w;
    src:.qetl.source.def cfg`source;
    setenv[`$.qetl.source.credential_var cfg`source;""];
    .qetl.job.bounded.state.release_lock w;
    .qetl.job.bounded.state.clear_checkpoint w;
    (cfg`dataset) set 0#(.qetl.transform.def cfg`transform)`output;
    / The primary input's rows: a source with supporting inputs returns them
    / all from its fixture as a dict of name -> table (#617).
    ts:(.qetl.source.primary[cfg`source;(src`fixture)[]]) src`time_column;
    `source_version`range_from`range_to!(`wrunv1;min ts;(max ts)+cfg`width)}

/ Call one of a worker's stamped methods.
call:{[w;nm] (` sv (.qetl.job.bounded.worker_root,w),nm)}

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_MODE;""];
    }

tearDown_release:{[] {.wruntest.call[x;`cleanup][]} each .wruntest.workers[];}

test_the_scan_found_the_workers:{[t]
    / Without this, every assertion below passes over an empty list.
    .qunit.assertTrue[1<count .wruntest.workers[];
        "more than one worker is declared - otherwise this file proves nothing"]};

test_every_worker_runs_to_completion_on_its_fixture:{[t]
    / The test the baseline was standing in for. Each worker goes init -> run
    / and must finish `completed`, having published at least one row: a run
    / that completed zero windows would satisfy a weaker assertion while
    / proving the lifecycle never started.
    results:{[w]
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        r:.wruntest.call[w;`run][];
        (w;r`state;0<r`windows_completed;0<r`rows_published)} each .wruntest.workers[];
    bad:results where not {[r] (r[1]=`completed) and r[2] and r 3} each results;
    .qunit.assertEquals[count bad;0;
        "every declared worker completes on its fixture, with windows and rows"]};

test_every_worker_reports_its_spec_after_init:{[t]
    / `spec` reads the state `init` wrote, so it is the cheapest end-to-end
    / check that the two agree - and it enters the stamped delegator, which a
    / structural test cannot.
    bad:{[w]
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        $[(.wruntest.call[w;`spec][])~spec; (); enlist w]} each .wruntest.workers[];
    .qunit.assertEquals[count raze bad;0;
        "each worker's spec reads back exactly the run spec init was given"]};

test_a_second_run_is_idle_for_every_worker:{[t]
    / Coverage is recorded, so a repeat of the same range has nothing to do.
    / This is retry-safety stated once for every worker rather than per worker.
    bad:{[w]
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        .wruntest.call[w;`run][];
        .wruntest.call[w;`cleanup][];
        .wruntest.call[w;`init][spec];
        r:.wruntest.call[w;`run][];
        $[0=r`windows_completed; (); enlist (w;r`windows_completed)]} each .wruntest.workers[];
    .qunit.assertEquals[count raze bad;0;
        "a repeated range is fully covered, so the second run does no windows"]};

test_a_dry_run_publishes_nothing_for_every_worker:{[t]
    / Dry run: fetch and transform happen, publication does not. Asserted for
    / every worker, because the flag is read by the shell and a worker that
    / overrode `publish` could ignore it.
    setenv[`UQF_DRY_RUN;"true"];
    bad:{[w]
        cfg:.qetl.job.bounded.def w;
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        .wruntest.call[w;`run][];
        $[0=count value cfg`dataset; (); enlist (w;count value cfg`dataset)]
        } each .wruntest.workers[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count raze bad;0;
        "a dry run leaves every worker's target empty"]};

/ Everything durable a run can change, as one comparable value: each file
/ under the status dir by size (the coverage ledger, etl_runs, etl_run_meta,
/ checkpoints), the in-memory ledgers by row count, and the worker's target.
/ Lock files and the per-run Airflow status files are left out - every mode
/ takes and releases the lock, and the status file is how a caller hears the
/ dry run itself finished. That file is asserted on its own, below.
durable:{[w]
    dir:.qetl.job.bounded.state.lock_dir[];
    fs:key hsym `$dir;
    fs:fs where not (fs like "*.lock") or fs like "airflow_status_*";
    sizes:{[dir;f] hcount hsym `$dir,"/",string f}[dir] each fs;
    tabs:`etl_coverage`etl_runs`etl_run_meta;
    rows:{$[x in tables `.; count value x; -1]} each tabs;
    (fs!sizes;tabs!rows;count value (.qetl.job.bounded.def w)`dataset)}

/ THE GUARD AGAINST A DRY RUN THAT IS NOT DRY. Rather than listing what a dry
/ run must not touch - the list that used to leak, because the run ledger,
/ the facts and the store's finish never reached it - this compares
/ everything durable before and after a full run, for every worker. A new
/ write anywhere in the run path fails it, whoever adds it.
test_a_dry_run_leaves_everything_durable_as_it_was:{[t]
    / Attached first, so a run that wrote etl_runs would show up as rows.
    .qetl.run.attach[];
    setenv[`UQF_MODE;"dry_run"];
    bad:{[w]
        spec:.wruntest.prepare w;
        before:.wruntest.durable w;
        .wruntest.call[w;`init][spec];
        r:.wruntest.call[w;`run][];
        after:.wruntest.durable w;
        st:.wruntest.status w;
        $[(before~after) and (r[`windows_completed]>0) and `completed~st`state; (); enlist (w;before;after;st`state)]
        } each .wruntest.workers[];
    setenv[`UQF_MODE;""];
    .qunit.assertEquals[count raze bad;0;
        "a dry run of every worker fetches and transforms, and leaves every ledger, checkpoint and target as it found them"]};

/ validate and plan never open the source or touch anything durable, and the
/ plan is the run: the windows plan names are the windows a run then
/ completes, and once they are covered the plan is empty.
test_validate_and_plan_touch_nothing_and_plan_what_a_run_does:{[t]
    .qetl.run.attach[];
    bad:{[w]
        spec:.wruntest.prepare w;
        before:.wruntest.durable w;
        v:.qetl.job.bounded.validate[w;spec];
        p:.qetl.job.bounded.plan_only[w;spec];
        after:.wruntest.durable w;
        .wruntest.call[w;`init][spec];
        r:.wruntest.call[w;`run][];
        .wruntest.call[w;`cleanup][];
        again:.qetl.job.bounded.plan_only[w;spec];
        ok:(before~after) and (`validated~v`state) and (`planned~p`state) and
            (p[`planned]=r`windows_completed) and (0<p`planned) and 0=again`planned;
        $[ok; (); enlist (w;v`state;p`planned;r`windows_completed;again`planned)]
        } each .wruntest.workers[];
    .qunit.assertEquals[count raze bad;0;
        "validate and plan write nothing, and plan names exactly the windows a run completes"]};

/ The status file Airflow's sensor polls, as the run left it.
status:{[w]
    path:.qetl.status.status_dir[],"/airflow_status_",string[.qetl.job.bounded.instance w],".txt";
    d:.j.k first read0 hsym `$path;
    @[d;`state;`$]}

/ A real run reports how it ended, in the file an orchestrator reads: the
/ only writer used to be a failure path nothing on the live path called, so
/ Airflow's sensor waited on a file no worker wrote.
test_a_run_reports_completed_then_idle_in_the_status_file:{[t]
    bad:{[w]
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        .wruntest.call[w;`run][];
        first_run:.wruntest.status w;
        .wruntest.call[w;`init][spec];
        .wruntest.call[w;`run][];
        second:.wruntest.status w;
        ok:(`completed~first_run`state) and (0<first_run`rows_published) and `idle~second`state;
        $[ok; (); enlist (w;first_run`state;second`state)]} each .wruntest.workers[];
    .qunit.assertEquals[count raze bad;0;"each worker's status file reads completed, then idle for a covered range"]};

/ A run killed mid-way leaves `running` in the file, and .qetl.status refuses
/ running -> starting. The next run records the dead one as failed, then
/ starts, rather than refusing to start at all.
test_a_run_after_a_crash_records_the_crash_then_starts:{[t]
    w:first .wruntest.workers[];
    spec:.wruntest.prepare w;
    / Whatever an earlier test left, `failed is always writable and may be
    / followed by `starting - so this sets up the crash from any state.
    .qetl.status.write_status[w;.qetl.job.bounded.instance w;`failed;spec;
        `cursor`rows_published`windows_completed!(0Np;0;0);"earlier test"];
    .qetl.status.write_status[w;.qetl.job.bounded.instance w;`starting;spec;
        `cursor`rows_published`windows_completed!(0Np;0;0);""];
    .qetl.status.write_status[w;.qetl.job.bounded.instance w;`running;spec;
        `cursor`rows_published`windows_completed!(0Np;0;0);""];
    .wruntest.call[w;`init][spec];
    .qunit.assertEquals[(.wruntest.status w)`state;`starting;"the new run starts after the orphaned one is recorded as failed"]};

/ A run that throws reports `failed` with the error, so the sensor fails the
/ task instead of waiting for it.
test_a_run_that_throws_reports_failed_with_its_error:{[t]
    w:first .wruntest.workers[];
    spec:.wruntest.prepare w;
    .wruntest.call[w;`init][spec];
    ns:.qetl.job.bounded.def[w]`ns;
    saved:value ` sv ns,`plan;
    (` sv ns,`plan) set {[cursor] '"boom in plan"};
    r:@[{.wruntest.call[x;`run][]};w;{x}];
    (` sv ns,`plan) set saved;
    st:.wruntest.status w;
    .qunit.assertEquals[(r;st`state;st`error);("boom in plan";`failed;"boom in plan");"the throw is re-raised and recorded"]};

test_every_worker_checkpoints_through_its_own_delegator:{[t]
    / `run` reaches the shell's checkpoint directly, so the stamped
    / `.qpipe.job.<w>.checkpoint` is only entered when a caller uses it - which is
    / why it stays uncovered by the lifecycle test above. Calling it is also
    / the only way to prove the delegator writes where the shell reads.
    bad:{[w]
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        cursor:spec`range_from;
        .wruntest.call[w;`checkpoint][cursor];
        $[cursor~.qetl.job.bounded.state.load_checkpoint[w;spec]; (); enlist w]} each .wruntest.workers[];
    .qunit.assertEquals[count raze bad;0;
        "the cursor written through each delegator is the cursor the shell stores"]};

\d .
