/ verify_coverage_schema.q - settles issue #60 in one command.
/ .
/ src/etl/core/coverage.q's schema is ASSUMED. It was inferred from what
/ ETL-07/ETL-08/ETL-09 require plus the frontend requirements' description of
/ coverage "by dataset, partition key, and time range" - and PARTITION KEY is
/ not in the assumed shape. Four files now depend on that assumption:
/ coverage.q, worker_runtime.q, tests/q/test_coverage.q and
/ uqf_frontend/queries.py's COVERAGE program.
/ .
/ The dangerous direction is specific. If the real table carries a partition
/ key, a query filtering only on dataset and source_version aggregates across
/ partitions, so a range covered in ONE partition and empty in the others is
/ reported COMPLETE. A consumer then reads a gap-ridden range believing it is
/ whole - and nothing errors, because every row it does find is valid.
/ .
/ Usage, on a machine that can reach the real ledger:
/ .
/   QHOME=~/.kx ~/.kx/bin/q scripts/verify_coverage_schema.q \
/       -target localhost:5010
/ .
/ or against a table in the current process:
/ .
/   QHOME=~/.kx ~/.kx/bin/q scripts/verify_coverage_schema.q -local 1
/ .
/ It prints a verdict and the exact `meta` output to paste into #60. It reads
/ metadata only - no data, no writes - so it is safe to run against a
/ production ledger.

\c 400 2000

\l src/etl/core/coverage.q

args:.Q.opt .z.x;

target:$[`target in key args; first args`target; ""];
uselocal:`local in key args;

if[(0=count target) and not uselocal;
    -1 "usage: q scripts/verify_coverage_schema.q -target host:port";
    -1 "   or: q scripts/verify_coverage_schema.q -local 1";
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
assumed:.qcov.schema;

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
    -1 "          coverage.q reads these, so every read against the real";
    -1 "          ledger fails or returns nulls. This is the LOUD failure -";
    -1 "          bad, but it announces itself.";
    -1 "";
    `problems set problems+1];

if[count partition_like;
    -1 "PARTITION ",", " sv string partition_like;
    -1 "          This is the SILENT failure #60 was filed for. The live";
    -1 "          table is partitioned and coverage.q does not filter on it,";
    -1 "          so intervals from different partitions compose together";
    -1 "          and a range covered in one partition but empty in the";
    -1 "          others is reported COMPLETE. Fix before any consumer";
    -1 "          trusts is_covered:";
    -1 "            1. add the column to .qcov.schema and init_ledger";
    -1 "            2. add it as a REQUIRED parameter to intervals/";
    -1 "               is_covered/missing/require_covered - required, not";
    -1 "               optional, for the same reason source_version is (ETL-09)";
    -1 "            3. add it to uqf_frontend/queries.py's COVERAGE program";
    -1 "            4. add a test that coverage in one partition does not";
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
     "MATCH - the assumed schema is correct. Close #60 and drop the ASSUMED note in coverage.q.";
     "MISMATCH - ",string[problems]," problem class(es) above. Paste this whole output into #60."];
-1 "===============================================";
exit $[0=problems; 0; 1];
