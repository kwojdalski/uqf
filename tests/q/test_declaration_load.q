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


\d .
