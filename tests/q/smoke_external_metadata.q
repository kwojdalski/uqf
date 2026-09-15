// smoke_external_metadata.q - the E-20 live external metadata check.
//
// E-20: "Run the live external metadata smoke test separately, when an
// external schema or adapter changes."
//
//   > The deterministic suite proves local behaviour, NOT that a configured
//   > external service is reachable or compatible.
//
// Deliberately NOT part of q-unit or `test.sh all`. Folding it in would make
// every local run depend on a remote host being up, and - worse - would
// train everyone to treat a red suite as "the network again", which is how a
// real schema change gets ignored.
//
// It is also deliberately not a qUnit suite. qUnit's job is deterministic
// assertions; this check is neither deterministic nor local, and dressing it
// up as a unit test would put a non-hermetic check in the same report as 491
// hermetic ones.
//
// Three outcomes, kept distinct because conflating them is the whole failure
// mode this file guards against:
//
//   SKIP  nothing is configured. Exit 0: there is nothing to check, and
//         failing here would make an unconfigured checkout look broken.
//   FAIL  configured but unreachable, or reachable and INCOMPATIBLE. Exit 1.
//   PASS  reachable and the expected columns are present.
//
// What it checks is METADATA, not data: that each declared source table
// exists and still has the columns the adapter reads. A value check would be
// non-deterministic against a live source; a column check is exactly the
// thing that breaks silently when an upstream schema changes, because a
// missing column reads as a null rather than as an error in most q code.

\c 400 1000

\l src/etl/core/worker_config.q

/ --- configuration ------------------------------------------------------

/ UQF_SMOKE_TARGETS is a semicolon-separated list of host:port entries, and
/ UQF_SMOKE_TABLES of table:col,col,col expectations. Both through plain
/ getenv rather than .qwcfg: this script runs outside a worker, so there are
/ no config layers to resolve, and pretending otherwise would suggest the
/ YAML has a say here when it does not.
targets_raw:getenv `UQF_SMOKE_TARGETS;
tables_raw:getenv `UQF_SMOKE_TABLES;

if[(0=count targets_raw) or 0=count tables_raw;
    -1 "SKIP  nothing configured - set UQF_SMOKE_TARGETS (host:port;...) and";
    -1 "      UQF_SMOKE_TABLES (table:col,col;...) to run this check.";
    -1 "";
    -1 "      E-20 keeps this lane separate precisely so an unconfigured";
    -1 "      checkout is not reported as a failure.";
    exit 0];

targets:";" vs targets_raw;
expectations:{[spec]
    parts:":" vs spec;
    if[2<>count parts;
        '"smoke: expected table:col,col, got \"",spec,"\""];
    (`$first parts;`$"," vs last parts)} each ";" vs tables_raw;

failures:0;
note:{[label;ok]
    -1 $[ok;"pass  ";"FAIL  "],label;
    if[not ok; `failures set failures+1];
    ok}

/ --- reachability -------------------------------------------------------

/ hopen with a TIMEOUT. An untimed hopen against a dead host blocks until the
/ OS gives up, which on some networks is minutes - long enough that a CI run
/ looks hung rather than failed.
timeout_ms:$[0=count getenv `UQF_SMOKE_TIMEOUT_MS; 5000j; "J"$getenv `UQF_SMOKE_TIMEOUT_MS];

connect:{[target] @[{hopen (hsym `$":",x;timeout_ms)};target;{[e] 0Ni}]}

handles:connect each targets;
{[t;h] note["reachable: ",t;not null h]}'[targets;handles];

live:handles where not null handles;
if[0=count live;
    -1 "";
    -1 "FAIL  no configured target was reachable - nothing to check compatibility against";
    exit 1];

h:first live;

/ --- compatibility ------------------------------------------------------

/ `meta` rather than a select: metadata is cheap, and a select against a live
/ table can be arbitrarily expensive on a source nobody here controls.
/ .
/ The trapped lambda takes ONE argument and reads the handle from the global
/ above, rather than taking both. `@[f;(h;tbl);handler]` applies f to the
/ PAIR as a single argument - @ is unary apply - so a two-parameter f fails
/ on rank, the handler fires, and the check reports "table absent" against a
/ perfectly healthy source. Exactly the wrong answer for this file to give:
/ a false schema-drift alarm is how a real one gets ignored.
/ .
/ `0!` runs on the remote side, inside the lambda sent over, so the reply is
/ an ordinary table rather than a keyed one.
check_table:{[expectation]
    tbl:first expectation;
    expected:last expectation;
    m:@[{[t] h({0!meta x};t)};tbl;{[e] (::)}];
    / a table the source does not have makes `meta` throw remotely, which
    / surfaces here as the handler's (::) - the one outcome that is a schema
    / problem rather than a column problem.
    if[(::)~m;
        :note["table present: ",string tbl;0b]];
    note["table present: ",string tbl;1b];
    present:exec c from m;
    missing:expected where not expected in present;
    note["columns present in ",string[tbl],$[count missing;" (missing: ",(", " sv string missing),")";""];
         0=count missing]}

check_table each expectations;

{@[hclose;x;::]} each live;

-1 "";
-1 "==================== smoke: external metadata ====================";
-1 $[0=failures;
     "all checks passed - the configured sources are reachable and still carry the expected columns";
     (string failures)," check(s) FAILED - an external schema or adapter has changed, or a source is down"];
-1 "==================================================================";
exit $[0=failures; 0; 1];
