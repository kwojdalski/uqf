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
uqs job new ws_quotes --kind connector --raw-table ws_quotes_raw \
    --outputs ws_quotes_book --schema "sym:symbol, bid:float, ask:float"
```

It writes:

  | File                                           | What it is                                                                                                                                                                 |
  | ---                                            | ---                                                                                                                                                                        |
  | `python/uqs/src/uqs/external/NAME_streamer.py` | the publisher process. Write **`batches()`**, which yields lists of records from the source. Connecting, typing the columns and publishing are done.                       |
  | `python/uqs/src/uqs/external/NAME_feed.py`     | its lifecycle, on the shared `DetachedProcess`, and a `FEED` that `uqs connector start\|stop\|restart\|status\|logs NAME` finds with no CLI edit                           |
  | `python/uqs/tests/test_NAME_streamer.py`       | a test that fails until it's written                                                                                                                                       |
  | the raw table and `src/etl/streaming/NAME.q`   | the q job subscribing to `--raw-table` and publishing `--outputs`, with its own failing test                                                                               |

Both tables start with the same `--schema`. The q job is where they come to
differ, as `databento1` folds 40 per-level columns into four vectors.

## Acknowledge after the publish

If the source can replay (a Kafka offset, a cursor, a sequence number), mark a
record consumed only **after** `publish` returns for it. `publish` sends through
`ACCEPT_Q` rather than a bare `.u.upd`: the segmented tickerplant hands an
update it refuses to its error log and returns, so a bare `.u.upd` answers
success for a batch that was never stored. `publish` raises instead, and
`to_columns` types each column as the raw table declares it, so a float column
of whole numbers is not sent as longs and refused. A crash between the publish
and the acknowledgement re-sends the record, which the q job can detect and drop
if every record carries a key unique at the source. The other order loses
records without a trace. See `kafka_streamer.py` for the worked case.

## Running it

Start the q job with the stack (or `uqs start NAME1`), then the publisher with
`uqs connector start NAME`. `uqs connector status NAME` shows whether it's
running and where its log is.

The scaffolded `NAME_feed.py` writes no host, port or credential of its own: it
imports them from `uqs.external.connection`, as every connector does, and lists
the shared credential option. The credential is the variable
`--tp-credential-env NAME` names, else `UQF_TP_CREDENTIAL`, else the starter
pack's labelled demo login ([connector
configuration](../reference/environment.md#connector-configuration)). It reaches
the streamer in its environment, read back with
`connection.credential_from_env()`, never as an argument: an argv is in `ps` and
in the connector's pid file. A start, or a dry run, refuses a raw table
`plant_tables.q` does not define.

`uqs job remove NAME` takes all of it out again: both Python files, their test,
the q job, and both tables when nothing else uses them.
