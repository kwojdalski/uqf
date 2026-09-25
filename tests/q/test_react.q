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

/ Rebuild the graph after tests that reset it, and clear the test reactions
/ they registered: the job graph is process-wide, so a namespace that left it
/ empty would take the next suite's ground out from under it.
tearDown_graph:{[] .qreact.reset[]; .qdag.adopt_all[];}

/ --- reactions in the job graph -------------------------------------------

test_a_plain_reaction_declares_no_output_and_is_a_terminal_node:{[t]
    / Not left OUT of the graph: a graph that omitted it would show the
    / dataset as a sink - "nothing consumes this" - which is a stronger and
    / wronger claim than "something consumes this and did not say what it
    / writes".
    .qdag.reset[];
    .qreact.on[`a;`glance;{[ds;f;t] 1}];
    .qdag.adopt_reactions[];
    d:.qdag.def .qdag.reaction_job[`a;`glance];
    .qunit.assertEquals[(d`kind;d`inputs;d`outputs);(`reaction;enlist `a;`$());
        "a plain reaction reads the dataset it watches and claims to write nothing"]};

test_a_declared_output_becomes_a_graph_edge:{[t]
    .qdag.reset[];
    .qreact.on_writing[`a;`build_b;`b;{[ds;f;t] 1}];
    .qdag.adopt_reactions[];
    .qunit.assertEquals[.qdag.producers `b;enlist .qdag.reaction_job[`a;`build_b];
        "what the reaction says it writes makes it a producer of that dataset"]};

test_two_reactions_of_one_name_on_different_datasets_are_two_nodes:{[t]
    / A reaction name is unique per DATASET, not globally, so the node's
    / identity has to carry both or two `rebuild`s collapse into one node
    / and the graph quietly loses an edge.
    .qdag.reset[];
    .qreact.on_writing[`a;`rebuild;`out_a;{[ds;f;t] 1}];
    .qreact.on_writing[`b;`rebuild;`out_b;{[ds;f;t] 1}];
    .qdag.adopt_reactions[];
    .qunit.assertEquals[asc exec job from .qdag.registry[] where kind=`reaction;
        asc .qdag.reaction_job'[`a`b;`rebuild`rebuild];
        "the dataset is part of a reaction node's identity"]};

/ THE POINT OF PUTTING REACTIONS IN THE GRAPH. Without this a reactive cycle
/ survives until max_depth stops it at RUNTIME, after it has half-run; with
/ it, the wiring is refused when the graph is built, and the message names
/ the reactions rather than leaving them to be traced by hand.
test_a_reactive_cycle_is_refused_by_the_graph:{[t]
    .qdag.reset[];
    .qreact.on_writing[`a;`to_b;`b;{[ds;f;t] 1}];
    .qreact.on_writing[`b;`to_a;`a;{[ds;f;t] 1}];
    .qdag.adopt_reactions[];
    err:@[{.qdag.topological[]; ""};::;{x}];
    .qunit.assertTrue[(err like "*cycle*") and err like "*a~to_b*";  / two likes: >1 inner * throws 'nyi
        "the cycle is refused at graph time and names the reactions in it"]};

test_a_worker_reaction_derives_its_output_rather_than_claiming_it:{[t]
    / The case that keeps dag.q's "derive, never re-declare" rule: the output
    / is read from the worker's own declaration, so the graph entry cannot
    / disagree with what the worker does.
    .qreact.on_worker[`upstream;`demo_deals_backfill;{[f;tt] `source_version`range_from`range_to!(`v1;f;tt)}];
    r:first select outputs, derived from .qreact.for_dataset `upstream;
    .qunit.assertEquals[(r`outputs;r`derived);(enlist (.qbw.def `demo_deals_backfill)`dataset;1b);
        "on_worker reads the target from .qbw rather than being told it"]};

test_an_asserted_output_is_reported_as_such:{[t]
    .qreact.on_writing[`a;`claims;`b;{[ds;f;t] 1}];
    .qreact.on_worker[`upstream;`demo_deals_backfill;{[f;tt] `source_version`range_from`range_to!(`v1;f;tt)}];
    a:.qreact.audit[]`asserted;
    .qunit.assertEquals[(.qdag.reaction_job[`a;`claims] in a;
                         .qdag.reaction_job[`upstream;`demo_deals_backfill] in a);(1b;0b);
        "a claimed edge is listed and a derived one is not, so a drawing can tell them apart"]};

test_a_worker_reaction_refuses_an_unregistered_worker:{[t]
    .qunit.assertError[{.qreact.on_worker[`a;x;{[f;tt] 1}]};`never_defined;
        "the output cannot be derived from a worker that does not exist"]};

test_a_worker_reaction_refuses_a_spec_function_of_the_wrong_arity:{[t]
    .qunit.assertError[{.qreact.on_worker[`a;`demo_deals_backfill;x]};{[f] f};
        "spec_fn is called with the published range, so it takes exactly two arguments"]};

test_on_writing_refuses_a_non_symbol_output:{[t]
    .qunit.assertError[{.qreact.on_writing[`a;`bad;x;{[ds;f;t] 1}]};"positions";
        "a string output would name no dataset the graph could match"]};

/ A reaction registered through on_worker actually RUNS the worker - the
/ wiring is not merely a graph entry.
test_a_worker_reaction_runs_that_worker:{[t]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    .testutil.reset_coverage_ledger[];
    .qbfstate.release_lock `demo_deals_backfill;
    .qbfstate.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qfeed.demo_deals.fixture[];
    .qreact.on_worker[`upstream;`demo_deals_backfill;
        {[f;tt] `source_version`range_from`range_to!(`rx_worker;f;tt)}];
    .qreact.notify[`upstream;.rxtest.d 1;.rxtest.d 4];
    .qunit.assertEquals[(count value `demo_deals;exec first outcome from .qreact.history);(3;`ok);
        "publishing upstream ran the downstream worker over the published range"]};

/ --- the real worker path -------------------------------------------------

/ THE POINT OF THE FILE. A real worker run fires the event, once per window,
/ with the window it actually published - not a timer noticing later.
test_a_worker_run_fires_the_event_for_every_window:{[t]
    .testutil.reset_coverage_ledger[];
    .qbfstate.release_lock `demo_deals_backfill;
    .qbfstate.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qfeed.demo_deals.fixture[];
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
    `demo_deals set 0#.qfeed.demo_deals.fixture[];
    .qreact.on[`demo_deals;`bad;{[ds;f;t] '"downstream is broken"}];
    .qwrk.demo_deals_backfill.init[`source_version`range_from`range_to!(`rx3;.rxtest.d 1;.rxtest.d 4)];
    r:.qwrk.demo_deals_backfill.run[];
    .qwrk.demo_deals_backfill.cleanup[];
    .qunit.assertEquals[(r`state;r`windows_failed;.qmatz.is_covered[`demo_deals;`;`rx3;.z.p;.rxtest.d 1;.rxtest.d 4]);
        (`completed;0;1b);
        "the upstream run completes and its coverage stands, whatever the downstream did"]};

\d .
