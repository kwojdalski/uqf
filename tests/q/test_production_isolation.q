/ test_production_isolation.q - production q stands without the test tree
/ (.qt.harness.production).
/ .
/ Its own file, not a case of test_suite_layout.q, because it reads every
/ production file as text: scripts/dev/test_impact.py selects it for any
/ change under src/ (#1690), and the suite-layout cases beside it - which
/ load every job suite to check them - have no reason to run then too.

.qt.harness.production.test_production_sources_never_mention_the_test_tree:{[t]
    / init.q and everything it loads must stand without .qt and .qunit: the
    / tree is loaded by processes that have no tests directory. The runtime
    / half - a fresh interpreter loading src/ finds neither - is
    / python/uqs/tests/test_etl_standalone_load.py.
    files:(.qt.support.q_files["src"]),(.qt.support.q_files["scripts/processes"]);
    code:raze {[f] ls:read0 hsym `$f; ls:ls where not (trim each ls) like "/*"; (f,": "),/:ls where
        (ls like "*.qt.*") or (ls like "*.qunit*") or ls like "*tests/lib*"} each files;
    .qunit.assertEquals[code;();"no production line refers to .qt, .qunit or tests/lib"]};
