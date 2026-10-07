/ test_plant_tables.q - every plant table's schema, written once
/ (src/etl/plant_tables.q, .qetl.plant) (.planttest).
/ .
/ A table is written by one job and read by others, so its shape belongs to
/ the table: jobs take it from .qetl.plant rather than declaring a copy.

\d .planttest

test_this_trees_tables_and_the_vendored_ones_are_both_there:{[t]
    n:.qetl.plant.names[];
    .qunit.assertTrue[all `orders`executions`client_flow in n;"this tree's own tables"];
    .qunit.assertEquals[asc .qetl.plant.vendored;`packets`quote`trade;"and the vendored three, read from database.q"];
    .qunit.assertTrue[not any .qetl.plant.vendored in .qetl.plant.own[];"own excludes the vendored ones"]};

test_a_schema_is_the_empty_table_time_first:{[t]
    s:.qetl.plant.schema `executions;
    .qunit.assertEquals[(type s;count s;first cols s);(98h;0;`time);"an empty table, time leading"]};

/ What kafka_flow's hand-written buffer got wrong: trade_id is a long.
test_a_schema_carries_the_plants_types:{[t]
    .qunit.assertEquals[exec t from meta .qetl.plant.schema `client_flow where c=`trade_id;enlist "j";
        "trade_id is a long on the plant, whatever a job might have typed"]};

test_the_vendored_quote_comes_from_database_q:{[t]
    .qunit.assertEquals[6#cols .qetl.plant.schema `quote;`time`sym`bid`ask`bsize`asize;"the starter pack's quote"]};

test_published_is_the_schema_without_time:{[t]
    .qunit.assertEquals[cols .qetl.plant.published `executions;1_cols .qetl.plant.schema `executions;
        "the tickerplant stamps time, so a job publishes the rest"]};

test_an_unknown_table_is_refused_by_name:{[t]
    .qunit.assertThrows[.qetl.plant.schema;`no_such_table;"plant: no table no_such_table*";
        "named, and pointed at the one file where schemas are written"]};

/ Loading defines no root tables: a process that loads src/ must not get
/ empty tables of these names shadowing live ones.
test_loading_defines_no_root_tables:{[t]
    .qunit.assertTrue[not `packets in tables `.;"packets is only ever in .qetl.plant"]};

test_materialise_defines_root_tables_from_their_schemas:{[t]
    .qetl.plant.materialise enlist `economic_calendar;
    r:(value `economic_calendar)~.qetl.plant.schema `economic_calendar;
    ![`.;();0b;enlist `economic_calendar];
    .qunit.assertTrue[r;"a root table, exactly the schema"]};

test_adopt_vendored_registers_each_definition_line:{[t]
    f:"build/test-status/plant_vendored_fixture.q";
    system"mkdir -p build/test-status";
    (hsym `$f) 0: ("/ a comment";"plantprobe:([]time:`timestamp$(); px:`float$())";"x:1");
    r:.qetl.plant.adopt_vendored f;
    s:.qetl.plant.schema `plantprobe;
    ![`.qetl.plant;();0b;enlist `plantprobe];
    .qunit.assertEquals[(r;cols s);(enlist `plantprobe;`time`px);"only the table definition, registered by name"]};

test_adopt_vendored_without_the_file_registers_nothing:{[t]
    .qunit.assertEquals[.qetl.plant.adopt_vendored "build/test-status/no_such_database.q";`symbol$();
        "a tree copied without the vendored pack"]};

/ What stage 2 removed, kept removed: no job file writes a plant table's
/ schema by hand. 22 such copies existed, in 12 files; any of them could
/ disagree with the plant, and one hand-written buffer did. A job takes a
/ plant table's shape from .qetl.plant (shape, published, columns).
test_no_job_file_declares_a_plant_table_by_hand:{[t]
    dirs:("src/etl/streaming";"src/etl/transforms";"src/etl/workers";"src/etl/sources";"src/etl/reactions");
    files:raze {[d] f:key hsym `$d; (d,"/"),/:string f where f like "*.q"} each dirs;
    bad:raze {[f]
        ls:read0 hsym `$f;
        defs:ls where {[l] (0<count l) and "([]"~3#(1+l?":")_l} each ls;
        names:`${(x?":")#x} each defs;
        (f,": "),/:string names where names in .qetl.plant.names[]} each files;
    .qunit.assertEquals[bad;();"every plant table's shape comes from src/etl/plant_tables.q"]};

/ --- nested columns: element types, declared once ------------------------

/ The gate. An undeclared nested column has no contract - checked exactly it
/ refused every valid ladder, as a wildcard it took atoms where vectors belong.
test_every_nested_column_of_this_trees_tables_is_declared:{[t]
    .qunit.assertEquals[.qetl.plant.undeclared[];`symbol$();
        "declare it with .qetl.plant.nested beside the table in src/etl/plant_tables.q"]};

/ A mkt_orderbook page in its published shape - sym, bid_prices, ask_prices.
book:{[bids;asks] ([] sym:(count bids)#`EURUSD; bid_prices:bids; ask_prices:asks)}

test_populated_float_ladders_fit_their_declared_type:{[t]
    page:book[(1.1 1.09;1.2 1.19);(1.11 1.12;1.21 1.22)];
    .qunit.assertEquals[.qetl.plant.problems[`mkt_orderbook;page];();
        "float vectors where the plant declares \"F\": the case exact meta equality refused"]};

test_an_atom_where_a_ladder_belongs_is_refused:{[t]
    page:book[(1.1 1.09;1.2);(1.11 1.12;1.21 1.22)];
    .qunit.assertThrows[{'"; " sv .qetl.plant.problems[`mkt_orderbook;x]};page;"mkt_orderbook.bid_prices row 1 holds a value of type -9h*";
        "a wildcard would have passed it; meta, reading only row 0, would too"]};

test_a_vector_of_the_wrong_type_is_refused:{[t]
    page:book[(1.1 1.09;1 2);(1.11 1.12;1.21 1.22)];
    .qunit.assertThrows[{'"; " sv .qetl.plant.problems[`mkt_orderbook;x]};page;"*row 1 holds a value of type 7h - each row must be a \"F\" list*";
        "longs where the plant takes floats"]};

test_string_columns_fit_a_declared_c:{[t]
    `.qetl.plant.ptest_strings set ([] time:`timestamp$(); id:(); v:`float$());
    .qetl.plant.nested[`ptest_strings;(enlist `id)!enlist "C"];
    page:([] id:("deal-1";"deal-22"); v:1 2f);
    r:.qetl.plant.problems[`ptest_strings;page];
    delete ptest_strings from `.qetl.plant;
    delete from `.qetl.plant.elements where table=`ptest_strings;
    .qunit.assertEquals[r;();"string identifiers where the plant declares \"C\""]};

test_an_empty_page_checks_columns_not_elements:{[t]
    .qunit.assertEquals[.qetl.plant.problems[`mkt_orderbook;.qetl.plant.published `mkt_orderbook];();
        "no rows, so no element can be wrong - an empty () has no type to compare"];
    p:.qetl.plant.published `mkt_orderbook;
    .qunit.assertThrows[{'"; " sv .qetl.plant.problems[`mkt_orderbook;x]};(reverse cols p)#p;
        "mkt_orderbook has columns*";"its column order still is"]};

test_missing_extra_or_reordered_columns_are_refused:{[t]
    p:.qetl.plant.published `mkt_orderbook;
    .qunit.assertThrows[{'"; " sv .qetl.plant.problems[`mkt_orderbook;x]};(1_cols p)#p;"mkt_orderbook has columns*";"one missing"];
    .qunit.assertThrows[{'"; " sv .qetl.plant.problems[`mkt_orderbook;x]};update extra:`float$() from p;"mkt_orderbook has columns*";"one extra"]};

test_a_scalar_column_of_the_wrong_type_is_refused:{[t]
    p:.qetl.plant.published `arbitrage;
    .qunit.assertThrows[{'"; " sv .qetl.plant.problems[`arbitrage;x]};update size:`long$() from p;"arbitrage.size is typed \"j\" - the plant takes \"f\"";
        "scalar columns are compared exactly, as the plant stores them"]};

test_a_column_declared_to_hold_anything_takes_anything:{[t]
    page:([] owner:`a`b; name:`x`y; old:(1;"s"); new:(`v;2.5); as_of:2#.z.p);
    .qunit.assertEquals[.qetl.plant.problems[`config_change;page];();
        "config_change's old and new are declared \" \" on purpose: they hold any value"]};

test_nested_refuses_a_column_that_is_not_nested:{[t]
    .qunit.assertThrows[.qetl.plant.nested[`mkt_orderbook;];(enlist `sym)!enlist "F";
        "nested: mkt_orderbook has no nested column sym*";"a scalar column's type is the schema's own"]};

test_nested_refuses_a_character_that_is_not_a_type:{[t]
    .qunit.assertThrows[.qetl.plant.nested[`mkt_orderbook;];(enlist `bid_prices)!enlist "?";
        "nested: mkt_orderbook's bid_prices must be a meta type character*";"only meta's own characters, or \" \""]};

test_nested_refuses_a_symbol_without_looking_it_up:{[t]
    .qunit.assertThrows[.qetl.plant.nested[`mkt_orderbook;];`bid_prices;
        "nested: mkt_orderbook's element types must be a dictionary*";"refused before `value` could read a variable"]};

/ --- transform inputs are the plant's tables (#817) -----------------------
/ .
/ The output side is held to the plant by test_job_output_contracts.q, which
/ drives every publishing job. This is the input side: a transform input
/ NAMED after a plant table must be some of that table's columns, with its
/ types. A job that copied a plant table's schema into its own file (cross.q
/ did) passed every test while its copy and the plant agreed, and refused
/ every live batch once the plant gained a column - trapped by TorQ, so the
/ process looked healthy and published nothing.

/ (transform; input) pairs whose input is named after a plant table, with
/ what disagrees: columns the plant table lacks, and columns whose type
/ differs. A nested column declared " " accepts any list, as the transform's
/ own check does.
/ @private
input_disagreements:{[]
    plant:.qetl.plant.names[];
    raze {[plant;tr]
        ins:(.qetl.transform.def tr)`inputs;
        named:(key ins) where (key ins) in plant;
        {[tr;ins;n]
            dm:exec c!t from meta ins n;
            pm:exec c!t from meta .qetl.plant.shape n;
            extra:(key dm) except key pm;
            shared:(key dm) inter key pm;
            typed:shared where not (" "=dm shared) or (dm shared)=pm shared;
            $[count[extra]+count typed;
              enlist `transform`input`not_in_plant`wrong_type!(tr;n;extra;typed);
              ()]}[tr;ins] each named}[plant] each .qetl.transform.defined[]}

test_every_transform_input_named_after_a_plant_table_is_that_table:{[t]
    bad:raze .planttest.input_disagreements[];
    .qunit.assertEquals[count bad;0;
        "every transform input named after a plant table carries only its columns, with its types: ",.Q.s1 bad]};

test_the_input_scan_found_inputs_to_check:{[t]
    / Without this the test above passes the day transforms stop being named
    / after plant tables, having checked nothing.
    plant:.qetl.plant.names[];
    named:raze {[plant;tr] k where (k:key (.qetl.transform.def tr)`inputs) in plant}[plant] each .qetl.transform.defined[];
    .qunit.assertTrue[`fx_orderbook in named;"cross_quotes' fx_orderbook input is among those checked"]};

test_an_input_that_disagrees_with_its_plant_table_is_caught:{[t]
    .qetl.transform.define[`planttest_stale_copy;`inputs`output`fn`examples!(
        (enlist `fx_orderbook)!enlist ([] time:`timestamp$(); sym:`symbol$(); bid_prices:(); venue:`symbol$());
        ([] sym:`symbol$());
        {[b] select sym from b};
        enlist `inputs`expected!(
            (enlist `fx_orderbook)!enlist ([] time:enlist 2026.10.07D10:00:00; sym:enlist `EURUSD;
                bid_prices:enlist enlist 1.1; venue:enlist `EBS);
            ([] sym:enlist `EURUSD)))];
    found:raze .planttest.input_disagreements[];
    .testutil.drop_rows[`.qetl.transform.registry;`planttest_stale_copy];
    hit:found where `planttest_stale_copy=found@\:`transform;
    .qunit.assertEquals[(first hit)`not_in_plant;enlist `venue;"a column the plant table does not carry is named"]};

\d .
