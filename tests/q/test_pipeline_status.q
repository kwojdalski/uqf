// test_pipeline_status.q - tests for .qpipe's status writer and its G-02
// transition rules (scripts/torq_pipeline.q).
//
// write_status had no direct q tests before this file - it was exercised only
// indirectly, through .qbfstate.fail. That is how the transition gap survived:
// the states were validated and the transitions were not, so every illegal
// one wrote cleanly.
//
// Load scripts/torq_pipeline.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .statustest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

spec:{[] `source_version`range_from`range_to!(`v1;.statustest.d 1;.statustest.d 2)}
progress:{[] `cursor`rows_published`windows_completed!(.statustest.d 2;10;1)}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ Each test starts with no status file for its instance, so no test inherits
/ another's state - which for a state MACHINE is the whole ballgame.
setUp_fresh:{[]
    system"rm -f build/test-status/airflow_status_st1.txt";
    }

write:{[state;err] .qpipe.write_status[`w;`st1;state;.statustest.spec[];.statustest.progress[];err]}

/ --- the writer's existing guarantees --------------------------------------

test_a_valid_status_writes:{[t]
    .qunit.assertEquals[0<count .statustest.write[`starting;""];1b;"a first status writes and returns its path"]};

test_an_unknown_state_is_refused:{[t]
    .qunit.assertError[{.statustest.write[x;""]};`bogus;"the state set is closed"]};

test_a_failed_state_must_carry_an_error:{[t]
    .qunit.assertError[{.statustest.write[`failed;x]};"";"a failure with no message is not a diagnosis"]};

test_a_reversed_range_is_refused:{[t]
    bad:`source_version`range_from`range_to!(`v1;.statustest.d 2;.statustest.d 1);
    .qunit.assertError[{.qpipe.write_status[`w;`st1;`running;x;.statustest.progress[];""]};bad;"a bad range can never reach the file (E-08)"]};

test_a_null_source_version_is_refused:{[t]
    bad:`source_version`range_from`range_to!(`;.statustest.d 1;.statustest.d 2);
    .qunit.assertError[{.qpipe.write_status[`w;`st1;`running;x;.statustest.progress[];""]};bad;"source_version is mandatory (E-09)"]};

/ --- reading the previous state --------------------------------------------

test_no_file_means_no_previous_state:{[t]
    .qunit.assertEquals[null .qpipe.previous_state `st1;1b;"a first write has nothing to transition from"]};

test_the_previous_state_is_read_back:{[t]
    .statustest.write[`starting;""];
    .qunit.assertEquals[.qpipe.previous_state `st1;`starting;"the writer can see what it last wrote, across a restart"]};

/ --- the transition rules (G-02) -------------------------------------------

test_a_first_state_is_always_permitted:{[t]
    .qunit.assertEquals[.qpipe.require_transition[`;`failed];1b;"a worker whose init failed before writing `starting must still be able to record it"]};

test_starting_may_begin_running:{[t]
    .qunit.assertEquals[.qpipe.require_transition[`starting;`running];1b;"the ordinary path"]};

/ running -> running is the next window, not a stutter.
test_running_may_continue_running:{[t]
    .qunit.assertEquals[.qpipe.require_transition[`running;`running];1b;"each window reports progress under the same state"]};

test_running_may_complete:{[t]
    .qunit.assertEquals[.qpipe.require_transition[`running;`completed];1b;"a finished run is terminal"]};

/ The case this rule exists for. Either direction resurrects a terminal run,
/ and because the file is overwritten the previous state is GONE - so a
/ correctly recorded failure silently reads as healthy.
test_a_completed_run_may_not_go_back_to_running:{[t]
    .qunit.assertError[{.qpipe.require_transition[`completed;x]};`running;"resurrecting a completed run hides whatever happens next"]};

test_a_failed_run_may_not_report_completed:{[t]
    .qunit.assertError[{.qpipe.require_transition[`failed;x]};`completed;"a failure overwritten by success is the worst possible silent outcome"]};

test_an_idle_run_may_not_go_back_to_running:{[t]
    .qunit.assertError[{.qpipe.require_transition[`idle;x]};`running;"idle is terminal for that run"]};

/ ...but beginning again IS legal. The rule is not "terminal is forever", it
/ is "you may begin a new run, but you may not slip from finished back to
/ in-progress without declaring it".
test_every_terminal_state_may_start_a_new_run:{[t]
    .qunit.assertEquals[
        .qpipe.require_transition[;`starting] each `idle`completed`failed;
        111b;
        "a new run announces itself, and that is always allowed"]};

/ A failure must ALWAYS be recordable, from any state. The first version of
/ this rule refused `failed -> failed`, which made a second consecutive
/ failure throw INSIDE .qbfstate.fail - the shell's own error path - masking
/ the original error with a complaint about state transitions. A rule that
/ exists to stop a failure being hidden must not itself hide one.
test_a_failure_is_recordable_from_any_state:{[t]
    .qunit.assertEquals[
        .qpipe.require_transition[;`failed] each `starting`running`idle`completed`failed;
        11111b;
        "a failure is always recordable, including after another failure"]};

/ ...and through the real error path, not just the rule in isolation. This is
/ the check that found the bug above.
test_two_consecutive_failures_both_record:{[t]
    system"rm -f build/test-status/airflow_status_st1.txt";
    .statustest.write[`failed;"first"];
    .qunit.assertEquals[0<count .statustest.write[`failed;"second"];1b;"the shell can report a second failure without throwing in its own handler"]};

test_an_unknown_previous_state_is_refused:{[t]
    .qunit.assertError[{.qpipe.require_transition[x;`running]};`nonsense;"a state nobody defined cannot be reasoned about"]};

/ The error must say what the transition would have HIDDEN, not merely that
/ it was refused - the reader of this error is someone debugging a run that
/ looked fine.
test_the_refusal_explains_what_it_prevents:{[t]
    err:@[{.qpipe.require_transition[`failed;x]; ""};`completed;{x}];
    .qunit.assertEquals[err like "*silently read as healthy*";1b;"the message names the consequence, not just the rule"]};

/ --- the writer enforces them ----------------------------------------------

test_the_writer_refuses_a_forbidden_transition:{[t]
    .statustest.write[`completed;""];
    .qunit.assertError[{.statustest.write[x;""]};`running;"the writer validates, so a caller cannot forget to"]};

test_the_writer_allows_a_new_run_after_completion:{[t]
    .statustest.write[`completed;""];
    .qunit.assertEquals[0<count .statustest.write[`starting;""];1b;"a new run after a completed one is the normal restart"]};

/ A refused write must leave the PREVIOUS status intact. Truncating it would
/ destroy the very record the rule exists to protect.
test_a_refused_write_leaves_the_recorded_status_alone:{[t]
    .statustest.write[`failed;"the real error"];
    @[{.statustest.write[x;""]};`completed;{x}];
    .qunit.assertEquals[.qpipe.previous_state `st1;`failed;"the failure survives an attempt to overwrite it with success"]};

test_the_ordinary_lifecycle_writes_end_to_end:{[t]
    .statustest.write[`starting;""];
    .statustest.write[`running;""];
    .statustest.write[`running;""];
    .statustest.write[`completed;""];
    .qunit.assertEquals[.qpipe.previous_state `st1;`completed;"start, two windows, finish"]};

\d .
