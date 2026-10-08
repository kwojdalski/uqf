# Upgrading without downtime

**Status: a design note, not built (#871).** `uqs deploy push --restart` still
stops the previous release before it starts the new one, on the same ports. What
is built is the measurement: every upgrade's report records its outage as
`downtime`. That covers when the previous release's processes began to stop,
when the new ones passed verification, and the seconds between. This page is the
design for removing that outage, written before the work, as
[`event-tape.md`](event-tape.md) was.

## Where the outage comes from

A push with `--restart` runs, in order:

1. **stop**: the previous release's profile, and the sidecar processes it ran;
2. **ports**: the new release's ports must be free, and they are the same ports,
   so nothing can start early;
3. **start**: the new release's profile;
4. **verify**: every process answers as itself, within `--verify-timeout`;
5. **activate**: `current` moves.

A client of `gateway1` or `rdb1` has nothing to talk to from step 1 until the
new processes come up in step 3. It is not told the new ones are trustworthy
until step 4. So the outage is stop plus start plus verification, and
verification alone can take up to `--verify-timeout` (180 s by default).
Streaming jobs replay what they missed when they restart, so no published row is
lost. But a query in that window fails.

## The switch

Run the new release beside the old one, then move clients to it:

1. **Start the new release on another base port.** Every process's port is
   `{KDBBASEPORT}+offset` (`scripts/processes/process_ports.csv`).
   `uqs start --port` moves the whole stack, and each runtime's span of ports
   already avoids every other's. The new release gets its own discovery,
   tickerplant, rdb and gateway on that base.
2. **Verify it there**, with the release's own verifier, as today.
3. **Move the clients** (below).
4. **Hand over each single writer** (below), in a fixed order.
5. **Stop the old release.** Then move `current` last, as today.

If anything fails before step 3, the old release is still serving and the new
one is stopped. That is a rollback with no outage at all.

## What must never run twice

Two copies of the stack read the same data directory (`UQS_DATA_ROOT`). Most of
it is safe to share, and four things are not. Each has exactly one writer, and
the switch has to hand it over rather than duplicate it.

  | Writer                                            | What it writes                                             | Why two break it                                                                                                         | Handover                                                                                                                                                                  |
  | ---                                               | ---                                                        | ---                                                                                                                      | ---                                                                                                                                                                       |
  | the tickerplant (`stp1`)                          | today's log under `tplogs/`                                | two writers to one log interleave records, and a replay of it is then wrong                                              | each release logs to a directory of its own release id; the new one's rdb replays the old one's log, read-only, then subscribes to its own                                |
  | end of day (`wdb1`, `sort1` and the sortworkers)  | the HDB partition for the day that just ended, and `sym`   | two writers append the same day twice, and both append to the HDB's `sym` file                                           | only the release that is `current` at end of day runs end of day: the switch is refused within a window of the roll, and the new release starts with end of day disabled  |
  | `uqs data hdb-check --fix` and the bootstrap fill | empty tables and null columns in old partitions, and `sym` | additive and idempotent, but they race end of day's writes to `sym`                                                      | already serialised by the deploy lock for a push; the new release's bootstrap must not fill while the old release's end of day runs                                       |
  | a bounded worker (`uqs backfill`)                 | partitions of past days, and `sym`                         | not started by a push, so the switch never starts one; one already running keeps writing through the old release's stack | the switch refuses while a worker holds its lock in the status directory, the same lock `uqs backfill` takes                                                              |

Shared and safe, because each already takes its own lock or is only read:

- the coverage, run and uptime ledgers in the status directory;
- the HDB as `hdb1` reads it;
- `shared/config/`.

`stream_health_<job>.txt` is not safe. It is one file per job, so two copies of
a job overwrite each other's. During the overlap, the soak (#869) and
`uqs summary` would read whichever wrote last. Each record already names the
process and pid that wrote it, so readers have to keep to the release they
check.

## Moving the clients

Three ways to give a client one stable address, from cheapest to most complete:

- **A stable gateway port.** Clients reach the stack through `gateway1`. If only
  the gateway kept a fixed port, a thin TCP proxy on that port could forward to
  whichever release's gateway is current. The switch then re-points the proxy:
  new connections reach the new release, and old ones finish where they are. The
  proxy holds no q state. Its cost is one more process to supervise.
- **Discovery hands out the new ports.** TorQ processes find each other through
  `discovery1`. A client that asks discovery for the gateway, instead of using a
  fixed port, follows the switch when the new release's discovery takes over the
  published discovery port. This needs every client to go through discovery, and
  most outside this stack do not.
- **The publishers move too.** External publishers find the tickerplant through
  discovery: cryptorust's recorders, the Databento and Kafka handlers. They must
  reconnect to the new tickerplant at the switch, or keep publishing into the
  old one after its subscribers have gone. Stopping the old tickerplant after
  the clients move forces the handover. Whether each publisher then reconnects
  by itself is unverified (see the open questions).

**Recommended:** the stable gateway port behind a proxy, plus stopping the old
tickerplant last to move the publishers. It needs no change to any client, and
it confines the new moving part to one process that holds no data.

## Order of the switch

1. New release up on its own base port, end of day disabled, verified, and
   soaked if `--soak` is given.
2. Refused here if the deploy lock is contested, a bounded worker holds its
   lock, or end of day is within its window.
3. The proxy points at the new gateway.
4. The new release's rdb has replayed the old tickerplant's log, and its own
   tickerplant is live.
5. The old tickerplant stops. Publishers reconnect to the new one through
   discovery.
6. End of day is enabled on the new release.
7. The old release's remaining processes stop, and `current` moves.

The outage this leaves is a publisher's reconnect in step 5. That is a delay in
publishing, not a failed query, and the measurement this note started from is
what will show it.

## Open questions

- Whether each external publisher reconnects through discovery by itself when
  its tickerplant stops, or has to be restarted.
- Whether the rdb's read-only replay of the old log, followed by its own
  subscription, can double-count rows published between the two. The tickerplant
  log's own sequence numbers are the obvious guard, and this needs a test
  against a real segmented tickerplant.
- Where the proxy runs, and who supervises it, on a server whose TorQ the site
  manages (`--torq-launcher`).
- Whether two stacks fit the licence's connection cap on KDB-X. PeachQ has no
  cap.
