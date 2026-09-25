# Scaffolding a feed

A feed invents its own rows on a timer. Nothing upstream of it exists in the
stack: the synthetic FX quotes, the order flow, the mock crypto recorder are all
feeds. If your rows come from a plant table, you want [etl.md](etl.md) instead.

## The command

```bash
uqs new-job pulsefeed --publishes pulse --columns "sym:symbol, px:float"
```

**There is no `--kind feed`.** A streaming job that subscribes to nothing is a
feed, and the scaffold derives that. Omitting `--subscribeto` is the whole
signal.

```
scaffold pulsefeed:
  create src/etl/streaming/pulsefeed.q (33 lines)
  append to scripts/processes/uqs_tables.q (3 lines)
  append to tests/q/test_stack_tables.q (1 line)
  append to scripts/processes/uqs_catalog.q (2 lines)
  create tests/q/test_pulsefeed.q (13 lines)
  append to tests/run_tests.q (1 line)
  note: describe pulse: replace its SCAFFOLDED line in scripts/processes/uqs_catalog.q ...
  note: implement .qsub.pulsefeed.on_timer, then replace the scaffolded test
  note: name pulsefeed1 in docs/architecture/stack.md - authored prose, checked by pytest
  note: add pulsefeed1 to a profile in python/uqs/src/uqs/model/profiles.py ...
```

## What you get

```q
\d .qsub.pulsefeed

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qstream.unwired `pulsefeed;

on_timer:{[]
    '"pulsefeed.on_timer: not implemented";
    }

\d .

.qstream.define[`pulsefeed;`procname`subscribeto`publishes`period`on_timer`note!(
    `pulsefeed1;
    `symbol$();                    / subscribes to nothing - this is what makes it a feed
    enlist `pulse;
    0D00:00:01;                    / the timer period, one second by default
    .qsub.pulsefeed.on_timer;
    "SCAFFOLDED: say why this exists, and why it does or does not start with the stack")];
```

**`on_timer` is niladic.** That is the difference from an etl, whose
`on_batch[t;x]` is handed the rows that arrived. A feed is handed nothing and
must produce everything, so whatever it needs between ticks --- a price level, a
sequence number, an RNG seed --- is state in its own namespace.

## The three rules a feed breaks most often

**Never publish `time`.** `.u.upd` stamps its own on receipt (invariant 1). A
`time` column in your output is silently overwritten, so a row you stamped in
the past arrives stamped now.

**Never call `.u.upd`.** Call `publish` in your own namespace. The runner wires
it to the tickerplant; a test wires it to a recorder and reads your output as
data. That is what lets the job be tested with no TorQ present, and
`scripts/gates/check_etl_layering.py` fails the build if anything under `src/`
reaches for `.qpipe`.

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

## The timer

`period` is `0D00:00:01` by default --- one second. It is a field in the
declaration, so changing it is a one-line edit, and a feed that should tick
faster than the plant can absorb is a decision to make deliberately rather than
discover.

## Then

Implement `on_timer`, replace `tests/q/test_pulsefeed.q` entirely, and add
`pulsefeed1` to a profile in
[`profiles.py`](../../python/uqs/src/uqs/model/profiles.py) --- a feed is
usually a *dependency* of something rather than a leaf, so it most often arrives
in a profile by being what a leaf reads, not by being named.
