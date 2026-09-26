// test_demo_deals_backfill.q - tests for src/etl/workers/demo_deals_backfill.q
// (.qpipe.job.demo_deals_backfill), the first real bounded worker.
//
// What these prove and what they do not. They prove the FRAMEWORK works end
// to end: contract, windowing, coverage, retry, dry-run, resumption. They say
// nothing about the real source's schema, because the source here is
// synthetic by design - only `.qetl.source.validate_live` on the work machine settles
// that.
//
// Load src/etl/core/*.q, src/etl/sources/demo_deals.q,
// src/etl/workers/demo_deals_backfill.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .ddbftest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.ddbftest.d from_n;.ddbftest.d to_n)}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    }

tearDown_release:{[] .qpipe.job.demo_deals_backfill.cleanup[];}

/ --- initialisation ----------------------------------------

test_init_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];.ddbftest.spec_for[`v1;1;4];"the worker implements every contract method and global"]};

test_a_null_source_version_is_refused_at_init:{[t]
    .qunit.assertError[{.qpipe.job.demo_deals_backfill.init x};.ddbftest.spec_for[`;1;4];"a run that cannot name its release cannot record coverage"]};

test_a_reversed_range_is_refused_at_init:{[t]
    .qunit.assertError[{.qpipe.job.demo_deals_backfill.init x};.ddbftest.spec_for[`v1;4;1];"a bad bound fails before any work happens, not part-way through"]};

/ Without a credential the worker takes the FIXTURE path. That is an explicit
/ statement that this is a demo - not a fallback for a failed connection,
/ which would turn an outage into synthetic data recorded as covered.
test_no_credential_means_the_fixture_path:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[null .qpipe.job.demo_deals_backfill.handle;1b;"an unconfigured credential selects the fixture, deliberately and visibly"]};

test_init_takes_the_single_instance_lock:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;1b;"one instance per worker is what keeps the checkpoint private"]};

/ --- a full pass --------------------------------------------------------

test_a_full_run_completes_every_window:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;3;0);"three days, three windows, none failed"]};

test_a_full_run_publishes_the_windowed_rows:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`rows_published;count value `demo_deals);(3;3);"three rows for three days of a five-row fixture - not the fixture three times"]};

test_a_full_run_leaves_the_range_covered:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;`v1;.z.p;.ddbftest.d 1;.ddbftest.d 4];1b;"the windows' coverage composes into the whole requested range"]};

test_the_cursor_lands_on_the_range_end:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`cursor;.ddbftest.d 4;"a completed run's cursor is the range's exclusive end"]};

/ --- coverage skipping -------------------------------------------

/ "Ran, found no work" is a SUCCESS, not a failure. An orchestrator
/ that cannot tell them apart retries a successful no-op forever.
test_a_second_run_is_idle_not_failed:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`idle;0);"an already-published range is idle, and idle is a success"]};

test_a_second_run_publishes_nothing_further:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[count value `demo_deals;3;"a retry does not duplicate published rows"]};

/ No merging across versions, in the direction that matters: a version bump exists to force
/ re-extraction, so v1 coverage must not suppress a v2 run.
test_a_version_bump_re_runs_the_whole_range:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v2;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;3);"a new source release re-fetches everything"]};

/ A retry after a partial run redoes only the gap. This is the case retry-safety
/ exists for, and the one a cursor alone cannot get right.
test_a_partial_range_is_narrowed_to_the_gap:{[t]
    .qetl.coverage.stage_completion[`demo_deals;`;`v1;.ddbftest.d 1;.ddbftest.d 2;1];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;2;"one day already published, two left to do"]};

/ A gap in the MIDDLE must not be bridged by a window spanning it: the
/ covered day sits between two uncovered ones, so the plan must produce two
/ separate runs of windows rather than one 3-day sweep.
test_a_middle_gap_does_not_bridge_covered_coverage:{[t]
    .qetl.coverage.stage_completion[`demo_deals;`;`v1;.ddbftest.d 2;.ddbftest.d 3;1];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;2;"day 1 and day 3 are planned; day 2 is skipped, not spanned"]};

/ --- resumption --------------------------------------------------

test_a_matching_checkpoint_resumes:{[t]
    / Days 1-2 were published by the run being resumed; its checkpoint is at
    / day 3. Resuming does day 3 only. The coverage rows are what an
    / interrupted run leaves behind - finish_window stages coverage before it
    / saves the checkpoint - so a checkpoint at day 3 with days 1-2 uncovered
    / is not a resume, it is a gap, and plan now treats it as one.
    .qetl.coverage.stage_completion[`demo_deals;`;`v1;.ddbftest.d 1;.ddbftest.d 3;2];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qetl.job.bounded.state.save_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;4];.ddbftest.d 3];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;1;"resuming at day 3, with days 1-2 covered, leaves one window"]};

/ The dangerous direction: a cursor from a narrower run must not be used to
/ resume a wider one, which would skip everything before it.
test_a_foreign_checkpoint_is_discarded:{[t]
    .qetl.job.bounded.state.save_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;2];.ddbftest.d 2];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;3;"a cursor from a different run specification is dropped, and the range is done in full"]};

/ --- dry run ----------------------------------------------------

test_a_dry_run_publishes_no_coverage:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count value `etl_coverage;0;"a diagnostic run leaves the ledger untouched"]};

test_a_dry_run_publishes_no_rows:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count value `demo_deals;0;"fetch and transform happen; publication does not"]};

test_a_dry_run_writes_no_checkpoint:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[null .qetl.job.bounded.state.load_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;4]];1b;"no resumable state survives a diagnostic run"]};

/ A dry run must be repeatable and must not make the next real run think
/ work was done.
test_a_real_run_after_a_dry_run_does_everything:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;3);"a dry run leaves nothing behind that suppresses the real one"]};

/ --- contract validation on the fetched rows ---------------------

/ Not belt-and-braces: a source that has dropped a column returns rows where
/ the missing column reads as a NULL in most q code, so without this the
/ worker publishes nulls and records the window as covered.
test_a_contract_breaking_source_fails_the_window:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    orig:.qetl.source.sources[`demo_deals]`fixture;
    .qetl.source.sources[`demo_deals]:@[.qetl.source.sources`demo_deals;`fixture;:;
        {([] deal_time:enlist .ddbftest.d 1; sym:enlist `EURUSD)}];
    r:@[{.qpipe.job.demo_deals_backfill.run[]};::;{`state`err!(`threw;x)}];
    .qetl.source.sources[`demo_deals]:@[.qetl.source.sources`demo_deals;`fixture;:;orig];
    .qunit.assertEquals[0=count value `etl_coverage;1b;"a source missing declared columns records no coverage, rather than publishing nulls as complete"]};

/ Coverage records an empty window deliberately, so the quality gate receives
/ one. The guard exists because the three checks below it select from an
/ empty table and would report no failures anyway - but only by accident of
/ how `select` behaves, not by intent, and an accident is not a contract.
test_the_quality_gate_passes_an_empty_batch:{[t]
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.quality_check[0#.qpipe.source.demo_deals.fixture[]];0;
        "an empty window has nothing to fail, and must not be reported as failing"]};

/ --- the contract methods themselves (#185 coverage) ---------------------

/ THE GAP THESE CLOSE. .qetl.job.bounded.state.require_contract checks the five methods
/ EXIST by name; the suite drives .qetl.job.bounded.* directly. So the delegators were
/ declared, existence-checked, and never executed - qcov reported every one
/ of them as an uncovered statement. A delegator with its arguments swapped,
/ .qetl.job.bounded.fetch[worker;to_ts;from_ts], would have passed every test in this
/ file while fetching a backwards window.
/ .
/ They are one line each and that is the point: the only thing that can be
/ wrong with them is the wiring, and the wiring is exactly what nothing
/ checked.

test_spec_delegates_to_the_shell:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.spec[];.qetl.job.bounded.spec `demo_deals_backfill;
        "the worker's spec is the shell's spec for it, not a second copy"]};

test_plan_delegates_and_passes_the_cursor:{[t]
    / Day 1 is published; the cursor stands at day 2. The plan is the two
    / remaining days. This is the wiring check it always was - a delegator
    / that dropped the cursor would still give 2 here, so the day-1 coverage
    / is what makes the count mean something: without it, coverage alone
    / would plan all three days whatever the cursor said, since a gap behind
    / the cursor is planned (see .qetl.job.bounded.plan).
    .qetl.coverage.stage_completion[`demo_deals;`;`v1;.ddbftest.d 1;.ddbftest.d 2;1];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.plan[.ddbftest.d 2];2;
        "with day 1 covered, a cursor at day 2 plans the two days after it"]};

/ A gap BEHIND the cursor is planned. This is the restatement case:
/ a finished run's cursor is range_to, and a supersede then withdraws a
/ window's coverage. The old plan took the cursor as a hard lower bound and
/ reported idle over a range the ledger said was missing - so a restatement
/ withdrew coverage that nothing would ever refill. Found live, in
/ tests/q/run_two_instances.q.
test_a_gap_behind_the_cursor_is_still_planned:{[t]
    / One claim PER WINDOW, as a real run stages them. supersede withdraws
    / by overlap, so a single three-day claim would be withdrawn whole by a
    / one-day restatement and three windows would be planned - correct, but
    / not the case this test is about.
    .qetl.coverage.stage_completion[`demo_deals;`;`v1;.ddbftest.d 1;.ddbftest.d 2;1];
    .qetl.coverage.stage_completion[`demo_deals;`;`v1;.ddbftest.d 2;.ddbftest.d 3;1];
    .qetl.coverage.stage_completion[`demo_deals;`;`v1;.ddbftest.d 3;.ddbftest.d 4;1];
    .qetl.coverage.supersede[`demo_deals;`;`v1;.ddbftest.d 2;.ddbftest.d 3];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    w:.qpipe.job.demo_deals_backfill.plan[.ddbftest.d 4];
    .qunit.assertEquals[count w;1;"the withdrawn day is planned although the cursor is past it"];
    .qunit.assertEquals[(first w)`range_from;.ddbftest.d 2;"and it is exactly the withdrawn day"]};

test_plan_with_a_null_cursor_plans_the_whole_range:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.plan[0Np];3;"no cursor means nothing is done yet"]};

test_fetch_delegates_with_its_window_the_right_way_round:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.fetch[.ddbftest.d 1;.ddbftest.d 2];
    .qunit.assertEquals[r`state;`ok;"one day of the fixture fetches cleanly"];
    .qunit.assertEquals[count r`result;1;
        "one day of a five-day fixture is one row - a swapped window would be empty or five"]};

test_publish_delegates_and_returns_the_row_count:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    batch:(.qpipe.job.demo_deals_backfill.fetch[.ddbftest.d 1;.ddbftest.d 2])`result;
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.publish batch;1;"publish reports what it wrote"];
    .qunit.assertEquals[count value `demo_deals;1;"and the row is actually in the target"]};

test_checkpoint_delegates_and_the_cursor_can_be_read_back:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.checkpoint[.ddbftest.d 2];
    .qunit.assertEquals[.qetl.job.bounded.state.load_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;4]];
        .ddbftest.d 2;
        "the cursor written through the delegator is the cursor the shell stores"]};

test_cleanup_delegates:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;0b;
        "cleanup releases the single-instance lock"]};

/ A run that THROWS must still release (#490). init takes the lock and only
/ cleanup releases it, so while the release sat on run's last line every
/ error path walked past it: the run logged `failed`, the lock outlived the
/ process, and the next run refused against a pid that had already exited.
/ Nothing releases on process exit either - this tree has no .z.exit - so the
/ throw path is the one the framework itself can be held to.
test_a_run_that_throws_still_releases_the_lock:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    / Break the plan, which run calls before it can reach any per-window
    / error handling, so the throw leaves run_body by the shortest path.
    saved:.qpipe.job.demo_deals_backfill.plan;
    .qpipe.job.demo_deals_backfill.plan:{[cursor] '"deliberate plan failure"};
    err:@[{.qpipe.job.demo_deals_backfill.run[]; ""};::;{x}];
    .qpipe.job.demo_deals_backfill.plan:saved;
    .qunit.assertTrue[err like "*deliberate plan failure*";
        "the error still reaches the caller - releasing the lock must not swallow it"];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;0b;
        "a run that threw released its lock, so the next run of this worker can start"]};

test_a_run_that_succeeds_releases_the_lock:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;0b;
        "and so did a run that finished - both exits go through the one release"]};

/ The five names the contract requires, called through the worker's OWN namespace
/ rather than the shell's - which is what an orchestrator does.
test_every_contract_method_is_callable_not_merely_present:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    ok:all {[nm] 100h=type value ` sv `.qpipe.job.demo_deals_backfill,nm} each .qetl.job.bounded.state.bounded_worker_methods;
    .qunit.assertEquals[ok;1b;
        "require_contract checks these names exist; this checks they are functions"]};

/ --- the shell's own guards (#124, #60) ---------------------------------

/ --- inheritance (#227) --------------------------------------------------

/ The worker's file writes none of the contract's names; define stamps them.
/ Checked against the contract's own lists, so a method added to either is
/ covered without editing this test.
test_define_stamps_the_contract_into_the_workers_namespace:{[t]
    names:key `.qpipe.job.demo_deals_backfill;
    wanted:.qetl.job.bounded.state.bounded_worker_globals,.qetl.job.bounded.inherited_methods;
    .qunit.assertEquals[wanted where not wanted in names;`symbol$();
        "every contract global and every delegator is present without being written in the file"]};

/ The delegate's signature is the shell's minus `worker`, in the shell's
/ order - so the wiring the tests above check cannot be swapped by a stamp,
/ and a reader of .qpipe.job.x.fetch at the prompt sees from_ts and to_ts.
test_a_stamped_delegate_carries_the_shells_parameter_names:{[t]
    .qunit.assertEquals[(value .qpipe.job.demo_deals_backfill.fetch)[1];`from_ts`to_ts;
        "the delegate's parameters are the shell's, in the shell's order"]};

/ THE OVERRIDE. A worker that defines its own publish before its define
/ keeps it, and run REACHES it: the rows land where the override put them
/ and not in the source's target. Before #227 the shell's run loop called
/ .qetl.job.bounded.publish whatever the worker had defined, so this test would have
/ found the target written and the override never called.
test_a_workers_own_publish_is_kept_and_reached_by_run:{[t]
    .ddbftest.seen:0#.qpipe.source.demo_deals.fixture[];
    `.qpipe.job.overriding_worker.publish set {[batch] .ddbftest.seen,:batch; count batch};
    .qetl.job.bounded.state.release_lock `overriding_worker;
    .qetl.job.bounded.state.clear_checkpoint `overriding_worker;
    .qetl.job.bounded.define[`overriding_worker;
        `source`dataset`width`transform!(`demo_deals;`overriding_worker_ds;1D;`demo_deals_passthrough)];
    .qpipe.job.overriding_worker.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.overriding_worker.run[];
    .qpipe.job.overriding_worker.cleanup[];
    .qetl.job.bounded.worker_cfg:(enlist `overriding_worker) _ .qetl.job.bounded.worker_cfg;
    .qunit.assertEquals[r`state;`completed;"the run completes through the override"];
    .qunit.assertEquals[count .ddbftest.seen;3;"every window's rows went through the worker's own publish"];
    .qunit.assertEquals[count value `demo_deals;0;
        "and none through the shell's, which would have written the source's target"]};

/ A reload re-runs the worker's define. The names it stamped the first time
/ are left alone, so the run specification of a worker already initialised
/ is not reset to nulls under it.
test_redeclaring_a_worker_keeps_its_state:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .ddbftest.with_declaration_restored[{
        .qetl.job.bounded.define[`demo_deals_backfill;
            `source`dataset`width`transform`check!
            (`demo_deals;`demo_deals;1D;`demo_deals_passthrough;.qpipe.job.demo_deals_backfill.quality_check)]}];
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.spec[];.ddbftest.spec_for[`v1;1;4];
        "a second define fills only absent names, and the run specification is not one"]};

/ --- the derived namespace (.qpipe.job) --------------------------------------

test_define_derives_the_workers_namespace:{[t]
    / The worker's name is its only name. Before this, a worker carried two -
    / `demo_deals_backfill` and `.qddbf` - and keeping them in step was a
    / convention nothing checked.
    .qunit.assertEquals[(.qetl.job.bounded.def `demo_deals_backfill)`ns;`.qpipe.job.demo_deals_backfill;
        "the namespace is .qpipe.job.<worker>, derived rather than declared"]};

test_the_derived_namespace_is_where_the_implementation_actually_is:{[t]
    / Not a tautology with the test above: that one reads what define stored,
    / this one checks the stored value names the namespace holding the
    / worker's own methods. A derivation that agreed with itself and with
    / nothing else would pass the first and fail here.
    .qunit.assertEquals[`quality_check in key .qetl.job.bounded.namespace `demo_deals_backfill;1b;
        "the derived namespace is the one the worker's file declared"]};

test_a_supplied_namespace_is_refused:{[t]
    / Refused, not silently overwritten. A worker declared with its own `ns`
    / would run under the derived namespace regardless, and the author would
    / meet that as a contract failure at init naming every missing method
    / rather than as a sentence naming the key they passed.
    .qunit.assertError[{.qetl.job.bounded.define[`ns_supplying_worker;x]};
        `ns`source`dataset`width`transform!(`.qddbf;`demo_deals;`some_other_ds;1D;`demo_deals_passthrough);
        "a worker may not choose its own namespace"]};

test_the_refusal_names_the_namespace_it_would_have_used:{[t]
    err:@[{.qetl.job.bounded.define[`ns_supplying_worker;x]; ""};
        `ns`source`dataset`width`transform!(`.qddbf;`demo_deals;`some_other_ds;1D;`demo_deals_passthrough);{x}];
    .qunit.assertTrue[err like "*.qpipe.job.ns_supplying_worker*";
        "the refusal says which namespace the worker will actually live in"]};


/ Two workers on one dataset AND one partition still produce coverage rows
/ nothing can tell apart, so that pair is still refused. What changed with
/ #185 is that the pair, not the dataset alone, is what has to be unique.
test_two_workers_may_not_claim_one_dataset_and_partition:{[t]
    .qunit.assertError[{.qetl.job.bounded.define[`clashing_worker;x]};
        `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough);
        "a second worker on one dataset and partition would produce coverage rows nothing can tell apart (#60)"]};

test_the_clash_error_names_the_existing_claimant:{[t]
    err:@[{.qetl.job.bounded.define[`clashing_worker;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough);{x}];
    .qunit.assertEquals[err like "*demo_deals_backfill*";1b;"the refusal names who already owns the dataset"]};

/ The name appearing is not enough. q evaluates right to left, so
/ `", " sv string clash, " - two workers..."` made `sv` join the EXPLANATION
/ character by character: the message read "...already claimed by
/ demo_deals_backfill,  , -,  , t, w, o, ..." and passed the test above,
/ because the name was still there immediately before the wreckage.
test_the_clash_error_reads_as_a_sentence:{[t]
    err:@[{.qetl.job.bounded.define[`clashing_worker;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough);{x}];
    .qunit.assertTrue[err like "*produce coverage rows nothing can tell apart";
        "the explanation survives intact to the end of the message"]};

/ --- the process that runs a worker ---------------------------------------

/ uqs derives its process registry from these declarations, so the
/ procname a worker declares is the process it runs as.
test_a_worker_runs_as_the_procname_it_declares:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.worker_cfg `demo_deals_backfill)`procname;`deals_backfill1;
        "demo_deals_backfill declares deals_backfill1, its process name before the registry was derived"]};

test_a_worker_declaring_no_procname_runs_as_its_name_and_1:{[t]
    .qetl.job.bounded.define[`noproc_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`NOPROC)];
    cfg:.qetl.job.bounded.worker_cfg `noproc_slice;
    .qetl.job.bounded.worker_cfg:(enlist `noproc_slice) _ .qetl.job.bounded.worker_cfg;
    .qunit.assertEquals[(cfg`procname;cfg`note);(`noproc_slice1;"");
        "an undeclared procname defaults to <worker>1 and an undeclared note to empty"]};

test_a_procname_that_is_not_a_symbol_is_refused:{[t]
    .qunit.assertThrows[{.qetl.job.bounded.define[`badproc_slice;x]};
        `source`dataset`width`transform`partition`procname!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`BADPROC;"p1");
        "*procname must be a symbol*";
        "a process name is a symbol, as everywhere else in the registry"]};

/ --- what the partition dimension unlocks (#185) --------------------------

/ THE POINT OF THE CHANGE. A backfill could not be parallelised: one worker
/ per dataset, however large the range. Two workers filling different
/ partitions of one dataset now register, because their coverage rows are
/ distinguishable and no read composes them.
test_two_workers_may_claim_one_dataset_in_different_partitions:{[t]
    .qetl.job.bounded.define[`eurusd_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD)];
    .qunit.assertEquals[
        .qetl.job.bounded.define[`usdjpy_slice;
            `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`USDJPY)];
        `usdjpy_slice;
        "two partitions of one dataset are two distinguishable claims, so both register"];
    .qetl.job.bounded.worker_cfg:(`eurusd_slice`usdjpy_slice) _ .qetl.job.bounded.worker_cfg;};

/ The refusal has to survive the new dimension: same dataset, same partition,
/ different worker is the case that was always wrong and still is.
test_two_workers_may_not_claim_one_partition_of_a_dataset:{[t]
    .qetl.job.bounded.define[`eurusd_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD)];
    err:@[{.qetl.job.bounded.define[`another_eurusd_slice;x]; ""};
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD);{x}];
    .qetl.job.bounded.worker_cfg:(enlist `eurusd_slice) _ .qetl.job.bounded.worker_cfg;
    .qunit.assertEquals[err like "*eurusd_slice*";1b;
        "the second claim on one dataset AND partition is refused, naming the holder"]};

/ A worker that declares no partition gets the ` sentinel, so it keeps
/ exactly the guarantee it had before the column existed - including being
/ refused a second claimant.
test_a_worker_declaring_no_partition_gets_the_sentinel:{[t]
    .qunit.assertEquals[.qetl.job.bounded.partition_of `demo_deals_backfill;`;
        "an undeclared partition resolves to `, not to (::)"]};

test_a_declared_partition_is_stored_as_given:{[t]
    .qetl.job.bounded.define[`eurusd_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD)];
    r:.qetl.job.bounded.partition_of `eurusd_slice;
    .qetl.job.bounded.worker_cfg:(enlist `eurusd_slice) _ .qetl.job.bounded.worker_cfg;
    .qunit.assertEquals[r;`EURUSD;"the declared partition is what coverage will be recorded under"]};

test_a_non_symbol_partition_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.define[`bad_slice;x]};
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;"EURUSD");
        "a string partition would be recorded as a char vector and match no read"]};

/ Redefining the SAME worker must stay legal - the shell's define is called
/ at load, and reloading a worker file is ordinary.
test_a_worker_may_redeclare_itself:{[t]
    .qunit.assertEquals[
        .ddbftest.with_declaration_restored[{
            .qetl.job.bounded.define[`demo_deals_backfill;
                `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough)]}];
        `demo_deals_backfill;
        "reloading a worker file re-runs its own define, which must not trip the clash guard"]};

/ The two real workers declare distinct (dataset;partition) pairs, so the
/ guard is satisfied by the tree as it stands rather than by luck. Keyed on
/ the PAIR now: checking datasets alone would fail the day a dataset is
/ deliberately split across workers, which is the thing #185 set out to allow.
test_the_two_shipped_workers_claim_distinct_dataset_partitions:{[t]
    c:value .qetl.job.bounded.worker_cfg;
    claims:flip (c[;`dataset];c[;`partition]);
    .qunit.assertEquals[count[claims];count distinct claims;
        "every registered worker owns its dataset and partition alone"]};

/ --- the cursor is forward-only ------------------------------------

/ Backfill is oldest-first by construction, and plan[] uses the cursor
/ as its LOWER bound - so a cursor pushed past windows that are still
/ uncovered means the next run plans from beyond them and never comes back.
/ Coverage still shows them as gaps, so nothing is wrongly reported complete;
/ they just never get filled. That made the ordering load-bearing and
/ unenforced, while .qetl.job.continuous.advance had guarded the same invariant on the
/ continuous path all along.
test_a_backwards_cursor_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.advanced_to[`demo_deals_backfill;x 0;x 1]};
        (.ddbftest.d 4;.ddbftest.d 2);
        "a cursor that moves backwards skips windows that are still uncovered"]};

test_a_standing_still_cursor_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.advanced_to[`demo_deals_backfill;x;x]};
        .ddbftest.d 3;
        "strictly forward - a repeated cursor would re-plan the same window forever"]};

test_the_first_cursor_of_a_run_is_allowed:{[t]
    / The loaded checkpoint is a null timestamp on a first run, and a null
    / cannot be compared - so the guard must let it through rather than
    / refusing every worker's opening window.
    .qunit.assertEquals[.qetl.job.bounded.advanced_to[`demo_deals_backfill;0Np;.ddbftest.d 2];
        .ddbftest.d 2;"a null current cursor is a first run, not a regression"]};

test_a_forward_cursor_is_returned_unchanged:{[t]
    .qunit.assertEquals[.qetl.job.bounded.advanced_to[`demo_deals_backfill;.ddbftest.d 2;.ddbftest.d 3];
        .ddbftest.d 3;"the guard is a pass-through on the legitimate path"]};

/ --- the heartbeat is actually written -----------------------------

/ .qetl.hb's own tests cover the table's behaviour. These two prove the worker
/ loop CALLS it - which every one of those tests would pass without.
test_a_real_run_beats_once_per_window:{[t]
    `worker_heartbeat set 0#value .qetl.hb.attach[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`hb1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    beats:first exec windows from .qetl.hb.report[] where worker=`demo_deals_backfill;
    .qunit.assertEquals[beats;"j"$r`windows_completed;
        "the heartbeat's window count matches the run's own completed count"]};

test_a_finished_run_does_not_read_as_wedged:{[t]
    / Without the terminal beat a completed worker keeps its last mid-window
    / state and ages into looking stuck - which is the exact failure the
    / status file already has and this table exists to avoid.
    `worker_heartbeat set 0#value .qetl.hb.attach[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`hb2;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[first exec state from .qetl.hb.report[] where worker=`demo_deals_backfill;
        r`state;"the last beat carries the run's terminal state"]};

test_an_idle_run_still_beats:{[t]
    / "Ran, found no work" must not look like a worker that stopped beating.
    `worker_heartbeat set 0#value .qetl.hb.attach[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`hb3;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[first exec state from .qetl.hb.report[] where worker=`demo_deals_backfill;
        `idle;"an idle second run beats idle rather than going quiet"]};

/ --- the transform ---------------------------------------------------------

/ Registered for one test and removed after it, so .xftest's "every
/ registered transform passes its examples" sees only shipped transforms.
double_notional:{[]
    .qetl.transform.define[`ddbftest_double;`inputs`output`fn`examples!(
        enlist[`batch]!enlist 0#.qpipe.source.demo_deals.fixture[];
        0#.qpipe.source.demo_deals.fixture[];
        {[batch] update notional:2*notional from batch};
        enlist `inputs`expected!(enlist[`batch]!enlist .qpipe.source.demo_deals.fixture[];update notional:2*notional from .qpipe.source.demo_deals.fixture[]))]};

/ Run f, then put the shipped worker's whole declaration back.
/ .
/ A test that redefines `demo_deals_backfill` with a PARTIAL dictionary does
/ not only change what it names: .qetl.job.bounded.define fills every absent optional from
/ its default (bounded_worker.q:239), so an omitted `procname` is silently
/ reset to `demo_deals_backfill1` - and STAYS reset for every test that runs
/ afterwards. That is what broke test_a_worker_runs_as_the_procname_it_declares,
/ which sorts after both of the redeclaring tests and asserts the
/ `deals_backfill1` the worker file actually declares.
/ .
/ Nothing caught it for two reasons worth knowing: the test passes in
/ isolation, and UQF_TEST_ORDER shuffles the NAMESPACE list rather than the
/ tests inside a namespace, so reverse and shuffle both reproduced the same
/ intra-suite order.
/ .
/ Same shape as with_transform below, for the whole dictionary rather than one
/ field.
with_declaration_restored:{[f]
    orig:.qetl.job.bounded.worker_cfg[`demo_deals_backfill];
    r:@[f;::;{(`threw;x)}];
    .qetl.job.bounded.worker_cfg[`demo_deals_backfill]:orig;
    r};

with_transform:{[nm;f]
    orig:.qetl.job.bounded.def[`demo_deals_backfill]`transform;
    .qetl.job.bounded.worker_cfg[`demo_deals_backfill;`transform]:nm;
    r:@[f;::;{(`threw;x)}];
    .qetl.job.bounded.worker_cfg[`demo_deals_backfill;`transform]:orig;
    .qetl.transform.registry:(`ddbftest_double`ddbftest_throws) _ .qetl.transform.registry;
    r};

test_the_published_rows_are_the_transform_output:{[t]
    / Proves the transform runs INSIDE do_window, between fetch and publish,
    / rather than only being declared.
    .ddbftest.double_notional[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`xf1;1;4]];
    .ddbftest.with_transform[`ddbftest_double;{.qpipe.job.demo_deals_backfill.run[]}];
    want:exec 2*notional from .qpipe.source.demo_deals.fixture[] where deal_id in exec deal_id from value `demo_deals;
    .qunit.assertEquals[exec notional from value `demo_deals;want;
        "what reaches the target is the transform's output, not the fetched batch"]};

test_a_throwing_transform_fails_the_window_and_publishes_nothing:{[t]
    .qetl.transform.registry[`ddbftest_throws]:.qetl.transform.registry`demo_deals_passthrough;
    .qetl.transform.registry[`ddbftest_throws;`fn]:{[batch] '"boom"};
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`xf2;1;4]];
    r:.ddbftest.with_transform[`ddbftest_throws;{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertEquals[(r`windows_failed;count value `demo_deals;
                         .qetl.coverage.is_covered[`demo_deals;`;`xf2;.z.p;.ddbftest.d 1;.ddbftest.d 4]);
        (3;0;0b);
        "a transform that throws takes the failed-fetch path: nothing published, nothing covered, the run continues"]};

test_a_worker_without_a_transform_is_refused:{[t]
    err:@[{.qetl.job.bounded.define[`no_transform;x]; ""};
        `source`dataset`width!(`demo_deals;`no_transform_ds;1D);{x}];
    .qunit.assertEquals[err like "*transform*";1b;"every job has a transform, even one that changes nothing"]};

test_a_transform_that_does_not_read_the_source_contract_is_refused:{[t]
    .qetl.transform.passthrough[`ddbftest_wrong_shape;`batch;([] sym:`symbol$(); px:`float$());([] sym:enlist `EURUSD; px:enlist 1.1)];
    err:@[{.qetl.job.bounded.define[`wrong_shape;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`wrong_shape_ds;1D;`ddbftest_wrong_shape);{x}];
    .qetl.transform.registry:(enlist `ddbftest_wrong_shape) _ .qetl.transform.registry;
    .qunit.assertEquals[err like "*does not read source demo_deals*";1b;
        "a transform written against another shape fails at declaration, not on the first window"]};

test_a_clocked_transform_is_refused_for_a_bounded_worker:{[t]
    err:@[{.qetl.job.bounded.define[`clocked;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`clocked_ds;1D;`cross_quotes);{x}];
    .qunit.assertEquals[err like "*takes as_of*";1b;"a window has no single instant to hand a transform, so one that needs it is refused by name"]};

/ --- the data-quality gate -----------------------------------------------

/ A fixture with one arithmetically impossible row. Swapped in as the
/ source's fixture so the worker fetches it through the normal path rather
/ than being handed a batch directly - the point is to prove the GATE runs
/ inside do_window, not that the check function works in isolation.
bad_fixture:{[]
    update rate:0f from .qpipe.source.demo_deals.fixture[] where deal_id=3};

with_bad_fixture:{[f]
    orig:(.qetl.source.def[`demo_deals])`fixture;
    .qetl.source.sources[`demo_deals;`fixture]:{[] .ddbftest.bad_fixture[]};
    r:@[f;::;{(`threw;x)}];
    .qetl.source.sources[`demo_deals;`fixture]:orig;
    r};

test_the_check_passes_the_real_fixture:{[t]
    / The gate must not fire on good data, or it would be turned off.
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.quality_check[.qpipe.source.demo_deals.fixture[]];0;
        "the shipped fixture is acceptable, so the gate is not simply always-on"]};

test_the_check_catches_a_nonpositive_rate:{[t]
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.quality_check[.ddbftest.bad_fixture[]];1;
        "a zero rate is arithmetically impossible for a deal and is reported"]};

test_a_failing_check_is_not_published:{[t]
    / The consequence that matters. Before the gate, the offending row
    / published and coverage recorded its window as complete.
    / .
    / Asserted on the OFFENDING ROW, not on the table being empty: the bad
    / deal falls in one window and the other windows are fine, so they
    / publish. That per-window granularity is the desired behaviour - one
    / bad day must not block the good ones - and my first version of this
    / test asserted zero rows and failed against correct code.
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`chk1;1;4]];
    .ddbftest.with_bad_fixture[{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertEquals[count select from value `demo_deals where deal_id=3;0;
        "the row that failed its check is absent, while clean windows publish"]};

test_a_failing_check_leaves_the_window_uncovered:{[t]
    / And the ledger does not claim it. This is the lie the gate removes:
    / without it, is_covered would report the window published forever.
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`chk2;1;4]];
    .ddbftest.with_bad_fixture[{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;`chk2;.z.p;.ddbftest.d 1;.ddbftest.d 4];0b;
        "a window that failed its check is not recorded as covered"]};

test_a_failing_check_counts_as_a_failed_window:{[t]
    / Terminal for that window, and the run continues rather than
    / throwing - the same treatment a failed fetch gets.
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`chk3;1;4]];
    r:.ddbftest.with_bad_fixture[{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertTrue[0<r`windows_failed;
        "a check failure is a failed window, not a crash and not a silent skip"]};

test_a_worker_without_a_check_still_runs:{[t]
    / The gate is optional. demo_events_backfill declares none, and must be
    / unaffected - otherwise adding the feature would have broken every
    / worker that had not yet adopted it.
    .qunit.assertEquals[count .qetl.job.bounded.run_check[`demo_events_backfill;.qpipe.source.demo_deals.fixture[]];0;
        "a worker that declares no check reports no failures"]};

test_a_non_function_check_is_refused:{[t]
    / Refused at run time rather than silently skipped: a check declared as
    / the wrong thing is a mistake worth hearing about, and skipping it would
    / leave the worker reading as protected when it is not.
    .qunit.assertError[{.qetl.job.bounded.run_check[`demo_deals_backfill;x]};`not_a_table;
        "a check must return a table of failures"]};

test_a_zero_width_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.define[`zero_width;x]};
        `source`dataset`width`transform!(`demo_deals;`something_else;0D00:00;`demo_deals_passthrough);
        "a zero width plans infinitely many empty windows"]};

\d .
