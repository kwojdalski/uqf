// read_checkpoint.q - a child process for the q-backfill-process lane. Loads
// only what it needs and reports the cursor it would resume from, sharing
// nothing with the parent but the status directory.
\l src/etl/core/status.q
\l src/etl/core/backfill_state.q
d:{[n] 2026.09.10D00:00:00.000000000+n*1D};
spec:`source_version`range_from`range_to!(`v1;d 1;d 4);
-1 "CURSOR:",.Q.s1 .qbfstate.load_checkpoint[`restartproc;spec];
exit 0;
