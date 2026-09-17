// test_react.q - tests for src/etl/core/react.q (.qreact), the publication
// event that recomputes a dataset when the one it reads is published.
//
// The tests that matter are the three the header names as properties, because
// each is a way this could be quietly wrong rather than visibly broken:
//
//   a cascade is a LOOP, not recursion - so a chain cannot blow the stack
//   a failing reaction does not fail the publication that already happened
//   a cycle terminates rather than spinning
//
// Plus the one that makes it useful at all: a real worker run fires the event
// with the window it actually published.
//
// Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .rxtest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

/ Every reaction here records into this, rather than each test inventing its
/ own global: a handler cannot close over a local (q lambdas do not capture),
/ so the record has to be a global somewhere.
fired:()

setUp_fresh:{[]
    .qreact.reset[];
    `.rxtest.fired set ();
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ A handler that records it ran. Built by name so several reactions can be
/ told apart in `fired`.
recorder:{[nm] {[nm;ds;f;t] `.rxtest.fired set .rxtest.fired,enlist (nm;ds;f;t)}[nm]}

/ --- registering ----------------------------------------------------------

test_a_reaction_registers_and_is_listed:{[t]
    .qreact.on[`a;`r1;.rxtest.recorder`r1];
    .qunit.assertEquals[exec name from .qreact.for_dataset `a;enlist `r1;
        "a registered reaction is listed for its dataset"]};

/ Re-registering REPLACES. The first version of `on` compared the parameter
/ to the column of the same name - inside a where-clause both sides resolve
/ to the COLUMN, so the filter matched nothing and appended a second handler.
/ Measured, not theorised: the smoke run showed r1 twice.
test_reregistering_replaces_rather_than_adding:{[t]
    .qreact.on[`a;`r1;.rxtest.recorder`first];
    .qreact.on[`a;`r1;.rxtest.recorder`second];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[(count .qreact.for_dataset `a;.rxtest.fired[;0]);(1;enlist `second);
        "the second registration replaces the first, and only it runs"]};

test_a_handler_of_the_wrong_arity_is_refused_at_registration:{[t]
    / Not at the first publication, which may be hours later and would be
    / attributed to the upstream job rather than to this wiring.
    .qunit.assertError[{.qreact.on[`a;`bad;x]};{[ds;f] ds};
        "a handler must take (dataset;range_from;range_to)"]};

test_off_stops_one_reaction_and_leaves_the_others:{[t]
    .qreact.on[`a;`keep;.rxtest.recorder`keep];
    .qreact.on[`a;`drop;.rxtest.recorder`drop];
    .qreact.off[`a;`drop];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[.rxtest.fired[;0];enlist `keep;"only the remaining reaction runs"]};

/ --- notifying ------------------------------------------------------------

test_a_notification_carries_the_range:{[t]
    / The whole reason this is an event rather than a poll: the handler is
    / told WHAT changed, so it can recompute that range instead of diffing a
    / ledger to find it.
    .qreact.on[`a;`r1;.rxtest.recorder`r1];
    .qreact.notify[`a;.rxtest.d 3;.rxtest.d 4];
    .qunit.assertEquals[first .rxtest.fired;(`r1;`a;.rxtest.d 3;.rxtest.d 4);
        "the handler receives the dataset and the published range"]};

test_a_dataset_with_no_reaction_is_a_no_op:{[t]
    .qunit.assertEquals[.qreact.notify[`nobody_cares;.rxtest.d 1;.rxtest.d 2];0;
        "publishing a dataset nothing reads runs nothing and does not throw"]};

test_every_reaction_on_a_dataset_runs:{[t]
    .qreact.on[`a;`one;.rxtest.recorder`one];
    .qreact.on[`a;`two;.rxtest.recorder`two];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[asc .rxtest.fired[;0];`one`two;"two consumers of one dataset both hear about it"]};

/ --- the three properties -------------------------------------------------

/ A -> B: the reaction on `a` publishes `b`, which has its own reaction.
/ Both must run, and the second must run from the DRAIN rather than nested
/ inside the first - which is what keeps a chain flat.
test_a_cascade_runs_the_whole_chain:{[t]
    .qreact.on[`a;`a_to_b;{[ds;f;t] .qreact.notify_from_here[`b;f;t]}];
    .qreact.on[`b;`b_done;.rxtest.recorder`b_done];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[.rxtest.fired[;0];enlist `b_done;
        "publishing a triggers b's reaction, with a's range carried through"]};

test_a_cascade_does_not_nest:{[t]
    / The property, asserted on the QUEUE rather than on the result: while a
    / handler runs, `draining` is set, so its own notification is appended
    / and returns 0 instead of starting a second drain. Recursion would
    / return 1 here and grow the stack on a long chain.
    .qreact.on[`a;`a_to_b;{[ds;f;t]
        `.rxtest.fired set .rxtest.fired,enlist (`inner_return;.qreact.notify_from_here[`b;f;t];0N;0N)}];
    .qreact.on[`b;`b_done;.rxtest.recorder`b_done];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[(first .rxtest.fired)1;0;
        "a notification raised inside a handler is queued, not run nested"]};

test_a_cycle_terminates:{[t]
    / a -> b -> a. .qdag.topological refuses a cycle at registration, but a
    / handler can publish anywhere, so the runtime needs its own guard: the
    / same (dataset;range) is dispatched at most once per drain.
    .qreact.on[`a;`to_b;{[ds;f;t] .qreact.notify_from_here[`b;f;t]}];
    .qreact.on[`b;`to_a;{[ds;f;t] .qreact.notify_from_here[`a;f;t]}];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[count select from .qreact.history where outcome=`ok;2;
        "a -> b -> a settles after each fires once rather than spinning"]};

test_the_same_range_fires_again_on_a_later_publication:{[t]
    / The cycle guard is per-drain on purpose. Publishing the same range
    / again later is a NEW event - a restatement, say - and must fire.
    .qreact.on[`a;`r1;.rxtest.recorder`r1];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[count .rxtest.fired;2;"the guard bounds one cascade, not the future"]};

test_a_runaway_chain_is_refused_at_max_depth:{[t]
    / Every publication is a new range, so the per-drain guard never bites:
    / depth is what stops this one.
    .qreact.on[`a;`deeper;{[ds;f;t] .qreact.notify_from_here[`a;f+1D;t+1D]}];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[count select from .qreact.history where outcome=`refused;1;
        "the chain stops at max_depth and says so rather than running forever"]};

test_a_failing_reaction_is_recorded_not_thrown:{[t]
    .qreact.on[`a;`bad;{[ds;f;t] '"boom"}];
    r:@[{.qreact.notify[`a;.rxtest.d 1;.rxtest.d 2]};::;{`threw}];
    .qunit.assertEquals[(r;exec first detail from .qreact.history where outcome=`failed);(1;"boom");
        "the failure is recorded and the notification returns normally"]};

test_a_failing_reaction_does_not_stop_the_others:{[t]
    .qreact.on[`a;`bad;{[ds;f;t] '"boom"}];
    .qreact.on[`a;`good;.rxtest.recorder`good];
    .qreact.notify[`a;.rxtest.d 1;.rxtest.d 2];
    .qunit.assertEquals[.rxtest.fired[;0];enlist `good;
        "one broken consumer does not deprive the rest of the event"]};

/ --- the DAG wiring -------------------------------------------------------

test_dag_consumers_names_who_reads_a_dataset:{[t]
    .qdag.adopt_all[];
    .qunit.assertEquals[.qreact.dag_consumers `nothing_reads_this;`$();
        "a dataset no job reads has no consumers, which is not an error"]};

test_the_audit_reports_a_reaction_no_job_declares:{[t]
    .qdag.adopt_all[];
    .qreact.on[`invented_dataset;`r1;.rxtest.recorder`r1];
    .qunit.assertTrue[`invented_dataset in .qreact.audit[]`undeclared;
        "a reaction on a dataset the graph does not know is reported, not refused"]};

/ --- the real worker path -------------------------------------------------

/ THE POINT OF THE FILE. A real worker run fires the event, once per window,
/ with the window it actually published - not a timer noticing later.
test_a_worker_run_fires_the_event_for_every_window:{[t]
    .testutil.reset_coverage_ledger[];
    .qbfstate.release_lock `demo_deals_backfill;
    .qbfstate.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qsdemo.fixture[];
    .qreact.on[`demo_deals;`watcher;.rxtest.recorder`watcher];
    .qwrk.demo_deals_backfill.init[`source_version`range_from`range_to!(`rx1;.rxtest.d 1;.rxtest.d 4)];
    .qwrk.demo_deals_backfill.run[];
    .qwrk.demo_deals_backfill.cleanup[];
    .qunit.assertEquals[(count .rxtest.fired;.rxtest.fired[0;2];.rxtest.fired[0;3]);
        (3;.rxtest.d 1;.rxtest.d 2);
        "three windows published, three events, the first carrying the first window's own range"]};

test_a_dry_run_publishes_nothing_and_fires_nothing:{[t]
    / A rehearsal must not trigger real downstream work.
    .testutil.reset_coverage_ledger[];
    .qbfstate.release_lock `demo_deals_backfill;
    .qbfstate.clear_checkpoint `demo_deals_backfill;
    .qreact.on[`demo_deals;`watcher;.rxtest.recorder`watcher];
    setenv[`UQF_DRY_RUN;"true"];
    .qwrk.demo_deals_backfill.init[`source_version`range_from`range_to!(`rx2;.rxtest.d 1;.rxtest.d 4)];
    .qwrk.demo_deals_backfill.run[];
    .qwrk.demo_deals_backfill.cleanup[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count .rxtest.fired;0;"a dry run publishes nothing, so it announces nothing"]};

/ A reaction that throws must not turn a successful materialisation into a
/ failed one: the rows are already written and the coverage already staged.
test_a_failing_reaction_leaves_the_window_published_and_covered:{[t]
    .testutil.reset_coverage_ledger[];
    .qbfstate.release_lock `demo_deals_backfill;
    .qbfstate.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qsdemo.fixture[];
    .qreact.on[`demo_deals;`bad;{[ds;f;t] '"downstream is broken"}];
    .qwrk.demo_deals_backfill.init[`source_version`range_from`range_to!(`rx3;.rxtest.d 1;.rxtest.d 4)];
    r:.qwrk.demo_deals_backfill.run[];
    .qwrk.demo_deals_backfill.cleanup[];
    .qunit.assertEquals[(r`state;r`windows_failed;.qcov.is_covered[`demo_deals;`;`rx3;.z.p;.rxtest.d 1;.rxtest.d 4]);
        (`completed;0;1b);
        "the upstream run completes and its coverage stands, whatever the downstream did"]};

\d .
