# Horizon job

A horizon job evaluates each event **some time after it**: a markout scores a
fill against the market 1s, 10s or 60s later, so the fill waits in a queue until
the prices that judge it have arrived. The queue, the timer, readiness, eviction
and the history bound are the kind's (`src/etl/core/horizon.q`). Your file is
the scoring. `demo_markout` and `crypto_markout` are horizon jobs.

## Command

```bash
uqs job new fill_marks --kind horizon --subscribe-to executions,market_data \
    --horizon 0D00:01:00 --columns "sym:symbol, horizon:timespan, markout_bps:float"
```

`--subscribe-to` takes two tables: the events first, then the reference.
`--horizon` is how long an event waits, as a q timespan. `--columns` shapes the
new table it publishes, named after the job. `--profile` or `--unprofiled`
decides where it starts, as for any standing job.

## What you get

- `src/etl/streaming/NAME.q` holds:
  - the two inputs, each its table's whole plant schema, to narrow;
  - the output;
  - `score`, which throws until written;
  - the two-input `.qetl.transform`, with one typed example row of each;
  - the `.qetl.job.stream.at_horizons` declaration.
- The output table in `plant_tables.q`, a catalog line, a failing test, and doc
  stubs, as for any job.

The file loads, and the transform suite fails on `score` until you write it.

## Then

Write `score[events;reference]` and its example. Then declare what your events
need. Every option is off by default, and each is in [the declaration
reference](../reference/pipeline-declarations.md):

- **`event_time`:** measure maturity on when an event happened (`source_time`),
  not when the plant received it.
- **`ready_on` `reference` with `legs`:** wait until every reference key the
  event needs, such as a cross pair's legs, has an as-of anchor and has advanced
  through the horizon.
- **`lookback`:** a window that reaches back before the event, for a markout at
  -60s.
- **`identity`:** a redelivered event replaces the pending one, and one already
  scored is dropped.
- **`expire_after`:** an event still not ready by then is given up on, kept in
  `expired` with the reason.

Several horizon grids or output shapes over one tape, for example long and wide
markouts, are several horizon jobs. Each keeps its own state.
