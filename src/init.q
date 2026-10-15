// init.q - loads every uqf quant module into one of seven domain namespaces
// under .qf:
//
//   .qf.px    theoretical pricing and the conventions it rests on -
//             ccy.q daycount.q calendar.q rates.q forwards.q options.q
//   .qf.mkt   analytics over observed market data - book.q cross.q
//             attribution_routes.q microstructure.q microstructure_venues.q
//             dqchecks.q asof.q
//   .qf.exec  execution quality and allocation - execution.q
//             execution_attribution.q allocation.q
//   .qf.port  positions, desk exposure, risk and limits - positions.q
//             desk_positions.q risk.q limits.q
//   .qf.math  numerical primitives - stats.q
//   .qf.util  schema checks, metadata tables, rendering and namespace
//             discovery - schema.q render.q metatables.q namespaces.q
//   .qf.demo  example defaults and the synthetic market - example_defaults.q
//             and src/etl/synthetic_market.q (the ETL loads that one)
//
// docs/reference/qf-domains.md has the domain boundaries, the helper
// conventions and the table from every former .q<abbrev> name to its new one.
// Run from the repository root, e.g. `q src/init.q` or `\l src/init.q`.
//
// The directories below are ORGANISATIONAL ONLY. Two things follow from
// that, and both are deliberate:
//
//   1. Neither directories nor files are namespaces. Several files share one
//      domain, and a file puts a name in its domain by writing
//      the name in full from the root context (`.qf.px.fwd_simple:{...}`) -
//      never `\d .qf.px`, which PeachQ cannot load - so a lambda's own
//      siblings are named in full too (`.qf.px.growth_simple[r;t]`). A reader learns the file->domain mapping from
//      the list above; it is not derivable from the path.
//
//      Because a domain is shared, a name must be defined by ONE file of it:
//      two files defining `foo:` leave the winner to load order, and
//      scripts/gates/check_module_deps.py fails the second. A call from one
//      file to another is always fully qualified (`.qf.px.pip_factor`, not
//      `pip_factor`), which is also what lets that gate see the dependency.
//
//      The ETL tree (src/etl/, loaded separately) nests its instances: bounded
//      workers under .qpipe.job (.qpipe.job.<worker name>, derived by
//      .qetl.job.bounded.define) and source declarations under .qpipe.source
//      (.qpipe.source.<source name>, checked against each file's own
//      source_name). src/namespaces.q (.qf.util) is the one enumeration that
//      knows the nesting, and every tool that lists namespaces goes through it.
//
//   2. The modules (files) form an ACYCLIC graph, and CI holds them to it:
//      scripts/gates/check_module_deps.py lists every allowed edge between
//      modules and refuses a new one, or a cycle, until the list says so
//      (#626). Pricing and execution used to depend on each other; now
//      execution builds on synthetic cross pricing (cross.q), both use the
//      shared book maths in book.q, and everything rests on foundation. The
//      domains themselves are NOT acyclic (forwards.q reads book.q, cross.q
//      reads ccy.q), so the graph is held at the module level.
//      Load order below still is not what makes calls resolve - q binds a
//      name when it is called - but it matches the dependency order.

\l src/foundation/schema.q
\l src/foundation/render.q
\l src/foundation/stats.q
\l src/foundation/ccy.q
\l src/foundation/daycount.q
\l src/foundation/calendar.q
\l src/foundation/rates.q
\l src/market_data/book.q
\l src/pricing/cross.q
\l src/pricing/attribution_routes.q
\l src/pricing/forwards.q
\l src/pricing/options.q
\l src/portfolio/risk.q
\l src/portfolio/positions.q
\l src/portfolio/allocation.q
\l src/portfolio/desk_positions.q
\l src/portfolio/limits.q
\l src/market_data/asof.q
\l src/execution/execution.q
\l src/execution/execution_attribution.q
\l src/market_data/microstructure.q
\l src/market_data/microstructure_venues.q
\l src/market_data/dqchecks.q
\l src/examples/example_defaults.q

\l src/metadata/metatables.q
\l src/namespaces.q
