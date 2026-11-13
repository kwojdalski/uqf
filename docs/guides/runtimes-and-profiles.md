# Runtimes, profiles and status

This page tells you which processes `uqs start` starts, how to start a different
set, how to read `uqs status`, which port is which, and how to change one
process's settings.

## What actually starts

A plain `uqs start` starts the processes marked `startwithall=1`. In the default
runtime these are the vendored starter pack's rows plus uqf's own pipelines.
`uqs process list` shows every declared process, its port and `startwithall`.

`reporter1`, `filealerter1`, `dqc1`/`dqcdb1` and `dqe1`/`dqedb1` stay off
because of the KDB-X community edition's connection limits. `killtick` and
`tpreplay1` are on-demand utilities.
[`data replay`](hdb.md#replaying-a-tickerplant-log) starts `tpreplay1` for one
replay.

After the launch, `start` and `restart` wait `--grace` (3 seconds by default)
and check every process they started. A process that died while loading fails
the command, which names it and prints the last lines of its log.

### Runtimes

A runtime is a whole stack: its processes, tables, config layers, data directory
and base port. `uqf` is the default. `torq` is the starter pack as it ships,
with nothing of this tree's added.

```bash
uqs --runtime torq start      # or UQS_RUNTIME=torq uqs start
uqs runtime list             # each one: processes, tables, layers, data, ports, what is up
uqs runtime diff uqf torq     # what one has that the other lacks
uqs --runtime crypto runtime prepare   # install the bundles it declares
```

  |                | `uqf` (default)                                                         | `torq`                                                            | `peachq`            | `crypto`                                                                                        | `fx`                                                                                        | `peachq-etl`                                                                        |
  | ---            | ---                                                                     | ---                                                               | ---                 | ---                                                                                             | ---                                                                                         | ---                                                                                 |
  | processes      | the vendored rows, with overlays, plus every pipeline                   | the vendored `process.csv`, unchanged: `feed1` on, `monitor1` off | as `torq`           | the vendored rows, with overlays, plus the `crypto` profile's pipelines and what they depend on | the vendored rows, with overlays, plus the `fx` profile's pipelines and what they depend on | as `peachq`, with overlays, plus its declared bundles' jobs and what they depend on |
  | tables         | `database.q` plus this tree's (`fx_orderbook`, the crypto mocks, ...)   | the starter pack's `database.q`: `trade`, `quote`, `packets`      | as `torq`           | `database.q` plus the tables those pipelines read and write                                     | `database.q` plus the tables those pipelines read and write                                 | `database.q` plus the tables those jobs read and write                              |
  | config layers  | TorQ's, `scripts/torqconfig` and `scripts/torqcode`, the starter pack's | TorQ's and the starter pack's                                     | as `torq`           | as `uqf`                                                                                        | as `uqf`                                                                                    | as `uqf`, from a flattened copy (below)                                             |
  | data directory | `output/uqs`                                                            | `output/uqs-torq`                                                 | `output/uqs-peachq` | `output/uqs-crypto`                                                                             | `output/uqs-fx`                                                                             | `output/uqs-peachq-etl`                                                             |
  | base port      | `6050`                                                                  | `6150`                                                            | `6450`              | `6250`                                                                                          | `6350`                                                                                      | `6550`                                                                              |
  | interpreter    | KDB-X                                                                   | KDB-X                                                             | PeachQ              | KDB-X                                                                                           | KDB-X                                                                                       | PeachQ                                                                              |

Each runtime has its own data directory and base port, and their port spans do
not overlap. So `uqs start` and `uqs --runtime torq start` run side by side with
no flags. Running the same query against both shows whether a problem is TorQ's
or this tree's. Runtimes are declared in
[`python/uqs/src/uqs/runtimes.py`](../../python/uqs/src/uqs/runtimes.py), and
[sidecar bundles](sidecar-bundles.md#bundles-a-runtime-declares) covers the
bundles `runtime prepare` installs. It and `uqs job install` refuse to install a
bundle into a development checkout or worktree: run them in a separate clone
marked with `.uqf-instance`
([instances](sidecar-bundles.md#install-into-an-instance)).

Each runtime declares its own profiles. `--profile` takes only those, and
`uqs profile list` shows only the selected runtime's. Start `crypto`, `fx` and
`peachq` with a profile:

```bash
uqs --runtime crypto start --profile crypto   # cryptomock1, crypto_markout1 and what they read, on 6250
uqs --runtime peachq start --profile capture
```

- `crypto` and `fx` hold one profile's pipelines and everything they depend on.
  A plain `start` there leaves the profile's own leaves off.
- On `torq`, commands that work only on this tree's pipelines refuse, and
  `uqs runtime diff uqf torq` lists them. Its `essential` profile leaves out
  `feed1`, so nothing publishes. `feed` adds it. `torq` has none of the [query
  policies](../architecture/query-policies.md).
- `peachq` is `torq`'s stack on [PeachQ](https://github.com/peachq-org/peachq),
  with no connection cap and no licence needed. `capture` leaves out `wdb1`,
  `sort1` and `reporter1`, and a gateway query that routes to `hdb1` fails. So
  end of day and the HDB do not work there. `peachq-etl` is experimental, see
  [sidecar bundles on PeachQ](sidecar-bundles.md#peachq).

A new runtime's HDB holds only the starter pack's two sample partitions. To
compare it with `uqf` on the same history, copy `uqf`'s partitions of the tables
both declare. A partition the target already holds is skipped unless
`--on-conflict replace`. A running HDB sees the new ones once restarted.

```bash
uqs --runtime torq data seed --from-runtime uqf             # the partitions it would copy
uqs --runtime torq data seed --from-runtime uqf --apply     # every table torq declares
```

### Profiles

A profile names the processes you came for, its leaves. Everything they read is
derived from the dependency graph. Profiles are declared in
`python/uqs/src/uqs/model/profiles.py`, by their leaves.

```bash
uqs profile list
uqs start --profile arbitrage
uqs start --profile depth,arbitrage   # two profiles that fit together
uqs start --profile essential      # the TorQ stack alone, no uqf jobs
uqs start --profile essential vectorize1 cross1   # a profile plus named processes
UQS_LICENCE_CONNECTIONS=32 uqs start --profile all   # on a licence that allows it
```

Process names given beside `--profile` are added to the set. They are not
widened to what they read, and `all` cannot be added. `restart`,
`start --follow` and `stop --profile` take the same combination.

The community licence leaves fourteen tickerplant slots: sixteen connections,
two held back for ad-hoc handles. A profile start over the cap is refused before
anything starts. A positional start over it is only warned about. Two profiles
that each fit can be over the cap together, as `fx` and `arbitrage` are. The
table shows the uqf runtime's profiles against those fourteen slots.
`uqs profile list` reports the same against this machine's licence.

<!-- BEGIN GENERATED profile table - scripts/generate/operational_docs.py; CI runs it with --check, so edit the profiles, not this table -->

  | profile     | leaves                                                                                                           | processes | plant slots          |
  | ---         | ---                                                                                                              | ---       | ---                  |
  | `all`       | `arbitrage1`, `cross1`, `crossarb1`, `fxorderbookfeed1`, `fxpositions1`, `last_value1`, `posbook1`, `vectorize1` | 29        | 19/14 - over the cap |
  | `arbitrage` | `arbitrage1`, `crossarb1`                                                                                        | 20        | 10/14                |
  | `crypto`    | `cryptomock1`, `crypto_markout1`, `crypto_book_1s1`                                                              | 19        | 9/14                 |
  | `default`   | `posbook1`, `fxpositions1`, `fxorderbookfeed1`                                                                   | 22        | 12/14                |
  | `depth`     | `vectorize1`, `cross1`                                                                                           | 18        | 8/14                 |
  | `essential` | none - the TorQ stack alone                                                                                      | 14        | 4/14                 |
  | `fx`        | `posbook1`, `fxpositions1`, `last_value1`                                                                        | 23        | 13/14                |

<!-- END GENERATED profile table -->

`all` is the union of the other profiles' leaves, and it needs a larger licence.
Set `UQS_LICENCE_CONNECTIONS` to the number of concurrent connections your
licence allows, and every start is held to that budget. On PeachQ there is no
cap. `all` leaves out `crypto`. Ask for both with `--profile all,crypto`.

`essential` is the TorQ stack with nothing on top. It also starts `reporter1`
and `tpreplay1`, which no other profile starts. `tpreplay1` exits at startup, so
expect it shown down. `--profile essential,fx` starts both sets. A profile stops
at a table fed from outside the stack: `fx` needs `crypto_book` but does not
start `cryptomock1`.

On a runtime with this tree's overlays, `housekeeping1` and `reporter1` read
`housekeeping.csv` and `reporter.csv` in the data directory. Every start
rewrites both, so the site's `appconfig` copies are not read.

### Skipping a start check

`start` and `restart` let you override two checks by name, for one command, with
a reason. `q-version-check` starts despite a KDB-X older than 5.0.
`profile-budget-check` starts a `--profile` past the connection-budget estimate.
Each use is appended to `start_overrides.jsonl` in the runtime's data directory.

```bash
uqs start rdb1 --override q-version-check --reason "checking which jobs load on uat's 4.1"
uqs start --profile fx --override profile-budget-check --reason "full licence on this host"
```

## Reading status

`uqs status` names the runtime and base port in its title. Its first line names
the q that runs the fleet and the connection budget that follows from it. The
whole command has one time budget, 120s by default. `uqs status --timeout 10`
fails fast and `uqs status --timeout 0` waits forever.

`Depends on` resolves a process's `Inputs` to the processes that produce them,
and names a table produced outside the process list as external. On a narrow
terminal, `uqs status --columns status` drops the graph columns.

### Idle subscribers

A subscriber started without its producer comes up, heartbeats and reports `up`.
It receives nothing, and the only symptom is an output table that stays empty.
`start` and `restart` warn about it, and `status` lists such processes as
`up, but idle` with the command that starts the producer.

The warnings never block a start, and some tables come from outside the process
list. Processes named in the same command count as present, so start a chain
together. [`--profile arbitrage`](#profiles) starts this chain with both
detectors, `arbitrage1` and [`crossarb1`](../services/cross-arbitrage.md).

```bash
uqs start marketdata1 superbook1 arbitrage1
```

### Responds column

`Status` comes from a PID lookup, and a hung process still has a PID. `Responds`
asks every `up` process whether it can complete the kdb+ handshake within
`--probe-timeout` (0.5s by default). No q code runs on the process, and
`--probe-timeout 0` skips the probe.

  | Responds   | Meaning                                                                               |
  | ---        | ---                                                                                   |
  | `4ms`      | it answered, in that long                                                             |
  | `timeout`  | it accepted the connection but did not answer in time: busy, or at its connection cap |
  | `reset`    | it dropped the connection, as a process past its connection cap does to a new handle  |
  | `rejected` | it closed the connection without answering: it refused the credentials                |
  | `refused`  | nothing is listening on its port                                                      |
  | `-`        | not probed: the process is down, or `status`'s own budget ran out                     |

### Batches column

A streaming job whose batch handler throws keeps its PID, heartbeat and
handshake, and drops the batch. `Batches` says what each job's batches did since
its last uptime beat, once a minute: `ok`, `failing (N)` with N its failed
batches so far, or `-` for a process that runs no streaming job.
`uqs job gaps <job>` lists the span a failing job dropped.

### monitor1

`monitor1` is the only process that collects heartbeats. Without it the
Heartbeat column reads "not collected" on a healthy stack. Upstream ships it off
and uqs turns it on. `uqs config set monitor1 startwithall=0` restores the
upstream behaviour. If `status` cannot reach it in time, the column reads
"monitor1 could not be reached".

Under the community licence, coverage is partial. `monitor1` stops monitoring
`sortworker`, `reporter`, `housekeeping`, `feed`, then `metrics` processes until
the rest fit in its 16 connections. Those show `-` in the Heartbeat column, and
`start`, `restart` and `status` print one line naming the proctypes given up.
`backfill` workers are not monitored. On a fully licensed kdb+/KDB-X, or on
PeachQ, nothing is dropped.

## Default ports

Every port is the stack's base port plus a fixed offset. The base is the
runtime's own, listed in the [runtime table](#runtimes), and `--base-port <n>`
overrides it. `uqs process list` prints every port at the base in effect. The
generated [process table](../reference/processes.md) has them at the default.

## Changing a process's config

`uqs config set` writes an override to `python/uqs/process_overrides.csv`, which
belongs to this host and is ignored by git. Every command regenerates
`process.csv` and applies the overrides, so do not edit the generated file. A
change takes effect on the next `start` or `restart` of that process.

```bash
uqs config show fxfeed1
uqs config show fxfeed1 startwithall
uqs config set fxfeed1 startwithall=0
```

The fields are `process.csv`'s columns: `host`, `port`, `proctype`, `procname`,
`U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`, `extras`, `qcmd`.

A value with a comma, a double quote or a newline is refused. To pass a list,
separate it with spaces. A column TorQ lacks, such as `taskset_no` for CPU
affinity, must be declared in `UQS_PROCESS_FIELDS` for every command. See
[columns the site launcher reads](deploy.md#columns-the-site-launcher-reads).

```bash
uqs config set fxfeed1 "extras=-pairs EURUSD USDJPY"
export UQS_PROCESS_FIELDS=taskset_no
uqs config set stp1 taskset_no=3:5
```

### What sources connect to

`uqs config sources` shows the `sources.csv` the stack reads. For each source it
shows where the credential comes from and what would stop it resolving.
`uqs config sources stub duckdb_deals` adds a `SCAFFOLDED` row for that source
to the gitignored application layer's file, for you to fill in. See [where a
source
connects](../reference/pipeline-declarations.md#where-a-source-connects-sourcescsv).
