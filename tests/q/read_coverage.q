/ read_coverage.q - print the coverage a FRESH process can see.
/ .
/ The child half of run_backfill_process.q's durability check. Deliberately
/ loads a minimal subset rather than src/etl/init.q: this must prove that the
/ ledger on disk is sufficient, so pulling in the whole tree - and anything
/ that might stage a row of its own - would weaken what the check establishes.
/ .
/ Run from the repository root, with UQFSTATUSDIR pointing at the directory
/ the parent used.

system"l src/init.q";
system"l src/etl/core/backfill_state.q";
system"l src/etl/core/materialisation.q";
system"l src/etl/core/status.q";

.qetl.coverage.attach[];
-1 "ROWS:",string count .qetl.coverage.ledger[];
/ "yes"/"no" rather than `string` of the boolean: `string 1b` is "1", not
/ "1b", so a caller matching on "1b" would never match and the check would
/ fail while the code was correct.
-1 "COVERED:",$[.qetl.coverage.is_covered[`durable_ds;`;`v1;.z.p;
    2026.09.11D00:00:00.000000000;2026.09.12D00:00:00.000000000];"yes";"no"];
exit 0
