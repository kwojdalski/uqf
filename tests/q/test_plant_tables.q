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

\d .
