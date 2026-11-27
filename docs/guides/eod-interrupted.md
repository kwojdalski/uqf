# An end of day interrupted in `.Q.gc[]`

What is left behind when the RDB is stopped while it is collecting garbage at
end of day, and what to do about it. In short: no memory is lost, whichever way
it is stopped. What an interrupt can leave wrong is the RDB's end-of-day
*state*, and only a SIGINT does that.

## What the collection frees

Nothing the process still needs. Clearing a table returns its memory to q's own
pool at once; `.Q.gc[]` only hands that pool back to the operating system. On a
30-million-row table:

  | Step                    | Used    | Heap    |
  | ----------------------- | ------- | ------- |
  | loaded                  | 1280 MB | 1600 MB |
  | cleared (`0#`)          | 0 MB    | 1600 MB |
  | after `.Q.gc[]` (49 ms) | 0 MB    | 64 MB   |

So memory a collection has not released yet is memory q holds free and reuses
for the next rows it receives. It is not leaked, and a process that exits gives
all of it back.

A collection over many small objects is slow: 4 million nested rows took 3 to 6
seconds to collect, where the flat table above took 49 ms.

## Where it runs

The RDB here runs with `reloadenabled:1b`
(`lib/torq-finance-starter-pack/appconfig/settings/rdb.q`). At `.u.end` it saves
nothing: it records each table's row count in `.rdb.eodtabcount` and returns.
The WDB writes the partition and then sends the RDB `reload[date]`
asynchronously, which runs (`lib/torq/code/processes/rdb.q`):

1. `dropfirstnrows` on each table: yesterday's rows go, today's stay;
2. `rmdtfromgetpar[date]`;
3. the attributes are reapplied;
4. `.gc.run[]`, which calls `.Q.gc[]`;
5. `eodtabcount[tabs]:0`;
6. `restoretimeout[]`, undoing the `\T 0` set a minute before the roll;
7. `Finished reloading RDB` is logged, and the WDB is told the outcome.

When the collection starts, yesterday's rows are on disk and out of the RDB.
Only steps 5 to 7 are still to come.

## What each signal does

No signal cuts `.Q.gc[]` short except SIGKILL. q acts on the others once the
collection has returned, with the heap fully released.

  | Signal                         | Steps 5 to 7                   | Process                  | The WDB is told                 |
  | ---                            | ---                            | ---                      | ---                             |
  | SIGINT (`kill -INT`, Ctrl-C)   | skipped: `reload` ends `'stop` | stays up                 | `reload failed with error stop` |
  | SIGTERM (`uqs stop`)           | run                            | exits when `reload` ends | `reloaded successfully`         |
  | SIGKILL (`uqs stop --kill`)    | not reached                    | gone at once             | nothing                         |

`uqs stop` waits 15 seconds, so over a longer collection it reports the RDB as
still running; the RDB exits by itself when `reload` returns. After a SIGKILL
the WDB releases the end of day when `eodwaittime` (10 seconds) runs out.

## After a SIGINT

The RDB keeps serving, with two things wrong:

- `.rdb.eodtabcount` still holds yesterday's counts. A second `reload` drops
  that many rows from **today's** data: in the test, the five rows received
  since the roll went.
- `\T` is still 0, so no query times out.

Either restart the RDB, or put both right on it
([`connecting.md`](connecting.md)):

```q
.rdb.eodtabcount[.rdb.subtables except .rdb.ignorelist]:0
.rdb.restoretimeout[]
```

After these a second `reload` drops nothing, and `\T` is back to its value.

## Restarting the RDB

A new RDB subscribes and replays the tickerplant's current log (`replaylog:1b`).
After the roll that is today's log, so it holds today's rows and nothing else;
yesterday's are in the HDB. It has no `.rdb.eodtabcount` until its own first
`.u.end`, so a `reload` that reaches it fails with `'eodtabcount` before it
drops anything.

## With `reloadenabled` off

Read from the code, not run. The RDB then saves at `.u.end` itself, and
`.rdb.savetable` collects after each table, smallest first. A SIGINT in one of
those collections leaves the tables after it unsaved and uncleared, mixing with
the next day's rows, and skips `restoretimeout`, the attributes,
`.save.postreplay` and the HDB notification. Their rows are in the day's
tickerplant log.

## How this was measured

On KDB-X 5.0 (2026.07.23), with TorQ's `reload`, `dropfirstnrows`,
`restoretimeout` and `.gc.run` loaded unchanged into a bare q process, a second
q process sending the WDB's own `reloadfunc`, and the signal sent 0.3 seconds
after `Starting garbage collect` was logged. It was not run on a live stack. The
WDB's own collections, after each save and sort, are not covered.
