// test_torq_deployment.q - which process.csv rows TorQ registers
// (.deploytest), scripts/torqcode/handlers/deployment.q (#1207).
//
// Under the `essential` profile sort1 was handed sortworker2 by discovery -
// a process.csv row nothing started - and retried its port forever. The
// handler drops the rows outside the deployment set uqs writes. These are its
// pure halves against a stand-in connection table; the wrapper on TorQ's own
// .servers.register, run against the vendored trackservers.q, is
// python/uqs/tests/test_deployment.py.

\l scripts/torqcode/handlers/deployment.q

\d .deploytest

/ discovery's answer to sort1: both workers, a DISCOVERY host:port with no
/ name, and a process process.csv does not declare.
conns:([]procname:`sortworker1`sortworker2``outside1;proctype:`sortworker`sortworker`discovery`other)
declared:`sort1`sortworker1`sortworker2
essential:`sort1`sortworker1

test_an_unselected_worker_is_dropped:{[t]
    got:.servers.indeployment[essential;declared;conns];
    .qunit.assertEquals[exec procname from got;`sortworker1``outside1;
        "sortworker2 is declared and not deployed; the rest are kept"]};

test_a_refresh_with_the_same_answer_drops_it_again:{[t]
    once:.servers.indeployment[essential;declared;conns];
    .qunit.assertEquals[.servers.indeployment[essential;declared;conns,once];once,once;
        "nothing a second answer carries brings sortworker2 back"]};

test_a_worker_started_later_is_kept:{[t]
    got:.servers.indeployment[essential,`sortworker2;declared;conns];
    .qunit.assertEquals[got;conns;"once uqs adds it to the set, every row passes"]};

test_no_deployment_set_filters_nothing:{[t]
    .qunit.assertEquals[.servers.indeployment[`symbol$();declared;conns];conns;
        "a stack started without a profile discovers as TorQ always has"]};

test_the_set_is_read_from_the_file_uqs_writes:{[t]
    f:"/tmp/uqf_test_deployment_",string[.z.i],".txt";
    (hsym`$f) 0: ("sort1";"";"sortworker1");
    got:.servers.deploymentset f;
    hdel hsym`$f;
    .qunit.assertEquals[got;`sort1`sortworker1;"one procname a line, blank lines skipped"]};

test_a_missing_file_is_an_empty_set:{[t]
    .qunit.assertEquals[.servers.deploymentset "/tmp/uqf_test_deployment_absent.txt";`symbol$();
        "no file: nothing to filter by"];
    .qunit.assertEquals[.servers.deploymentset "";`symbol$();"no variable: the same"]};

test_loading_the_file_outside_torq_wraps_nothing:{[t]
    .qunit.assertEquals[`registerall in key `.servers;0b;
        "no trackservers.q register here, so none is wrapped"]};

\d .
