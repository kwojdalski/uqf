# Attaching a job to an existing TorQ stack

This page shows how to make a uqf job publish into a TorQ stack that this
checkout did not start, and how to reverse it. The stack is a running fleet with
its own tickerplant, rdb, wdb and hdb. For an external publisher that is not a q
process, use [`connector`](uqs.md).

## What attaching changes

Attaching gives the job's process the stack's identity (its base port, from
which every process port and the discovery address follow) and registers its
table, because a tickerplant publishes only the tables it was started with. On
the stack, apply makes these changes in order:

1. The tickerplant registers the table.
2. Each consumer subscribes, which creates the empty table and replays the
   tickerplant's log for it. Its other subscriptions stay as they were.
3. A marked block goes into the tickerplant's schema file. This is the one step
   that changes what a restart does.

A declared [sort rule](#sort-rules) adds rows to the stack's `sort.csv`, and
`--process-file` adds one row to its process file ([native TorQ
start](#native-torq-start)). Each process keeps an ownership record,
`.uqfattach.owned`. Attaching does not restart a process, edit vendored TorQ, or
leave q code on a process. A change that would need a restart is a blocker in
the plan. Detaching reverses only what the attachment created.

## What the stack must already have

Preflight reads the stack and prints each finding with its cause. These findings
need a change on the stack before you attach:

  | Finding                                             | What the stack needs                                                                                              |
  | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
  | `LOGGING_MODE`, `LOGS_DISABLED`, `CHAINED_TP`       | a tickerplant that logs one file per table and is not chained. Fixing it needs a restart                          |
  | `CONSUMER_FILTERED`, `CONSUMER_IGNORES`, `WDB_MODE` | an rdb and wdb that do not filter or ignore the table, and a wdb that saves. Otherwise it would never persist     |
  | `SORT_CONFIG`                                       | a wdb `sort.csv` that can sort the table, or end of day fails. Declare a [sort rule](#sort-rules), or add a row   |
  | `PERSIST_UNAVAILABLE`                               | a tickerplant `-schemafile` that can be appended to, or the registration is lost on a restart. Else `--transient` |
  | `TORQ_VERSION`                                      | TorQ 5.2.12, the one tested version. Another one can be [accepted as a risk](#accepting-a-finding)                |

Every column of the table needs a concrete type, or end of day can abort for
every table. Write ``note:`symbol$()``. For a list per row, add a line giving
the element type: ``nested[`book;`bid_prices`bid_sizes!"FJ"]``.

Preflight does not check connection headroom. Under a licence with a limit (the
KDB-X community licence allows 16 per process), an attachment adds one
persistent connection to the tickerplant and one to discovery.

## Plan and validate

`--dry-run` reads the stack and prints the plan: the stack and publisher
identity, the table schemas, every affected component, the changes and their
reversibility, and the findings.

```bash
uqs job attachment add pb-uat --job pb_general_ledger --bundle ../piggybank \
    --base-port 6000 --consumer rdb1 --consumer wdb1 --dry-run
```

It writes nothing and exits 1 on a blocker. A new attachment needs `--job` and
`--base-port`, which has no default. `--host` defaults to `localhost` and
`--consumer` to all consumers. `uqs job attachment add --help` lists the rest.

### Accepting a finding

Four blockers can be accepted as a risk: `TORQ_VERSION`, `SORT_CONFIG`,
`PORT_CONFLICT` and `PUBLISHER_RUNNING`. Pass `--override` once per finding and
process, spelled as `uqs override list` names it (`torq-version:stp1`), with a
mandatory `--reason`. There is no blanket `--force`.

An override accepts a risk and does not make the version a tested one. Accepting
`PUBLISHER_RUNNING` means rows published during registration may be lost. An
acceptance is void when the facts change, and verify then fails. Without a
terminal, add `--accept-risks` to `--yes`. `--clear-overrides` withdraws every
acceptance.

## Apply

Without `--dry-run` the command shows the same plan, asks, then saves the
configuration, applies, verifies and starts the publisher.

```bash
uqs job attachment add pb-uat --job pb_general_ledger --bundle ../piggybank \
    --base-port 6000 --consumer rdb1 --consumer wdb1
uqs job attachment list                # attachment state and publisher state, per attachment
```

Every check runs again, and apply refuses while the publisher is running. A
failed apply or verify stops there, with the publisher not started. Nothing is
rolled back for you. Run the same command again, which is safe, or
`uqs job attachment remove NAME`.

After the first execution the attachment is reused by name
(`uqs job attachment add NAME`). Options that contradict the saved ones are
refused, and `--replace` replaces exactly those. Every option it is not given
keeps its saved value, as it does without `--replace`: the persist mode and
schema writer, the user, password variable and ssh destination, the native
settings and the accepted risks. Storage pins stay unless an option that decides
them changed (another stack, tickerplant or consumers).

The pins are the tickerplant's log directory and schema file and the wdb's save
and HDB directories. They are read when the attachment is saved, from the wdbs
of the stack, including those it does not manage, so `--consumer rdb1` still
pins an HDB. Every later plan, verify, backfill and `uqs data --attachment`
checks them against the same wdbs. A pin is never moved for you: when the stack
is restarted on another HDB the plan reports `STORAGE_MISMATCH`, verify takes
`verified` back, and backfill and `data check` refuse. If the move is intended,
re-pin with `uqs job attachment add NAME --replace --repin-storage`. It reads
every pin again from the stack, shows each one old -> new, and is refused when
the wdbs disagree on a location. As a replacement it is refused while the
publisher runs or a backfill owns the attachment, and is applied and verified
again. A saved replacement is no longer `verified` until it is applied and
verified. While the publisher is running a `--replace` is refused and nothing is
saved: stop the publisher (`uqs stop PROCNAME --base-port N`) and run it again.
While an attached backfill runs, a `--replace` is refused too, as
`uqs job attachment remove` is: let the run finish or stop it, then run the same
command again. The configuration is saved as `attachment.json` under
`$UQS_ATTACH_DIR`, else `$UQS_DATA_ROOT/attachments`, else `output/attachments`.
It holds the name of the password's environment variable and no password.

A durable attachment replaced with `--transient` keeps no block. Before anything
was published the apply removes its block from the schema file (hash checked,
and the plan shows the removal). After publication the block is kept, as
`uqs job attachment remove` keeps it, because a tickerplant restart and the
log's replay need it. That replacement is refused (`BLOCK_AFTER_PUBLICATION`).

## Verify and start

Verify publishes no row. It checks that the tickerplant holds the table as a
logged pub/sub table, and that every consumer holds it, is subscribed and will
save it. It also re-runs the preflight. Passing makes the attachment `verified`,
and a failed re-verification takes it back to `applied`.

The publisher starts only while the attachment is `verified` and only under its
stack id, whatever starts it, `uqs exec -- debug pb_ledger1` included. A bare
`uqs start pb_ledger1` is refused with the right command named. If an
`attachment.json` cannot be read, every start is refused. Make it readable, or
move the folder out of the attachments directory if the attachment is gone.

A job attached to two stacks has one publisher name and two attachments. Each
start is judged by the attachment on the stack it starts under, so each
publisher starts under its own stack id, whatever state the other attachment is
in. A removed attachment keeps its folder. Under the stack it was attached to,
the publisher stays stopped until `uqs job attachment add` has verified it
again. Under any other base port it no longer holds the publisher.

`--no-start` applies and verifies, and leaves the publisher stopped. The same
command without it starts it later. `--follow`, `--trace`, `--level` and
`--stop-on-exit` are `uqs start`'s.

## Backfilling into the attached stack's HDB

`uqs job backfill --attachment NAME` writes into the HDB that the saved
attachment names. Without it, a backfill writes into the uqf runtime's own HDB
(`KDBHDB`), whatever `--base-port` says.

```bash
uqs job backfill piggybank_ledger_backfill --attachment pb-uat \
    --from 2026-10-01 --to 2026-10-02 --source-version historical-v1 \
    --on-conflict replace --mode plan          # then --dry-run, then run
```

`--mode plan` asks the stack read-only questions and reports what a run would
do. The rows go only into the HDB, in the partition of the day they happened.
Nothing is published through the tickerplant, and today's RDB is not populated.

- The attachment must be `verified`, and its HDB unsegmented and on a local
  filesystem of the machine that runs `uqs`.
- Every live wdb and rdb of the stack, managed or not, must report the pinned
  HDB. After the stack moved it, re-pin it with
  `uqs job attachment add NAME --replace --repin-storage`.
- When a start of the publisher would ask the stack (a stack on another machine,
  or a table a restart would not restore, such as a `--transient` one), the
  tickerplant must still register every attached table. One it lost in a restart
  is refused, as the start is: re-apply with `uqs job attachment add NAME`. So
  is a shared table whose ownership record no longer lists the attachment among
  its users. A `--writer` run with `--override any-writer` checks nothing, this
  included.
- Every affected date must be before the date any writer is filling.
- A `compression` process on the stack is a blocker.
- A run is not coordinated with `dbmaint` or q run against the directory by
  hand, or with an end of day started by hand.

The end-of-day fence refuses a run within an hour before the next roll, within
an hour after the last, or while a reload is open, all on the wdb's clock. A run
under way stops writing when the fence begins.

`replace` clears only its own window. A run does not fill other dates with the
table, so an older partition that lacks the table stays without it.
`uqs data check --attachment NAME --apply` writes an empty table into those
partitions, on the stack's host ([one table, in any
HDB](hdb.md#one-table-in-any-hdb)).

A failed window is neither written nor covered, and the next run redoes it.
`written_not_queryable` (exit 3) means the rows are written and covered, but a
reader has not reloaded or does not serve the table. Run the same command again:
it writes nothing and asks the readers again. A partitioned HDB takes its table
list from its latest partition, so a newly attached table stays invisible until
the stack's next end of day writes it or `uqs data check` fills it.

`--follow` streams the run's log, and Ctrl-C stops the stream only. With
`--follow --stop-on-exit` the first Ctrl-C asks the run to stop between windows.
The windows it completed are covered, and the same command again does the rest.
`uqs job attachment remove` and `uqs job attachment add --replace` refuse while
a run is alive. `uqs job runs stop JOB --attachment NAME` writes its stop file
in the state directory of the destination the run was launched into, as its
owner record names it, and `uqs job runs list --attachment NAME` reads that
run's ledger.

### Waiving the end-of-day fence

Only the fence's timing can be waived, for one invocation, with a reason:

```bash
uqs job backfill JOB --attachment NAME --from START --to END \
  --skip-eod-fence --reason "Approved maintenance window"
```

`--override eod-fence` is the same waiver, and `uqs override list` names it. It
is taken by `--mode plan`, `--dry-run` and a run on the machine that holds the
attachment. It is refused without `--attachment`, with `--writer` and with
`--mode validate`. Through `uqs --target NAME`, it is sent as
`--override eod-fence`.

The waiver admits the run within the fence and gives the runner no write
deadline. Every other check still applies.

The waiver is not saved with the attachment, so the next backfill without the
flag is fenced again. A dry run or run that uses it first appends an entry to
`<status dir>/attached/<attachment>-<id>/backfill_overrides.jsonl`, and does not
start if that fails. `uqs job attachment show NAME` lists the entries.

### Seeing what happened

- `uqs job attachment status` shows each destination's worker, state, outcome
  and log, and what to do next when the rows are not queryable.
- `uqs logs --attachment NAME --worker WORKER [-f]` shows the latest run's log
  for that worker, or for `<dataset>.receiver`. `--stamp` picks an older run.
- `uqs job runs list | show --attachment NAME` reads the run ledger.
- `uqs job attachment show NAME` lists each waiver used.

## Detach

```bash
uqs job attachment remove pb-uat --dry-run
uqs job attachment remove pb-uat
```

Detach stops the publisher. A publisher the site's `torq.sh` started from the
attachment's row in the stack's process file is stopped too: `uqs stop` treats
the process file the attachment registered in as its own for that one process.
One started from any other process file is never signalled; the refusal names
the `torq.sh stop PROC -csv FILE` that stops it. It then reads the stack for
evidence that anything was published. Evidence that cannot be read counts as
publication. Once every check has passed, and before anything on the stack
changes, the row, if there is one, comes out of the process file.

- Before any publication, the attachment is rolled back. What it created is
  removed in reverse order: the schema block, the consumers' table and
  subscription, then the tickerplant's registration, logs and metadata. Sort
  rows it added go too. Anything that existed before is not touched.
- After publication nothing is deleted. The registration, rows, logs, schema
  block and sort rows stay, because removing them breaks replay or changes what
  a restart does. The attachment becomes `detached`, and applying again adopts
  what stayed. Destructive cleanup is outside this workflow.
- What stays lasts only as long as the tickerplant keeps it. A table a block
  defines comes back after a restart. A table no block defines, such as a
  `--transient` attachment's, is kept only until the tickerplant restarts.
  Detach lists each such table under `restart:`. After the restart, rows not yet
  written by end of day are only in the tickerplant log, and nothing replays it.
  Let end of day run before you restart, or keep that log and replay it by hand.

A refused detach changes nothing but the stopped publisher, names the manual
action and cannot be overridden. It refuses while another attachment uses the
table, and when the schema file or the process-file row was edited since apply.
"Uses" is read from the tickerplant's ownership record and from the other
attachments saved on this machine for the same stack: a restart can drop a
`--transient` user from the record, and the detach then names it instead. A
failure part way is `rollback_partial`: run detach again.

## Restart behaviour

  | State                                       | A tickerplant restart                                                                                                                                                          |
  | ------------------------------------------  | ----------------------------------------------------------------------------------------------------------------------------------------------------                           |
  | applied, with the schema block              | the block recreates the table and its ownership record                                                                                                                         |
  | applied, `--transient`                      | gone, and a running publisher's rows are dropped. Stop it (`uqs stop PROC --base-port N`), then `uqs job attachment add NAME` restores it                                      |
  | sharing a `--transient` table               | gone too: no block defines it. The plan warned `SHARED_RUNTIME_ONLY`; a start asks the stack and is refused                                                                    |
  | `--transient` user of a block-defined table | the table comes back, but its ownership record no longer lists this user: a start is refused and the creator's detach names it until `uqs job attachment add NAME` joins again |
  | adopting a table the schema file defines    | the stack's own definition recreates it; no block is written for it. The plan said `STACK_DEFINES`                                                                             |
  | adopting a runtime-only table               | gone: no block is written for a table the attachment did not create. The plan warned `ADOPTED_RUNTIME_ONLY`; a start asks the stack and is refused                             |
  | rolled back                                 | the block went with the registration: the table does not come back                                                                                                             |
  | detached after publication                  | the block stayed, so the table comes back and its logs replay                                                                                                                  |
  | detached after publication, no block        | the table is gone, and end of day writes no partition for it. Unsaved rows are only in the log. `--dry-run` reports it as `detached:`, not to apply                            |

With a `--transient` table lost, `--dry-run` reports `registration-lost`. While
the publisher still runs, `uqs job attachment add NAME` and its `--dry-run` are
refused and save nothing: the rows it published since the restart were dropped,
and apply refuses while it runs. Stop it first.

TorQ does not reconnect an rdb or wdb to a restarted tickerplant. Restart them
yourself. End of day needs nothing extra.

## What a publish acknowledges

This covers a polling feed, a job that declares `poll`. Delivery is at least
once for rows the tickerplant accepted and kept. It is not exactly-once.

The publish is synchronous. When it returns, a `defaultbatch` tickerplant has
logged the rows, an `immediate` one has logged and sent them to subscribers, and
a `memorybatch` one holds them in memory only until its next timer.

A tick fetches the page after the saved cursor, publishes it, then saves the
cursor (`<name>.cursor` in `$UQF_STATUS_DIR`, else `$TORQDATA/status`). A tick
interrupted between the publish and the save leaves its page in doubt.

- Without a `row_key` on the poll, an in-doubt page is published again. The log
  says `in-doubt page published again - possibly duplicated` at WARNING.
- With a `row_key`, the publisher asks an rdb which keys it holds and publishes
  only the absent rows. A row logged but not yet delivered, or already written
  out at end of day, is sent again. A row another publisher sent under the same
  key is left out.
- A send repeated after the handle closed mid-call is logged as
  `possibly duplicated`.

Rows are lost when `memorybatch` held them as the tickerplant died, when its
error mode trapped a failing `.u.upd`, and when it no longer defines the table.
A poll that declares `ack` 1b fails the tick with `was not acknowledged` when
the logged row count did not advance by the rows sent, which catches the trapped
rows. `memorybatch` refuses `ack`. Nothing confirms that an rdb or wdb received
the rows, or that the log write reached the disk.

## Sort rules

A stack's `sort.csv` `default` rows may name a column the attached table lacks,
and preflight then refuses with `SORT_CONFIG`. A table that needs its own layout
declares it beside its definition, in the bundle's `tables.q` or
`src/etl/plant_tables.q`:

```q
piggybank_general_ledger:([] time:`timestamp$(); sym:`symbol$(); amount:`float$())
sorting[`piggybank_general_ledger;`sym`time!`p`]
```

The keys are the sort columns in order, the values each one's attribute (`` ` ``
for none). With a declaration, apply backs up each `sort.csv` that sorts the
table into the attachment's `backups/`, appends the declared rows and reloads
each sort process. A conflict with the stack's own rows for the table is a
blocker, `SORT_CONFLICT`. So is a process whose live rules differ from its file,
`SORT_DRIFT`. The plan names the manual step.

## Native TorQ start

Without `--process-file` only uqs starts the publisher. A stack restarted with
the site's own `torq.sh start all` comes back without ingestion. With the
option, the publisher is registered in the stack's process file:

```bash
uqs job attachment add pb-uat --job pb_general_ledger --bundle ../piggybank \
    --base-port 6000 --process-file /opt/torq/appconfig/process.prd.csv --dry-run
uqs job attachment add pb-uat --start-with-all     # in the stack's `start all` from now on
uqs job attachment add pb-uat --no-start-with-all  # registered, started on demand only
```

- The file must be on this machine, and so must the stack.
- `--start-with-all` needs every table to survive a tickerplant restart, so it
  is refused with `--transient`, and for a table the plan or verify warns is
  runtime-only, whatever the persist mode says. It is not a boot service or
  crash supervision.
- Set the variable `--password-env` names in the environment `torq.sh` runs in.
- Run the add again after you set or unset
  [`KDBSECURE`](../reference/environment.md).

## A publisher on this machine, a stack on another

The publisher runs on the machine `uqs` runs on, so its source driver and
credentials stay there. Only the tables travel, over q IPC. `--host` names the
stack's machine.

- Start the publisher on its own. A start that names it beside other processes
  is refused.
- The stack must reach the publisher back. Discovery connects to this machine's
  host name, saved in the attachment as `advertised_host`. The plan refuses a
  loopback name and a name discovery cannot resolve. If discovery cannot open
  the publisher's port after the start, the publisher is stopped.
- Three credentials meet. The attachment's (`--user`, `--password-env`) lets
  `uqs` ask the stack. The publisher's own TorQ credential connects it to
  discovery and the tickerplant. The stack's lets discovery connect back.

The tickerplant's schema file is on the stack's machine, so the default is
refused with `PERSIST_UNAVAILABLE`. Choose one:

- `--schema-writer tickerplant`. The tickerplant appends the block to its own
  file over the same q IPC. The block is q that it runs at every start, and the
  saved option records that the stack's operators agreed to it.
- `--transient`. A tickerplant restart drops the registration, and nothing on
  this machine is told. Stop the publisher (`uqs stop PROC --base-port N`), then
  run `uqs job attachment add NAME`, as on one machine (Restart behaviour).
- Run `uqs` on the stack's machine.

These are not verified across two real machines: a network between them
(firewall, NAT, VPN, routing), name resolution that differs by side, separate
file systems and users, a whole TorQ stack on the other machine on KDB-X, and
clocks, latency and timeouts over a real link.

## Extracting on one machine, writing on the stack's

`uqs job backfill --attachment` writes the HDB from the machine that runs the
worker. When the source's driver and credentials must stay elsewhere, the
extraction machine sends each window to a receiver on the stack's machine, which
needs uqf and KDB-X and none of the job's code. The receiver writes under a
contract, exported from the worker and approved by the stack's operator. Here
`etl-host` extracts and `uat-host` holds the stack:

```bash
# etl-host: attach, then export the worker's receiver contract
uqs job attachment add pb-uat --job pb_general_ledger --bundle ../piggybank --base-port 6000 \
    --host uat-host --consumer rdb1 --consumer wdb1 --yes
uqs job attachment contract export piggybank_ledger_backfill --output ledger.contract.json

# carry both to uat-host, into its $UQS_DATA_ROOT/attachments (here /srv/uqs).
# The attachment's directory holds no secret.
scp -r "$UQS_DATA_ROOT/attachments/pb-uat" uat-host:/srv/uqs/attachments/
scp ledger.contract.json uat-host:

# uat-host: approve the contract for the attachment, then start the receiver
uqs job attachment contract approve ledger.contract.json --attachment pb-uat --on-conflict upsert --on-conflict replace
uqs job attachment contract list --attachment pb-uat          # its identity, e.g. 3f9a0c2e7b1d4a56
uqs job receiver start --contract 3f9a0c2e --attachment pb-uat --listen-port 7090

# etl-host: extract, and send each window to the receiver
uqs job backfill piggybank_ledger_backfill --attachment pb-uat --writer uat-host:7090 \
    --from 2026-10-01 --to 2026-10-02 --source-version historical-v1 --wait
```

- Export and approve the contract again when the worker's output changes, or
  when the attachment is pointed at another HDB. Do not write one by hand.
- A batch resent after a lost acknowledgement is answered `duplicate`, and
  nothing is appended.
- Under `append` or `fail`, a batch the receiver was writing when it stopped is
  refused as in doubt. Check the target, then `.qetl.io.remote.forget` it.
- While a receiver runs, `uqs job attachment remove` on the stack's machine
  refuses. `uqs job receiver stop --attachment NAME` stops it. An extractor then
  fails its next window without covering it, and the next run sends it again.
- Reactions of the backfilled dataset (`rebuild_positions`, for example) are
  refused and recorded as owed. They belong on the stack's side.

### Without a receiver

`--override any-writer --reason TEXT` with `--writer` skips the receiver and
everything it checks. HOST:PORT is then any q process of the stack, usually its
wdb.

```bash
uqs job backfill piggybank_ledger_backfill --attachment pb-uat \
    --writer uat-host:6055 --override any-writer --reason "completed history" \
    --from 2026-10-01 --to 2026-10-02 --source-version historical-v1 --wait
```

Nothing is checked on the stack's side:

- It appends only, and a window whose acknowledgement was lost is appended
  again.
- Columns that differ from a partition on disk fail inside the wdb, possibly
  after some dates are written.
- Rows dated today land in a partition that the wdb's own end of day then writes
  over.
- The wdb blocks during each call, so live ingestion queues behind a large date.
- No reader is told to reload. Run `\l .` on the HDB processes yourself.
