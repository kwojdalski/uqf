// test_every_worker_runs.q - every declared bounded worker, driven end to end
// on its own fixture (.wruntest).
//
// WHY THIS FILE EXISTS. coverage_baseline.txt carried eighteen `.qwrk` names -
// the largest single cluster in it - under the heading that the two workers
// concerned reach "a real database, a real ODBC driver". They do not. A source
// takes the FIXTURE path whenever no credential is set, which is exactly what
// `.qsrc.has_credentials` decides and what `fetch_window` branches on: a null
// handle reads the fixture. Both workers run to `completed` on it.
//
// So the gap was never external. demo_deals_backfill and demo_events_backfill
// each had a hand-written lifecycle test; databento_book_backfill and
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
// are synthetic by design, and only `.qsrc.validate_live` against the real
// thing settles that. What it proves is that the FRAMEWORK carries each
// declared worker from init to completed: contract, windowing, transform,
// publication, coverage and cursor.
//
// Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .wruntest

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ Every worker the tree declares, from its file rather than from the registry:
/ the tests register fixture workers into `.qbfstate` at run time, and those
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
    cfg:.qbw.declaration w;
    src:.qsrc.declaration cfg`source;
    setenv[`$.qsrc.credential_var cfg`source;""];
    .qbfstate.release_lock w;
    .qbfstate.clear_checkpoint w;
    (cfg`dataset) set 0#(.qxf.declaration cfg`transform)`output;
    ts:(src`fixture)[] src`timecolumn;
    `source_version`range_from`range_to!(`wrunv1;min ts;(max ts)+cfg`width)}

/ Call one of a worker's stamped methods.
call:{[w;nm] (` sv (.qbw.worker_root,w),nm)}

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qwcfg.reset[];
    .qwcfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
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
        cfg:.qbw.declaration w;
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        .wruntest.call[w;`run][];
        $[0=count value cfg`dataset; (); enlist (w;count value cfg`dataset)]
        } each .wruntest.workers[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count raze bad;0;
        "a dry run leaves every worker's target empty"]};

test_every_worker_checkpoints_through_its_own_delegator:{[t]
    / `run` reaches the shell's checkpoint directly, so the stamped
    / `.qwrk.<w>.checkpoint` is only entered when a caller uses it - which is
    / why it stays uncovered by the lifecycle test above. Calling it is also
    / the only way to prove the delegator writes where the shell reads.
    bad:{[w]
        spec:.wruntest.prepare w;
        .wruntest.call[w;`init][spec];
        cursor:spec`range_from;
        .wruntest.call[w;`checkpoint][cursor];
        $[cursor~.qbfstate.load_checkpoint[w;spec]; (); enlist w]} each .wruntest.workers[];
    .qunit.assertEquals[count raze bad;0;
        "the cursor written through each delegator is the cursor the shell stores"]};

\d .
