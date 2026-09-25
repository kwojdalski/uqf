// test_run.q - tests for src/etl/core/run.q (run identity and
// materialisation metadata). Load src/etl/core/materialisation.q and
// src/etl/core/run.q before this file - .qetl.run.record calls
// .qetl.coverage.require_interval, and the attribution tests read the coverage ledger.

\d .runtest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

beforeNamespace_isolate:{[]
    // Set explicitly rather than inherited from whichever suite ran first.
    // The ledger is persisted now, so where it persists TO is this suite's
    // business, and depending on another file's setenv is exactly the kind
    // of ordering coupling that makes a suite pass alone and fail in a run.
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .testutil.reset_coverage_ledger[];
    }

/ A fresh run ledger, metadata table and coverage ledger per test, and no run
/ in flight. `release` rather than `finish`: a test that failed mid-way may
/ have left a run open, and finishing it here would write an outcome nothing
/ observed.
setUp_fresh:{[]
    .qetl.run.release[];
    ![`.;();0b;`etl_runs`etl_run_meta];
    / The FILES too. Both tables are persisted now, so emptying the
    / in-memory copies is no longer a reset: the next begin[] reloads from
    / disk first and resurrects the previous test's runs - which made the
    / two unfinished[] tests count rows they never created.
    @[{system"rm -f ",x};.qetl.run.table_path `etl_runs;{[e] (::)}];
    @[{system"rm -f ",x};.qetl.run.table_path `etl_run_meta;{[e] (::)}];
    .qetl.run.init_runs[];
    .qetl.run.init_meta[];
    // Clears the FILE as well as the table. Since the ledger is persisted,
    // emptying the in-memory copy alone is not a reset - the next
    // stage_completion reloads from disk first and resurrects the previous
    // test's rows.
    .testutil.reset_coverage_ledger[];
    }

/ --- identity ---------------------------------------------------------------

/ The bug this file exists to prevent. `first 1?0Ng` is the obvious way to
/ mint a guid and q seeds its random state identically at every process
/ start, so it returns the SAME value in every fresh process. Run ids stamp a
/ SHARED ledger, so that would merge two executions under one identity -
/ wrong attribution, which reads as correct, rather than absent attribution,
/ which is visibly absent.
/ .
/ Within one process the collision cannot be observed (the generator advances
/ after the first call), so this asserts the property that actually protects
/ the ledger: two ids minted from the same process differ, and mint does not
/ consume the shared random stream that `1?0Ng` would.
test_minted_ids_are_distinct:{[t]
    ids:.qetl.run.mint each til 8;
    .qunit.assertEquals[count distinct ids;8;"every minted run id is distinct"]};

test_mint_does_not_depend_on_the_random_seed:{[t]
    / Same seed, two mints: if mint used 1?0Ng these would be equal, which is
    / exactly the cross-process collision restated in a form one process can
    / observe.
    system"S 12345";
    a:.qetl.run.mint[];
    system"S 12345";
    b:.qetl.run.mint[];
    .qunit.assertFalse[a~b;"a reset random seed must not reproduce a run id"]};

test_no_run_is_in_flight_initially:{[t]
    .qunit.assertEquals[.qetl.run.is_running[];0b;"a process with no begun run has none in flight"]};

test_current_is_the_null_guid_outside_a_run:{[t]
    .qunit.assertEquals[null .qetl.run.current[];1b;"current[] answers honestly rather than throwing"]};

test_require_current_refuses_outside_a_run:{[t]
    .qunit.assertError[{[x] .qetl.run.require_current[]};::;"no run in flight"]};

test_begin_makes_a_run_current:{[t]
    id:.qetl.run.begin[`w1];
    .qunit.assertEquals[.qetl.run.current[];id;"begin returns the id it made current"]};

test_a_second_begin_is_refused:{[t]
    .qetl.run.begin[`w1];
    .qunit.assertError[{[x] .qetl.run.begin[`w2]};::;"already in flight"]};

test_release_clears_without_recording_an_outcome:{[t]
    id:.qetl.run.begin[`w1];
    .qetl.run.release[];
    .qunit.assertEquals[(.qetl.run.is_running[];first exec status from .qetl.run.of_run[id]);
        (0b;`running);"release clears process state but leaves the run reading running"]};

/ --- lifecycle --------------------------------------------------------------

test_a_begun_run_is_recorded_immediately:{[t]
    id:.qetl.run.begin[`w1];
    / Recorded at begin, not at finish: a ledger holding only runs that
    / finished would be blind to the executions a reader most wants to find.
    .qunit.assertEquals[count .qetl.run.of_run[id];1;"the row exists before the run ends"]};

test_a_begun_run_is_unfinished:{[t]
    .qetl.run.begin[`w1];
    .qunit.assertEquals[count .qetl.run.unfinished[];1;"a run in flight reports as unfinished"]};

test_finish_records_the_outcome_and_closes_the_run:{[t]
    id:.qetl.run.begin[`w1];
    .qetl.run.finish[`completed];
    r:first .qetl.run.of_run[id];
    .qunit.assertEquals[(r`status;r[`ended_at]<0Wp;.qetl.run.is_running[]);
        (`completed;1b;0b);"finish sets status and ended_at, and clears the current run"]};

test_finish_updates_rather_than_appends:{[t]
    id:.qetl.run.begin[`w1];
    .qetl.run.finish[`completed];
    / One execution is one row. A second row would make "how many runs were
    / there" ambiguous, and every count over this ledger wrong by the number
    / of runs that finished.
    .qunit.assertEquals[count .qetl.run.of_run[id];1;"finishing a run does not append a second row"]};

test_finish_only_touches_its_own_run:{[t]
    a:.qetl.run.begin[`w1];
    .qetl.run.finish[`completed];
    b:.qetl.run.begin[`w2];
    .qetl.run.finish[`failed];
    .qunit.assertEquals[(first exec status from .qetl.run.of_run[a];
                         first exec status from .qetl.run.of_run[b]);
        (`completed;`failed);"each run keeps its own outcome"]};

test_finish_refuses_outside_a_run:{[t]
    .qunit.assertError[{[x] .qetl.run.finish[`completed]};::;"no run in flight"]};

test_an_interrupted_run_stays_running:{[t]
    / The state a crashed process leaves behind, and the one a reader needs
    / to be able to find. Nothing here can detect a crash, so `running` on a
    / run whose process is gone IS the signal.
    id:.qetl.run.begin[`w1];
    .qetl.run.release[];
    .qunit.assertEquals[(first exec status from .qetl.run.of_run[id];count .qetl.run.unfinished[]);
        (`running;1);"an abandoned run remains visible as unfinished"]};

/ --- metadata ---------------------------------------------------------------

test_record_stores_one_row_per_fact:{[t]
    .qetl.run.begin[`w1];
    n:.qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;`rows`note!(5;"ok")];
    .qunit.assertEquals[(n;count .qetl.run.facts_of[.qetl.run.current[]]);(2;2);"two facts become two rows"]};

test_record_refuses_outside_a_run:{[t]
    .qunit.assertError[{[x] .qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;(enlist `a)!enlist 1]};
        ::;"no run in flight"]};

test_record_refuses_a_non_dictionary:{[t]
    .qetl.run.begin[`w1];
    .qunit.assertError[{[x] .qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;x]};
        ([] a:1 2);"facts must be a dictionary"]};

test_record_refuses_an_empty_interval:{[t]
    / Metadata is keyed by the window, so a window that covers nothing is as
    / meaningless here as it is in the coverage ledger.
    .qetl.run.begin[`w1];
    .qunit.assertError[{[x] .qetl.run.record[`ds1;x;x;(enlist `a)!enlist 1]};
        .runtest.d 1;"a zero-width window"]};

test_values_of_every_type_round_trip_as_text:{[t]
    .qetl.run.begin[`w1];
    .qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;
        `s`str`n`b!(`v1;"hello";42;0b)];
    got:exec label!text from .qetl.run.facts_of[.qetl.run.current[]];
    .qunit.assertEquals[got;`s`str`n`b!("v1";"hello";"42";"0b");
        "a symbol loses its backtick, a string is unquoted, and everything is text"]};

test_facts_about_spans_runs:{[t]
    / The cross-run comparison run identity was added to make askable: one
    / window materialised twice, both sets of facts side by side.
    .qetl.run.begin[`w1];
    .qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;(enlist `rows)!enlist 5];
    .qetl.run.finish[`completed];
    .qetl.run.begin[`w1];
    .qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;(enlist `rows)!enlist 9];
    .qetl.run.finish[`completed];
    / `enlist each "59"`, not `(,"5";,"9")`: a `,` opening a parenthesised
    / list item does not parse in q - it reads as a join with nothing on its
    / left. Each value here is a ONE-CHARACTER STRING, and ("5";"9") would be
    / the two-char vector "59" instead, which is a different assertion.
    .qunit.assertEquals[exec text from .qetl.run.facts_about[`ds1;.runtest.d 1;.runtest.d 2];
        enlist each "59";"both runs' facts about one window are visible together"]};

test_facts_of_isolates_one_run:{[t]
    a:.qetl.run.begin[`w1];
    .qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;(enlist `rows)!enlist 5];
    .qetl.run.finish[`completed];
    .qetl.run.begin[`w1];
    .qetl.run.record[`ds1;.runtest.d 1;.runtest.d 2;(enlist `rows)!enlist 9];
    .qunit.assertEquals[exec text from .qetl.run.facts_of[a];enlist enlist "5";
        "one run's facts exclude another's"]};

/ --- attribution on the coverage ledger -------------------------------------

test_coverage_carries_the_current_run:{[t]
    id:.qetl.run.begin[`w1];
    .qetl.coverage.stage_completion[`ds1;`;`v1;.runtest.d 1;.runtest.d 2;10];
    .qunit.assertEquals[first exec run_id from .qetl.coverage.ledger[];id;
        "a materialisation is stamped with the run that produced it"]};

test_coverage_outside_a_run_records_a_null_run:{[t]
    / An honest null, not an invented identity. A materialisation staged by
    / hand or by a test genuinely belongs to no run, and saying so is better
    / than attributing it to one that did not happen.
    .qetl.coverage.stage_completion[`ds1;`;`v1;.runtest.d 1;.runtest.d 2;10];
    .qunit.assertEquals[null first exec run_id from .qetl.coverage.ledger[];1b;
        "coverage staged outside a run is recorded as unattributed"]};

test_materialisations_of_groups_one_execution:{[t]
    id:.qetl.run.begin[`w1];
    .qetl.coverage.stage_completion[`ds1;`;`v1;.runtest.d 1;.runtest.d 2;10];
    .qetl.coverage.stage_completion[`ds2;`;`v1;.runtest.d 1;.runtest.d 2;20];
    .qetl.run.finish[`completed];
    .qetl.run.begin[`w2];
    .qetl.coverage.stage_completion[`ds3;`;`v1;.runtest.d 1;.runtest.d 2;30];
    .qunit.assertEquals[asc exec dataset from .qetl.coverage.materialisations_of[id];
        `s#`ds1`ds2;"one run's materialisations exclude a later run's"]};

test_materialisations_of_includes_superseded_rows:{[t]
    / What a run produced does not change when a later run restates it.
    / Hiding withdrawn rows would make a fully-restated run look like a run
    / that did nothing.
    id:.qetl.run.begin[`w1];
    .qetl.coverage.stage_completion[`ds1;`;`v1;.runtest.d 1;.runtest.d 2;10];
    .qetl.run.finish[`completed];
    .qetl.coverage.supersede[`ds1;`;`v1;.runtest.d 1;.runtest.d 2];
    .qunit.assertEquals[count .qetl.coverage.materialisations_of[id];1;
        "a superseded materialisation is still something that run produced"]};

test_contributing_runs_lists_every_execution_behind_a_dataset:{[t]
    a:.qetl.run.begin[`w1];
    .qetl.coverage.stage_completion[`ds1;`;`v1;.runtest.d 1;.runtest.d 2;10];
    .qetl.run.finish[`completed];
    b:.qetl.run.begin[`w1];
    .qetl.coverage.stage_completion[`ds1;`;`v1;.runtest.d 2;.runtest.d 3;10];
    .qetl.run.finish[`completed];
    .qunit.assertEquals[.qetl.coverage.contributing_runs[`ds1;`;`v1];(a;b);
        "a backfill run in slices shows every run that contributed, in order"]};

test_contributing_runs_is_version_specific:{[t]
    / Same reasoning as no-merging-across-versions everywhere else in the ledger: attribution
    / under one release says nothing about another.
    .qetl.run.begin[`w1];
    .qetl.coverage.stage_completion[`ds1;`;`v1;.runtest.d 1;.runtest.d 2;10];
    .qetl.run.finish[`completed];
    b:.qetl.run.begin[`w1];
    .qetl.coverage.stage_completion[`ds1;`;`v2;.runtest.d 1;.runtest.d 2;10];
    .qunit.assertEquals[.qetl.coverage.contributing_runs[`ds1;`;`v2];enlist b;
        "runs under v1 do not appear in v2's attribution"]};

/ --- the schema guard -------------------------------------------------------

test_the_coverage_schema_includes_run_id:{[t]
    .qunit.assertEquals[`run_id in .qetl.coverage.schema;1b;
        "run_id is part of the declared coverage shape, not an extra column"]};

test_require_run_schema_accepts_the_table_it_describes:{[t]
    .qetl.run.init_runs[];
    .qunit.assertEquals[.qetl.run.require_run_schema[];1b;"the declared shape and the built table agree"]};

test_require_run_schema_rejects_a_foreign_table:{[t]
    / The case the guard exists for: a ledger some other process built to a
    / different shape, whose reads here would silently return nulls.
    `etl_runs set ([] run_id:`guid$(); worker:`symbol$());
    .qunit.assertError[{[x] .qetl.run.require_run_schema[]};::;"is missing"]};

\d .
