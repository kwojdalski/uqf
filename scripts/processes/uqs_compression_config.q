/ uqs_compression_config.q - point TorQ's .cmp at the compression policy uqs generates (#2152).
/ .
/ Load it BEFORE ${KDBCODE}/common/compress.q (VENDORED_LOAD_OVERLAY in
/ python/uqs/src/uqs/stack/procs.py, for the process that runs .cmp). compress.q
/ reads its config path once, as it loads:
/     inputcsv:@[value;`inputcsv;first .proc.getconfigfile["compressionconfig.csv"]]
/ so a value set here is the one it keeps.
/ .
/ Why it is needed at all: .proc.getconfigfile returns the FIRST of the
/ application's appconfig, KDBSERVCONFIG and KDBCONFIG that holds the file, and
/ the starter pack ships its own appconfig/compressionconfig.csv - so a
/ generated file under scripts/torqconfig would never be read. The policy is
/ declared in src/etl/compression_policy.q; scripts/generate/derive.py
/ compression-config keeps scripts/torqconfig/compressionconfig.csv current, and
/ bootstrap copies it into TORQDATA (python/uqs/src/uqs/stack/compression.py),
/ beside the generated process.csv and reporter.csv.

.cmp.inputcsv:hsym `$getenv[`TORQDATA],"/compressionconfig.csv";
