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

`plan`, `validate`, `status`, `apply`, `verify`, `start` and `detach` exist.

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

## Accepting a blocker

A blocker can be accepted explicitly. This is for a finding you have assessed
yourself - most commonly `TORQ_VERSION`, because the site runs a TorQ this
procedure was never tested on (the site's is TorQ 4.2.0; the tested version is
5.2.12) - and it is the supported alternative to editing the validation code.

```bash
uqs job attach plan piggybank-uat --job piggybank_general_ledger --stack-id 6000 \
    --host 127.0.0.1 --discovery 127.0.0.1:6001 \
    --override TORQ_VERSION:discovery1 --override TORQ_VERSION:stp1 \
    --override TORQ_VERSION:rdb1 --override TORQ_VERSION:wdb1 \
    --override-reason "Validated against the site's TorQ 4.2.0 build"
uqs job attach apply piggybank-uat --yes --accept-risks
```

**An override accepts a risk. It does not establish compatibility or
correctness.** Accepting `TORQ_VERSION` does not add the version to
`SUPPORTED_TORQ`, and nothing in the output says it works.

- **Scoped.** `CODE:COMPONENT`, one finding on one process. An override for
  `stp1` says nothing about `rdb1`. There is no blanket `--force`.
- **A reason is mandatory**, and the override records who accepted it, when, and
  the facts it was accepted against (for a version, the version reported).
- **Still visible.** An accepted finding is shown as `accepted`, with its
  reason, the concrete failure risk and what the operator has to do, in `plan`,
  `validate`, `status` and `verify`. It is never shown as passing.
- **Void when the facts change.** Every `plan`, `validate`, `apply` and `verify`
  re-evaluates it against the stack as it is now; another TorQ version, another
  missing sort column or another process on the port makes it stale, and the
  blocker is back. `verify` fails on a stale acceptance, which closes the
  publisher's start gate.
- **Confirmed separately.** `apply` with accepted risks asks for them by name;
  `--yes` answers only "change the stack?". Use `--accept-risks` where there is
  no terminal. The confirmation and the overrides in force are recorded in the
  manifest.
- **Persisted** in `attachment.json` and the manifest. `plan --clear-overrides`
  withdraws all of them.

  | Code                                                                                               | Overridable | Because / the risk accepted                                                                         |
  | ---------------------                                                                              | ----------- | ------------------------------------------------------------------------------------------------    |
  | `TORQ_VERSION`                                                                                     | yes         | the TorQ internals driven here are unverified on that version; prefer `--runtime-only`              |
  | `SORT_CONFIG`                                                                                      | yes         | the wdb's end-of-day sort of the table fails; add a `sort.csv` row before end of day                |
  | `PORT_CONFLICT`                                                                                    | yes         | the publisher cannot bind its port; free it before starting the publisher                           |
  | `PUBLISHER_RUNNING`                                                                                | yes         | rows published during registration may be lost; stop the publisher                                  |
  | `SCHEMA_INCOMPATIBLE`, `NOT_PUBLISHED`                                                             | no          | a proven mismatch, or ownership that is unclear                                                     |
  | `STACK_MISMATCH`                                                                                   | no          | the process belongs to another stack                                                                |
  | `LOGGING_MODE`, `LOGS_DISABLED`, `CHAINED_TP`, `CONSUMER_FILTERED`, `CONSUMER_IGNORES`, `WDB_MODE` | no          | fixing them needs a restart or a config edit this workflow never makes; the table would not persist |
  | `PERSIST_UNAVAILABLE`                                                                              | no          | use `--runtime-only`, which accepts losing the registration on a tickerplant restart                |
  | `STORAGE_MISMATCH`, unreachable or missing processes                                               | no          | plan again; nothing can be verified                                                                 |

Detach's refusals (drift, unknown ownership, a subscriber that is not ours) are
the destructive-cleanup protections and cannot be overridden at all.

The host check treats a stack host given as an address this machine owns
(`127.0.0.1`, any `127.x`, or its own LAN address) as local; it used to compare
names only, so the machine's own non-loopback IP made a schema file on this very
disk look like it was on another machine. That was a false positive and is
fixed, not something to override.

## Apply, verify, start

```bash
uqs job attach apply pb-uat           # shows the plan, asks, then changes the stack
uqs job attach verify pb-uat          # reads only; passing permits the publisher
uqs job attach start pb-uat           # starts the job's process under the stack id
```

**apply** carries out exactly the changes the plan showed, in this order, and
nothing is restarted:

1. the tickerplant registers the table (one atomic call: if a step inside it
   throws, the steps already done are undone inside the same call);
2. each consumer subscribes to it, which creates the empty table and replays the
   tickerplant's log for it;
3. the marked block goes into the tickerplant's schema file. This is last on
   purpose: it is the only step that changes what a *restart* does, so an
   interrupted apply restarts back to the stack as it was.

The publisher is not started, and apply refuses outright if it is already
running. Every step is first written to the **manifest** (`manifest.json`, next
to `attachment.json`) as `pending` and finished after the call, so an
interruption leaves `pending` (outcome unknown), never a change nobody recorded.
Re-running apply asks the process about each unknown step - our token is on
whatever we created - instead of guessing, and a stack that already matches is
left alone: **no call that changes anything is made.**

A step that fails is reported with what is done, what is unknown and what
failed. Nothing is rolled back behind your back: that would be a second sequence
of remote changes with failures of its own, and cross-process atomicity is not
claimed. Run apply again, or `detach`.

What the manifest records for each operation: the step, the process, the table,
its status, the **ownership** (`created`: this attachment made it and may remove
it; `existing`: it was there and is never removed; `shared`: another attachment
made it and this one depends on it), what was true before, what it produced and
what it depends on. The ownership token is also written *into* the processes
(`.uqfattach.owned`, and inside the schema file's block, so a restarted
tickerplant comes back knowing the table is this attachment's). A second
attachment to the same table joins the first's record as a user, so detaching
the first is refused while it is still needed.

**verify** checks, without publishing a row, that the consumers can receive and
persist the table: the tickerplant holds it as a pub/sub table with the planned
shape, logged and rolled with the rest, time-stamped, its log open and on disk
and found by a replaying subscriber; every consumer holds the table, is
subscribed, does not ignore it, and will include it at its next save; the
subscriber handles on the tickerplant match; the ownership records are ours; the
schema file's block is unchanged. (A canary row would be a publication that
could never be taken back, so there isn't one.) Passing makes the attachment
`verified`; a failed re-verification takes it back to `applied`.

**start** is the only way the job's process is started under the attachment, and
the gate is not just this command. `stack/start_policy.refuse_start` - below
`uqs start`, the MCP server and the browser's start route - refuses the
attachment's publisher unless the attachment is `verified` **and** the start is
under the attachment's stack id. A bare `uqs start pb_ledger1`, which would use
the runtime's default base port and look for discovery at `6051`, is refused,
naming the attachment and the right command.

## Detach

```bash
uqs job attach detach pb-uat --dry-run   # every check, nothing changed, publisher not even stopped
uqs job attach detach pb-uat
```

Detach stops the publisher first, then decides from **evidence read off the
stack** - never from a flag this tool set - whether anything was published: the
tickerplant's message and row counters and buffered rows, any log larger than
the empty header (today's, and earlier days' under the log directory), rows in
the rdb/wdb, a partition in the wdb's save directory or the HDB. Evidence that
cannot be read (storage on another machine, a location never recorded) counts as
publication, because absence is unproven.

**Before any publication**, the state this attachment *created* is removed, in
the reverse of the order it was applied: the schema-file block, the wdb's and
rdb's table and subscription, then the tickerplant's registration, log files and
metadata. A pre-existing table, subscription, process or log configuration is
never touched, and each removal is re-verified inside the process (empty, ours,
nobody else subscribed) in the same call that does it.

**After publication** nothing is deleted. The publisher is stopped and every
registration, row, log and the schema block stays, because removing any of them
breaks replay of the log, loses rows the consumers hold, or changes what a
restart does. The attachment becomes `detached`; applying it again adopts what
stayed. Destructive cleanup is outside this workflow.

### When detach refuses

A reversal is refused, changing nothing but the publisher (stopped first, and
left stopped), when any of these holds, and each is listed with what a person
has to do:

  | Drift or unknown ownership                                                        | Manual action named                                     |
  | -------------------------------------------------------------------------------   | ------------------------------------------------------- |
  | a process that holds something this attachment created is not reachable           | start it, or remove the table by hand once it is safe   |
  | a table no longer has the shape that was created                                  | leave it, or drop it by hand                            |
  | the ownership record is missing or another attachment's                           | ownership cannot be shown; leave it                     |
  | another attachment is a user of the table                                         | detach that one first, or keep the table                |
  | a subscriber that is not one of the attached consumers, or a filtered one         | resolve it, then detach again                           |
  | the schema file was edited inside the block, or outside it, since apply           | restore it, or edit the block out by hand               |
  | a consumer's table was attached through a tickerplant table this one did not make | unsubscribe by hand                                     |

A consumer that restarted rebuilt its table from the tickerplant and lost its
in-memory ownership record; its copy is ours whenever the tickerplant's record
is.

A failure part way is reported as `rollback_partial` with what was done and what
is still in place. Nothing is claimed to be atomic across processes. Every step
is safe to repeat, so run detach again.

## Restart behaviour

  | State                          | A tickerplant restart                                                                                          |
  | ------------------------------ | -------------------------------------------------------------------------------------------------------------- |
  | applied, `schema-file` persist | the block recreates the table and its ownership record; consumers restarted afterwards take it with the rest   |
  | applied, `--runtime-only`      | the table is gone; `status` reports `registration-lost`, `verify` fails, `apply` restores it                   |
  | rolled back                    | the block is gone with the registration: the table does not come back                                          |
  | detached after publication     | the block stayed, so the table comes back, its logs replay, and `status` checks it                             |

TorQ does not reconnect an rdb or wdb to a restarted tickerplant on its own;
restarting the consumers afterwards is the usual TorQ practice, and they then
subscribe to every table the tickerplant has. The workflow never restarts them
for you.

## What "registering a table" touches, in TorQ 5.2.12

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
the tickerplant, `apply` (the default, `schema-file`) appends one marked block
to its `-schemafile`, leaving the rest of the file untouched. `--runtime-only`
is the explicit alternative: a restart drops the table and the attachment
reports `registration-lost` until it is applied again; `plan` already records
and checks the choice. The consumers need nothing persisted: restarted, they
subscribe to what the tickerplant has.
