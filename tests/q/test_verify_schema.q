/ test_verify_schema.q - which producers fill a dataset, and whether each one's
/ output fits the destination (.qetl.verify, src/etl/core/verify_schema.q, #1851).
/ (.qt.qetl.verify.schema).
/ .
/ Reference values throughout: the demo_deals pair is the in-tree example, and a
/ finding's every part - producer, destination, column, expected, actual - is
/ asserted by value, because a finding that names the wrong column is worse than none.

.qt.qetl.verify.schema.deals:`demo_deals

/ The rows a producer returns, as the pair's own fixture gives them.
.qt.qetl.verify.schema.rows:{[] (cols .qetl.plant.published `demo_deals)#.qpipe.source.demo_deals.fixture[]}

/ A streaming job over demo_deals that declares a different row key, registered
/ for a test and forgotten after.
.qt.qetl.verify.schema.rival:`vs_rival

.qt.qetl.verify.schema.forget:{[]
    `.qetl.job.stream.jobs set (enlist .qt.qetl.verify.schema.rival) _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set (enlist `vs_rival1) _ .qetl.job.stream.procnames;}

.qt.qetl.verify.schema.tearDown_forget:{[] .qt.qetl.verify.schema.forget[];}

.qt.qetl.verify.schema.test_both_producers_of_the_demo_deals_dataset_are_found:{[t]
    p:.qetl.verify.producers .qt.qetl.verify.schema.deals;
    .qunit.assertEquals[(p`producer;p`mode);(`demo_deals_feed`demo_deals_backfill;`stream`bounded);
        "the job graph's producers: the polling feed and the worker whose dataset it is"];
    .qunit.assertEquals[2#(),/:p`key_cols;(enlist `deal_id;enlist `deal_id);
        "each takes its row key from the source it reads, neither declaring one of its own"]};

.qt.qetl.verify.schema.test_the_grouping_is_the_job_graphs_own:{[t]
    keep:.qetl.dag.jobs;
    .qetl.dag.adopt_all[];
    graph:.qetl.dag.producers `demo_deals;
    .qetl.dag.jobs::keep;
    .qunit.assertEquals[(.qetl.verify.producers `demo_deals)`producer;graph;"no second grouping: the graph's, in its order"];
    .qunit.assertEquals[.qetl.dag.jobs~keep;1b;"and asking leaves the registry as it was"]};

.qt.qetl.verify.schema.test_a_dataset_with_only_a_reaction_and_one_worker_is_still_listed:{[t]
    .qunit.assertEquals[(.qetl.verify.producers `deal_positions)`mode;enlist `reaction;"a derived dataset's producer is the reaction"]};

.qt.qetl.verify.schema.test_a_dataset_nobody_fills_has_no_producers:{[t]
    .qunit.assertEquals[count .qetl.verify.producers `no_such_dataset;0;"nothing publishes it and no worker fills it"]};

.qt.qetl.verify.schema.test_the_destination_generates_what_the_plant_stamps:{[t]
    d:.qetl.verify.destination .qt.qetl.verify.schema.deals;
    .qunit.assertEquals[d`generated;enlist `time;"time is the plant's own, derived from the schema"];
    .qunit.assertEquals[cols d`published;`deal_id`deal_time`sym`side`notional`rate;"what a producer publishes"];
    .qunit.assertTrue[(::)~.qetl.verify.destination `no_such_dataset;"no plant table, so no destination to hold a producer to"]};

.qt.qetl.verify.schema.test_the_declared_output_of_the_backfill_fits_the_destination:{[t]
    p:.qetl.verify.producers .qt.qetl.verify.schema.deals;
    f:.qetl.verify.schema_findings[`demo_deals_backfill;`demo_deals;last p`declared;`declared];
    .qunit.assertEquals[count f;0;"the transform's declared output is the plant's published shape"]};

.qt.qetl.verify.schema.test_rows_that_fit_pass:{[t]
    .qunit.assertEquals[count .qetl.verify.schema_findings[`p;`demo_deals;.qt.qetl.verify.schema.rows[];`sample];0;
        "the fixture's rows, in the plant's order"]};

.qt.qetl.verify.schema.test_a_retyped_column_names_producer_destination_column_and_both_types:{[t]
    r:update deal_id:`float$deal_id from .qt.qetl.verify.schema.rows[];
    f:.qetl.verify.schema_findings[`demo_deals_feed;`demo_deals;r;`sample];
    .qunit.assertEquals[count f;1;"one column is wrong"];
    .qunit.assertEquals[first flip value flip f;
        (`demo_deals_feed;`demo_deals;`deal_id;`type;"long (j)";"float (f)";`sample);
        "producer, destination, column, problem, expected and actual"]};

.qt.qetl.verify.schema.test_a_missing_and_an_extra_column_are_each_named:{[t]
    r:update surprise:1b from delete rate from .qt.qetl.verify.schema.rows[];
    f:.qetl.verify.schema_findings[`p;`demo_deals;r;`sample];
    .qunit.assertEquals[(f`column;f`problem;f`expected;f`actual);
        (`rate`surprise;`missing`extra;("float (f)";"column absent");("column absent";"boolean (b)"));
        "rate is absent and surprise is not the destination's"]};

.qt.qetl.verify.schema.test_columns_in_another_order_are_found_though_the_names_agree:{[t]
    r:`rate`deal_id`deal_time`sym`side`notional#.qt.qetl.verify.schema.rows[];
    f:.qetl.verify.schema_findings[`p;`demo_deals;r;`declared];
    .qunit.assertEquals[(f`problem;f`column);(6#`order;`deal_id`deal_time`sym`side`notional`rate);
        "a positional publish would land each value in its neighbour's column"];
    .qunit.assertEquals[f[0]`expected`actual;("position 0";"position 1");"the position wanted and the position held"]};

.qt.qetl.verify.schema.test_a_nested_column_holding_atoms_is_found_by_element_type:{[t]
    ok:([] sym:`a`b; bid_prices:(1 2f;3 4f); ask_prices:(1 2f;3 4f));
    bad:([] sym:`a`b; bid_prices:(1 2f;3 4f); ask_prices:1 2f);
    .qunit.assertEquals[count .qetl.verify.schema_findings[`p;`mkt_orderbook;ok;`sample];0;"float vectors are what it declares"];
    f:.qetl.verify.schema_findings[`p;`mkt_orderbook;bad;`sample];
    .qunit.assertEquals[(f`column;f`problem;f`expected;f`actual);
        (enlist `ask_prices;enlist `nested;enlist "every row a float (f) list";enlist "row 0 holds type -9h (and 1 more rows)");
        "the column, and what its rows hold instead"]};

.qt.qetl.verify.schema.test_producers_declaring_different_keys_are_a_finding:{[t]
    .qetl.job.stream.define[.qt.qetl.verify.schema.rival;`procname`subscribe_to`outputs`period`poll!(
        `vs_rival1;`symbol$();enlist `demo_deals;0D00:00:01;
        `fetch`normalize`next_cursor`row_key!({[c] ::};{[p] p};{[p] ::};`sym))];
    p:.qetl.verify.producers .qt.qetl.verify.schema.deals;
    f:.qetl.verify.key_findings[`demo_deals;p];
    .qunit.assertEquals[(count f;first f`problem;first f`expected);(1;`key;"one row key for the dataset");
        "the keys disagree, and the finding says so"];
    .qunit.assertTrue[(first f`actual) like "*vs_rival keys on sym*";"naming the producer and its key"]};

.qt.qetl.verify.schema.test_the_pair_agrees_on_its_key:{[t]
    .qunit.assertEquals[count .qetl.verify.key_findings[`demo_deals;.qetl.verify.producers `demo_deals];0;
        "both key on deal_id, a column the destination takes"]};
