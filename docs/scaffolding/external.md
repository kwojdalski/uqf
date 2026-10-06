# External

An external feed brings in data a q process can't subscribe to itself: a Kafka
topic, a vendor's websocket, a broker's API. It's two halves, as [Databento and
Kafka](../services/README.md#external-sources) are:

- **a Python publisher** outside q, which holds the subscription and publishes
  the raw records onto the tickerplant;
- **a q streaming job** that subscribes to that raw table and reshapes it into
  the table the rest of the stack reads.

## Command

```bash
uqs job new ws_quotes --kind external --raw-table ws_quotes_raw \
    --publishes ws_quotes_book --columns "sym:symbol, bid:float, ask:float"
```

It writes:

  | File                                           | What it is                                                                                                                                            |
  | ---                                            | ---                                                                                                                                                   |
  | `python/uqs/src/uqs/external/NAME_streamer.py` | the publisher process. Write **`batches()`**, which yields lists of records from the source. Connecting, converting to columns and `.u.upd` are done. |
  | `python/uqs/src/uqs/external/NAME_feed.py`     | its lifecycle, on the shared `DetachedProcess`, and a `FEED` that `uqs feed start\|stop\|status NAME` finds with no CLI edit                          |
  | `python/uqs/tests/test_NAME_streamer.py`       | a test that fails until it's written                                                                                                                  |
  | the raw table and `src/etl/streaming/NAME.q`   | the q job subscribing to `--raw-table` and publishing `--publishes`, with its own failing test                                                        |

Both tables start with the same `--columns`. The q job is where they come to
differ, as `databento1` folds 40 per-level columns into four vectors.

## Acknowledge after the publish

If the source can replay (a Kafka offset, a cursor, a sequence number), mark a
record consumed only **after** `.u.upd` returns for it. A crash in between then
re-sends it, which the q job can detect and drop if every record carries a key
unique at the source. The other order loses records without a trace. See
`kafka_streamer.py` for the worked case.

## Running it

Start the q job with the stack (or `uqs start NAME1`), then the publisher with
`uqs feed start NAME`. `uqs feed status NAME` shows whether it's running and
where its log is.

`uqs job remove NAME` takes all of it out again: both Python files, their test,
the q job, and both tables when nothing else uses them.
