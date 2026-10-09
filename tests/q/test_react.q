// test_react.q - tests for src/etl/core/react.q (.qetl.reaction), the publication
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

/ The datasets these tests react to (`a, `b, `upstream, ...) are invented,
/ and .qetl.reaction.register refuses one no bounded worker fills (#531). So
/ `fillable` is stubbed per test to add them to the real list, and restored
/ after - a stub here rather than a test-mode switch in react.q, which would
/ be a way to turn the refusal off in production.
real_fillable:.qetl.reaction.fillable
invented:`a`b`c`upstream`invented_dataset
setUp_fillable:{[] .qetl.reaction.fillable:{[] .rxtest.real_fillable[],.rxtest.invented};}
tearDown_fillable:{[] .qetl.reaction.fillable:.rxtest.real_fillable;}

setUp_fresh:{[]
    .qetl.reaction.reset[];
    `.rxtest.fired set ();
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ A handler that records it ran. Built by name so several reactions can be
/ told apart in `fired`.
recorder:{[nm] {[nm;ds;f;t] `.rxtest.fired set .rxtest.fired,enlist (nm;ds;f;t)}[nm]}

/ --- registering ----------------------------------------------------------

test_a_reaction_registers_and_is_listed:{[t]
    .qetl.reaction.on[`a;`r1;.rxtest.recorder`r1];
    .qunit.assertEquals[exec name from .qetl.reaction.for_dataset `a;enlist `r1;
        "a registered reaction is listed for its dataset"]};

/ Re-registering REPLACES. The first version of `on` compared the parameter
/ to the column of the same name - inside a where-clause both sides resolve
/ to the COLUMN, so the filter matched nothing and appended a second handler.
/ Measured, not theorised: the smoke run showed r1 twice.
test_reregistering_replaces_rather_than_adding:{[t]
    .qetl.reaction.on[`a;`r1;.rxtest.recorder`first];
    .qetl.reaction.on[`a;`r1;.rxtest.recorder`second];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[(count .qetl.reaction.for_dataset `a;.rxtest.fired[;0]);(1;enlist `second);
        "the second registration replaces the first, and only it runs"]};

test_a_handler_of_the_wrong_arity_is_refused_at_registration:{[t]
    / Not at the first publication, which may be hours later and would be
    / attributed to the upstream job rather than to this wiring.
    .qunit.assertError[{.qetl.reaction.on[`a;`bad;x]};{[ds;f] ds};
        "a handler must take (dataset;range_from;range_to)"]};

/ #531: a dataset no bounded worker fills would register and never fire -
/ refused at registration, by every way to register, not only by the scaffold.
test_a_dataset_no_bounded_worker_fills_is_refused_by_on:{[t]
    .qunit.assertThrows[{.qetl.reaction.on[`quote;`r1;x]};.rxtest.recorder`r1;
        "on: r1 watches quote, which no bounded worker fills*";
        "quote is published by streaming jobs only, so a reaction on it would never run"]};

test_a_dataset_no_bounded_worker_fills_is_refused_by_on_writing:{[t]
    .qunit.assertThrows[{.qetl.reaction.on_writing[`nosuchdataset;`r1;`out;x]};.rxtest.recorder`r1;
        "*no bounded worker fills*";"on_writing is held to the same rule as on"]};

test_a_dataset_no_bounded_worker_fills_is_refused_by_on_worker:{[t]
    .qunit.assertThrows[
        {.qetl.reaction.on_worker[`quote;`demo_deals_backfill;x]};
        {[f;tt] `source_version`range_from`range_to!(`v1;f;tt)};
        "*no bounded worker fills*";"on_worker is held to the same rule as on"]};

test_a_refused_reaction_is_not_registered:{[t]
    @[{.qetl.reaction.on[`quote;`r1;x]};.rxtest.recorder`r1;{[e] ::}];
    .qunit.assertEquals[count .qetl.reaction.for_dataset `quote;0;"the refusal stores nothing"]};

test_fillable_is_what_the_bounded_workers_fill:{[t]
    real:.rxtest.real_fillable[];
    .qunit.assertEquals[(`demo_deals in real;`quote in real);(1b;0b);
        "demo_deals_backfill fills demo_deals; no worker fills quote"]};

/ #541: a reaction reads what was published through `published`, not the
/ dataset by name, because under .qetl.io.hdb there is no table to name.
test_published_hands_a_handler_the_rows_of_its_notification:{[t]
    `.rxtest.seen set ();
    .qetl.reaction.on[`a;`reader;{[ds;f;tt] `.rxtest.seen set .qetl.reaction.published[]}];
    rows:([] x:1 2 3);
    .qetl.reaction.notify_rows[`a;.rxtest.d[1];.rxtest.d[2];rows];
    .qunit.assertEquals[.rxtest.seen;rows;"the handler read exactly the rows the notification carried"]};

test_published_refuses_outside_a_reaction:{[t]
    .qunit.assertThrows[{.qetl.reaction.published[]};::;
        "published: only a reaction's handler*";"there is no publication to read outside a dispatch"]};

test_published_refuses_a_notification_that_carried_no_rows:{[t]
    / A bare notify has no rows; the handler's refusal is recorded, not an empty table.
    .qetl.reaction.on[`a;`reader;{[ds;f;tt] .qetl.reaction.published[]}];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertTrue[(exec last detail from .qetl.reaction.history) like "published: this notification carried no rows*";
        "a handler asking for rows a bare notify never sent is refused, naming why"]};

test_each_cascaded_notification_keeps_its_own_rows:{[t]
    `.rxtest.seen set ();
    .qetl.reaction.on[`a;`to_b;{[ds;f;tt] .qetl.reaction.notify_rows[`b;f;tt;([] y:10 20)]}];
    .qetl.reaction.on[`b;`reader;{[ds;f;tt] `.rxtest.seen set .qetl.reaction.published[]}];
    .qetl.reaction.notify_rows[`a;.rxtest.d[1];.rxtest.d[2];([] x:1 2 3)];
    .qunit.assertEquals[.rxtest.seen;([] y:10 20);"a downstream reaction reads its own publication, not the upstream one"]};

test_off_stops_one_reaction_and_leaves_the_others:{[t]
    .qetl.reaction.on[`a;`keep;.rxtest.recorder`keep];
    .qetl.reaction.on[`a;`drop;.rxtest.recorder`drop];
    .qetl.reaction.off[`a;`drop];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[.rxtest.fired[;0];enlist `keep;"only the remaining reaction runs"]};

/ --- notifying ------------------------------------------------------------

test_a_notification_carries_the_range:{[t]
    / The whole reason this is an event rather than a poll: the handler is
    / told WHAT changed, so it can recompute that range instead of diffing a
    / ledger to find it.
    .qetl.reaction.on[`a;`r1;.rxtest.recorder`r1];
    .qetl.reaction.notify[`a;.rxtest.d[3];.rxtest.d[4]];
    .qunit.assertEquals[first .rxtest.fired;(`r1;`a;.rxtest.d[3];.rxtest.d[4]);
        "the handler receives the dataset and the published range"]};

test_a_dataset_with_no_reaction_is_a_no_op:{[t]
    .qunit.assertEquals[.qetl.reaction.notify[`nobody_cares;.rxtest.d[1];.rxtest.d[2]];0;
        "publishing a dataset nothing reads runs nothing and does not throw"]};

test_every_reaction_on_a_dataset_runs:{[t]
    .qetl.reaction.on[`a;`one;.rxtest.recorder`one];
    .qetl.reaction.on[`a;`two;.rxtest.recorder`two];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[asc .rxtest.fired[;0];`one`two;"two consumers of one dataset both hear about it"]};

/ --- the three properties -------------------------------------------------

/ A -> B: the reaction on `a` publishes `b`, which has its own reaction.
/ Both must run, and the second must run from the DRAIN rather than nested
/ inside the first - which is what keeps a chain flat.
test_a_cascade_runs_the_whole_chain:{[t]
    .qetl.reaction.on[`a;`a_to_b;{[ds;f;t] .qetl.reaction.notify_from_here[`b;f;t]}];
    .qetl.reaction.on[`b;`b_done;.rxtest.recorder`b_done];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[.rxtest.fired[;0];enlist `b_done;
        "publishing a triggers b's reaction, with a's range carried through"]};

test_a_cascade_does_not_nest:{[t]
    / The property, asserted on the QUEUE rather than on the result: while a
    / handler runs, `draining` is set, so its own notification is appended
    / and returns 0 instead of starting a second drain. Recursion would
    / return 1 here and grow the stack on a long chain.
    .qetl.reaction.on[`a;`a_to_b;{[ds;f;t]
        `.rxtest.fired set .rxtest.fired,enlist (`inner_return;.qetl.reaction.notify_from_here[`b;f;t];0N;0N)}];
    .qetl.reaction.on[`b;`b_done;.rxtest.recorder`b_done];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[(first .rxtest.fired)1;0;
        "a notification raised inside a handler is queued, not run nested"]};

test_a_cycle_terminates:{[t]
    / a -> b -> a. .qetl.dag.topological refuses a cycle at registration, but a
    / handler can publish anywhere, so the runtime needs its own guard: the
    / same (dataset;range) is dispatched at most once per drain.
    .qetl.reaction.on[`a;`to_b;{[ds;f;t] .qetl.reaction.notify_from_here[`b;f;t]}];
    .qetl.reaction.on[`b;`to_a;{[ds;f;t] .qetl.reaction.notify_from_here[`a;f;t]}];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[count select from .qetl.reaction.history where outcome=`ok;2;
        "a -> b -> a settles after each fires once rather than spinning"]};

test_the_same_range_fires_again_on_a_later_publication:{[t]
    / The cycle guard is per-drain on purpose. Publishing the same range
    / again later is a NEW event - a restatement, say - and must fire.
    .qetl.reaction.on[`a;`r1;.rxtest.recorder`r1];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[count .rxtest.fired;2;"the guard bounds one cascade, not the future"]};

test_a_runaway_chain_is_refused_at_max_depth:{[t]
    / Every publication is a new range, so the per-drain guard never bites:
    / depth is what stops this one.
    .qetl.reaction.on[`a;`deeper;{[ds;f;t] .qetl.reaction.notify_from_here[`a;f+1D;t+1D]}];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[count select from .qetl.reaction.history where outcome=`refused;1;
        "the chain stops at max_depth and says so rather than running forever"]};

test_a_failing_reaction_is_recorded_not_thrown:{[t]
    .qetl.reaction.on[`a;`bad;{[ds;f;t] '"boom"}];
    r:@[{.qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]]};::;{`threw}];
    .qunit.assertEquals[(r;exec first detail from .qetl.reaction.history where outcome=`failed);(1;"boom");
        "the failure is recorded and the notification returns normally"]};

test_a_failing_reaction_does_not_stop_the_others:{[t]
    .qetl.reaction.on[`a;`bad;{[ds;f;t] '"boom"}];
    .qetl.reaction.on[`a;`good;.rxtest.recorder`good];
    .qetl.reaction.notify[`a;.rxtest.d[1];.rxtest.d[2]];
    .qunit.assertEquals[.rxtest.fired[;0];enlist `good;
        "one broken consumer does not deprive the rest of the event"]};

/ --- the DAG wiring -------------------------------------------------------

test_dag_consumers_names_who_reads_a_dataset:{[t]
    .qetl.dag.adopt_all[];
    .qunit.assertEquals[.qetl.reaction.dag_consumers `nothing_reads_this;`$();
        "a dataset no job reads has no consumers, which is not an error"]};

test_the_audit_reports_a_reaction_no_job_declares:{[t]
    .qetl.dag.adopt_all[];
    .qetl.reaction.on[`invented_dataset;`r1;.rxtest.recorder`r1];
    .qunit.assertTrue[`invented_dataset in .qetl.reaction.audit[]`undeclared;
        "a reaction on a dataset the graph does not know is reported, not refused"]};

test_the_audit_runs_when_every_dataset_is_wired:{[t]
    / The healthy case threw 'type: (!). flip () over no unwired datasets.
    / An empty graph and one reaction is the smallest tree with nothing
    / unwired; tearDown_graph rebuilds the real graph after.
    .qetl.dag.reset[];
    .qetl.reaction.on[`demo_deals;`wired;.rxtest.recorder`wired];
    a:.qetl.reaction.audit[];
    .qunit.assertEquals[(count a`unwired;type key a`unwired);(0;11h);
        "nothing unwired is an empty symbol-keyed dict, not an error"]};

test_history_keeps_the_most_recent_outcomes:{[t]
    keep:.qetl.reaction.history_limit;
    .qetl.reaction.history_limit:3;
    .qetl.reaction.history:.qetl.reaction.empty_history[];
    {.qetl.reaction.record[`ds;`r;0;0Np;0Np;`ok;string x]} each til 5;
    .qetl.reaction.history_limit:keep;
    .qunit.assertEquals[exec detail from .qetl.reaction.history;string 2 3 4;
        "the newest three survive - not the first three, with every later outcome dropped"]};

/ Rebuild the graph after tests that reset it, and clear the test reactions
/ they registered: the job graph is process-wide, so a namespace that left it
/ empty would take the next suite's ground out from under it.
tearDown_graph:{[] .qetl.reaction.reset[]; .qetl.dag.adopt_all[];}

/ --- reactions in the job graph -------------------------------------------

test_a_plain_reaction_declares_no_output_and_is_a_terminal_node:{[t]
    / Not left OUT of the graph: a graph that omitted it would show the
    / dataset as a sink - "nothing consumes this" - which is a stronger and
    / wronger claim than "something consumes this and did not say what it
    / writes".
    .qetl.dag.reset[];
    .qetl.reaction.on[`a;`glance;{[ds;f;t] 1}];
    .qetl.dag.adopt_reactions[];
    d:.qetl.dag.def .qetl.dag.reaction_job[`a;`glance];
    .qunit.assertEquals[(d`kind;d`inputs;d`outputs);(`reaction;enlist `a;`$());
        "a plain reaction reads the dataset it watches and claims to write nothing"]};

test_a_declared_output_becomes_a_graph_edge:{[t]
    .qetl.dag.reset[];
    .qetl.reaction.on_writing[`a;`build_b;`b;{[ds;f;t] 1}];
    .qetl.dag.adopt_reactions[];
    .qunit.assertEquals[.qetl.dag.producers `b;enlist .qetl.dag.reaction_job[`a;`build_b];
        "what the reaction says it writes makes it a producer of that dataset"]};

test_two_reactions_of_one_name_on_different_datasets_are_two_nodes:{[t]
    / A reaction name is unique per DATASET, not globally, so the node's
    / identity has to carry both or two `rebuild`s collapse into one node
    / and the graph quietly loses an edge.
    .qetl.dag.reset[];
    .qetl.reaction.on_writing[`a;`rebuild;`out_a;{[ds;f;t] 1}];
    .qetl.reaction.on_writing[`b;`rebuild;`out_b;{[ds;f;t] 1}];
    .qetl.dag.adopt_reactions[];
    .qunit.assertEquals[asc exec job from .qetl.dag.registry[] where kind=`reaction;
        asc .qetl.dag.reaction_job'[`a`b;`rebuild`rebuild];
        "the dataset is part of a reaction node's identity"]};

/ THE POINT OF PUTTING REACTIONS IN THE GRAPH. Without this a reactive cycle
/ survives until max_depth stops it at RUNTIME, after it has half-run; with
/ it, the wiring is refused when the graph is built, and the message names
/ the reactions rather than leaving them to be traced by hand.
test_a_reactive_cycle_is_refused_by_the_graph:{[t]
    .qetl.dag.reset[];
    .qetl.reaction.on_writing[`a;`to_b;`b;{[ds;f;t] 1}];
    .qetl.reaction.on_writing[`b;`to_a;`a;{[ds;f;t] 1}];
    .qetl.dag.adopt_reactions[];
    err:@[{.qetl.dag.topological[]; ""};::;{x}];
    .qunit.assertTrue[(err like "*cycle*") and err like "*a~to_b*";  / two likes: >1 inner * throws 'nyi
        "the cycle is refused at graph time and names the reactions in it"]};

test_a_worker_reaction_derives_its_output_rather_than_claiming_it:{[t]
    / The case that keeps dag.q's "derive, never re-declare" rule: the output
    / is read from the worker's own declaration, so the graph entry cannot
    / disagree with what the worker does.
    .qetl.reaction.on_worker[`upstream;`demo_deals_backfill;{[f;tt] `source_version`range_from`range_to!(`v1;f;tt)}];
    r:first select outputs, derived from .qetl.reaction.for_dataset `upstream;
    .qunit.assertEquals[(r`outputs;r`derived);(enlist (.qetl.job.bounded.def `demo_deals_backfill)`dataset;1b);
        "on_worker reads the target from .qetl.job.bounded rather than being told it"]};

test_an_asserted_output_is_reported_as_such:{[t]
    .qetl.reaction.on_writing[`a;`claims;`b;{[ds;f;t] 1}];
    .qetl.reaction.on_worker[`upstream;`demo_deals_backfill;{[f;tt] `source_version`range_from`range_to!(`v1;f;tt)}];
    a:.qetl.reaction.audit[]`asserted;
    .qunit.assertEquals[(.qetl.dag.reaction_job[`a;`claims] in a;
                         .qetl.dag.reaction_job[`upstream;`demo_deals_backfill] in a);(1b;0b);
        "a claimed edge is listed and a derived one is not, so a drawing can tell them apart"]};

test_a_worker_reaction_refuses_an_unregistered_worker:{[t]
    .qunit.assertError[{.qetl.reaction.on_worker[`a;x;{[f;tt] 1}]};`never_defined;
        "the output cannot be derived from a worker that does not exist"]};

test_a_worker_reaction_refuses_a_spec_function_of_the_wrong_arity:{[t]
    .qunit.assertError[{.qetl.reaction.on_worker[`a;`demo_deals_backfill;x]};{[f] f};
        "spec_fn is called with the published range, so it takes exactly two arguments"]};

test_on_writing_refuses_a_non_symbol_output:{[t]
    .qunit.assertError[{.qetl.reaction.on_writing[`a;`bad;x;{[ds;f;t] 1}]};"positions";
        "a string output would name no dataset the graph could match"]};

/ A reaction registered through on_worker actually RUNS the worker - the
/ wiring is not merely a graph entry.
test_a_worker_reaction_runs_that_worker:{[t]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    .testutil.reset_coverage_ledger[];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    .qetl.reaction.on_worker[`upstream;`demo_deals_backfill;
        {[f;tt] `source_version`range_from`range_to!(`rx_worker;f;tt)}];
    .qetl.reaction.notify[`upstream;.rxtest.d[1];.rxtest.d[4]];
    .qunit.assertEquals[(count value `demo_deals;exec first outcome from .qetl.reaction.history);(3;`ok);
        "publishing upstream ran the downstream worker over the published range"]};

/ --- the real worker path -------------------------------------------------

/ THE POINT OF THE FILE. A real worker run fires the event, once per window,
/ with the window it actually published - not a timer noticing later.
test_a_worker_run_fires_the_event_for_every_window:{[t]
    .testutil.reset_coverage_ledger[];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    .qetl.reaction.on[`demo_deals;`watcher;.rxtest.recorder`watcher];
    .qpipe.job.demo_deals_backfill.init[`source_version`range_from`range_to!(`rx1;.rxtest.d[1];.rxtest.d[4])];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qunit.assertEquals[(count .rxtest.fired;.rxtest.fired[0;2];.rxtest.fired[0;3]);
        (3;.rxtest.d[1];.rxtest.d[2]);
        "three windows published, three events, the first carrying the first window's own range"]};

test_a_dry_run_publishes_nothing_and_fires_nothing:{[t]
    / A rehearsal must not trigger real downstream work.
    .testutil.reset_coverage_ledger[];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    .qetl.reaction.on[`demo_deals;`watcher;.rxtest.recorder`watcher];
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[`source_version`range_from`range_to!(`rx2;.rxtest.d[1];.rxtest.d[4])];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count .rxtest.fired;0;"a dry run publishes nothing, so it announces nothing"]};

/ A reaction that throws must not turn a successful materialisation into a
/ failed one: the rows are already written and the coverage already staged.
/ But the run is not `completed either (#632): a dataset derived from it is
/ stale, and green would tell Airflow and the browser otherwise. It ends
/ `partial, with the reactions it owes counted.
test_a_failing_reaction_leaves_the_window_published_and_covered:{[t]
    .rxtest.fresh_deals[];
    r:.rxtest.run_deals[`rx3;{[ds;f;t] '"downstream is broken"}];
    .qunit.assertEquals[(r`state;r`windows_failed;r`reactions_owed;.qetl.coverage.is_covered[`demo_deals;`;`rx3;.z.p;.rxtest.d[1];.rxtest.d[4]]);
        (`partial;0;3;1b);
        "the coverage stands, and the run is partial: three windows' reactions are owed"]};

/ One demo_deals_backfill run over d1..d4 (three 1D windows), with one
/ reaction, `bad`, registered as `handler` - re-registering replaces it.
run_deals:{[version;handler]
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    .qetl.reaction.on[`demo_deals;`bad;handler];
    .qpipe.job.demo_deals_backfill.init[`source_version`range_from`range_to!(version;.rxtest.d[1];.rxtest.d[4])];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    r}

fresh_deals:{[] .testutil.reset_coverage_ledger[]; `demo_deals set 0#.qpipe.source.demo_deals.fixture[];}

/ What an orchestrator reads: the status file. `partial is `failed there,
/ and the count and the error say why.
test_owed_reactions_reach_the_status_file:{[t]
    .rxtest.fresh_deals[];
    .rxtest.run_deals[`rx5;{[ds;f;t] '"downstream is broken"}];
    w:`demo_deals_backfill;
    s:.j.k first read0 hsym `$(.qetl.status.status_dir[]),"/airflow_status_",string[.qetl.job.bounded.instance w],".txt";
    .qunit.assertEquals[(s`state;s`reactions_owed);("failed";3f);"the file reads failed, and says three reactions are owed"];
    .qunit.assertTrue[(s`error) like "3 reaction(s) owed over 3 window(s) (bad)*";"the error names the reaction and the windows"]};

/ Every run re-fires what is owed first, so the retry heals it: with the
/ handler fixed, the next run finds its windows covered, replays the owed
/ reactions, and ends idle with nothing owed.
test_the_next_run_repays_what_was_owed:{[t]
    .rxtest.fresh_deals[];
    .rxtest.run_deals[`rx6;{[ds;f;t] '"downstream is broken"}];
    r:.rxtest.run_deals[`rx6;.rxtest.recorder`bad];
    .qunit.assertEquals[(r`state;r`reactions_owed;count .rxtest.fired);(`idle;0;3);
        "three owed reactions re-fired and succeeded, so the rerun is idle and clean"]};

/ An idle run is idle only when it owes nothing: one that still cannot
/ repay is partial, or a broken handler would read green from the second
/ run on.
test_an_idle_run_that_still_owes_is_partial:{[t]
    .rxtest.fresh_deals[];
    .rxtest.run_deals[`rx7;{[ds;f;t] '"downstream is broken"}];
    r:.rxtest.run_deals[`rx7;{[ds;f;t] '"still broken"}];
    .qunit.assertEquals[(r`state;r`windows_completed;r`reactions_owed);(`partial;0;3);
        "no window to run, three reactions still owed"]};

/ An unreadable reaction ledger is not "nothing owed" (#808): the guard used
/ to trap the read error into an empty table, so the second run below ended
/ `idle over a dataset nobody could say was current. Both generations are
/ damaged: durable_get falls back to `.bak`, so one bad file alone is read
/ from the other and is not this case.
test_an_unreadable_reaction_ledger_fails_the_run_rather_than_reading_idle:{[t]
    .rxtest.fresh_deals[];
    .rxtest.run_deals[`rx8;.rxtest.recorder`bad];
    path:.qetl.reaction.outcomes_path[];
    {(hsym `$x) 1: 0x00010203} each (path;path,".bak");
    r:@[.rxtest.run_deals[`rx8;];.rxtest.recorder`bad;{[e] `threw`error!(1b;e)}];
    .qetl.reaction.reset_outcomes[];
    @[hdel;hsym `$path,".bak";::];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    state:$[`state in key r; r`state; `threw];
    .qunit.assertFalse[state in `idle`completed;"the run does not read as a success"];
    w:`demo_deals_backfill;
    st:.j.k first read0 hsym `$(.qetl.status.status_dir[]),"/airflow_status_",string[.qetl.job.bounded.instance w],".txt";
    .qunit.assertEquals[st`state;"failed";"the status file reads failed"];
    .qunit.assertTrue[(st`error) like "*cannot tell which reactions demo_deals_backfill owes*";"and says the reaction ledger is why"]};

\d .
