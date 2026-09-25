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
    .qunit.assertEquals[(.qdag.def[`cross])`inputs;enlist `quotes;
        "a job's declared inputs come back as given"]};

test_an_atom_is_normalised_to_a_vector:{[t]
    / The trap this repository keeps hitting: a single symbol is an ATOM, so
    / a consumer doing `first x` or `count x` on it gets 1 and the symbol
    / itself rather than a one-element list. Normalising once at
    / registration means no consumer has to remember - the same fix
    / .qsrc.define applies to row_key.
    .qdag.register[`solo;`kind`inputs`outputs!(`stream;`one_table;`another)];
    d:.qdag.def `solo;
    .qunit.assertEquals[(type d`inputs;count d`inputs);(11h;1);
        "a single input symbol is stored as a one-element symbol vector"]};

test_an_empty_input_list_survives:{[t]
    .qdag.register[`root;`kind`inputs`outputs!(`continuous;`$();`some_tbl)];
    .qunit.assertEquals[count (.qdag.def[`root])`inputs;0;
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
    .qunit.assertEquals[(.qdag.def[`j])`inputs;enlist `c;
        "reloading a file replaces its registration rather than failing"]};

test_an_unregistered_job_is_refused:{[t]
    .qunit.assertError[{.qdag.def x};`no_such_job;
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

test_d2_names_each_edge_with_its_table:{[t]
    chain[];
    m:.qdag.d2[];
    .qunit.assertTrue[m like "*feed -> cross: quotes*";
        "the diagram labels each edge with the table that flows along it"]};

test_d2_marks_an_external_input:{[t]
    chain[];
    .qunit.assertTrue[(.qdag.d2[]) like "*ext_external_deals*";
        "data entering the system is drawn as its own node"]};

test_d2_declares_every_external_node_before_using_it:{[t]
    / d2 takes a node's label from a declaration, not from inside an edge, so
    / an external input referenced only by an edge would render with its
    / mangled id ("ext_event_tape_demo_deals") as its visible label. The
    / mermaid version needed no such line, which is why this test is new
    / rather than renamed.
    chain[];
    lines:"\n" vs .qdag.d2[];
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

test_json_carries_the_order_and_the_edges:{[t]
    chain[];
    j:.qdag.to_json[];
    .qunit.assertTrue[(j like "*\"order\"*") and j like "*\"external_inputs\"*";
        "a viz tool gets the order and the entry points without parsing d2"]};

/ --- adoption -----------------------------------------------------------

test_workers_are_adopted_from_their_own_declarations:{[t]
    / Derive, never re-declare: a worker names a source, the source declares
    / its remote table and its target, so the worker restates nothing. A
    / worker that declared its own inputs could disagree with the source it
    / actually reads.
    adopted:.qdag.adopt_workers[];
    .qunit.assertTrue[0<count adopted;
        "the shipped bounded workers register themselves into the graph"]};

/ EVERY adopted worker, not `first key .qbw.worker_cfg`.
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
    .qdag.adopt_workers[];
    workers:key .qbw.worker_cfg;
    .qunit.assertTrue[0<count workers;"there are adopted workers to check"];
    {[w]
        cfg:.qbw.worker_cfg w;
        src:.qsrc.def cfg`source;
        .qunit.assertTrue[all `table_name`target in key src;
            "source ",string[cfg`source]," declares table_name and target - if this",
                " fails, a rename reached the contract and not this test"];
        d:.qdag.def w;
        .qunit.assertEquals[(d`inputs;d`outputs);
            ((),.qdag.external_ref[cfg`source;src`table_name];(),src`target);
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
    d:.qdag.def `demo_deals_backfill;
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

test_a_d2_id_contains_only_safe_characters:{[t]
    / The first safe_id replaced dots and spaces only, then met an `@` from
    / external_ref and emitted an id the renderer cannot parse - a diagram
    / that silently fails to render. An allow-list cannot be outgrown that
    / way. Under d2 a dot is worse still: `a.b` is valid there, and means `b`
    / nested inside `a`, so an unsanitised dot would draw a DIFFERENT graph
    / rather than refusing to draw.
    .qdag.adopt_all[];
    / Check the generated id directly rather than pattern-matching the whole
    / diagram: safe_id is the thing under test, and a `like` over the joined
    / output was both fragile and, in its first form, an error.
    bad:(.qdag.safe_id `$"event_tape@demo_deals") where not
        (.qdag.safe_id `$"event_tape@demo_deals") in .qdag.id_chars;
    .qunit.assertEquals[count bad;0;
        "every character of a node id is one d2 accepts"]};

test_adopted_feeders_are_roots:{[t]
    .qdag.adopt_feeders[];
    fs:key .qcont.feeds;
    if[0=count fs; :.qunit.assertTrue[1b;"no feeders registered in this suite"]];
    .qunit.assertEquals[count (.qdag.def first fs)`inputs;0;
        "a continuous feeder tails a live feed, so it declares no inputs"]};

\d .
