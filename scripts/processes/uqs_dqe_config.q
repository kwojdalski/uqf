/ uqs_dqe_config.q - point TorQ's DQE at the query list uqs generates.
/ .
/ Loaded by dqe1 BEFORE ${KDBCODE}/processes/dqe.q (VENDORED_LOAD_OVERLAY in
/ python/uqs/src/uqs/stack/procs.py). dqe.q reads its config path once, as
/ `configcsv:@[value;`.dqe.configcsv;<the vendored file>]`, so a value set
/ here first is the one it keeps. Nothing else in TorQ reads it.
/ .
/ The generated file is the vendored rows plus this tree's metatables
/ (python/uqs/src/uqs/stack/dqe.py), written into TORQDATA by bootstrap,
/ beside the generated process.csv and database.q.

\d .dqe

configcsv:hsym `$getenv[`TORQDATA],"/dqengineconfig.csv";

\d .
