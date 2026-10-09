# Real-time subscribers

An outside process can follow a table live, the way kdb+tick's real-time
subscriber (RTS) does: it subscribes to a tickerplant and receives every update
as it is published. The first such client is cryptorust's engine, which shows
`crypto_execution_quality` in its terminal.

```text
feed -> stp1 -> rdb1 / wdb1 ...
          \
           sctp1 -> outside subscriber (subscribe, then snapshot)
```

## Where to connect

**`sctp1`, the chained tickerplant, and never `stp1`.** `stp1` is on the
critical path and its connection budget is tight. A slow subscriber's unsent
messages queue on the publisher, so a slow GUI on `sctp1` grows `sctp1`'s queue,
not `stp1`'s. `stp1` and the data tiers do not accept the subscriber's login at
all.

  | Runtime  | `sctp1` (base + 15) | `gateway1` (base + 7) |
  | -------- | ------------------- | --------------------- |
  | `uqf`    | `6065`              | `6057`                |
  | `crypto` | `6265`              | `6257`                |
  | `fx`     | `6365`              | `6357`                |

`--port` moves both, as it moves every process.

**The login is `rts`.** `sctp1` is started with its own access list: the starter
pack's logins plus the users in
[`scripts/torqconfig/permissions/subscriber_users.csv`](../../scripts/torqconfig/permissions/subscriber_users.csv).
On the gateway, `rts` holds the `analyst` role, which may call
`.dataaccess.getdata` under the query policies and nothing else. Raw q is
refused.

## Subscribe

Call `.u.sub` synchronously, once per table, with `` ` `` for every symbol:

```q
h:hopen `:localhost:6265:rts:rts
h(".u.sub";`crypto_execution_quality;`)
/ (`crypto_execution_quality; empty table with the schema)
```

The reply is the table's name and an empty copy of it: its columns and types.
After that, `sctp1` pushes asynchronous messages:

```q
(`upd; `crypto_execution_quality; table)
```

`table` is a q table, already stamped with `time` by `stp1`. `sctp1` batches on
its 200 ms timer, so one message usually carries several rows.

## Snapshot

A subscriber that starts mid-session has no rows until the next update. So
subscribe first, then ask the gateway for recent rows, then apply the live
updates. A row can then appear in both. Rows carry no sequence number, so
de-duplicate on the table's identity columns (for `crypto_execution_quality`,
`fill_id` and `horizon`).

```q
g:hopen `:localhost:6257:rts:rts
g(`.dataaccess.getdata;`tablename`starttime`endtime!(`crypto_execution_quality;.z.p-0D00:05;.z.p))
```

The request is held to the table's row in
[`querypolicy.csv`](../../scripts/torqconfig/dataaccess/querypolicy.csv), as any
ordinary user's is. A table can be subscribed to from outside only if it is in
the catalog (`.qcat.describe`, see
[`uqs_catalog.q`](../../scripts/processes/uqs_catalog.q)) and has a policy row.
Today that is `crypto_execution_quality`.

## Never publish back

A subscriber must never publish what it receives onto a tickerplant. If it did,
every row would come back to it as a new update, round and round. cryptorust's
recorders publish into the plant, so its kdb subscriber marks received rows as
display data that can reach neither a recorder nor the trading path.

## Testing a subscriber on this machine

Check delivery with a real client: cryptorust's `kxkdb`, or `kola` from Python.
Hand-run q scripts did not reliably process asynchronous pushes here, and that
looks like a tickerplant that publishes nothing. On the tickerplant,
`.stpps.subrequestall` lists each table's subscribed handles and
`.stplg.msgcount` counts what it received.
