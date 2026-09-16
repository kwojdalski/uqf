# The order/trade event tape

**Status:** contract decided 2026-09-16 (issue #46). Shape implemented in
`src/etl/sources/demo_events.q`; two of the six features it unblocks are
implemented in `src/market_data/microstructure.q`.

## Why this document exists

`quotes` in this library is periodic book **snapshots**. Seven features in
`docs/ROADMAP.md`'s "Explicitly out of scope" section were blocked on the
absence of a per-event tape, and the roadmap says plainly that adding one is
"a bigger scope decision than adding a function". This is that decision.

## Which tape this is — and which it is not

There are two different things people call an event tape, and they unblock
different features. This one is the **market microstructure tape**:

| | market microstructure tape (this one) | OMS order-lifecycle tape (not this) |
|---|---|---|
| whose events | every participant's, as the venue publishes them | only our own orders |
| actions | add / cancel / trade | new / ack / fill / cancel / reject |
| `side` on a trade | the **aggressor's** side | our side |
| unblocks | flow toxicity: signed flow, VPIN, arrival rate, large-trade and odd-lot ratios, cancel-to-trade | execution quality: fill rate, hit ratio, order-to-fill latency |

The ROADMAP's seven items are all in the left column — they need
`action`, an aggressor `side`, and a per-event `size`. An earlier comment on
#46 described the right column instead; that was wrong, and the shape below
is the left one.

The OMS tape is a separate, legitimate thing this tree does not have. Much
of execution quality is already covered without it: `hit_ratio_by` and
`fill_ratio` work off a `requests` table, and `markout_at_horizons` off
`trades`.

## The shape

```q
event_tape:([] time:`timestamp$();  sym:`symbol$();   action:`symbol$();
               side:`long$();       size:`float$();   price:`float$();
               order_id:`long$();   pip_factor:`long$())
```

A deliberate **superset of `trades`** (`time`,`sym`,`side`,`size`,
`pip_factor`), so a tape filtered to `action=`trade`` is trade-shaped and
the existing markout family keeps working on it. Two divergences, both
deliberate:

- **`price`, not `trade_price`.** An add or a cancel has a price and is not
  a trade; calling the column `trade_price` on an add event would be a name
  that lies. A caller feeding `markout_at_horizons` renames on the way in —
  one `xcol`, at the boundary, where the meaning actually changes.
- **`action` is a symbol**, `` `add`cancel`trade ``, not the ROADMAP's
  shorthand `{A,C,T}`. A single character is an **atom** in q, so a `"A"`
  column is a char vector whose elements do not compare against a
  one-element string — a trap this repository has hit repeatedly
  (see `docs/…` and `scripts/check_q_traps.py`). Symbols cost nothing, grep
  cleanly, and cannot silently fail to match.

### Column meanings, and the two that are easy to get wrong

| column | meaning |
|---|---|
| `time` | when the venue published the event. **Sorted ascending** — see the contract below |
| `sym` | the instrument |
| `action` | `` `add `` (order posted), `` `cancel `` (order pulled), `` `trade `` (execution) |
| `side` | **`1` buy, `-1` sell**, matching the convention used throughout this library. On a `` `trade `` this is the **aggressor's** side — the side that crossed the spread — not the resting order's. That is what makes signed flow mean "buying pressure" rather than "one of the two parties was a buyer, which is always true" |
| `size` | the event's own quantity, **unsigned**. Sign lives in `side`, so a caller multiplies. Storing a signed size would make `sum size` meaningless |
| `price` | the price the event happened at |
| `order_id` | links an `` `add `` to its later `` `cancel `` or `` `trade ``. Not needed by the counting ratios, needed by anything about order lifetime |
| `pip_factor` | carried from `trades` so pip conversions work unchanged |

### The sortedness contract

**The tape must be sorted ascending by `time`**, and functions over it say so
rather than sorting defensively. Two reasons, both learned elsewhere in this
tree:

- A rolling-window function over an unsorted tape returns a plausible wrong
  number rather than an error — the same class of silent defect
  `cross_book_at` and `markout_at_horizons` guard against by **rejecting**
  unsorted input instead of sorting it.
- Sorting inside each function would be O(n log n) per call on a tape that is
  already sorted by construction, for every call.

So: the ingesting worker publishes in event order, and a function given an
unsorted tape **throws**. `.qmicro.require_sorted_tape` is that check.

## What it unblocks — six of the ROADMAP's seven

| feature | status |
|---|---|
| `cancel_to_trade_ratio` (#23) | **implemented** — the acceptance test's own choice as the simplest |
| `signed_trade_flow` / cumulative delta (#19) | **implemented** — the canonical flow metric, and what `vpin` builds on |
| `trade_arrival_rate` (#26) | unblocked: a windowed count of `action=`trade`` |
| `large_trade_ratio` (#27) | unblocked: needs a size threshold decision |
| `odd_lot_trade_ratio` / `odd_lot_imbalance` (#20-21) | unblocked: needs an odd-lot size definition, which is venue-specific |
| `vpin` (#25) | unblocked: volume-bucketed signed flow, built on `signed_trade_flow` |

**`order_count_imbalance` (#18) is NOT unblocked by this**, and the ROADMAP
says why: it needs resting-order **counts per level** (`bid_ct_NN` /
`ask_ct_NN`) alongside `bid_sizes`/`ask_sizes` in `quotes`. That is a
snapshot-schema change, not an event-tape one. Six of seven, not seven.

## Ingestion

`demo_events` is a registered source under the E-12 contract
(`src/etl/sources/demo_events.q`). That declaration **is** the ingestion
contract: it is validated at registration, its fixture is checked against it
on every commit, and `.qsrc.fetch_window` windows it identically to a live
source.

**No dedicated worker file was written, and that is a finding rather than an
omission.** `demo_deals_backfill.q` is 238 lines of which exactly **four** are
worker-specific (`worker_name`, `source_name`, `dataset`, `width`); the other
234 are framework glue — `init`, `plan`, `fetch`, `publish`, `checkpoint`,
`run`, `do_window`, `cleanup`. A second worker would duplicate 234 lines to
change four, and the duplicate would then have to be kept in step by hand
every time the framework moved.

The right fix is a generic bounded-worker shell parameterised on those four
values, with both workers as thin declarations over it. That is a refactor of
the one working worker rather than an addition, so it is filed separately
instead of being done in the same change as a new feature.

The source is **synthetic**, per A-04: every column is one any venue's tape
would carry, and the fixture's values are invented. Nothing about a real
venue's schema or a bank's data is recoverable from it.
