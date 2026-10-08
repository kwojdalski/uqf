/ test_declaration_load.q - .qetl.load: which declaration files a process
/ loads when it says what it runs (#902). The selected LOAD itself needs a
/ fresh process - this one has loaded the whole tree - and runs in
/ python/uqs/tests/test_peachq_pipelines.py against a schema without `quote`.

\d .dloadtest

test_no_names_select_no_files:{[t]
    .qunit.assertEquals[count .qetl.load.files `symbol$();0;"the infrastructure-only selection"];
    }

test_a_process_takes_in_the_peer_job_it_calls:{[t]
    .qunit.assertEquals[.qetl.load.files `superbook1;
        ("src/etl/streaming/market_data.q";"src/etl/streaming/superbook.q");
        "superbook calls market_data's helpers"];
    }

test_names_resolve_in_load_order_whatever_order_they_come_in:{[t]
    a:.qetl.load.files `superbook1`eq_orderbook;
    .qunit.assertEquals[a;.qetl.load.files `eq_orderbook`superbook1;"order-free"];
    .qunit.assertEquals[a;.qetl.load.order where .qetl.load.order in a;"init.q's order"];
    }

test_an_unknown_name_is_refused_by_name:{[t]
    .qunit.assertThrows[.qetl.load.files;`nosuchproc1;"*no declared process or job named nosuchproc1*";
        "a typo must not load nothing and look like it worked"];
    }

test_a_failed_load_names_the_file:{[t]
    .qunit.assertThrows[.qetl.load.one;"src/etl/streaming/nosuch.q";
        "etl load: src/etl/streaming/nosuch.q failed: *";"which file stopped the load"];
    }


/ A fresh directory holding `files`, empty when there are none.
dir_with:{[files] d:first system"mktemp -d"; {[d;f] (hsym `$d,"/",f) 0: enlist "/ x"}[d] each files; d}

test_a_full_load_takes_a_directory_s_q_files_alphabetically:{[t]
    d:dir_with[("b.q";"a.q";"notes.txt")];
    .qunit.assertEquals[.qetl.load.dir_files d;(d,"/a.q";d,"/b.q");
        "only .q files, in the order init.q has always loaded them"]};

test_only_reactions_may_be_empty:{[t]
    d:dir_with[()];
    .qunit.assertThrows[.qetl.load.dir_files;d;"etl load: no .q files under *";
        "an empty sources/ or workers/ is a broken tree, not an empty one"];
    r:d,"/reactions"; system"mkdir -p ",r;
    .qunit.assertEquals[.qetl.load.dir_files r;();"a tree need have no reactions"]};

test_an_empty_selection_loads_no_declaration:{[t]
    / `only` is what a process sets before src/etl/init.q; set here, then
    / removed, so no later suite sees a selection nobody made.
    .qetl.load.only:`symbol$();
    r:@[.qetl.load.declarations;("src/etl/sources";"src/etl/workers");{x}];
    delete only from `.qetl.load;
    .qunit.assertEquals[r;();"the infrastructure-only selection loads nothing"]};

\d .
