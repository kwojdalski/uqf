/ test_dag.q - the job graph: inputs, outputs, ordering and rendering
/ (.dagtest).
/ .
/ setUp resets the registry before EVERY test, because .qdag.jobs is global
/ and a test that registered a cycle would otherwise leave every later test
/ ordering an unorderable graph. test_source_contract's setUp once wiped the
/ whole source registry and broke 21 tests in other files; the difference
/ here is that .qdag.jobs belongs to no other suite, so resetting it is
/ contained. The adoption tests rebuild from the real registries and then
/ reset like everything else.

\d .dagtest

setUp_empty_graph:{[] .qdag.reset[];}

/ A small three-job chain used by most tests:
/   feed -> quotes -> cross -> cross_rates -> report
/ with report also reading external_deals, which nothing produces.
chain:{[]
    .qdag.register[`feed;`kind`inputs`outputs!(`stream;`$();`quotes)];
    .qdag.register[`cross;`kind`inputs`outputs!(`stream;`quotes;`cross_rates)];
    .qdag.register[`report;
        `kind`inputs`outputs!(`bounded;`cross_rates`external_deals;`report_tbl)];
    ()}

/ --- registration -------------------------------------------------------

test_a_registered_job_reports_its_spec:{[t]
    chain[];
    .qunit.assertEquals[(.qdag.declaration[`cross])`inputs;enlist `quotes;
        "a job's declared inputs come back as given"]};

test_an_atom_is_normalised_to_a_vector:{[t]
    / The trap this repository keeps hitting: a single symbol is an ATOM, so
    / a consumer doing `first x` or `count x` on it gets 1 and the symbol
    / itself rather than a one-element list. Normalising once at
    / registration means no consumer has to remember - the same fix
    / .qsrc.register applies to row_key.
    .qdag.register[`solo;`kind`inputs`outputs!(`stream;`one_table;`another)];
    d:.qdag.declaration `solo;
    .qunit.assertEquals[(type d`inputs;count d`inputs);(11h;1);
        "a single input symbol is stored as a one-element symbol vector"]};

test_an_empty_input_list_survives:{[t]
    .qdag.register[`root;`kind`inputs`outputs!(`continuous;`$();`some_tbl)];
    .qunit.assertEquals[count (.qdag.declaration[`root])`inputs;0;
        "a job with no inputs is a root, not an error"]};

test_a_missing_spec_key_is_refused:{[t]
    .qunit.assertError[{.qdag.register[`broken;x]};
        `kind`inputs!(`stream;`a);
        "a spec without outputs is refused at registration"]};

test_an_unknown_kind_is_refused:{[t]
    / Closed vocabulary: `streaming` for `stream` would otherwise create a
    / silent new category every consumer has to learn about.
    .qunit.assertError[{.qdag.register[`broken;x]};
        `kind`inputs`outputs!(`streaming;`a;`b);
        "a kind outside the declared set is refused"]};

test_registering_twice_replaces:{[t]
    .qdag.register[`j;`kind`inputs`outputs!(`stream;`a;`b)];
    .qdag.register[`j;`kind`inputs`outputs!(`stream;`c;`d)];
    .qunit.assertEquals[(.qdag.declaration[`j])`inputs;enlist `c;
        "reloading a file replaces its registration rather than failing"]};

test_an_unregistered_job_is_refused:{[t]
    .qunit.assertError[{.qdag.declaration x};`no_such_job;
        "asking for a job nothing registered names it rather than returning a null"]};

/ --- the graph ----------------------------------------------------------

test_producers_and_consumers_of_a_table:{[t]
    chain[];
    .qunit.assertEquals[(.qdag.producers[`quotes];.qdag.consumers[`quotes]);
        (enlist `feed;enlist `cross);
        "a table knows which job writes it and which reads it"]};

test_a_table_nobody_writes_has_no_producer:{[t]
    chain[];
    .qunit.assertEquals[count .qdag.producers[`external_deals];0;
        "an external input has no producer, which is not an error"]};

test_edges_are_derived_not_declared:{[t]
    chain[];
    e:.qdag.edges[];
    real:select upstream, tbl, downstream from e where not null upstream;
    .qunit.assertEquals[count real;2;
        "two producer-to-consumer edges fall out of three jobs' declarations"]};

test_an_external_input_still_gets_an_edge:{[t]
    / With a null upstream, so a drawing shows where data ENTERS rather than
    / silently omitting it - an omitted row would make `report` look like a
    / root that reads nothing.
    chain[];
    e:.qdag.edges[];
    ext:select from e where null upstream;
    .qunit.assertEquals[(count ext;first ext`tbl);(1;`external_deals);
        "an input nothing produces appears as an edge with no upstream"]};

test_external_inputs_and_sinks:{[t]
    chain[];
    .qunit.assertEquals[(.qdag.external_inputs[];.qdag.sinks[]);
        (enlist `external_deals;enlist `report_tbl);
        "where data enters and where it comes to rest"]};

/ --- ordering -----------------------------------------------------------

test_topological_order_respects_dependencies:{[t]
    chain[];
    .qunit.assertEquals[.qdag.topological[];`feed`cross`report;
        "every job is ordered after everything it reads from"]};

test_layers_group_jobs_that_can_run_together:{[t]
    / Two independent feeds must land in ONE layer, not two - the parallelism
    / is the question a DAG is usually asked, and an order alone hides it.
    .qdag.register[`feed_a;`kind`inputs`outputs!(`stream;`$();`ta)];
    .qdag.register[`feed_b;`kind`inputs`outputs!(`stream;`$();`tb)];
    .qdag.register[`join;`kind`inputs`outputs!(`stream;`ta`tb;`tc)];
    l:.qdag.layers[];
    .qunit.assertEquals[(count l;count first l;count last l);(2;2;1);
        "two independent roots share a layer, and their consumer follows"]};

test_a_cycle_is_refused_and_names_the_jobs:{[t]
    / A DAG generator that returned SOME order for a cyclic graph would be
    / worse than one that refused: the order would look runnable.
    .qdag.register[`a;`kind`inputs`outputs!(`stream;`tb;`ta)];
    .qdag.register[`b;`kind`inputs`outputs!(`stream;`ta;`tb)];
    .qunit.assertError[{[u] .qdag.topological[]};::;
        "a cycle is refused rather than silently ordered"]};

test_a_lone_job_orders_fine:{[t]
    .qdag.register[`only;`kind`inputs`outputs!(`bounded;`$();`out)];
    .qunit.assertEquals[.qdag.topological[];enlist `only;
        "a graph with one job and no edges still orders"]};

test_an_empty_graph_orders_to_nothing:{[t]
    .qunit.assertEquals[count .qdag.topological[];0;
        "no jobs means no order, rather than a throw"]};

/ --- rendering ----------------------------------------------------------

test_mermaid_names_each_edge_with_its_table:{[t]
    chain[];
    m:.qdag.mermaid[];
    .qunit.assertTrue[m like "*feed -->|quotes| cross*";
        "the diagram labels each edge with the table that flows along it"]};

test_mermaid_marks_an_external_input:{[t]
    chain[];
    .qunit.assertTrue[(.qdag.mermaid[]) like "*ext_external_deals*";
        "data entering the system is drawn as its own node"]};

test_json_carries_the_order_and_the_edges:{[t]
    chain[];
    j:.qdag.to_json[];
    .qunit.assertTrue[(j like "*\"order\"*") and j like "*\"external_inputs\"*";
        "a viz tool gets the order and the entry points without parsing mermaid"]};

/ --- adoption -----------------------------------------------------------

test_workers_are_adopted_from_their_own_declarations:{[t]
    / Derive, never re-declare: a worker names a source, the source declares
    / its remote table and its target, so the worker restates nothing. A
    / worker that declared its own inputs could disagree with the source it
    / actually reads.
    adopted:.qdag.adopt_workers[];
    .qunit.assertTrue[0<count adopted;
        "the shipped bounded workers register themselves into the graph"]};

test_an_adopted_worker_reads_its_sources_table_and_writes_its_target:{[t]
    .qdag.adopt_workers[];
    w:first key .qbw.worker_cfg;
    d:.qdag.declaration w;
    cfg:.qbw.worker_cfg w;
    src:.qsrc.declaration cfg`source;
    .qunit.assertEquals[(d`inputs;d`outputs);
        ((),.qdag.external_ref[cfg`source;src`table];(),src`target);
        "an adopted worker reads its source's table (source-qualified) and writes its target"]};

/ --- the real graph ------------------------------------------------------

test_the_real_graph_is_acyclic:{[t]
    / The test that found the bug. Both shipped sources declare `table` and
    / `target` as the SAME symbol - demo_deals reads a remote `demo_deals`
    / and writes a local `demo_deals` - so keyed on the bare name each worker
    / consumed exactly what it produced, and adopt_all[] reported a cycle
    / among both workers. Correctly, given what it had been told.
    / .
    / A graph nobody has ordered is a graph whose model has not been tested.
    r:.qdag.adopt_all[];
    .qunit.assertTrue[0<count .qdag.topological[];
        "the whole shipped job graph orders, so it is genuinely a DAG"]};

test_the_real_graph_has_every_declaring_registry_in_it:{[t]
    r:.qdag.adopt_all[];
    .qunit.assertTrue[(0<count r`workers) and 0<count r`pipelines;
        "bounded workers and the generated streaming processes both land in one graph"]};

test_a_workers_remote_table_is_a_different_node_from_its_target:{[t]
    / The fix, asserted directly rather than only via the acyclic test - so
    / a future change that reverted the qualification fails HERE, naming the
    / reason, rather than failing as a mysterious cycle.
    .qdag.adopt_workers[];
    d:.qdag.declaration `demo_deals_backfill;
    .qunit.assertTrue[not any (d`inputs) in d`outputs;
        "a remote source table and a local target with the same name are distinct nodes"]};

test_external_ref_keeps_both_halves:{[t]
    / Parseable on purpose: a viz tool should be able to split a node back
    / into source and table rather than treating it as opaque.
    / `event_tape@demo_deals is NOT a symbol literal: @ is q's APPLY
    / operator, so that parses as `event_tape applied to demo_deals and
    / errors with a bare backtick. The cast form is the only way to write it.
    .qunit.assertEquals[.qdag.external_ref[`demo_deals;`event_tape];`$"event_tape@demo_deals";
        "an external node names the table and the source it lives on"]};

test_a_mermaid_id_contains_only_safe_characters:{[t]
    / The first safe_id replaced dots and spaces only, then met an `@` from
    / external_ref and emitted an id mermaid cannot parse - a diagram that
    / silently fails to render. An allow-list cannot be outgrown that way.
    .qdag.adopt_all[];
    / Check the generated id directly rather than pattern-matching the whole
    / diagram: safe_id is the thing under test, and a `like` over the joined
    / output was both fragile and, in its first form, an error.
    bad:(.qdag.safe_id `$"event_tape@demo_deals") where not
        (.qdag.safe_id `$"event_tape@demo_deals") in .qdag.id_chars;
    .qunit.assertEquals[count bad;0;
        "every character of a node id is one mermaid accepts"]};

test_adopted_feeders_are_roots:{[t]
    .qdag.adopt_feeders[];
    fs:key .qcont.feeds;
    if[0=count fs; :.qunit.assertTrue[1b;"no feeders registered in this suite"]];
    .qunit.assertEquals[count (.qdag.declaration first fs)`inputs;0;
        "a continuous feeder tails a live feed, so it declares no inputs"]};

\d .
