# Feed

A feed invents its own rows on a timer. Nothing upstream of it exists in the
stack: the synthetic FX quotes, the order flow, the mock crypto recorder are all
feeds. If your rows come from a plant table, you want [etl.md](etl.md) instead.

## Command

```bash
uqs job new pulsefeed --publishes pulse --columns "sym:symbol, px:float"
```

**There is no `--kind feed`.** A streaming job that subscribes to nothing is a
feed, and the scaffold derives that. Omitting `--subscribe-to` is the whole
signal.

```
scaffold pulsefeed:
  create src/etl/streaming/pulsefeed.q (33 lines)
  append to src/etl/plant_tables.q (3 lines)
  append to tests/q/test_stack_tables.q (1 line)
  append to scripts/processes/uqs_catalog.q (2 lines)
  create tests/q/test_pulsefeed.q (13 lines)
  append to tests/run_tests.q (1 line)
  note: describe pulse: replace its SCAFFOLDED line in scripts/processes/uqs_catalog.q ...
  note: implement .qpipe.job.pulsefeed.on_timer, then replace the scaffolded test
  note: name pulsefeed1 in docs/architecture/stack.md - authored prose, checked by pytest
  note: add pulsefeed1 to a profile in python/uqs/src/uqs/model/profiles.py ...
```

## What you get

```q
\d .qpipe.job.pulsefeed

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `pulsefeed;

on_timer:{[]
    '"pulsefeed.on_timer: not implemented";
    }

\d .

.qetl.job.stream.define[`pulsefeed;`procname`subscribe_to`publishes`period`on_timer`note!(
    `pulsefeed1;
    `symbol$();                    / subscribes to nothing - this is what makes it a feed
    enlist `pulse;
    0D00:00:01;                    / the timer period, one second by default
    .qpipe.job.pulsefeed.on_timer;
    "SCAFFOLDED: say why this exists, and why it does or does not start with the stack")];
```

**`on_timer` is niladic.** That is the difference from an etl, whose
`on_batch[t;x]` is handed the rows that arrived. A feed is handed nothing and
must produce everything, so whatever it needs between ticks --- a price level, a
sequence number, an RNG seed --- is state in its own namespace.

## Rules

**Never publish `time`.** `.u.upd` stamps its own on receipt (invariant 1). A
`time` column in your output is silently overwritten, so a row you stamped in
the past arrives stamped now.

**Never call `.u.upd`.** Call `publish` in your own namespace. The runner wires
it to the tickerplant; a test wires it to a recorder and reads your output as
data. That is what lets the job be tested with no TorQ present, and
`scripts/gates/check_etl_layering.py` fails the build if anything under `src/`
reaches for `.qtorq`.

**Keep the randomness in `on_timer`, and nowhere else.** This is the convention
every feed in the tree follows, and it is not about seeding --- it is about
where `rand` is allowed to appear. The row-building function takes the draws as
*arguments*, so it is deterministic and its output can be asserted; `on_timer`
is the only place that draws.

`crypto_mock.q` states it plainly on its fill decision: *"The draws are
ARGUMENTS --- (at touch?; bid uniform; ask uniform; bid fraction; ask fraction) ---
so this function is deterministic and its decisions can be asserted; on_timer
supplies the randomness."* `fx_trades_feed.q` and `fx_orders_feed.q` say the
same about theirs.

The payoff is the test. A feed whose logic is a pure function of its draws can
be asserted exactly; one that calls `rand` inside its row builder can only be
checked for shape.

## Timer

`period` is `0D00:00:01` by default --- one second --- and `--period` sets it:
`--period 0D00:00:00.500` for twice a second. It is a field in the declaration,
so changing it later is a one-line edit, and a feed that should tick faster than
the plant can absorb is a decision to make deliberately rather than discover.

## Polling a source

A feed that ticks makes rows up; a feed that polls fetches them from somewhere
else. Scaffold the second with `--poll`:

```
uqs job new rates_feed --publishes rates --columns "sym:symbol, mid:float" --poll
uqs job new book_feed --publishes books --columns "sym:symbol, px:float" \
    --poll --cursor-fields time,securityId,priceBookType
```

Instead of `on_timer` it writes `fetch`, `normalize` and `next_cursor`, each
throwing until written, and declares them as `poll`. `--cursor-fields` makes the
cursor the row's position and writes the `load`, `save` and `advances` trio and
`next_cursor` for it. `--poll` refuses a job that subscribes: the plant feeds
that one, so there is nothing to fetch.

A feed that fetches from outside the stack - a REST endpoint, another database -
declares its steps as `poll` instead of writing `on_timer`, and gets a timer
built from them that fetches the page after its cursor, normalizes it, publishes
it and then advances the cursor:

```q
.qetl.job.stream.define[`vectorize2;`procname`subscribe_to`publishes`period`poll!(
    `vectorize2_1;`symbol$();enlist `wide_book;0D00:00:05;
    `fetch`normalize`next_cursor`source!(
        fetch;                         / [cursor] -> the page after it
        normalize;                     / [page] -> what is published
        {[page] last page`ts};         / [page] -> the cursor that acknowledges it
        `vectorize_source))];          / optional: lets a preview say live or fixture
```

`normalize` returns a table when the feed publishes one table, else a dictionary
of table -> rows. An optional `close` releases whatever `fetch` opened, and
`cursor` names the cursor file when it is not the job's name.

A cursor that is not a timestamp declares how it is kept: `load`, `save` and
`advances`, all three or none. A source that pages through rows sharing a
timestamp needs the row's position, and the stock answer keeps every field:

```q
`load`save`advances!(
    .qetl.job.continuous.load_cursor_value;                     / a q value, types intact
    .qetl.job.continuous.save_cursor_value;
    .qetl.job.continuous.lexically_after[`time`securityId`priceBookType])
```

with `next_cursor` returning `` `time`securityId`priceBookType#last page``. The
timer checks the cursor advances before it publishes, and saves it only after
the publish succeeds; the preview runs the same check and saves nothing.

Both stock cursors survive a crash mid-save. The new cursor is written beside
the old one and renamed into place, and the old one is kept as `.bak`. A cursor
file that will not parse falls back to `.bak` - one page fetched again - and is
refused when that is unreadable too. It is never read as "no cursor", which a
feed would take as its first run and republish everything. A feed with its own
`load` and `save` should write through `.qetl.job.bounded.state.durable_lines`
or `durable_set` to get the same.

Declaring the steps is what makes the feed previewable:

```
uqs stream preview vectorize2 --sample 10
```

fetches one real page and shows what it would publish and where the cursor would
move, holding each table against the plant's. Nothing is published, and the
cursor is read, never written - so it is safe beside the running feed. It exits
1 when the output would not fit the plant or the cursor would not advance. A
feed written as one `on_timer` cannot be previewed: running it would publish.
Nor can a job that subscribes, whose input comes from the plant.

`--trace` shows the queries the page's fetch sends: each one before it goes, as
a block, with the job and the cursor it fetched after, and again with its rows
and milliseconds or its error. That includes the fetch that hung, if the preview
times out. Only a query sent through a traced path appears. For a polling feed
that's `.qetl.source.ipc_call[h;{[c] select ... where time>c};enlist cursor]`,
the cursor-shaped `.qetl.source.ipc`. A fetch that writes `h(...)` itself still
works but stays invisible, so it isn't allowed: `uqs install-jobs` refuses a
sidecar with such a line, naming it, and the tree's own feeds are tested for it.
A line that must stay direct says so with `/ untraced: <why>`.

```
uqs stream preview vectorize2 --trace
```

### Previewing recent data

The saved cursor can be far behind, and a first run's lookback wider than you
want while troubleshooting. `--last` previews a recent window instead of the
next page:

```
uqs stream preview vectorize2 --last 30s --sample 1 --trace
```

The window is `[now - 30s, now)`, with `now` one UTC instant captured once.
`--last` takes a positive whole number and `ms`, `s`, `m`, `h` or `d`. The
preview builds a temporary starting cursor for the window's start, fetches after
it, and keeps the rows inside the window. It judges each row with the feed's own
`next_cursor` and `advances`, so a compound cursor's tie-breakers decide the
edges the way they decide a run's progress. The saved cursor is not read or
written, nothing is published, and the live poll's lookback and period are
unchanged. The output says it is a recent-data sample, not the running job's
next page. It shows the window, the temporary cursor, the rows fetched and kept,
and the page limit; `--trace` puts the window and the limit on every query line.

The starting cursor is the feed's own. A timestamp cursor needs nothing: it
starts 1ns before the instant. A feed with its own `load`, `save` and `advances`
must declare `start_cursor`, `[instant] -> the cursor just before instant`, in
its `poll`. Without it, `--last` refuses the feed, because only the feed knows
its tie-breakers' types. `uqs job new --poll --cursor-fields` writes the step
for you to fill in:

```q
start_cursor:{[instant] `time`securityId`priceBookType!(instant;`;0N)}
```

A feed may also declare `page_limit`, the most rows one fetch returns. A recent
preview reports it, and says so when the page was full: the window may then hold
rows past the ones shown. `--last` narrows the data asked for. It does not make
the query fast; filtering and partition pruning are the source adapter's.

"Fits the plant" is `.qetl.plant.problems`, and the timer applies the same check
before it publishes: a page that does not fit is refused whole, with nothing
published and the cursor left where it was. Each table's columns, and their
order, must match the plant's. Each plain column's type must match exactly. Each
list column - one the schema declares as `()`, like a price ladder - has every
row checked against the element type `src/etl/plant_tables.q` declares for it
beside the table:

```q
mkt_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); ask_prices:())
nested[`mkt_orderbook;`bid_prices`ask_prices!"FF"];
```

`"F"` is float vectors, `"C"` strings, `"S"` symbol lists, `"P"` timestamp
lists, and `" "` a column that holds any value on purpose. A table with an
undeclared list column fails `test_plant_tables.q`, and a page for it is
refused, because an empty `()` has no type of its own to compare against. An
empty page passes its list columns, since it has no rows to hold the wrong
thing; its columns are still checked.

## Then

Implement `on_timer`, replace `tests/q/test_pulsefeed.q` entirely, and add
`pulsefeed1` to a profile in
[`profiles.py`](../../python/uqs/src/uqs/model/profiles.py) (or scaffold with
`--profile NAME`) --- a feed is usually a *dependency* of something rather than
a leaf, so it most often arrives in a profile by being what a leaf reads, not by
being named.
