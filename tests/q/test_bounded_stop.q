// test_bounded_stop.q - a clean stop for a bounded run: a stop file, checked between windows
// (src/etl/core/bounded_stop.q, #2679, #2677).
//
// The run is the real lifecycle on demo_deals_backfill (three daily windows, d1..d4). Only the io
// manager is replaced, by one that counts the windows it was asked to write and, after the Nth,
// does what an operator does: creates or removes the run's stop file. So the file appears
// BETWEEN two windows, exactly where a launcher's Ctrl-C would land.
//
// Load src/etl/core/*.q, src/etl/sources/demo_deals.q, src/etl/workers/demo_deals_backfill.q,
// tests/lib/qunit.q and tests/lib/support.q before this file.


.qt.qetl.job.bounded.stop.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

.qt.qetl.job.bounded.stop.spec:`source_version`range_from`range_to!(`v1;.qt.qetl.job.bounded.stop.d[1];.qt.qetl.job.bounded.stop.d[4])

/ the release a v1 run on demo_deals' fixture records under
.qt.qetl.job.bounded.stop.fv:`$"v1~fixture"

.qt.qetl.job.bounded.stop.worker:`demo_deals_backfill

/ how many windows have been written since the manager was installed, and what to do after each
.qt.qetl.job.bounded.stop.written:0
.qt.qetl.job.bounded.stop.after:()!()

.qt.qetl.job.bounded.stop.path:{[] .qetl.job.bounded.stop.path[.qt.qetl.job.bounded.stop.worker;.z.i]}

.qt.qetl.job.bounded.stop.lock_dir:{[] hsym `$(.qetl.status.status_dir[]),"/etl_coverage.lock"}

.qt.qetl.job.bounded.stop.touch:{[] (hsym `$.qt.qetl.job.bounded.stop.path[]) 0: enlist "stop"}

.qt.qetl.job.bounded.stop.remove:{[] @[hdel;hsym `$.qt.qetl.job.bounded.stop.path[];{[e] (::)}]}

/ Run the action the test wants after the Nth window: n!action, an action a niladic function. Only the
/ worker's own table counts - its reaction writes deal_positions in the same window.
.qt.qetl.job.bounded.stop.tick:{[target]
    if[not target~`demo_deals; :(::)];
    `.qt.qetl.job.bounded.stop.written set 1+.qt.qetl.job.bounded.stop.written;
    n:.qt.qetl.job.bounded.stop.written;
    if[n in key .qt.qetl.job.bounded.stop.after; .qt.qetl.job.bounded.stop.after[n][]]}

.qt.qetl.job.bounded.stop.manager:{[]
    w:{[target;batch] r:.qetl.io.write_memory[target;batch]; .qt.qetl.job.bounded.stop.tick[target]; r};
    k:{[target;batch;opts] r:.qetl.io.write_memory_keyed[target;batch;opts]; .qt.qetl.job.bounded.stop.tick[target]; r};
    `write`write_keyed!(w;k)}

.qt.qetl.job.bounded.stop.status:{[] .j.k first read0 hsym `$.qetl.status.file_path .qt.qetl.job.bounded.stop.worker}

.qt.qetl.job.bounded.stop.run:{[]
    .qpipe.job.demo_deals_backfill.init .qt.qetl.job.bounded.stop.spec;
    .qpipe.job.demo_deals_backfill.run[]}

.qt.qetl.job.bounded.stop.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qetl.job.bounded.stop.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    .qetl.run.release[];
    .qt.qetl.job.bounded.stop.remove[];
    `.qt.qetl.job.bounded.stop.written set 0;
    `.qt.qetl.job.bounded.stop.after set ()!();
    `.qt.qetl.job.bounded.stop.saved set .qetl.io.default;
    .qetl.io.default:.qt.qetl.job.bounded.stop.manager[];
    }

.qt.qetl.job.bounded.stop.tearDown_release:{[]
    .qetl.io.default:.qt.qetl.job.bounded.stop.saved;
    .qt.qetl.job.bounded.stop.remove[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    }

.qt.qetl.job.bounded.stop.saved:()!()

/ --- the path --------------------------------------------------------------

.qt.qetl.job.bounded.stop.test_the_path_names_the_worker_and_the_process_in_the_status_directory:{[t]
    p:.qetl.job.bounded.stop.path[`demo_deals_backfill;4321i];
    .qunit.assertEquals[p;(.qetl.status.status_dir[]),"/demo_deals_backfill.4321.stop";"<status_dir>/<worker>.<pid>.stop, beside the worker's lock"];
    .qunit.assertEquals[p like "*",.qetl.job.bounded.stop.suffix;1b;"and it ends with the suffix the facts hand to python"]};

.qt.qetl.job.bounded.stop.test_a_request_for_another_process_is_not_a_request_for_this_one:{[t]
    (hsym `$.qetl.job.bounded.stop.path[`demo_deals_backfill;.z.i+1i]) 0: enlist "stop";
    got:.qetl.job.bounded.stop.requested `demo_deals_backfill;
    @[hdel;hsym `$.qetl.job.bounded.stop.path[`demo_deals_backfill;.z.i+1i];{[e] (::)}];
    .qunit.assertEquals[got;0b;"a stale file for a pid that is not this run's stops nothing"]};

/ --- a stop between windows --------------------------------------------------

/ The stop file appears while window 2 is being written, so it is there before window 3.
.qt.qetl.job.bounded.stop.test_a_stop_file_before_window_3_stops_the_run_after_window_2:{[t]
    `.qt.qetl.job.bounded.stop.after set (enlist 2)!enlist .qt.qetl.job.bounded.stop.touch;
    r:.qt.qetl.job.bounded.stop.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed;r`rows_published);(`stopped;2;0;2);"two windows finished, the third never started"];
    .qunit.assertEquals[.qt.qetl.job.bounded.stop.written;2;"the third window was not even written"];
    .qunit.assertEquals[.qetl.job.bounded.exit_code r`state;4i;"and the code is the stop code, not a failure's"]};

.qt.qetl.job.bounded.stop.test_a_stopped_run_leaves_exactly_the_completed_windows_in_the_ledger:{[t]
    `.qt.qetl.job.bounded.stop.after set (enlist 2)!enlist .qt.qetl.job.bounded.stop.touch;
    r:.qt.qetl.job.bounded.stop.run[];
    fv:.qt.qetl.job.bounded.stop.fv;
    d:.qt.qetl.job.bounded.stop.d;
    .qunit.assertEquals[count select from .qetl.coverage.ledger[] where dataset=`demo_deals;2;"one coverage row per completed window"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;fv;.z.p;d 1;d 3];1b;"windows 1 and 2 are covered"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;fv;.z.p;d 3;d 4];0b;"window 3 is not, and is not claimed"];
    .qunit.assertEquals[r`cursor;d 3;"the cursor is the end of the last window that finished"]};

.qt.qetl.job.bounded.stop.test_a_stopped_run_releases_both_locks_and_removes_its_stop_file:{[t]
    `.qt.qetl.job.bounded.stop.after set (enlist 2)!enlist .qt.qetl.job.bounded.stop.touch;
    .qt.qetl.job.bounded.stop.run[];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held .qt.qetl.job.bounded.stop.worker;0b;"the worker lock is free"];
    .qunit.assertEquals[()~key .qt.qetl.job.bounded.stop.lock_dir[];1b;"the coverage lock is free"];
    .qunit.assertEquals[()~key hsym `$.qt.qetl.job.bounded.stop.path[];1b;"the stop file is gone"]};

.qt.qetl.job.bounded.stop.test_a_stopped_run_says_stopped_in_its_status_file_and_its_run_ledger:{[t]
    `.qt.qetl.job.bounded.stop.after set (enlist 2)!enlist .qt.qetl.job.bounded.stop.touch;
    .qt.qetl.job.bounded.stop.run[];
    s:.qt.qetl.job.bounded.stop.status[];
    .qunit.assertEquals[(s`state;"j"$s`windows_completed);("stopped";2j);"the status file: stopped, two windows done"];
    .qunit.assertTrue[(s`error) like "stopped on request after 2 of 3 window(s)*";"and it says why, in the error field"];
    .qunit.assertEquals[exec last status from .qetl.run.runs[] where worker=.qt.qetl.job.bounded.stop.worker;`stopped;"the run ledger closed the run as stopped"]};

.qt.qetl.job.bounded.stop.test_a_rerun_after_a_stop_does_the_remaining_window_only:{[t]
    `.qt.qetl.job.bounded.stop.after set (enlist 2)!enlist .qt.qetl.job.bounded.stop.touch;
    .qt.qetl.job.bounded.stop.run[];
    `.qt.qetl.job.bounded.stop.after set ()!();
    `.qt.qetl.job.bounded.stop.written set 0;
    r:.qt.qetl.job.bounded.stop.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;.qt.qetl.job.bounded.stop.written);(`completed;1;1);"the rerun plans window 3 alone and finishes the range"];
    .qunit.assertEquals[count value `demo_deals;3;"three rows in all: nothing rewritten, nothing missing"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;.qt.qetl.job.bounded.stop.fv;.z.p;.qt.qetl.job.bounded.stop.d[1];.qt.qetl.job.bounded.stop.d[4]];1b;"the whole range is covered"]};

.qt.qetl.job.bounded.stop.test_a_stop_found_before_the_first_window_does_no_work:{[t]
    .qt.qetl.job.bounded.stop.touch[];
    r:.qt.qetl.job.bounded.stop.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;.qt.qetl.job.bounded.stop.written);(`stopped;0;0);"nothing was started"];
    .qunit.assertEquals[count select from .qetl.coverage.ledger[] where dataset=`demo_deals;0;"and nothing is claimed"]};

/ --- no request, no effect ---------------------------------------------------

.qt.qetl.job.bounded.stop.test_a_stop_file_removed_before_the_next_window_changes_nothing:{[t]
    `.qt.qetl.job.bounded.stop.after set (enlist 1)!enlist {.qt.qetl.job.bounded.stop.touch[]; .qt.qetl.job.bounded.stop.remove[]};
    r:.qt.qetl.job.bounded.stop.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;3);"asked then withdrawn between windows: the run is unaffected"]};

.qt.qetl.job.bounded.stop.test_a_run_with_no_stop_file_is_unaffected:{[t]
    r:.qt.qetl.job.bounded.stop.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;.qetl.job.bounded.exit_code r`state);(`completed;3;0i);"no file, a normal run"]};

.qt.qetl.job.bounded.stop.test_another_runs_stop_file_leaves_this_run_alone_and_in_place:{[t]
    other:hsym `$.qetl.job.bounded.stop.path[`demo_deals_backfill;.z.i+1i];
    other 0: enlist "stop";
    r:.qt.qetl.job.bounded.stop.run[];
    left:not ()~key other;
    @[hdel;other;{[e] (::)}];
    .qunit.assertEquals[(r`state;left);(`completed;1b);"the run ignores it, and does not remove what is not its own"]};

/ --- the vocabulary ------------------------------------------------------------

.qt.qetl.job.bounded.stop.test_stopped_is_a_terminal_state_and_not_a_success:{[t]
    .qunit.assertEquals[(`stopped in .qetl.status.terminal_states;`stopped in .qetl.status.status_states where 0i=.qetl.job.bounded.exit_code each .qetl.status.status_states);(1b;0b);"terminal, and an orchestrator does not read it as success"];
    .qunit.assertEquals[.qetl.status.require_transition[`running;`stopped];1b;"a running worker may stop"];
    .qunit.assertThrows[{.qetl.status.require_transition[`stopped;`completed]};(::);"require_transition: stopped -> completed is forbidden*";"a stopped run cannot be resurrected as completed"]};
