/ uqs_compression_process.q - housekeeping1, as the process `uqs data compress` drives (#2147).
/ .
/ Loaded LAST by housekeeping1 (VENDORED_LOAD_OVERLAY in python/uqs/src/uqs/stack/procs.py),
/ after TorQ's own housekeeping.q, torq_pipeline.q and the compression wrapper.
/ .
/ .qtorq.compress.run asks every HDB to reload through .qtorq.reload_hdb, which finds
/ them in .servers. TorQ's housekeeping process connects to nothing - TorQ's default
/ settings leave .servers.CONNECTIONS empty and .servers.STARTUP off - so this adds the
/ HDBs and starts the connections: the two lines torq_pipeline.q's connect_etl runs
/ for the tickerplant. housekeeping's own login is on the starter pack's access list.

.servers.CONNECTIONS:distinct .servers.CONNECTIONS,.qtorq.hdb_type;
.servers.startup[];
