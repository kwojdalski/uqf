/ test_markout_schema.q - every markout dataset carries the markout contract's
/ core columns, with the core's types, in the core's relative order
/ (docs/architecture/markout-contract.md, #1923; the test is #1930).
/ .
/ Three things here are declared once and read by the tests:
/   .qt.qetl.plant.markout.core       the core columns (name; q type char), in order,
/                                     read from .qpipe.transform.markout_core
/                                     (core_cols for the names, result for the types).
/   .qt.qetl.plant.markout.datasets   which plant tables are markout datasets and their
/                                     layout. plant_tables.q carries no marker (it is
/                                     owned elsewhere until #1925 merges); when it
/                                     does, derive this from the marker instead.
/   .qt.qetl.plant.markout.exemptions the datasets that have not migrated yet, each
/                                     with the issue that removes the exemption.
/ Per the proposed answers in the contract's open questions: core columns keep
/ their relative order and domain columns may sit between them; wide datasets
/ carry the core too; a long dataset also requires kind and step, which are not
/ core columns.

/ The core columns, in order: a table of name and q type char (as meta reports it).
/ @return table: c (symbol), t (char)
/ @eg count .qt.qetl.plant.markout.core[]  ->  24
.qt.qetl.plant.markout.core:{[]
    m:exec c!t from meta .qpipe.transform.markout_core.result;
    c:.qpipe.transform.markout_core.core_cols;
    ([] c:c; t:m c)}

/ Every markout dataset on the plant, with its layout (`long or `wide).
/ @return dictionary: table name -> layout
.qt.qetl.plant.markout.datasets:{[]
    `fx_markout`fx_markout_wide`crypto_execution_quality`demo_execution_quality!`long`wide`long`long}

/ Datasets that do not yet carry the core, each with the issue that removes the
/ exemption and why. An exemption is deleted by the issue named, not edited.
/ @return table: dataset, issue, reason
.qt.qetl.plant.markout.exemptions:{[]
    ([] dataset:`fx_markout`fx_markout_wide`crypto_execution_quality`demo_execution_quality;
        issue:`$("#1935";"#1935";"#1936";"#1937");
        reason:("FX gains the core columns in the domain-module migration";
            "derived from fx_markout, so it gains them with it";
            "replaced by a new crypto dataset, which is written with the core";
            "the demo job is retired; it gets no dataset"))}

/ What stops a table conforming. Empty means it conforms.
/ @param schema table: the dataset's schema
/ @param layout symbol: `long or `wide (a long dataset also needs kind and step)
/ @return list of strings, one per problem
/ @eg .qt.qetl.plant.markout.problems[.qetl.plant.schema `fx_markout;`long]
.qt.qetl.plant.markout.problems:{[schema;layout]
    core:.qt.qetl.plant.markout.core[];
    m:exec c!t from meta schema;
    need:core[`c];
    if[layout=`long; need,:`kind`step];
    missing:need where not need in key m;
    p:{"missing column ",string x} each missing;
    have:select from core where c in key m;
    bad:select from have where not t=m c;
    if[count bad; p,:{"column ",string[x]," is type ",(1#y)," not ",1#z}'[bad`c;m bad`c;bad`t]];
    at:(cols schema)?have`c;
    if[not at~asc at; p,:enlist "core columns are out of order"];
    p}

/ A table with exactly the given columns: name and q type char.
.qt.qetl.plant.markout.build:{[spec] flip (spec`c)!{x$()} each spec`t}

/ A conforming dataset: the core in order, a domain column between core ones,
/ and kind and step after them.
.qt.qetl.plant.markout.conforming:{[]
    c:.qt.qetl.plant.markout.core[];
    extra:([] c:`domain_a`kind`step;t:"fsj");
    .qt.qetl.plant.markout.build[(2#c),(1#extra),(2_c),1_extra]}

.qt.qetl.plant.markout.test_the_core_is_24_distinct_columns_time_first:{[t]
    c:.qt.qetl.plant.markout.core[];
    .qunit.assertEquals[(count c;count distinct c`c;first c`c);(24;24;`time);"the contract's table"]};

.qt.qetl.plant.markout.test_a_conforming_dataset_has_no_problems:{[t]
    s:.qt.qetl.plant.markout.conforming[];
    .qunit.assertEquals[.qt.qetl.plant.markout.problems[s;`long];();"domain columns between core ones are allowed"];
    .qunit.assertEquals[.qt.qetl.plant.markout.problems[`kind`step _ s;`wide];();"a wide dataset needs no kind or step"]};

.qt.qetl.plant.markout.test_a_dropped_core_column_is_detected:{[t]
    s:.qt.qetl.plant.markout.conforming[];
    .qunit.assertEquals[.qt.qetl.plant.markout.problems[(enlist `rate_time) _ s;`long];enlist "missing column rate_time";"named"]};

.qt.qetl.plant.markout.test_a_retyped_core_column_is_detected:{[t]
    s:.qt.qetl.plant.markout.conforming[];
    r:update size:`long$() from s;
    .qunit.assertEquals[.qt.qetl.plant.markout.problems[r;`long];enlist "column size is type j not f";"named, with both types"]};

.qt.qetl.plant.markout.test_reordered_core_columns_are_detected:{[t]
    s:.qt.qetl.plant.markout.conforming[];
    r:`side`sym xcols s;
    .qunit.assertEquals[.qt.qetl.plant.markout.problems[r;`long];enlist "core columns are out of order";"relative order"]};

.qt.qetl.plant.markout.test_a_long_dataset_without_kind_and_step_is_detected:{[t]
    s:.qt.qetl.plant.markout.conforming[];
    .qunit.assertEquals[.qt.qetl.plant.markout.problems[`kind`step _ s;`long];("missing column kind";"missing column step");"required in long"]};

/ A markout dataset cannot be added to the plant without being registered here.
.qt.qetl.plant.markout.test_every_markout_looking_plant_table_is_registered:{[t]
    n:.qetl.plant.names[];
    found:n where (n like "*markout*") or n like "*execution_quality*";
    .qunit.assertEquals[asc found;asc key .qt.qetl.plant.markout.datasets[];"a new markout table must be listed in datasets"]};

.qt.qetl.plant.markout.test_exemptions_name_registered_datasets_and_an_issue:{[t]
    e:.qt.qetl.plant.markout.exemptions[];
    .qunit.assertTrue[all e[`dataset] in key .qt.qetl.plant.markout.datasets[];"a registered dataset"];
    .qunit.assertTrue[all (string e`issue) like "#[0-9]*";"each names the issue that removes it"];
    .qunit.assertTrue[all 0<count each e`reason;"each states why"]};

/ Every dataset conforms or is exempt: an unmigrated one is not silently accepted.
.qt.qetl.plant.markout.test_every_dataset_conforms_or_is_exempt:{[t]
    d:.qt.qetl.plant.markout.datasets[];
    ok:{[d;x] 0=count .qt.qetl.plant.markout.problems[.qetl.plant.schema x;d x]}[d] each key d;
    bad:key[d] where not ok;
    .qunit.assertEquals[asc bad except .qt.qetl.plant.markout.exemptions[]`dataset;`symbol$();"nonconforming datasets without an exemption"]};

/ The exempt datasets that now conform: their exemptions are stale.
/ @param names symbols: exempt datasets
/ @param schemas dictionary: dataset -> its schema
/ @param layouts dictionary: dataset -> layout
/ @return symbols
.qt.qetl.plant.markout.stale:{[names;schemas;layouts]
    ok:{[s;l;n] 0=count .qt.qetl.plant.markout.problems[s n;l n]}[schemas;layouts] each names;
    names where ok}

/ An exemption must not outlive the migration it waits for.
.qt.qetl.plant.markout.test_no_exemption_names_a_conforming_dataset:{[t]
    d:.qt.qetl.plant.markout.datasets[];
    n:.qt.qetl.plant.markout.exemptions[]`dataset;
    .qunit.assertEquals[.qt.qetl.plant.markout.stale[n;n!.qetl.plant.schema each n;d];`symbol$();
        "conforms now: delete its exemption (and the issue it names)"]};

.qt.qetl.plant.markout.test_a_stale_exemption_is_detected:{[t]
    s:.qt.qetl.plant.markout.conforming[];
    r:update size:`long$() from s;
    .qunit.assertEquals[.qt.qetl.plant.markout.stale[`ok`bad;`ok`bad!(s;r);`ok`bad!`long`long];enlist `ok;
        "only the dataset that conforms is stale"]};

/ The real datasets are not vacuously accepted: each unmigrated one has problems.
.qt.qetl.plant.markout.test_the_unmigrated_datasets_are_caught_by_the_check:{[t]
    p:count each .qt.qetl.plant.markout.problems[;`long] each .qetl.plant.schema each `fx_markout`crypto_execution_quality;
    .qunit.assertTrue[all p>0;"the check finds the missing core columns in real schemas"]};
