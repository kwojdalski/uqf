/ export_compression_config.q - print TorQ's compressionconfig.csv from the
/ declared compression policy (#2152).
/ .
/ The policy is declared in src/etl/compression_policy.q and validated by
/ .qetl.compression (src/etl/core/compression.q); this loads the tree and prints
/ the rows that module renders, one line each. scripts/generate/
/ compression_config.py runs it, and derive.py compression-config writes the
/ output to scripts/torqconfig/compressionconfig.csv, which TorQ finds before
/ its own lib/torq/config copy.
/ .
/ Run from the repository root: q scripts/generate/export_compression_config.q -q

\l src/init.q
\l src/etl/init.q

-1 .qetl.compression.csv[];
exit 0
