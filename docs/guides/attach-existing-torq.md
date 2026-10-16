# Attaching a job to an existing TorQ stack

A uqf job can publish into a TorQ stack that this checkout did not start: a
running fleet with its own tickerplant, rdb, wdb and hdb. `uqs job attach` makes
that explicit and reversible. It sets up the two things the job's process needs
from the stack: the **stack's identity** (its base port, `-stackid` /
`KDBBASEPORT`, from which every process port and the discovery address follow)
and the **table** (a tickerplant publishes only tables it was started with). It
lives under `job` because the unit is a job and its tables;
[`connector`](uqs.md) is for external publishers that are not q processes.

```text
uqs job attach NAME --dry-run   preflight + plan; changes nothing
uqs job attach NAME             preflight + plan, ask, then apply -> verify -> start
uqs job detach NAME             stop the publisher, reverse what is safe to
```

What it guarantees:

- **It never restarts a process.** Everything it does to a running process is an
  ordinary IPC call. What would need a restart (a tickerplant logging all tables
  into one file, say) is a *blocker* in the plan, with the manual action named.
- **It never edits vendored TorQ** and leaves no q code on a process. The one
  thing it leaves is an ownership record, `.uqfattach.owned`.
- **It does not default the stack.** `--stack-id` has no default; every later
  command reads the stack id, discovery address and storage locations from the
  saved attachment.
- **It only reverses what it created**, from a recorded manifest.

## Walkthrough

```bash
uqs job attach pb-uat --job pb_general_ledger --bundle ../piggybank \
    --stack-id 6000 --consumer rdb1 --consumer wdb1 --dry-run
uqs job attach pb-uat --job pb_general_ledger --bundle ../piggybank \
    --stack-id 6000 --consumer rdb1 --consumer wdb1
uqs list attachments                # attachment state and publisher state, per attachment
uqs job detach pb-uat --dry-run
uqs job detach pb-uat
```

`--dry-run` resolves the configuration, reads the stack and prints the plan: the
stack and publisher identity, the table schemas, every affected component, the
changes and their reversibility, and the findings. It writes nothing - no
`attachment.json`, no manifest, no lock file - and exits 1 on a blocker.

Without `--dry-run` the same plan is shown, then a confirmation, then the stages
in order: save the configuration, apply, verify, start the publisher. Every
check runs again at execution, whatever a preview showed. A failed apply or
verify stops there: the publisher is not started, the command exits 1 and prints
what to run next. Running the same command again is safe: what is in place is
left alone, and an already verified attachment with its publisher running is
re-verified and left running.

- `--no-start` applies and verifies, and leaves the publisher stopped; the same
  command without it starts it later.
- `--yes` skips the confirmation; the plan and results are still printed.
- `--json` prints one JSON document; executing with it needs `--yes` (and
  `--accept-risks` when risks are accepted), and refuses with a JSON error
  otherwise.
- The global `uqs --debug` adds the configuration resolution, every verify
  check, timings and recovery detail. It works through `uqs --target`, and does
  not change how the publisher logs.

A new attachment needs `--job` and `--stack-id`; `--host` (default `localhost`),
`--discovery HOST:PORT` (default: the stack id + 1), `--tickerplant`,
`--consumer` (default: all), `--user`, `--password-env`,
`--tplogdir`/`--schema-file` (pin storage) and `--runtime-only` are optional.
After the first execution the attachment is reused by name. Options that
contradict the saved ones are refused; `--replace` rebuilds it from them,
keeping its manifest and accepted risks, and is refused while the manifest holds
changes on another stack or for other tables. Credentials are redacted from
every output. `uqs job attach --help` lists the rest.

### Migration from the stage commands

  | Before                                   | Now                                                        |
  | ---------------------------------------- | ---------------------------------------------------------- |
  | `uqs job attach plan NAME ...`           | `uqs job attach NAME ... --dry-run`                        |
  | `uqs job attach validate NAME`           | `uqs job attach NAME --dry-run`                            |
  | `uqs job attach apply NAME`              | `uqs job attach NAME --no-start`                           |
  | `uqs job attach verify NAME`             | `uqs job attach NAME --no-start`                           |
  | `uqs job attach start NAME`              | `uqs job attach NAME`                                      |
  | `uqs job attach status NAME`             | `uqs list attachments`, or `uqs job attach NAME --dry-run` |
  | `uqs job attach detach NAME [--dry-run]` | `uqs job detach NAME [--dry-run]`                          |
  | `plan --no-save`                         | `--dry-run`                                                |

The old forms are refused with the command that replaced them.

### Configuration

The first execution writes `attachment.json` under `$UQS_ATTACH_DIR`, else
`$UQS_DATA_ROOT/attachments`, else `output/attachments`: the stack id, discovery
address, tickerplant and consumers, the user and the *name* of the environment
variable holding the password (never the password), the persist mode, and the
storage the stack reported (tickerplant log directory, schema file, wdb save
directory, hdb directory). A later run that finds the stack somewhere else
refuses. Preflight only ever asks the stack `probe`, a read-only question.

### Apply

Apply, the second stage, carries out exactly the changes the plan showed, in
order:

1. the tickerplant registers the table (one atomic call; a failing step undoes
   the steps already done inside it);
2. each consumer subscribes, which creates the empty table and replays the
   tickerplant's log for it;
3. a marked block goes into the tickerplant's schema file. This is last on
   purpose: it is the only step that changes what a *restart* does.

The publisher is not started, and apply refuses if it is running. Each step is
written to the manifest (`manifest.json`, beside `attachment.json`) as `pending`
before it runs, so an interruption leaves an unknown outcome, never an
unrecorded change. Re-running asks the process about each unknown step, and a
stack that already matches is left alone. A failed step is reported with what is
done, unknown and failed; nothing is rolled back behind your back. Run
`uqs job attach NAME` again, or `uqs job detach NAME`.

The manifest records each operation's ownership: `created` (this attachment made
it and may remove it), `existing` (never removed), or `shared` (another
attachment made it and this one depends on it; detaching the first is refused
while it is needed). The token is also written into the processes, including the
schema file's block. An attachment that shares a table another one created
writes a block of its own, `<name>+uses`, which makes it a user of that table
again after a tickerplant restart; its detach removes that block.

### Verify and start

Verify checks, without publishing a row, that the tickerplant holds the table as
a logged pub/sub table of the planned shape, that every consumer holds it, is
subscribed, does not ignore it and will include it at its next save, and that
the ownership records and the schema block are ours. Passing makes the
attachment `verified`; a failed re-verification takes it back to `applied`.

The gate is not just `uqs job attach`. `stack/start_policy.refuse_start`, below
`uqs start`, the MCP server and the browser's start route, refuses the
attachment's publisher unless the attachment is `verified` **and** the start is
under its stack id. A bare `uqs start pb_ledger1` would use the runtime's
default base port, and is refused with the right command named. `uqs start`,
`start all` and `restart all` are gated too: `all` is expanded first to the
startwithall=1 processes it launches, and a job declared `start_with_all 1b` is
one of them. The gate fails closed: a publisher whose `manifest.json` cannot be
read (permissions, truncated, no `state`) is refused, and an `attachment.json`
that cannot be read refuses every start, since its publisher cannot be known.
Make the file readable again, or move the folder out of the attachments
directory if the attachment is gone.

### Detach

Detach stops the publisher, then decides from evidence read off the stack, never
from a flag this tool set, whether anything was published: tickerplant message
and row counters and buffers, logs larger than the empty header, rows in the
rdb/wdb, a partition in the wdb's save directory or the hdb. Evidence that
cannot be read counts as publication.

- **Before any publication**, what the attachment *created* is removed in
  reverse order: the schema block, the consumers' table and subscription, then
  the tickerplant's registration, logs and metadata. Anything that pre-existed
  is never touched, and each removal is re-checked inside the process (empty,
  ours, nobody else subscribed).
- **After publication** nothing is deleted: registration, rows, logs and the
  schema block stay, because removing them breaks replay or changes what a
  restart does. The attachment becomes `detached`; applying again adopts what
  stayed. Destructive cleanup is outside this workflow.

Detach refuses, changing nothing but the stopped publisher, when a process
holding something it created is unreachable; a table no longer has the created
shape; the ownership record is missing or another attachment's; another
attachment uses the table; a subscriber is not one of the attached consumers; or
the schema file was edited since apply. Each refusal names the manual action. A
failure part way is `rollback_partial`, listing what is still in place; every
step is safe to repeat, so run detach again.

## Findings and overrides

The plan lists findings as blockers, warnings, or what will be adopted. A
blocker can be accepted explicitly where the table says so; this is the
supported alternative to editing the validation code.

```bash
uqs job attach pb-uat --job pb_general_ledger --stack-id 6000 \
    --override TORQ_VERSION:discovery1 --override TORQ_VERSION:stp1 \
    --override TORQ_VERSION:rdb1 --override TORQ_VERSION:wdb1 \
    --override-reason "Reviewed against the site's TorQ build" --yes --accept-risks
```

**An override accepts a risk. It does not establish compatibility.** Accepting
`TORQ_VERSION` does not add the version to the tested list.

- **Scoped**: `CODE:COMPONENT`, one finding on one process. There is no blanket
  `--force`.
- **A reason is mandatory** (`--override-reason`); the override records who
  accepted it, when, and the facts it was accepted against.
- **Still visible**: shown as `accepted` with the failure risk and what the
  operator must do, in the plan and the verify checks.
- **Void when the facts change** (another TorQ version, another missing sort
  column, another process on the port): re-evaluated at every preflight, apply
  and verify; verify fails on a stale acceptance.
- **Confirmed separately**: execution asks for accepted risks by name. `--yes`
  answers only "change the stack?"; use `--accept-risks` without a terminal.
- **Persisted** in `attachment.json` and the manifest; `--clear-overrides`
  withdraws all of them, and is saved even when that leaves a blocker.

  | Code                                                                                                                 | Overridable | Refused because / risk accepted                                                                                                                      |
  | -----------------------------------------------------------------------------                                        | ----------- | -----------------------------------------------------------------------------------------------------------------                                    |
  | `TORQ_VERSION`                                                                                                       | yes         | the process runs a TorQ other than the tested one; its internals are unverified there (prefer `--runtime-only`)                                      |
  | `SORT_CONFIG`                                                                                                        | yes         | the wdb's `sort.csv` cannot sort the table; end of day would fail. Add a row first                                                                   |
  | `PORT_CONFLICT`                                                                                                      | yes         | the publisher's port is held by another process; free it before starting                                                                             |
  | `PUBLISHER_RUNNING`                                                                                                  | yes         | the publisher is running; rows published during registration may be lost                                                                             |
  | `SCHEMA_INCOMPATIBLE`, `NOT_PUBLISHED`                                                                               | no          | the table exists with other columns or order (no conversion), or is not a pub/sub table                                                              |
  | `UNTYPED_COLUMN`                                                                                                     | no          | a saved attachment has an untyped `()` column (a bundle cannot: the bundle itself is refused); declare a type and plan again                         |
  | `STACK_MISMATCH`                                                                                                     | no          | a process answers but belongs to another stack                                                                                                       |
  | `LOGGING_MODE`, `LOGS_DISABLED`, `CHAINED_TP`                                                                        | no          | the tickerplant does not log one file per table, or at all, or is chained; fixing it needs a restart                                                 |
  | `CONSUMER_FILTERED`, `CONSUMER_IGNORES`, `WDB_MODE`                                                                  | no          | an rdb/wdb filters or ignores the table, or the wdb does not save: it would never persist                                                            |
  | `PERSIST_UNAVAILABLE`                                                                                                | no          | the registration could not survive a tickerplant restart (no writable local `-schemafile`); use `--runtime-only`                                     |
  | `STORAGE_MISMATCH`                                                                                                   | no          | a pinned location is not what the stack reports; `--replace` with the right one                                                                      |
  | `DISCOVERY_UNREACHABLE`, `UNREACHABLE`, `NO_TICKERPLANT`, `AMBIGUOUS_TICKERPLANT`, `NO_CONSUMER`, `UNKNOWN_CONSUMER` | no          | an endpoint does not answer or rejects the credential, or there is nothing (or more than one thing) to publish to or persist with                    |

Detach's refusals are destructive-cleanup protections and cannot be overridden.

## Restart behaviour

The registration lives in memory. By default (`schema-file`) apply appends one
marked block to the tickerplant's `-schemafile` and leaves every other byte
untouched, line endings included; removing the block restores the file exactly,
whatever order attachments sharing the file are removed in. `--runtime-only`
skips it.

  | State                          | A tickerplant restart                                                                                       |
  | ------------------------------ | -------------------------------------------------------------------------------------------                 |
  | applied, `schema-file` persist | the block recreates the table and its ownership record                                                      |
  | applied, `--runtime-only`      | the table is gone; `--dry-run` reports `registration-lost`, verify fails, `uqs job attach NAME` restores it |
  | rolled back                    | the block went with the registration: the table does not come back                                          |
  | detached after publication     | the block stayed, so the table comes back and its logs replay                                               |

End of day needs nothing extra: the tickerplant re-reads its tables then, and
the wdb hands its table list to the sort process.

## Limits and known issues

- **Tested TorQ: 5.2.12 only.** Any other version raises `TORQ_VERSION`; the
  override accepts the risk without establishing compatibility. A compatibility
  run of TorQ 4.2.0 with starter pack 1.11.1 on KDB-X (not kdb+ 4.x) found every
  TorQ symbol attach uses present and 195 of 196 attach tests passing with 4.2.0
  allowed. One behavioural difference: 4.2.0's STP end of day (`.stplg.stpeod`)
  does not flush batched rows before the end-of-day message.
- **TorQ does not reconnect an rdb or wdb to a restarted tickerplant.** The
  operator restarts them, after which they subscribe to every table the
  tickerplant has; the workflow never restarts a process it did not start.
- **Older HDB partitions lack the table** until repaired after the first end of
  day: `uqs data hdb-check --fix` (dbmaint,
  [#1428](https://github.com/kwojdalski/uqf/pull/1428)). The plan warns with
  `HDB_HISTORY`.
- **Connection cap.** Under a licence with a connection limit (the KDB-X
  community licence allows 16 per process), an attachment adds one persistent
  publisher connection to the tickerplant and one to discovery. Preflight does
  not check the headroom.
- **One rule until the bug below is fixed.** Do not name an attached table after
  a q or TorQ global (`upd`, `sym`, `loghandles`, `date`, `endofday`): preflight
  passes it and apply overwrites the global on the tickerplant, rdb and wdb
  ([#1450](https://github.com/kwojdalski/uqf/issues/1450)).
- **Every column needs a concrete type.** A bundle's `tables.q` is refused if a
  column is a bare `()` with no declared element type, or one declared
  `nested[...]` as `" "` (any value). The tickerplant resets such a column to
  `()` after every publish and the wdb keeps it general, so each batch decides
  its stored type afresh; once the wdb has written one shape, a batch of another
  fails the append, TorQ's writedown re-raises it, and end of day aborts for
  every table (not just this one). Write ``note:`symbol$()``, or for a list per
  row (an order book's price ladder) a `()` column plus a line giving the
  element type, which the uqf publisher checks on every row:
  ``nested[`book;`bid_prices`bid_sizes!"FJ"]``. Tested on a stack: such a column
  survives flushes whose row counts and vector lengths differ, and end of day. A
  publisher that is not uqf must keep to the declared element type itself
  ([#1451](https://github.com/kwojdalski/uqf/issues/1451)). TorQ's own fix, a
  per-table trap in the wdb's writedown, belongs upstream.
- **Open bugs** in the workflow are tracked as [GitHub issues titled
  `uqs job attach`](https://github.com/kwojdalski/uqf/issues?q=is%3Aissue+is%3Aopen+%22uqs+job+attach%22).
