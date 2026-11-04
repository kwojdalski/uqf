/ read_snapshots.q - print the configuration snapshots a FRESH process can see.
/ .
/ The child half of run_backfill_process.q's snapshot-durability check (#1999). Loads a
/ minimal subset deliberately: this must prove the file on disk is sufficient, so it pulls
/ in nothing that could record a snapshot of its own.
/ .
/ Run from the repository root, with UQF_STATUS_DIR pointing at the directory the parent
/ used. It prints one line per fact, and a refused store as REFUSED: and the reason.

system"l src/init.q";
system"l src/etl/core/status.q";
system"l src/etl/core/backfill_state.q";
system"l src/etl/core/config_snapshot.q";

r:@[{(1b;.qetl.snapshot.attach[])};::;{[e] (0b;e)}];
if[not first r; -1 "REFUSED:",last r; exit 0];
l:.qetl.snapshot.list[`];
-1 "SNAPSHOTS:",string count l;
-1 "DIGESTS_OK:",string all l`digest_ok;
-1 "IDS:",";" sv string l`snapshot_id;
-1 "PARAMS:",.qf.util.full .qetl.snapshot.content_of first l`snapshot_id;
exit 0
