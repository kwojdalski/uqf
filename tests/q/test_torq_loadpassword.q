// test_torq_loadpassword.q - the outgoing-credential file order
// (.loadpwtest), scripts/torqcode/handlers/loadpassword.q.
//
// TorQ's own loader read columns `1 0` of .proc.getconfig's paths, which is
// base-then-app only while there are two layers. uqs sets KDBSERVCONFIG, so
// there are three, and the base layer - where every process's credential
// lives - was never read. passwordfiles is the order that replaces it.

\l scripts/torqcode/handlers/loadpassword.q

\d .loadpwtest

/ .proc.getconfig[;2]'s shape for one name: most specific layer first.
three:{[name] `$("app/";"serv/";"base/"),\:name}
two:{[name] `$("app/";"base/"),\:name}

test_with_a_service_layer_the_base_files_are_read_first:{[t]
    got:.servers.passwordfiles three each ("default.txt";"rdb.txt");
    .qunit.assertEquals[got;`$("base/default.txt";"base/rdb.txt";"serv/default.txt";"serv/rdb.txt";"app/default.txt";"app/rdb.txt");
        "base, then service, then app - the base layer is no longer dropped"]};

test_without_a_service_layer_the_order_is_torqs_own:{[t]
    got:.servers.passwordfiles two each ("default.txt";"rdb.txt");
    .qunit.assertEquals[got;`$("base/default.txt";"base/rdb.txt";"app/default.txt";"app/rdb.txt");
        "what trackservers.q's columns 1 0 gave for two layers, unchanged"]};

test_a_name_with_no_proctype_contributes_nothing:{[t]
    / .proc.parentproctype is often null; getconfig then gives null paths.
    got:.servers.passwordfiles (three "default.txt";3#`);
    .qunit.assertEquals[got;`$("base/default.txt";"serv/default.txt";"app/default.txt");
        "nulls and duplicates are dropped"]};

test_loading_the_file_outside_torq_does_not_try_to_read_passwords:{[t]
    / The loader sets USERPASS on the first file it finds; no file was read.
    .qunit.assertEquals[@[value;`.servers.USERPASS;{[e] `unset}];`unset;
        "the guard keeps a test process from running the loader"]};

\d .
