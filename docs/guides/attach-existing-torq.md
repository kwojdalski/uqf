# Attaching a job to an existing TorQ stack

A uqf job can publish into a TorQ stack that was not started by this checkout: a
fleet that is already running, with its own tickerplant, rdb, wdb and hdb. The
job's process needs two things from that stack before it may start, and both
used to be done by hand:

- the **stack's identity**: its base port (`-stackid`, `KDBBASEPORT`), from
  which every process port and the discovery address follow. A process started
  with uqf's default base port looks for discovery at `6051`; the fleet's is at
  `6001`.
- the **table**: a tickerplant only publishes tables it was started with. The
  job's `assert_publishable` refuses to start against one that lacks its table.

`uqs job attach` makes both explicit and reversible. This guide is for an
operator attaching, for example, a sidecar bundle's `piggybank_general_ledger`
job to a running fleet. It lives under `job` because the unit is a job (its
publisher process and its tables); [`connector`](uqs.md) is for external
publishers that are not q processes.

```text
plan -> validate -> apply -> verify -> start -> (detach)
read-only            changes the stack    permits the publisher
```

**Today only the read-only half exists**: `plan`, `validate` and `status`.
`apply`, `verify`, `start` and `detach` follow in the next changes of #1424; the
tables below that describe what they will do are the design they are built to,
and are marked as such.

## What the workflow guarantees

- **It never restarts a shared process.** Everything it does to a running
  process is an ordinary IPC call; anything that would need a restart (a
  tickerplant logging all tables into one file, say) is a *blocker* in the plan,
  with the manual action named.
- **It never edits vendored TorQ** or the starter pack's shipped files, and it
  leaves no q code behind on a process. A lambda is sent, runs and is gone. The
  one thing an attachment leaves in a process is its ownership record,
  `.uqfattach.owned`.
- **It does not default the stack.** `--stack-id` has no default, the discovery
  address is written down once by `plan`, and every later command reads both,
  and the storage locations, from the saved attachment.
- **It only ever reverses what it created**, from a recorded manifest.

## Plan, then validate

```bash
uqs job attach plan pb-uat --job pb_general_ledger --bundle ../piggybank \
    --stack-id 6000 --consumer rdb1 --consumer wdb1
uqs job attach validate pb-uat        # exit 1 on any blocker
uqs job attach status pb-uat
```

`plan` reads the stack and prints:

- the stack it found (id, host, discovery), and the job, its process and port;
- **every affected component**: the tickerplant (registers the table and logs
  it), the consumers (subscribe, persist), the processes it deliberately does
  *not* manage (other rdb/wdb, a chained tickerplant), and the rest of the stack
  (hdb, sort, gateway: nothing until end of day);
- the **changes** apply would make, in order, with what each can be undone as
  before and after publication;
- the **findings**: blockers, warnings, and what will be adopted.

It writes `attachment.json` (under `$UQS_ATTACH_DIR`, else
`$UQS_DATA_ROOT/attachments`, else `output/attachments`): the stack id,
discovery address, tickerplant and consumers, the credential (the user and the
*name* of the environment variable holding the password, never the password),
the persist mode, and the storage the stack reported: tickerplant log directory,
schema file, wdb save directory and hdb directory. A later run that finds the
stack somewhere else refuses; it does not fall back to a default. `--no-save`
writes nothing; `--replace` rebuilds a saved attachment.

The stack itself is only ever asked `probe`, a read-only question. A test holds
that.

### What validate refuses

  | Code                                    | Refused because                                                                                                                                                      |
  | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------       |
  | `DISCOVERY_UNREACHABLE`, `UNREACHABLE`  | a configured endpoint does not answer, or rejects the credential                                                                                                     |
  | `STACK_MISMATCH`                        | a process answers but says it belongs to another stack: discovery at `6051` serves stack `6050`, not `6000`                                                          |
  | `TORQ_VERSION`                          | the process runs a TorQ the procedure was not tested on (tested: `5.2.12`, the vendored version; UAT's version is not recorded in this repository)                   |
  | `LOGGING_MODE`, `LOGS_DISABLED`         | the tickerplant does not log one file per table, or at all: a shared log cannot be attributed to a table, taken back, or replayed per table                          |
  | `CHAINED_TP`                            | the target is a chained tickerplant                                                                                                                                  |
  | `SCHEMA_INCOMPATIBLE`                   | the table exists with other columns, types or column order. No automatic conversion                                                                                  |
  | `NOT_PUBLISHED`                         | the table exists on the tickerplant but is not one of its pub/sub tables                                                                                             |
  | `CONSUMER_FILTERED`, `CONSUMER_IGNORES` | an rdb/wdb subscribes with a filter or a fixed list, or ignores the table: it would never persist it, and its configuration is not ours to edit                      |
  | `SORT_CONFIG`                           | the wdb's `sort.csv` cannot sort the table (no row and no default, or it names a column the table lacks): end of day would fail                                      |
  | `STORAGE_MISMATCH`                      | a pinned location is not what the stack reports                                                                                                                      |
  | `PERSIST_UNAVAILABLE`                   | the registration could not survive a tickerplant restart (no `-schemafile`, a remote or read-only file, or a file uqf generates); choose `--runtime-only`, or fix it |
  | `PORT_CONFLICT`, `PUBLISHER_RUNNING`    | the publisher's port (stack id + its offset in `process_ports.csv`) is held by another process, or the publisher is already running                                  |
  | `NO_TICKERPLANT`, `NO_CONSUMER`         | nothing to publish to, or nothing to persist it                                                                                                                      |

Warnings are things that are true and worth knowing: a running rdb/wdb the
attachment does not manage will not hold the table until it restarts; a chained
tickerplant learns schemas from its parent at start; and the HDB's older
partitions lack the table, so a query spanning dates before the first end of day
after publication needs it added to them (that is `dbmaint`, #1423, not this
workflow).

## What "registering a table" touches, in TorQ 5.2.12 (what `apply` will do)

TorQ builds a segmented tickerplant's tables, log handles and pub/sub state
once, at start, from its schema file. There is no API to add a table to a
running one, so apply does for one table what start-up does for all of them (the
code is `python/uqs/src/uqs/attach/q/`, read against `lib/torq/code/`):

  | State                                                                                       | On start-up                          | A live registration                                                                 |
  | ------------------------------------------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------               |
  | the table                                                                                   | the schema file defines it           | sets an empty typed table                                                           |
  | `.stpps.t`, `.stpps.schemas`, `.stpps.tabcols`                                              | `.stpps.init`, `.stpps.attrstrip`    | adds the table to each (not re-running them: `attrstrip` rewrites every live table) |
  | `.stplg.updtab`, `.t`, `.logtabs`, `.rolltabs`, `.msgcount`, `.rowcount`                    | `.stplg.init`                        | adds the table (without these a period roll skips its log)                          |
  | the table's log file and `currlog` handle                                                   | `.stplg.openlog`                     | `.stplg.openlog` for this table only                                                |
  | `.stpm.metatable` and the on-disk `stpmeta`                                                 | `.stpm.updmeta`                      | the same call: this is how a replaying subscriber finds the log                     |
  | an rdb/wdb's table and subscription                                                         | `.sub.subscribe` for all tables      | `.sub.subscribe` for this table only, schema and log replay included                |
  | `.rdb.subtables`                                                                            | set by `.rdb.subscribe`              | adds the table (or `reload` leaves rows behind)                                     |

End of day is self-healing: the tickerplant re-reads its root tables then, so a
live-registered table is logged the next day too. The wdb's table list is
computed at each save and handed to the sort process at end of day, so it needs
nothing else.

**Restart behaviour.** The registration lives in memory. To survive a restart of
the tickerplant, `apply` (default `--persist schema-file`) will append one
marked block to its `-schemafile`, leaving the rest of the file untouched.
`--runtime-only` is the explicit alternative: a restart drops the table and the
attachment reports `registration-lost` until it is applied again; `plan` already
records and checks the choice. The consumers need nothing persisted: restarted,
they subscribe to what the tickerplant has.
