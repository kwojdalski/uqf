# Licensing

Everything in this repository is MIT licensed (see [`LICENSE`](LICENSE)),
**except** the seven vendored dependencies below:

- `tests/lib/qunit.q`, vendored from [TimeStored's
  qUnit](https://github.com/timestored/kdb/blob/master/qunit/qunit.q) (see also
  [the guide](https://www.timestored.com/kdb-guides/kdb-regression-unit-tests)),
  distributed under its own license (CC BY-NC-SA 2.0 UK -
  Attribution-NonCommercial-ShareAlike). That file's non-commercial term applies
  only to the test framework itself, not to `src/`; if you need to use this
  library commercially and want to keep a fully-commercial-license test setup,
  swap `tests/lib/qunit.q` for a permissively-licensed alternative (e.g.
  [q-unit](https://github.com/jasraj/q-unit) or
  [qtb2](https://github.com/ktsr42/qtb2)) - the `tests/lib/support.q` helper and
  all `test_*.q` files use only qUnit's documented
  `assertThat`/`assertEquals`/`assertTrue`/`assertFalse`/`assertError` API, so
  swapping frameworks should be a small, mechanical change.
- `lib/kdb-parquet/`, vendored from
  [DataIntellectTech/kdb-parquet](https://github.com/DataIntellectTech/kdb-parquet)
  at commit `e5cd641`. **Unlike the vendored files above, upstream has no
  LICENSE file at all** (verified against its full git tree, not just GitHub's
  auto-detection) - no explicit grant of rights exists, so ordinary copyright
  applies. It's vendored here regardless, at the repo owner's explicit choice;
  see `lib/kdb-parquet/NOTICE.md` for the full caveat plus what wasn't copied
  (the Arrow submodule) and why the checked-in `libPQ.so` (a Linux x86-64 build)
  can't be used as-is on this repo's primary macOS dev machine. Not loaded by
  `src/init.q` or anything else in this repo, and not verified working here -
  it's a native `2:` extension and would need a from-source rebuild for the
  target platform before it could be loaded. KDB-X also bundles its own official
  parquet module at `~/.kx/mod/kx/pq/`, worth checking as a licensed alternative
  first.
- `lib/torq/`, vendored from
  [DataIntellectTech/TorQ](https://github.com/DataIntellectTech/TorQ) at commit
  `a6cee6c`, distributed under the MIT License (full text at
  `lib/torq/LICENSE-torq`) - permissive and fine to combine with this
  repository's MIT code. TorQ is a full kdb+ production framework (process
  management, tickerplant/RDB/HDB/gateway, EOD lifecycle, monitoring) - this
  library is a stateless collection of pure pricing/risk/execution functions
  with no long-running processes for any of that machinery to manage, so nothing
  in `src/*.q` calls into it. Not loaded by `src/init.q` or anything else in
  this repo, and not verified working here; vendored for reference only, at the
  repo owner's explicit choice.
- `lib/qAutomatedTrading/`, vendored from
  [shahrzl/qAutomatedTrading](https://github.com/shahrzl/qAutomatedTrading) at
  commit `e508156`, distributed under the MIT License (full text at
  `lib/qAutomatedTrading/LICENSE-qAutomatedTrading`) - permissive and fine to
  combine with this repository's MIT code. A small automated-trading example
  (tick replay, order management, a portfolio/P&L tracker) - the part of
  interest here is `histTickData/timersvc.q`'s pattern for simulating a live
  feed from historical data: load a CSV into a table, then a `.z.ts` timer
  callback advances through it row-by-row on a fixed interval, publishing each
  row rather than generating the whole synthetic dataset upfront in one
  vectorized batch (uqf's own `scripts/*.q` examples do the latter). Not loaded
  by `src/init.q` or anything else in this repo; vendored for reference only.
- `lib/ml/`, vendored from [KxSystems/ml](https://github.com/KxSystems/ml) at
  release `4.1.0` (commit `5509fa6`), distributed under the Apache License 2.0
  (full text at `lib/ml/LICENSE-ml`) - permissive and fine to combine with this
  repository's MIT code; Apache 2.0 asks that its licence and any NOTICE travel
  with redistributed copies, which they do. KX's machine-learning toolkit
  (`.ml`), with its `nlp` (`.nlp`) and `automl` (`.automl`) modules. Four
  pickle-based example models were not copied - see `lib/ml/NOTICE.md`. Not
  loaded by `src/init.q` or anything else in this repo, and not verified working
  here: it needs a Python bridge (PyKX or embedPy) that PeachQ does not support.
  Vendored for reference only, at the repo owner's explicit choice.
- `lib/kx-dbmaint/`, vendored from
  [KxSystems/kdb](https://github.com/KxSystems/kdb) (`utils/dbmaint.q` and its
  `dbmaint.md`) at commit `1daebf7`, distributed under the Apache License 2.0
  (full text at `lib/kx-dbmaint/LICENSE-kx-kdb`) - permissive and fine to
  combine with this repository's MIT code; Apache 2.0 asks that its licence
  travel with redistributed copies, and release artifacts carry it explicitly
  (the one part of `lib/` they include). KX's partitioned-database maintenance
  utility, loaded by `scripts/gates/fill_hdb_partitions.q` for
  `uqs data hdb-check --fix` and `--fix-hdb`; provenance in
  `lib/kx-dbmaint/NOTICE.md`. Distinct from the unmodified fixture copy inside
  `lib/torq`, which nothing here uses.
- `lib/torq-finance-starter-pack/`, vendored from
  [DataIntellectTech/TorQ-Finance-Starter-Pack](https://github.com/DataIntellectTech/TorQ-Finance-Starter-Pack)
  at commit `50fcd5a`, distributed under the MIT License (full text at
  `lib/torq-finance-starter-pack/LICENSE-torq-finance-starter-pack`) -
  permissive and fine to combine with this repository's MIT code. The layered
  reference application built on top of `lib/torq` - a full example data-capture
  system (feed handlers, tickerplant, RDB, an HDB with two days of sample
  quote/trade data, gateway config) rather than the bare framework TorQ itself
  is. Same rationale as `lib/torq`: this library has no long-running processes
  for any of that machinery to manage, so nothing in `src/*.q` calls into it -
  vendored for reference only, at the repo owner's explicit choice.
  `src/etl/plant_tables.q` declares this tree's own tickerplant tables, covering
  some of the same shapes (quotes/trades) without the process/feed-handler layer
  this pulls in. It can still be started up and queried, though - the `uqs` CLI
  (a Typer CLI) and `uqs-mcp` (a FastMCP server exposing the same controls as
  MCP tools) bridge it with `lib/torq` (see
  [docs/guides/uqs.md](docs/guides/uqs.md)) so the two vendored trees can run as
  one demo without either being modified.
