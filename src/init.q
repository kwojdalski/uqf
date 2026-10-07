// init.q - loads every uqf module, each into its own flat namespace (one
// per file - .qstats, .qccy, .qdcf, .qrates, .qcross, .qfwd, .qopt, .qrisk, .qpos,
// .qalloc, .qdesk, .qlimit, .qexec, .qbook, .qmicro, .qdqc, .qexdef - kept
// single-level throughout, not nested under a shared .q parent.
// .
// Flat namespaces began as a portability constraint and are now a CONVENTION
// this tree keeps: one flat .q<abbrev> per file, with the file list as
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
//      The ETL tree (src/etl/, loaded separately) makes two deliberate
//      exceptions, both for INSTANCES rather than modules: bounded workers
//      nest under .qpipe.job (.qpipe.job.<worker name>, derived by .qetl.job.bounded.define) and
//      source declarations under .qpipe.source (.qpipe.source.<source name>, checked
//      against each file's own source_name). src/namespaces.q (.qns) is the
//      one enumeration that knows this, and every tool that lists
//      namespaces goes through it.
//
//   2. The library modules form an ACYCLIC graph, and CI holds them to it:
//      scripts/gates/check_module_deps.py lists every allowed edge between
//      namespaces and refuses a new one, or a cycle, until the list says so
//      (#626). Pricing and execution used to depend on each other; now
//      execution builds on synthetic cross pricing (.qcross), both use the
//      shared book maths in .qbook, and everything rests on foundation.
//      Load order below still is not what makes calls resolve - q binds a
//      name when it is called - but it now matches the dependency order.

\l src/foundation/schema.q
\l src/foundation/render.q
\l src/foundation/stats.q
\l src/foundation/ccy.q
\l src/foundation/daycount.q
\l src/foundation/calendar.q
\l src/foundation/rates.q
\l src/market_data/book.q
\l src/pricing/cross.q
\l src/pricing/forwards.q
\l src/pricing/options.q
\l src/portfolio/risk.q
\l src/portfolio/positions.q
\l src/portfolio/allocation.q
\l src/portfolio/desk_positions.q
\l src/portfolio/limits.q
\l src/execution/execution.q
\l src/market_data/microstructure.q
\l src/market_data/dqchecks.q
\l src/examples/example_defaults.q

\l src/metadata/metatables.q
\l src/namespaces.q
