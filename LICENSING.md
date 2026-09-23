# Licensing

Everything in this repository is MIT licensed (see [`LICENSE`](LICENSE)), **except**
the six vendored dependencies below:

- `tests/lib/qunit.q`, vendored from
  [TimeStored's qUnit](https://github.com/timestored/kdb/blob/master/qunit/qunit.q)
  (see also [the guide](https://www.timestored.com/kdb-guides/kdb-regression-unit-tests)),
  distributed under its own license (CC BY-NC-SA 2.0 UK -
  Attribution-NonCommercial-ShareAlike). That file's non-commercial term
  applies only to the test framework itself, not to `src/`; if you need to
  use this library commercially and want to keep a fully-commercial-license
  test setup, swap `tests/lib/qunit.q` for a permissively-licensed
  alternative (e.g. [q-unit](https://github.com/jasraj/q-unit) or
  [qtb2](https://github.com/ktsr42/qtb2)) - the `tests/lib/testutil.q` helper
  and all `test_*.q` files use only qUnit's documented
  `assertThat`/`assertEquals`/`assertTrue`/`assertFalse`/`assertError` API,
  so swapping frameworks should be a small, mechanical change.
- `lib/log4q.q`, vendored from
  [prodrive11's log4q](https://github.com/prodrive11/log4q/blob/master/log4q.q),
  distributed under the Apache License 2.0 (full text at
  `lib/LICENSE-log4q`) - permissive and fine to combine with this
  repository's MIT code. Not loaded by `src/init.q` (nothing in `src/`
  depends on it); load it explicitly (`\l lib/log4q.q`) from whichever
  script wants logging. **Known gap:** one of its internal helper
  functions (`.log4q.l`, used to render the log message pattern) relies on
  a variable being assigned mid-expression and read earlier in that same
  expression - valid, standard q right-to-left evaluation, and confirmed
  working under KDB-X.
- `lib/kdb-parquet/`, vendored from
  [DataIntellectTech/kdb-parquet](https://github.com/DataIntellectTech/kdb-parquet)
  at commit `e5cd641`. **Unlike the vendored files above, upstream has no
  LICENSE file at all** (verified against its full git tree, not just
  GitHub's auto-detection) - no explicit grant of rights exists, so
  ordinary copyright applies. It's vendored here regardless, at the repo
  owner's explicit choice; see `lib/kdb-parquet/NOTICE.md` for the full
  caveat plus what wasn't copied (the Arrow submodule) and why the
  checked-in `libPQ.so` (a Linux x86-64 build) can't be used as-is on this
  repo's primary macOS dev machine. Not loaded by `src/init.q` or anything
  else in this repo, and not verified working here - it's a native `2:`
  extension and would need a from-source rebuild for the target platform
  before it could be loaded. KDB-X also bundles its own official parquet
  module at
  `~/.kx/mod/kx/pq/`, worth checking as a licensed alternative first.
- `lib/torq/`, vendored from [DataIntellectTech/TorQ](https://github.com/DataIntellectTech/TorQ)
  at commit `a6cee6c`, distributed under the MIT License (full text at
  `lib/torq/LICENSE-torq`) - permissive and fine to combine with this
  repository's MIT code. TorQ is a full kdb+ production framework (process
  management, tickerplant/RDB/HDB/gateway, EOD lifecycle, monitoring) -
  this library is a stateless collection of pure pricing/risk/execution
  functions with no long-running processes for any of that machinery to
  manage, so nothing in `src/*.q` calls into it. Not loaded by `src/init.q`
  or anything else in this repo, and not verified working here; vendored
  for reference only, at the repo owner's explicit choice.
- `lib/qAutomatedTrading/`, vendored from
  [shahrzl/qAutomatedTrading](https://github.com/shahrzl/qAutomatedTrading)
  at commit `e508156`, distributed under the MIT License (full text at
  `lib/qAutomatedTrading/LICENSE-qAutomatedTrading`) - permissive and fine
  to combine with this repository's MIT code. A small automated-trading
  example (tick replay, order management, a portfolio/P&L tracker) - the
  part of interest here is `histTickData/timersvc.q`'s pattern for
  simulating a live feed from historical data: load a CSV into a table,
  then a `.z.ts` timer callback advances through it row-by-row on a fixed
  interval, publishing each row rather than generating the whole synthetic
  dataset upfront in one vectorized batch (uqf's own `scripts/*.q`
  examples do the latter). Not loaded by `src/init.q` or anything else in
  this repo; vendored for reference only.
- `lib/torq-finance-starter-pack/`, vendored from
  [DataIntellectTech/TorQ-Finance-Starter-Pack](https://github.com/DataIntellectTech/TorQ-Finance-Starter-Pack)
  at commit `50fcd5a`, distributed under the MIT License (full text at
  `lib/torq-finance-starter-pack/LICENSE-torq-finance-starter-pack`) -
  permissive and fine to combine with this repository's MIT code. The
  layered reference application built on top of `lib/torq` - a full
  example data-capture system (feed handlers, tickerplant, RDB, an HDB
  with two days of sample quote/trade data, gateway config) rather than
  the bare framework TorQ itself is. Same rationale as `lib/torq`: this
  library has no long-running processes for any of that machinery to
  manage, so nothing in `src/*.q` calls into it - vendored for reference
  only, at the repo owner's explicit choice. `env/`'s own table schemas
  cover some of the same shapes (quotes/trades) at a much lighter weight,
  without the process/feed-handler layer this pulls in. It can still be
  started up and queried, though - the `uqs` CLI
  (a Typer CLI) and `uqs_mcp.py` (a FastMCP server exposing the same
  controls as MCP tools) bridge it with `lib/torq` (see [docs/guides/uqs.md](docs/guides/uqs.md))
  so the two vendored trees can run as one demo without either being
  modified.
