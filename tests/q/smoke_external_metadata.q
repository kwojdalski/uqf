// smoke_external_metadata.q - the ETL-20 live external metadata check.
//
// ETL-20: "Run the live external metadata smoke test separately, when an
// external schema or adapter changes."
//
//   > The deterministic suite proves local behaviour, NOT that a configured
//   > external service is reachable or compatible.
//
// Deliberately NOT part of q-unit or `test.py all`. Folding it in would make
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
\l src/etl/core/materialisation.q
\l src/etl/core/source_contract.q
\l src/etl/sources/demo_deals.q

/ --- configuration ------------------------------------------------------

/ Command-line flags, one word per entry:
/ .
/   q tests/q/smoke_external_metadata.q -targets host:port [host:port ...]
/       -tables table:col,col [table:col,col ...] [-timeout_ms 5000]
/ .
/ scripts/test.py smoke passes them as --targets, --tables and --timeout-ms.
/ They were UQF_SMOKE_* environment variables, with ';' between entries -
/ which needed quoting in any shell, and outlived the run they were set for.
/ Read straight off the command line rather than through .qwcfg: this
/ script runs outside a worker, so there are no config layers to resolve.
opts:.Q.opt .z.x;
targets:$[`targets in key opts; opts`targets; ()];
table_specs:$[`tables in key opts; opts`tables; ()];

if[(0=count targets) or 0=count table_specs;
    -1 "SKIP  nothing configured - pass -targets host:port ... and";
    -1 "      -tables table:col,col ... to run this check.";
    -1 "";
    -1 "      ETL-20 keeps this lane separate precisely so an unconfigured";
    -1 "      checkout is not reported as a failure.";
    exit 0];

expectations:{[spec]
    parts:":" vs spec;
    if[2<>count parts;
        '"smoke: expected table:col,col, got \"",spec,"\""];
    (`$first parts;`$"," vs last parts)} each table_specs;

failures:0;
note:{[label;ok]
    -1 $[ok;"pass  ";"FAIL  "],label;
    if[not ok; `failures set failures+1];
    ok}

/ --- reachability -------------------------------------------------------

/ hopen with a TIMEOUT. An untimed hopen against a dead host blocks until the
/ OS gives up, which on some networks is minutes - long enough that a CI run
/ looks hung rather than failed.
timeout_ms:$[`timeout_ms in key opts; "J"$first opts`timeout_ms; 5000j];
if[null timeout_ms; '"smoke: -timeout_ms is not a whole number of milliseconds"];

connect:{[target] @[{hopen (hsym `$":",x;timeout_ms)};target;{[e] 0Ni}]}

handles:connect each targets;
{[t;h] note["reachable: ",t;not null h]}'[targets;handles];

live:handles where not null handles;
if[0=count live;
    -1 "";
    -1 "FAIL  no configured target was reachable - nothing to check compatibility against";
    exit 1];

h:first live;
/ ...and the host it belongs to. Needed because the source-contract checks
/ below must know WHICH target this handle reaches: `target` is only ever a
/ lambda parameter above, never a global, so referring to it here silently
/ resolved to nothing.
live_target:first targets where not null handles;

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

/ --- ETL-12's live half: every registered source, against its own contract --

/ The requirement is that live external metadata is validated against "that
/ SAME contract" the fixture is validated against. The fixture side runs in
/ the deterministic suite (test_source_contract.q); this is the other side,
/ and it is the same declaration and the same comparison - which is what
/ makes the fixture meaningful rather than a thing that merely exists.
/ .
/ Each registered source is checked only when its credential names THIS
/ target, so a run pointed at one host does not report every source as
/ broken. A source whose credential is unset is skipped, not failed: ETL-20's
/ whole point is that an unconfigured checkout is not a failure.
check_source:{[source]
    if[not .qsrc.has_credentials source;
        -1 "skip  ",string[source]," (",.qsrc.credential_var[source]," unset)";
        :1b];
    if[not (.qsrc.require_credentials source)~live_target;
        -1 "skip  ",string[source]," (configured for a different target)";
        :1b];
    r:@[{.qsrc.validate_live[x;h]; ""};source;{x}];
    note["contract: ",string[source],$[count r;" - ",r;""];0=count r]}

check_source each .qsrc.registered[];

{@[hclose;x;::]} each live;

-1 "";
-1 "==================== smoke: external metadata ====================";
-1 $[0=failures;
     "all checks passed - the configured sources are reachable and still carry the expected columns";
     (string failures)," check(s) FAILED - an external schema or adapter has changed, or a source is down"];
-1 "==================================================================";
exit $[0=failures; 0; 1];
