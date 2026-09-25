// try_acquire.q - a child process for the q-backfill-process lane. Prints
// REFUSED or ACQUIRED, so the parent can assert on mutual exclusion rather
// than on an exit code that a load error would also produce.
\l src/etl/core/status.q
\l src/etl/core/backfill_state.q
r:@[{.qetl.job.bounded.state.acquire_lock x; "ACQUIRED"};`crossproc;{"REFUSED: ",x}];
-1 r;
if[r~"ACQUIRED"; .qetl.job.bounded.state.release_lock `crossproc];
exit 0;
