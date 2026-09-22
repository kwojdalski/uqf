# Roadmap: order-book / microstructure feature candidates

What is left to build in `src/market_data/microstructure.q`. Everything else
this file once listed — two tiers of snapshot and rolling features, and six of
the seven the event tape unblocked — is implemented, tested in
`tests/q/test_microstructure.q`, and documented where it lives: the qDoc block
on each function, collected in [`man.q`](man.q). A candidate table that
restates a built function is one more place for the signature to go stale, so
built candidates are struck from here rather than marked done.

**Provenance.** The formulas were mined from an external equity LOB deep-RL
feature catalog built on Databento MBP-10 data, then re-expressed against
uqf's shape. This tree has periodic book *snapshots*
(`` `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes ``, level-0-first
vectors per row — see `forwards.q`'s `require_quotes_cols`) plus an event tape
(`docs/architecture/event-tape.md`), not an L3 order-by-order feed. The three
items below survive that translation and are not built.

Conventions follow the rest of the library: `pip_factor` (10000 / 100 for
JPY), `side` (1 buy / -1 sell), snake_case, one qDoc block per function,
tested on KDB-X.

## Open

Each is blocked on one decision, not on effort.

| Candidate | Blocked on |
|---|---|
| `order_count_imbalance` (#18) | A **snapshot-schema change**: it needs resting-order *counts* per level (`bid_ct_NN`/`ask_ct_NN`) beside `bid_sizes`/`ask_sizes` in `quotes`. The event tape does nothing for this — it is the one item the tape did not unblock. |
| `odd_lot_trade_ratio` (#20) | An odd-lot size, which is venue-specific. In FX there is no round-lot convention at all, so settle whether this is meaningful for this tree's instruments before implementing, not after. |
| `odd_lot_imbalance` (#21) | The same decision as #20. |

Adding a genuine order-by-order feed would be a bigger scope decision than
adding a function, and only #18 would be affected by it.

## The tape is a superset of `trades`

Worth knowing before building anything here: the event tape is deliberately a
superset of the `trades` shape `` `time`sym`side`size`pip_factor `` that
`markout_at_horizons` already consumes, so a tape filtered to
`` action=`trade `` is trade-shaped and the existing markout family works on
it unchanged. The one divergence is `price` rather than `trade_price`, because
an add or a cancel has a price and is not a trade — see
[`architecture/event-tape.md`](architecture/event-tape.md).
