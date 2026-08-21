// init.q - loads every uqf module, each into its own flat namespace (one
// per file - .qstats, .qccy, .qdcf, .qrates, .qfwd, .qopt, .qrisk, .qexec,
// .qbook, .qmicro, .qex - kept single-level throughout, not nested under
// a shared .q parent, since multi-level \d namespace paths don't resolve
// under the PeachQ interpreter this repo also targets).
// Run from the repository root, e.g. `q src/init.q` or `\l src/init.q`.

\l src/stats.q
\l src/ccy.q
\l src/daycount.q
\l src/rates.q
\l src/forwards.q
\l src/options.q
\l src/risk.q
\l src/execution.q
\l src/book.q
\l src/microstructure.q
\l src/example_defaults.q
