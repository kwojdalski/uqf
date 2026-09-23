# The cryptorust recorders

cryptorust's kdb recorders publish real venue books and fills onto this
stack's tickerplant; `cryptomock1` stands in for them.
Starting, stopping and inspecting the stack as a whole is in
[running the uqf stack](../guides/uqf-stack.md).

## The market-data recorder

`uqf-stack crypto start`/`stop`/`status` (a nested command group, not
flat `crypto-*` commands - these don't drive `torq.sh`/`process.csv` at
all, a distinct enough concern to read as its own namespace) are a proof
of concept that this demo's kdb+ infra isn't TorQ/q-specific: anything
that can speak kdb+ IPC can publish onto the same tickerplant alongside
the q feeds ([synthetic feeds](synthetic-feeds.md)), including a process written in an entirely different
language, in a completely separate project. Specifically, they build and
launch a sibling checkout of [cryptorust](https://github.com/kwojdalski/cryptorust)
(a Rust crypto trading system - see `~/github_projects/cryptorust`) - its
own `kdb-market-data-recorder` binary (`src/bin/kdb_market_data_recorder.rs`
there) connects live venue order books (Binance, Bybit, ...) straight to
this demo's `stp1` over raw IPC (via the [`kxkdb`](https://github.com/KxSystems/kxkdb)
crate) and calls `.u.upd` directly - the exact same wire protocol
the q feeds use, just from Rust instead of q -
with its own reconnect-on-drop loop, so a `stp1` restart doesn't take it
down permanently.

```
uqf-stack crypto start                    # binance_spot, BTC-USDT/ETH-USDT by default
uqf-stack crypto start --venues binance_spot,bybit_spot --symbols BTC-USDT
uqf-stack crypto status
uqf-stack crypto stop
```

Rows land in `crypto_book` (`time`/`venue`/`sym`/`bid_prices`/`bid_sizes`/
`ask_prices`/`ask_sizes` - see its definition in `scripts/processes/uqf_stack_tables.q`),
flowing through `rdb1`/`wdb1`/`hdb` exactly like `quote`/`trade`/`quotes`/
`wide_book`:

```
uqf-stack query "select from crypto_book" --port <rdb1's port>
```

Requires a `~/github_projects/cryptorust` checkout (override the path via
`$CRYPTORUST_ROOT`) with a Rust toolchain on `PATH` - `crypto start` runs
`cargo build` itself the first time, which can take a while. It also
reuses this demo's own `feed:pass` credential (see `appconfig/passwords/
feed.txt`) to authenticate against `stp1`'s access-list, same as any other
feed process here - no separate cryptorust-side credential to set up.

## The fills recorder: simulated and real fills, two tables

`uqf-stack crypto fills-start`/`fills-stop`/`fills-status` are a separate
proof of concept, alongside the book recorder above: cryptorust's own
`kdb-fills-recorder` binary (`src/bin/kdb_fills_recorder.rs`) polls an
*already-running* cryptorust service's OMS over its own IPC unix socket
(default `/tmp/beacon.sock`) and republishes new fills onto this demo's
`stp1`, the bridge role the `posbook` and `markout` streaming jobs play
inside this repo's own uqf stack - except this one bridges two entirely
different IPC protocols (cryptorust's JSON-RPC and kdb+'s wire protocol)
rather than two kdb+ processes. It polls two independent methods each
tick, into two separate tables:

- **`get_recent_fills` -> `crypto_sim_fills`** - the market-making bot's
  *simulated* (paper) fill model: a probabilistic fill simulation run
  against live market data, not a confirmed order that actually executed
  on an exchange. Traced precisely in that binary's own doc header.
- **`get_recent_real_fills` -> `crypto_trades`** - real, confirmed
  exchange fills (cryptorust's `services::trading::execution::Fill`, with
  `venue`/`symbol`/`exchange_fill_id`/`fee`), fed from `Oms::
  subscribe_fills()` on the cryptorust side. This method didn't exist
  until it was added specifically to close this gap - see
  `connectors/ipc/ipc.rs`'s real-fill listener task and
  `connectors/ipc/cycle.rs`'s handler on the cryptorust side.

Both are empty/unavailable whenever the polled service has no OMS
attached or no fills have happened yet - not an error, just nothing to
publish that tick.

Unlike `crypto start`, this doesn't launch its own exchange connectors -
it needs a cryptorust service already running (e.g. `helm start beacon`
inside the cryptorust checkout), with its OMS/trading cycle active before
either method returns anything.

```
uqf-stack crypto fills-start                       # polls /tmp/beacon.sock, tags sim rows BTC-USDT
uqf-stack crypto fills-start --oms-socket-path /tmp/beacon.sock --symbol ETH-USDT
uqf-stack crypto fills-status
uqf-stack crypto fills-stop
```

Rows land in `crypto_sim_fills` (`time`/`sym`/`side`/`trade_price`/`size`/
`realized_delta_pnl` - see its definition in `scripts/processes/uqf_stack_tables.q`)
and `crypto_trades` (`time`/`sym`/`venue`/`side`/`trade_price`/`size`/
`fee`/`fee_currency`/`exchange_fill_id` - defined in `scripts/processes/uqf_stack_tables.q`):

```
uqf-stack query "select from crypto_sim_fills" --port <rdb1's port>
uqf-stack query "select from crypto_trades" --port <rdb1's port>
```
