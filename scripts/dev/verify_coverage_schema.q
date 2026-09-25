/ verify_coverage_schema.q - check a coverage ledger against the shape
/ src/etl/core/materialisation.q declares.
/ .
/ Written to settle issue #60, which asked whether the assumed shape matched
/ a canonical one. That question is closed: this tree is the primary
/ lineage and canonical is frozen, so the shape materialisation.q declares IS the
/ schema and there is nothing else to compare it to.
/ .
/ What the script is still for is DRIFT: a ledger some other process built
/ to a different shape. Every read in materialisation.q filters on dataset,
/ partition and source_version, so an extra column that distinguishes rows
/ BEYOND those makes those reads aggregate across it, and a range covered for
/ one value of it reports as COMPLETE for all of them. Nothing errors,
/ because every row found is valid. That is the failure this catches.
/ .
/ `partition` itself was once the column this script warned about. It is
/ declared and filtered on now (#185), so it can no longer appear as an extra
/ - what the check below still catches is a SECOND partitioning dimension,
/ one this tree does not know it should be slicing on.
/ .
/ .qmatz.require_schema is the same check inside a worker's init, reached via
/ .qmatz.attach. This is the standalone form, for looking at a ledger without
/ starting a worker.
/ .
/ Usage, on a machine that can reach the real ledger:
/ .
/   QHOME=~/.kx ~/.kx/bin/q scripts/dev/verify_coverage_schema.q \
/       -target localhost:5010
/ .
/ or against a table in the current process:
/ .
/   QHOME=~/.kx ~/.kx/bin/q scripts/dev/verify_coverage_schema.q -local 1
/ .
/ It prints a verdict and the exact `meta` output to paste into #60. It reads
/ metadata only - no data, no writes - so it is safe to run against a
/ production ledger.

\c 400 2000

\l src/etl/core/materialisation.q

args:.Q.opt .z.x;

target:$[`target in key args; first args`target; ""];
uselocal:`local in key args;

if[(0=count target) and not uselocal;
    -1 "usage: q scripts/dev/verify_coverage_schema.q -target host:port";
    -1 "   or: q scripts/dev/verify_coverage_schema.q -local 1";
    exit 2];

/ Names that would plausibly BE a partition key, since the requirements name
/ the concept without naming the column. Reported rather than assumed: the
/ point is to find out, not to guess a second time.
partition_candidates:`date`dt`partition`part`pdate`month`int`sym;

fetch_meta:{[]
    if[uselocal;
        if[not `etl_coverage in tables `.;
            '"verify: -local was given but this process has no etl_coverage table"];
        :0!meta `etl_coverage];
    h:@[{hopen (hsym `$":",x;5000j)};target;{'"verify: cannot reach ",target," (",x,")"}];
    r:@[{[handle] handle({0!meta x};`etl_coverage)};h;{'"verify: etl_coverage not found on ",target," (",x,")"}];
    @[hclose;h;::];
    r}

m:fetch_meta[];
present:exec c from m;
assumed:.qmatz.schema;

-1 "";
-1 "=================== etl_coverage: live schema ===================";
-1 .Q.s m;
-1 "";

missing:assumed where not assumed in present;
extra:present where not present in assumed;
partition_like:extra where extra in partition_candidates;

-1 "assumed columns : ",", " sv string assumed;
-1 "live columns    : ",", " sv string present;
-1 "";

problems:0;

if[count missing;
    -1 "MISSING   ",", " sv string missing;
    -1 "          materialisation.q reads these, so every read against the real";
    -1 "          ledger fails or returns nulls. This is the LOUD failure -";
    -1 "          bad, but it announces itself.";
    -1 "";
    `problems set problems+1];

if[count partition_like;
    -1 "PARTITION ",", " sv string partition_like;
    -1 "          This is the SILENT failure #60 was filed for, in its";
    -1 "          remaining form: a SECOND partitioning dimension, beyond the";
    -1 "          `partition` column this tree already filters on. Intervals";
    -1 "          from different values of it compose together, so a range";
    -1 "          covered for one but empty for the others is reported";
    -1 "          COMPLETE. Fix before any consumer trusts is_covered - the";
    -1 "          same four steps #185 followed for `partition` itself:";
    -1 "            1. add the column to .qmatz.schema and init_ledger";
    -1 "            2. add it as a REQUIRED parameter to intervals/";
    -1 "               is_covered/missing/require_covered - required, not";
    -1 "               optional, for the same reason source_version is";
    -1 "            3. add it to uqf_frontend/queries.py's COVERAGE program";
    -1 "            4. add a test that coverage under one value does not";
    -1 "               satisfy a query for another";
    -1 "";
    `problems set problems+1];

if[count extra except partition_like;
    -1 "EXTRA     ",", " sv string extra except partition_like;
    -1 "          Present live, unknown here. Harmless to READS, but worth";
    -1 "          adding if a writer is expected to populate them.";
    -1 ""];

-1 "=================== verdict ===================";
-1 $[0=problems;
     "MATCH - the ledger agrees with the shape materialisation.q declares.";
     "MISMATCH - ",string[problems]," problem class(es) above. Paste this whole output into #60."];
-1 "===============================================";
exit $[0=problems; 0; 1];
