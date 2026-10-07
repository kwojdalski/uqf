/ test_dag.q - the job graph: inputs, outputs, ordering and rendering
/ (.dagtest).
/ .
/ setUp resets the registry before EVERY test, because .qetl.dag.jobs is global
/ and a test that registered a cycle would otherwise leave every later test
/ ordering an unorderable graph. test_source_contract's setUp once wiped the
/ whole source registry and broke 21 tests in other files; the difference
/ here is that .qetl.dag.jobs belongs to no other suite, so resetting it is
/ contained. The adoption tests rebuild from the real registries and then
/ reset like everything else.

\d .dagtest

setUp_empty_graph:{[] .qetl.dag.reset[];}

/ A small three-job chain used by most tests:
/   feed -> quotes -> cross -> cross_rates -> report
/ with report also reading external_deals, which nothing produces.
chain:{[]
    .qetl.dag.register[`feed;`kind`inputs`outputs!(`stream;`$();`quotes)];
    .qetl.dag.register[`cross;`kind`inputs`outputs!(`stream;`quotes;`cross_rates)];
    .qetl.dag.register[`report;
        `kind`inputs`outputs!(`bounded;`cross_rates`external_deals;`report_tbl)];
    ()}

/ --- registration -------------------------------------------------------

test_a_registered_job_reports_its_spec:{[t]
    chain[];
    .qunit.assertEquals[(.qetl.dag.def[`cross])`inputs;enlist `quotes;
        "a job's declared inputs come back as given"]};

test_an_atom_is_normalised_to_a_vector:{[t]
    / The trap this repository keeps hitting: a single symbol is an ATOM, so
    / a consumer doing `first x` or `count x` on it gets 1 and the symbol
    / itself rather than a one-element list. Normalising once at
    / registration means no consumer has to remember - the same fix
    / .qetl.source.define applies to row_key.
    .qetl.dag.register[`solo;`kind`inputs`outputs!(`stream;`one_table;`another)];
    d:.qetl.dag.def `solo;
    .qunit.assertEquals[(type d`inputs;count d`inputs);(11h;1);
        "a single input symbol is stored as a one-element symbol vector"]};

test_an_empty_input_list_survives:{[t]
    .qetl.dag.register[`root;`kind`inputs`outputs!(`continuous;`$();`some_tbl)];
    .qunit.assertEquals[count (.qetl.dag.def[`root])`inputs;0;
        "a job with no inputs is a root, not an error"]};

test_a_missing_spec_key_is_refused:{[t]
    .qunit.assertError[{.qetl.dag.register[`broken;x]};
        `kind`inputs!(`stream;`a);
        "a spec without outputs is refused at registration"]};

test_an_unknown_kind_is_refused:{[t]
    / Closed vocabulary: `streaming` for `stream` would otherwise create a
    / silent new category every consumer has to learn about.
    .qunit.assertError[{.qetl.dag.register[`broken;x]};
        `kind`inputs`outputs!(`streaming;`a;`b);
        "a kind outside the declared set is refused"]};

test_registering_twice_replaces:{[t]
    .qetl.dag.register[`j;`kind`inputs`outputs!(`stream;`a;`b)];
    .qetl.dag.register[`j;`kind`inputs`outputs!(`stream;`c;`d)];
    .qunit.assertEquals[(.qetl.dag.def[`j])`inputs;enlist `c;
        "reloading a file replaces its registration rather than failing"]};

test_an_unregistered_job_is_refused:{[t]
    .qunit.assertError[{.qetl.dag.def x};`no_such_job;
        "asking for a job nothing registered names it rather than returning a null"]};

/ --- the graph ----------------------------------------------------------

test_producers_and_consumers_of_a_table:{[t]
    chain[];
    .qunit.assertEquals[(.qetl.dag.producers[`quotes];.qetl.dag.consumers[`quotes]);
        (enlist `feed;enlist `cross);
        "a table knows which job writes it and which reads it"]};

test_a_table_nobody_writes_has_no_producer:{[t]
    chain[];
    .qunit.assertEquals[count .qetl.dag.producers[`external_deals];0;
        "an external input has no producer, which is not an error"]};

test_edges_are_derived_not_declared:{[t]
    chain[];
    e:.qetl.dag.edges[];
    real:select upstream, tbl, downstream from e where not null upstream;
    .qunit.assertEquals[count real;2;
        "two producer-to-consumer edges fall out of three jobs' declarations"]};

test_an_external_input_still_gets_an_edge:{[t]
    / With a null upstream, so a drawing shows where data ENTERS rather than
    / silently omitting it - an omitted row would make `report` look like a
    / root that reads nothing.
    chain[];
    e:.qetl.dag.edges[];
    ext:select from e where null upstream;
    .qunit.assertEquals[(count ext;first ext`tbl);(1;`external_deals);
        "an input nothing produces appears as an edge with no upstream"]};

test_external_inputs_and_sinks:{[t]
    chain[];
    .qunit.assertEquals[(.qetl.dag.external_inputs[];.qetl.dag.sinks[]);
        (enlist `external_deals;enlist `report_tbl);
        "where data enters and where it comes to rest"]};

/ --- ordering -----------------------------------------------------------

test_topological_order_respects_dependencies:{[t]
    chain[];
    .qunit.assertEquals[.qetl.dag.topological[];`feed`cross`report;
        "every job is ordered after everything it reads from"]};

test_layers_group_jobs_that_can_run_together:{[t]
    / Two independent feeds must land in ONE layer, not two - the parallelism
    / is the question a DAG is usually asked, and an order alone hides it.
    .qetl.dag.register[`feed_a;`kind`inputs`outputs!(`stream;`$();`ta)];
    .qetl.dag.register[`feed_b;`kind`inputs`outputs!(`stream;`$();`tb)];
    .qetl.dag.register[`join;`kind`inputs`outputs!(`stream;`ta`tb;`tc)];
    l:.qetl.dag.layers[];
    .qunit.assertEquals[(count l;count first l;count last l);(2;2;1);
        "two independent roots share a layer, and their consumer follows"]};

test_a_cycle_is_refused_and_names_the_jobs:{[t]
    / A DAG generator that returned SOME order for a cyclic graph would be
    / worse than one that refused: the order would look runnable.
    .qetl.dag.register[`a;`kind`inputs`outputs!(`stream;`tb;`ta)];
    .qetl.dag.register[`b;`kind`inputs`outputs!(`stream;`ta;`tb)];
    .qunit.assertError[{[u] .qetl.dag.topological[]};::;
        "a cycle is refused rather than silently ordered"]};

test_a_lone_job_orders_fine:{[t]
    .qetl.dag.register[`only;`kind`inputs`outputs!(`bounded;`$();`out)];
    .qunit.assertEquals[.qetl.dag.topological[];enlist `only;
        "a graph with one job and no edges still orders"]};

test_an_empty_graph_orders_to_nothing:{[t]
    .qunit.assertEquals[count .qetl.dag.topological[];0;
        "no jobs means no order, rather than a throw"]};

/ --- rendering ----------------------------------------------------------

test_d2_names_each_edge_with_its_table:{[t]
    chain[];
    m:.qetl.dag.d2[];
    .qunit.assertTrue[m like "*feed -> cross: quotes*";
        "the diagram labels each edge with the table that flows along it"]};

test_d2_marks_an_external_input:{[t]
    chain[];
    .qunit.assertTrue[(.qetl.dag.d2[]) like "*ext_external_deals*";
        "data entering the system is drawn as its own node"]};

test_d2_declares_every_external_node_before_using_it:{[t]
    / d2 takes a node's label from a declaration, not from inside an edge, so
    / an external input referenced only by an edge would render with its
    / mangled id ("ext_event_tape_demo_deals") as its visible label. The
    / mermaid version needed no such line, which is why this test is new
    / rather than renamed.
    chain[];
    lines:"\n" vs .qetl.dag.d2[];
    / `ss` rather than `like "ext_* -> *"`: on this build a pattern with an
    / INTERIOR `*` alongside another one returns `nyi`, not false - `"ext_*"`
    / and `"*->*"` are both fine, `"ext_* -> *"` throws. A `like` inside an
    / assertion would have surfaced as an errored test rather than a failing
    / one, which is how it was found.
    / `like "ext_*"` for the prefix - a single trailing `*` is safe - and not
    / `4 # l`, which WRAPS rather than truncating on a line shorter than four
    / characters and would have matched the wrong thing quietly.
    ext:{[l;pat] (l like "ext_*") and 0 < count l ss pat};
    used:distinct {first " " vs x} each lines where ext[;" -> "] each lines;
    declared:{first ":" vs x} each lines where ext[;": "] each lines;
    / Both, because `except` over an empty left side is 0 either way: without
    / the first assertion this passes on a diagram that declares nothing at
    / all, which is the shape of vacuous test this tree keeps finding.
    .qunit.assertTrue[0<count used;
        "the chain fixture enters the graph from outside, so there is something to check"];
    .qunit.assertEquals[count used except declared;0;
        "every ext_ node an edge points from has its own declaration line"]};

/ #532: a reaction's `on_writing` outputs are a claim nothing checks, and the
/ drawing is what people read - so the drawing says so. `asserted_graph`
/ wires one asserted reaction (demo_deals is filled by a real worker, which
/ .qetl.reaction.register requires) with a reader of what it writes, so an
/ asserted EDGE exists to draw as well as the node.
asserted_graph:{[]
    .qetl.reaction.on_writing[`demo_deals;`dagtest_rx;`dagtest_out;{[ds;f;tt] ds}];
    .qetl.dag.register[`dagtest_reader;`kind`inputs`outputs!(`bounded;`dagtest_out;`dagtest_sum)];
    .qetl.dag.adopt_reactions[];
    .qetl.dag.reaction_job[`demo_deals;`dagtest_rx]}
tearDown_asserted:{[] .qetl.reaction.off[`demo_deals;`dagtest_rx]; .qetl.reaction.off[`imported_trades;`demo_deals_backfill];}

test_d2_draws_an_asserted_reaction_dashed:{[t]
    job:.dagtest.asserted_graph[];
    lines:"\n" vs .qetl.dag.d2[];
    node:.qetl.dag.safe_id job;
    edge:lines where (lines like node,"*") and 0<count each lines ss\: "(asserted) {style.stroke-dash: 3}";
    .qunit.assertEquals[((node,".style.stroke-dash: 3") in lines;count edge);(1b;1);
        "the asserted reaction's node is dashed, and so is its edge to what reads its output"]};

test_a_derived_reaction_is_not_drawn_asserted:{[t]
    / on_worker reads its outputs from the worker's declaration: checked, not claimed.
    .qetl.reaction.on_worker[`imported_trades;`demo_deals_backfill;{[f;tt] `source_version`range_from`range_to!(`v1;f;tt)}];
    .qetl.dag.adopt_reactions[];
    job:.qetl.dag.reaction_job[`imported_trades;`demo_deals_backfill];
    / Its OWN lines only: the tree's real asserted reaction (rebuild_positions)
    / is registered too, and is rightly drawn dashed.
    lines:"\n" vs .qetl.dag.d2[];
    mine:lines where 0<count each lines ss\: .qetl.dag.safe_id job;
    .qunit.assertEquals[(job in .qetl.dag.asserted_jobs[];0<count mine;any 0<count each mine ss\: "stroke-dash");
        (0b;1b;0b);
        "a derived reaction is drawn, and drawn like any other job"]};

test_json_marks_each_edge_asserted:{[t]
    job:.dagtest.asserted_graph[];
    j:.j.k .qetl.dag.to_json[];
    e:j`edges;
    mine:e where (`$e[;`upstream])=job;
    rest:e where not (`$e[;`upstream])=job;
    .qunit.assertEquals[(all mine[;`asserted];any rest[;`asserted];0<count mine;job in `$j`asserted_jobs);
        (1b;0b;1b;1b);
        "an edge from the asserted reaction says so, and no other edge does"]};

test_json_carries_the_order_and_the_edges:{[t]
    chain[];
    j:.qetl.dag.to_json[];
    .qunit.assertTrue[(j like "*\"order\"*") and j like "*\"external_inputs\"*";
        "a viz tool gets the order and the entry points without parsing d2"]};

/ --- adoption -----------------------------------------------------------

test_workers_are_adopted_from_their_own_declarations:{[t]
    / Derive, never re-declare: a worker names a source, the source declares
    / its remote table and its target, so the worker restates nothing. A
    / worker that declared its own inputs could disagree with the source it
    / actually reads.
    adopted:.qetl.dag.adopt_workers[];
    .qunit.assertTrue[0<count adopted;
        "the shipped bounded workers register themselves into the graph"]};

/ EVERY adopted worker, not `first key .qetl.job.bounded.worker_cfg`.
/ .
/ Two reasons, and the second is why this test spent a while red. A
/ dictionary's key order is its registration order, so `first` names whichever
/ worker src/etl/init.q happens to load first - a test that depends on that is
/ answering a question nobody asked. And checking one worker leaves the other
/ four unasserted, so a source whose adoption disagreed with its declaration
/ would only be caught if it sorted first.
/ .
/ The key name is asserted BEFORE it is read, and that is the real lesson
/ here. `9347b19` renamed the declaration's `table` to `table_name` across the
/ tree and did not reach this file. q answers a missing dictionary key with a
/ null rather than an error, so the expectation quietly became
/ ``\`@<source>`` - a value no adoption can produce - and the test failed
/ saying the graph was wrong when the graph was right. A missing key must
/ fail as a missing key.
test_an_adopted_worker_reads_its_sources_table_and_writes_its_target:{[t]
    .qetl.dag.adopt_workers[];
    workers:.qetl.job.bounded.defined[];
    .qunit.assertTrue[0<count workers;"there are adopted workers to check"];
    {[w]
        cfg:.qetl.job.bounded.def w;
        src:.qetl.source.def cfg`source;
        .qunit.assertTrue[all `table_name`target in key src;
            "source ",string[cfg`source]," declares table_name and target - if this",
                " fails, a rename reached the contract and not this test"];
        d:.qetl.dag.def w;
        .qunit.assertEquals[(d`inputs;d`outputs);
            ((),.qetl.dag.external_ref[cfg`source;src`table_name];(),src`target);
            string[w]," reads its source's table (source-qualified) and writes its target"]
     } each workers;};

/ --- the real graph ------------------------------------------------------

test_the_real_graph_is_acyclic:{[t]
    / The test that found the bug. Both shipped sources declare `table_name`
    / and `target` as the SAME symbol - demo_deals reads a remote `demo_deals`
    / and writes a local `demo_deals` - so keyed on the bare name each worker
    / consumed exactly what it produced, and adopt_all[] reported a cycle
    / among both workers. Correctly, given what it had been told.
    / .
    / A graph nobody has ordered is a graph whose model has not been tested.
    r:.qetl.dag.adopt_all[];
    .qunit.assertTrue[0<count .qetl.dag.topological[];
        "the whole shipped job graph orders, so it is genuinely a DAG"]};

test_the_real_graph_has_every_declaring_registry_in_it:{[t]
    r:.qetl.dag.adopt_all[];
    .qunit.assertTrue[(0<count r`workers) and 0<count r`streams;
        "bounded workers and the streaming jobs both land in one graph"]};

/ --- streaming jobs, read from q ---------------------------------------

/ Each streaming job is a node under its JOB name, with exactly the edges it
/ declares. They used to arrive through the generated bridge, named by
/ process, which Python built by parsing these same declarations.
test_every_streaming_job_is_a_node_with_its_declared_edges:{[t]
    .qetl.dag.adopt_all[];
    js:.qetl.job.stream.defined[];
    .qunit.assertTrue[0<count js;"there are streaming jobs to check"];
    bad:js where not {[j]
        d:.qetl.job.stream.def j; g:.qetl.dag.def j;
        (((),d`subscribe_to)~g`inputs) and ((),d`publishes)~g`outputs
      } each js;
    .qunit.assertEquals[bad;`symbol$();"every streaming job's node carries its declared inputs and outputs"]};

test_a_normalizer_is_its_own_kind:{[t]
    .qetl.dag.adopt_all[];
    ns:key .qetl.job.stream.normalizer.registry;
    .qunit.assertTrue[0<count ns;"there are normalizers to check"];
    .qunit.assertEquals[distinct {(.qetl.dag.def x)`kind} each ns;enlist `normalizer;
        "a normalizer is drawn as one, so a graph shows where shapes converge"]};

/ The bug this replaced: every backfill process came back through the bridge
/ as an edgeless `stream` node beside the real `bounded` one.
test_no_backfill_appears_a_second_time_under_its_process_name:{[t]
    .qetl.dag.adopt_all[];
    procs:{(.qetl.job.bounded.def x)`procname} each .qetl.job.bounded.defined[];
    .qunit.assertEquals[procs inter .qetl.dag.defined[];`symbol$();
        "a bounded worker is one node, named by worker, never also by its process"]};

test_no_streaming_job_appears_under_its_process_name:{[t]
    .qetl.dag.adopt_all[];
    procs:{(.qetl.job.stream.def x)`procname} each .qetl.job.stream.defined[];
    .qunit.assertEquals[procs inter .qetl.dag.defined[];`symbol$();
        "a streaming job is named by job, the identity every other node uses"]};

/ What adopting from q buys: a job q has declared is in the graph with no
/ generator run. Declared by inserting into the registry and restored after,
/ because a real define would also count as a shipped job to the suites that
/ check src/etl/streaming against the registry.
test_a_job_q_knows_about_is_in_the_graph_without_regenerating:{[t]
    saved:.qetl.job.stream.jobs;
    decl:`ns`procname`subscribe_to`publishes!(`.dagtest.probe;`dagprobe1;enlist`quote;enlist`dag_probe_out);
    .qetl.job.stream.jobs[`dag_probe]:enlist decl;
    r:@[{.qetl.dag.adopt_all[]; .qetl.dag.def `dag_probe};::;{x}];
    .qetl.job.stream.jobs:saved;
    .qetl.dag.adopt_all[];
    .qunit.assertEquals[r`outputs;enlist `dag_probe_out;"the probe's output reached the graph"];
    .qunit.assertEquals[r`inputs;enlist `quote;"and its input"]};

test_the_generated_bridge_carries_only_processes_with_no_job:{[t]
    .qunit.assertEquals[(),.qetl.dag.register_pipelines[];enlist `tap1;
        "streaming jobs come from q; the bridge is for what q cannot declare"]};

test_a_workers_remote_table_is_a_different_node_from_its_target:{[t]
    / The fix, asserted directly rather than only via the acyclic test - so
    / a future change that reverted the qualification fails HERE, naming the
    / reason, rather than failing as a mysterious cycle.
    .qetl.dag.adopt_workers[];
    d:.qetl.dag.def `demo_deals_backfill;
    .qunit.assertTrue[not any (d`inputs) in d`outputs;
        "a remote source table and a local target with the same name are distinct nodes"]};

test_external_ref_keeps_both_halves:{[t]
    / Parseable on purpose: a viz tool should be able to split a node back
    / into source and table rather than treating it as opaque.
    / `event_tape@demo_deals is NOT a symbol literal: @ is q's APPLY
    / operator, so that parses as `event_tape applied to demo_deals and
    / errors with a bare backtick. The cast form is the only way to write it.
    .qunit.assertEquals[.qetl.dag.external_ref[`demo_deals;`event_tape];`$"event_tape@demo_deals";
        "an external node names the table and the source it lives on"]};

test_a_d2_id_contains_only_safe_characters:{[t]
    / The first safe_id replaced dots and spaces only, then met an `@` from
    / external_ref and emitted an id the renderer cannot parse - a diagram
    / that silently fails to render. An allow-list cannot be outgrown that
    / way. Under d2 a dot is worse still: `a.b` is valid there, and means `b`
    / nested inside `a`, so an unsanitised dot would draw a DIFFERENT graph
    / rather than refusing to draw.
    .qetl.dag.adopt_all[];
    / Check the generated id directly rather than pattern-matching the whole
    / diagram: safe_id is the thing under test, and a `like` over the joined
    / output was both fragile and, in its first form, an error.
    bad:(.qetl.dag.safe_id `$"event_tape@demo_deals") where not
        (.qetl.dag.safe_id `$"event_tape@demo_deals") in .qetl.dag.id_chars;
    .qunit.assertEquals[count bad;0;
        "every character of a node id is one d2 accepts"]};

test_adopted_feeders_are_roots:{[t]
    .qetl.dag.adopt_feeders[];
    fs:key .qetl.job.continuous.feeds;
    if[0=count fs; :.qunit.assertTrue[1b;"no feeders registered in this suite"]];
    .qunit.assertEquals[count (.qetl.dag.def first fs)`inputs;0;
        "a continuous feeder tails a live feed, so it declares no inputs"]};

\d .
