// init.q - loads every uqf module, each into its own flat namespace (one
// per file - .qstats, .qccy, .qdcf, .qrates, .qfwd, .qopt, .qrisk, .qpos,
// .qexec, .qbook, .qmicro, .qdqc, .qexdef - kept single-level throughout, not
// nested under a shared .q parent.
// .
// Flat namespaces began as a portability constraint and are now a CONVENTION
// this tree keeps (N-01): one flat .q<abbrev> per file, with the file list as
// the registry. The original constraint is gone, but the convention is load
// bearing in its own right - the filename-to-namespace tie is what the naming
// auditor checks and what docs/man.q's registry is generated against, and 30
// namespaces now depend on it.
// Run from the repository root, e.g. `q src/init.q` or `\l src/init.q`.
//
// The directories below are ORGANISATIONAL ONLY. Two things follow from
// that, and both are deliberate:
//
//   1. Namespaces do not track directories. `src/foundation/ccy.q` is still
//      .qccy, not .qfnd.ccy - the flat single-level scheme is unchanged, so
//      no call site or test assertion moved when the files did. A reader
//      learns the file->namespace mapping from the list above or README's
//      table; it is not derivable from the path.
//
//   2. The directories do NOT imply a dependency layering, and it would be
//      wrong to assume one. The module graph is not acyclic:
//        pricing/forwards.q   -> .qexec.sweep_price, .qexec.markout
//        execution/execution.q -> .qfwd.ts_col, .qfwd.apply_col_precedence
//      That is a genuine cycle between pricing/ and execution/, and it
//      resolves only because q binds names at call time rather than at
//      definition time. Likewise market_data/dqchecks.q reaches into
//      .qfwd (pricing), .qmicro (its own group) and .qpos (portfolio), and
//      market_data/microstructure.q reaches into .qexec and .qfwd.
//      So load order below is for readability, not correctness - see the
//      kdb-q-conventions skill, which spells out that q resolves function
//      names at call time.
//
// The one ordering constraint that is real: pricing/forwards.q defines
// module-level DATA (ts_col, col_precedence), not just functions, and
// execution/execution.q reads it. Still fine at call time, but it is state
// rather than code, so it is worth knowing about.

\l src/foundation/stats.q
\l src/foundation/ccy.q
\l src/foundation/daycount.q
\l src/foundation/rates.q
\l src/pricing/forwards.q
\l src/pricing/options.q
\l src/portfolio/risk.q
\l src/portfolio/positions.q
\l src/execution/execution.q
\l src/market_data/book.q
\l src/market_data/microstructure.q
\l src/market_data/dqchecks.q
\l src/examples/example_defaults.q

\l src/metadata/metatables.q
