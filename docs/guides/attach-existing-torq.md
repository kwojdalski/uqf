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
uqs job attachment list                # attachment state and publisher state, per attachment
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
`--discovery HOST:PORT` (default: the stack id + 1; its HOST must be the
`--host` machine, since a stack is attached on one host), `--tickerplant`,
`--consumer` (default: all), `--user`, `--password-env`,
`--tplogdir`/`--schema-file` (pin storage), `--runtime-only` and
`--remote-schema-file` are optional. After the first execution the attachment is
reused by name. Options that contradict the saved ones are refused; `--replace`
rebuilds it from them, keeping its manifest and accepted risks, and is refused
while the manifest holds changes on another stack or for other tables.
Credentials are redacted from every output. `uqs job attach --help` lists the
rest.

### A publisher on this machine, a stack on another (#1651)

The publisher always runs on the machine `uqs` runs on, so its source driver and
credentials stay there, and only the tables travel, over q IPC. `--host` names
the stack's machine.

- **Start, stop and the running check** are this machine's: the publisher's port
  is checked here, never on `--host`, where another process may hold it.

- **The publisher is told the stack's discovery.** A TorQ process finds
  discovery through its process.csv, whose discovery row is this machine's.
  Every start of the publisher, `uqs job attach` or a bare `uqs start`, adds
  `-extras -.servers.DISCOVERY :HOST:PORT` from the attachment, so it registers
  with the stack's discovery and gets the tickerplant from there. `-extras`
  reaches every process of one start, so the publisher is started on its own; a
  start that names it beside other processes is refused.

- **The stack's processes are asked where discovery lists them**, not at
  `--host`. A loopback name in that listing means the stack's own machine, so it
  is asked at `--host`. A name that does not resolve from here is reported as
  that, so a tunnel to discovery alone does not pass for a reachable stack.

- **The stack must be able to reach the publisher back.** TorQ registers the
  publisher under this machine's host name, recorded in the attachment as
  `advertised_host`, and discovery connects back to what it lists. With the
  stack elsewhere, the plan refuses:
  - `ADVERTISED_LOOPBACK`: the name is a loopback one;
  - `ADVERTISED_UNRESOLVABLE`: discovery cannot resolve the name (it is asked
    with `.Q.addr`, read-only).

  Neither can be overridden.

- **The stack must reach the running publisher.** The port can only be tried
  once the publisher listens, so it is tried then. Straight after the start, a
  `reach` stage asks discovery, with the read-only `reach` operation, to open a
  handle to `advertised_host:port` and close it at once. Discovery opens it the
  way TorQ's `.servers.opencon` does, with the stack's own credentials and a
  2-second timeout, and it is retried for up to 30 seconds while the publisher
  loads. If it fails, `PUBLISHER_UNREACHABLE_FROM_STACK` names the address and
  q's reason (`timeout`, `access`, `hop. OS reports: ...`). The attachment then
  goes back to `applied`, the publisher this run started is stopped, and the run
  fails, so it is never reported attached. Verify asks the same question
  whenever the publisher already runs here. The code cannot be overridden.

- **The tickerplant's schema file is on the stack's machine**, so this machine
  never opens its path. The default, `schema-file`, is refused there with
  `PERSIST_UNAVAILABLE`, which names the three ways out:
  - `--remote-schema-file`: the tickerplant writes the block itself (below);
  - `--runtime-only`: no block, and the registration is lost on a restart;
  - run `uqs` on the stack's machine.

- **Runtime-only** is the persistence that needs nothing of the stack's disk.
  The plan's `RUNTIME_ONLY` warning and verify's output both say that a
  tickerplant restart drops the registration and the attachment must be
  re-applied. With the stack elsewhere they add that nothing on this machine is
  told of that restart: stop the publisher, then run `uqs job attach NAME`.
  `uqs job attach` re-verifies that the tickerplant still holds the table before
  it starts the publisher. So does every other start of it (`uqs start`, a
  profile, the MCP server, the browser): for a runtime-only attachment or a
  stack on another machine, the start gate asks the tickerplant, with the
  read-only `probe` and a 3-second timeout, whether each attached table is still
  one of its tables and its pub/sub tables. A table it lost refuses the start,
  naming the table, the stack and `uqs job attach NAME` to re-apply. A stack
  that does not answer refuses it too: unknown is not read as registered. An
  attachment on this machine with schema-file persistence is not asked.

#### Durable registration through the tickerplant

`--remote-schema-file` is recorded in the attachment as
`persist: remote-schema-file`, like `--runtime-only`. It is not a flag for one
run, and it excludes `--runtime-only`. The block is the one `schema-file`
writes, with the same token, and only the process that writes it changes:

- **The tickerplant changes its own file.** The `schema_file` operation runs in
  the tickerplant, on the file it was started with (`-schemafile`), never on a
  path taken from the request. It needs no remote filesystem access, no SSH and
  no new service: only the q IPC and the credential every other operation uses.

- **Python composes, the tickerplant checks.** `uqs` reads the file through the
  tickerplant and computes one append or one cut, by the rules `schema-file`
  uses. The tickerplant refuses the change unless:
  - the file's md5 is still the one the change was computed from;
  - an append is exactly one whole block for this name and token, after a
    newline only when the file lacks a final one, and the file does not hold
    that block yet;
  - a cut removes exactly that block, from the start of a line, and at most the
    file's final newline.

- **How the bytes are written.** An append opens the file for appending, so the
  bytes already there are never rewritten. A cut keeps the old bytes in
  `<file>.uqs-attach.bak`, writes the new ones to `<file>.uqs-attach.tmp` and
  `cp`s them over the file, which keeps its mode, owner and inode. It reads the
  file back, then deletes both. If the copy fails, the backup stays for a
  person.

- **Preflight asks the tickerplant, too.** The plan reports `WILL_PERSIST` or
  `ALREADY_PERSISTED`, or refuses with `PERSIST_UNAVAILABLE` when the file:
  - is not given (`-schemafile`);
  - does not exist on the stack's machine;
  - cannot be appended to by the tickerplant's user;
  - is one uqf generates.

- **Journalled and checked like the local block.** The manifest's `schema_block`
  (or `schema_uses`) step records the tickerplant as the block's `process`.
  Verify, detach's drift check and the removal read the block back through that
  tickerplant, whatever the attachment's settings say later. A tickerplant that
  cannot be asked is a refusal, never a local read.

- **Reversed before publication only.** On a stack elsewhere the publication
  evidence on disk is read by the tickerplant too (`disk_marks`, read-only):
  - earlier days' logs;
  - a partition in the wdb's save directory or in the HDB.

  So detach can tell "nothing was published", remove the block through the
  tickerplant, and then roll back the rest in reverse order. After publication
  the block stays, as it does on one machine.

The operation runs code, through the tickerplant's access list, in a process
other people depend on. It only appends or cuts a block it can check, but the
block is q that the tickerplant runs at every start. Anyone the access list lets
send this already has that power, so the opt-in grants no new privilege. It does
record, in the attachment, that the stack's operators agreed to it.

#### Credentials, in each direction

Three credentials meet when the stack is elsewhere. Each refusal has its own
code, so it is not reported as an unreachable address. None can be overridden.

  | Credential                                                                      | Used for                                                             | Refused                                                                                                                      |
  | ------------------------------------------------------------------------------- | -------------------------------------------------------------------  | ----------------------------------------------------------------------------------------------------------------             |
  | The attachment's (`--user`, `--password-env`)                                   | `uqs` asking the stack's processes, at plan, apply, verify, detach   | `STACK_REFUSES_ATTACH_CREDENTIALS`, from kola's authentication failure, in place of `DISCOVERY_UNREACHABLE` or `UNREACHABLE` |
  | The publisher's own TorQ credential (`.servers.USERPASS`, its passwords files)  | the publisher connecting to discovery and the tickerplant            | `STACK_REFUSES_PUBLISHER_CREDENTIALS`                                                                                        |
  | The stack's (discovery's `.servers.USERPASS`)                                   | discovery connecting back to the publisher                           | `PUBLISHER_REFUSES_STACK_CREDENTIALS`                                                                                        |

The two directions are tried with the read-only `reach` operation once the
publisher runs, straight after the start and in verify: - **The publisher to the
stack.** `uqs` asks the running publisher, with its own local credential, to
open and close a handle to discovery and to the tickerplant. The publisher opens
it with its own credential, the one it publishes with. - **The stack to the
publisher.** Discovery is asked to open a handle to `advertised_host:port`.

q's `access` is the refusal in both directions. Any other failure is
`STACK_UNREACHABLE_FROM_PUBLISHER` or `PUBLISHER_UNREACHABLE_FROM_STACK`. As
with reaching back, a failure after the start closes the gate again and stops
the publisher this run started. Verify reports each direction as its own check.

The tickerplant is tried at the address `uqs` reached it at. If discovery lists
it under a loopback name, the publisher's own TorQ connects to that literal name
instead, which is this machine, not the stack's.

#### What one machine has not shown

`test_attach_two_hosts.py` runs these operations against real KDB-X processes on
one machine: - the stack's processes are asked at this machine's host name,
which is not `localhost` and resolves to a non-loopback address, and the
attachment treats that host as remote; - the publisher is advertised under this
machine's IPv4 address, another name than the stack host, and discovery connects
back to that; - each process enforces its own access list (`-U`), with a
separate credential for each direction.

The tickerplant appends and removes the block in its own file, leaving its
bytes, mode and inode as they were. It refuses a stale or malformed change, and
reads the publication evidence off its disk. Each credential refusal comes back
as its own code.

Still unverified without a second machine: - **A network between them.** No
firewall, NAT, VPN or routing: every connection stays inside one host's network
stack, so a port the stack's machine cannot open is never seen. - **Name
resolution that differs by side.** Both "machines" share one resolver and one
hosts file. A name that resolves here and not on the stack's machine (or to a
different address) is caught only by discovery's own `.Q.addr` check, which ran
on the same host. - **Separate file systems and users.** The tickerplant here
runs as the same OS user as the tests, on a disk `uqs` could also read. That no
local read happens is shown with the fake stack, whose schema file is at a path
that does not exist here, not by a real second disk. The tickerplant's own user,
owner and permissions on a shared production file are untested. - **A whole TorQ
stack on the other machine, on KDB-X.** The lane runs plain q processes with the
attach operations. A whole TorQ stack in another container, publisher rows
included (acceptance criterion 1), runs on PeachQ only: see [Testing against a
remote stack](#testing-against-a-remote-stack). - **Clocks, latency and
timeouts** across a real link: the 2-second `reach` timeout and the 30-second
start wait were never tested over a slow path.

#### What a publish acknowledges

This is what a polling feed (a job declaring `poll`,
`src/etl/core/stream_poll.q`) does when `torq_stream.q` runs it. A job with its
own `on_timer` or `on_batch` orders its own steps. None of it is exactly-once.

- **The send is synchronous.** The job's `publish` is
  `` .qtorq.publish_reconnecting[`.qproc.stream.h;;] ``. It sends
  `` h (`.u.upd;t;x) `` on the handle `.qproc.stream.h` holds, and returns once
  the tickerplant's `.u.upd` has returned. What `.u.upd` has done by then
  depends on the segmented tickerplant's batch mode
  (`lib/torq/code/segmentedtickerplant/stplog.q`):

  | Batch mode     | When the publish returns, the rows are                                                            |
  | -------------- | ------------------------------------------------------------------------------------------------- |
  | `defaultbatch` | in the tickerplant's memory and written to the table's log; sent to subscribers on its next timer |
  | `immediate`    | written to the log and sent to the subscribers                                                    |
  | `memorybatch`  | in the tickerplant's memory only; logged and sent on its next timer                               |

- **One tick runs in this order:**
  1. load the cursor, and read the in-flight marker beside it;
  2. fetch the page after the cursor;
  3. normalize it;
  4. refuse the whole page if it does not fit the plant's tables, or if its next
     cursor would not move;
  5. if the marker says the previous page is in doubt, settle it (below);
  6. write the marker;
  7. publish each table in turn, confirming each with the tickerplant when the
     poll declares `ack`;
  8. save the cursor;
  9. remove the marker.

  A timestamp cursor is the file `<name>.cursor` (the poll's `cursor`, else the
  job's name) in `$UQF_STATUS_DIR`, else `$TORQDATA/status`, on the publisher's
  machine. The marker is `<name>.inflight` in the same directory. It holds the
  cursor the page was fetched after (`from`), the cursor it would move to
  (`to`), and the page's row count. Both files are written to a temporary file,
  the old one kept as `.bak`, then renamed into place. Neither is fsynced. A
  feed with its own `load` and `save` keeps its cursor where those put it, and
  its marker still goes in the status directory.

- **A reconnect after a tickerplant restart.** When the handle is closed, the
  publish reopens it once, through TorQ's server registry
  (`.servers.gethandlebytype`, which retries every registered tickerplant whose
  handle is closed), stores the new handle in `.qproc.stream.h`, logs
  `tickerplant handle reopened`, and sends again.
  - A handle that was already closed before the send is reopened first, so
    nothing is sent twice.
  - A handle that closes during the send is reopened and the send repeated. This
    is logged as `possibly duplicated`, because the old tickerplant may have
    logged the first send before it went.
  - If no tickerplant can be reached, the error propagates. The tick fails, the
    cursor stays, and the next tick tries again.
  - An error on a handle that is still open is the tickerplant's own, and is
    raised as it is.

  The reconnect restores the publish handle only. A runtime-only registration is
  still lost with the restart, so re-apply the attachment
  (`uqs job attach NAME`) as above.

- **An in-doubt page is settled, not blindly resent.** A marker found at the
  start of a tick means the previous page was interrupted between the publish
  and the save: by a crash, a kill, a publish that threw part-way, or a refused
  acknowledgement. Some of it may already be in the tickerplant. If the saved
  cursor equals the marker's `to`, the save happened and only the removal did
  not, so the marker is removed and nothing is in doubt. Otherwise:
  - **With a declared `row_key`** (the poll's `row_key`, symbols naming the
    columns that identify a published row, never `time`), the publisher asks an
    rdb which keys of the re-fetched page each table already holds, and
    publishes only the absent rows. The rdb is found through discovery the first
    time a page is in doubt, so it costs a connection only then. The tick logs
    `in-doubt page resolved by row key` with the cursor range and the rows it
    left out. If no rdb can be reached, or its answer is not one boolean per
    key, the tick fails and nothing is sent.
  - **Without a `row_key`**, or with no reader wired (the plain-q runner,
    `run_stream.q`), the page is published again, as before. The tick logs
    `in-doubt page published again - possibly duplicated` at WARNING with the
    marker's `from` and `to` and the reason. It is also counted in the job's
    health: `.qetl.stream_health.doubts_of`, and `possibly_duplicated_pages` and
    `possibly_duplicated_rows` in `stream_health_<job>.txt`.

  What the rdb answers is what it holds, not what the tickerplant logged. A row
  logged but not yet delivered (`defaultbatch` delivers on its timer), rows an
  rdb has already written out at end of day, and rows another publisher sent
  under the same key all answer wrongly. The first two are sent again, and the
  third is left out.

- **An acknowledgement, when asked for.** A poll declaring `ack` 1b confirms
  each publish. On the publish handle, before and after the `.u.upd`, it reads
  how many rows of the table the tickerplant has logged. That is
  `.stplg.rowcount`, plus `.stplg.tmprowcount` in `defaultbatch`, where the log
  write happens in `.u.upd` but the count only on the timer. If the count did
  not advance by the rows the publish reports sending, the tick fails with
  `was not acknowledged`. The cursor does not move, and the page is in doubt on
  the next tick. This catches rows that the error mode trapped and rows that the
  tickerplant never logged. Without `ack`, nothing is asked. `memorybatch` logs
  only on its timer, so it refuses `ack` before anything is sent.

  The count is the tickerplant's, for all publishers together. Another
  publisher's rows can make a short publish look counted, and a restart or end
  of day that resets the counters can make a counted one look short.

- **What is still not acknowledged.**
  - That an rdb or wdb received or saved the rows.
  - That the log write reached the disk. Nothing asks for an fsync, not even
    `ack`.
  - Without `ack`, that the rows were accepted. With the tickerplant's error
    mode on (`.stplg.errmode`), a `.u.upd` that throws is written to its error
    log and the call returns normally. A table the tickerplant does not define
    discards the rows without an error (`torq_pipeline.q`, invariant 8), which
    `.qtorq.assert_publishable` checks only when the publisher starts.

  Delivery is therefore at least once from the cursor's side, for rows the
  tickerplant accepted and kept. Duplicates can still reach the tables: a page
  republished without a `row_key`, a send repeated after the handle closed
  mid-call, and the row-key gaps above. Each is logged, and the first is
  counted. Rows can still be lost: rows `memorybatch` held when the tickerplant
  died, rows its error mode trapped (unless `ack` is on), and rows sent onto a
  table it no longer defines.

### Migration from the stage commands

  | Before                                   | Now                                                           |
  | ---------------------------------------- | ----------------------------------------------------------    |
  | `uqs job attach plan NAME ...`           | `uqs job attach NAME ... --dry-run`                           |
  | `uqs job attach validate NAME`           | `uqs job attach NAME --dry-run`                               |
  | `uqs job attach apply NAME`              | `uqs job attach NAME --no-start`                              |
  | `uqs job attach verify NAME`             | `uqs job attach NAME --no-start`                              |
  | `uqs job attach start NAME`              | `uqs job attach NAME`                                         |
  | `uqs job attach status NAME`             | `uqs job attachment list`, or `uqs job attach NAME --dry-run` |
  | `uqs job attach detach NAME [--dry-run]` | `uqs job detach NAME [--dry-run]`                             |
  | `plan --no-save`                         | `--dry-run`                                                   |

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

1. the tickerplant registers the table in one call with a best-effort in-process
   rollback, not an atomic one: when a step fails, it and the steps before it
   are undone inside the same call, and the answer lists any step whose undo
   itself failed;
2. each consumer subscribes, which creates the empty table and replays the
   tickerplant's log for it. Every other subscription the consumer holds stays
   as it was, including the inactive ones TorQ resubscribes from when their
   tickerplant comes back (TorQ's own subscribe would delete those);
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

A consumer that already held the table, with the same shape, but had no live
subscription for it is subscribed to the attachment's tickerplant. The table
stays `existing`; the subscription it gained (and its `.rdb.subtables` entry,
when attach added one) is recorded separately as `created`, as a
`consumer_subscribe` operation, so a rollback takes back exactly that
subscription and nothing else. This is recorded only when the tickerplant's
table is the attachment's own; through a table it does not own, the subscription
is left in place, as before.

### Sort rules

A stack's `sort.csv` `default` rows may name a column the attached table lacks
(`securityId`, say), and end of day then cannot sort it: preflight refuses with
`SORT_CONFIG`. A table that needs its own layout declares it beside its
definition, in the bundle's `tables.q` or `src/etl/plant_tables.q`:

```q
piggybank_general_ledger:([] time:`timestamp$(); sym:`symbol$(); amount:`float$())
sorting[`piggybank_general_ledger;`sym`time!`p`]
```

The keys are the sort columns in order, the values each one's attribute (`` ` ``
for none) - the `column`, `att` and `sort=1` of a `sort.csv` row.
`.qetl.plant.sorting` and the bundle reader refuse a column the table lacks or
holds lists in, an attribute other than `p s g u`, and `p` or `s` off the first
column. Nothing is inferred from column names: without a declaration the stack's
own rows decide, and `SORT_CONFIG` stays a blocker when they cannot sort the
table.

With a declaration, the plan reads every process that sorts the table - each
managed wdb and the sort and sortworker processes it names - the file each
loads, and that file's and the live `.sort.params` rows for the table. It shows
them as `present` (reused, never removed), `add`, or a conflict, which is a
blocker (`SORT_CONFLICT`): attaching never replaces an operator's sort policy.
Apply then, before registering the table:

1. appends exactly the declared rows to the file, in its column order and line
   endings, after copying it into the attachment's `backups/`. The write is an
   atomic replace that keeps the file's mode, refused if the file changed since
   the plan you confirmed;
2. reloads each process with TorQ's own loader (`.sort.getsortcsv` on the file
   that process is configured with) and checks that the declared rows are live.

A failed write, reload or check fails apply, and the publisher is not started.
Re-running is safe: rows already in the file are not written twice, and only the
reload is repeated. Where no safe reload exists - a process that is running but
does not answer, or whose live rules differ from its file for other tables
(`SORT_DRIFT`: a reload would also apply that edit) - the plan refuses and names
the manual step. A sort or sortworker process that is not running is a warning:
it reads the file when it starts.

TorQ's loader takes no comment lines, so the file holds no marker. Ownership is
in the manifest, one `sort_rows` operation per file and table: the exact text
added, the rows, and the file's hash before and after. Verify checks the rows in
each file and live on each process. Detach before publication removes only rows
this attachment added, only while they are exactly as written, and refuses when
an operator changed them, added rows for the table, or another attachment's
manifest relies on them; it reloads and checks the live rules before reporting
the rows reverted. After publication the rows are kept.

### Verify and start

Verify checks, without publishing a row, that the tickerplant holds the table as
a logged pub/sub table of the planned shape, that every consumer holds it, is
subscribed, does not ignore it and will include it at its next save, and that
the ownership records and the schema block are ours. It also re-runs the whole
preflight and fails on every blocker the plan would refuse on that is not
validly accepted - a sort.csv that lost its default row, a wdb that stopped
saving, a consumer that became filtered - with one exception: the attachment's
own publisher running, which is what a started attachment looks like. Passing
makes the attachment `verified`; a failed re-verification takes it back to
`applied`.

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
cannot be read counts as publication. With the stack on another machine, the
tickerplant reads the logs and partitions on its disk (`disk_marks`); this
machine never looks for those paths.

- **Before any publication**, what the attachment *created* is removed in
  reverse order: the schema block, the consumers' table and subscription (or,
  for a table a consumer already held, only the subscription attach added), then
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
    --reason "Reviewed against the site's TorQ build" --yes --accept-risks
```

**An override accepts a risk. It does not establish compatibility.** Accepting
`TORQ_VERSION` does not add the version to the tested list.

- **Scoped**: `CODE:COMPONENT`, one finding on one process. There is no blanket
  `--force`.
- **A reason is mandatory** (`--reason`, formerly `--override-reason`); the
  override records who accepted it, when, and the facts it was accepted against.
- **Still visible**: shown as `accepted` with the failure risk and what the
  operator must do, in the plan and the verify checks.
- **Void when the facts change** (another TorQ version, another missing sort
  column, another process on the port): re-evaluated at every preflight, apply
  and verify; verify fails on a stale acceptance.
- **Confirmed separately**: execution asks for accepted risks by name. `--yes`
  answers only "change the stack?"; use `--accept-risks` without a terminal.
- **Persisted** in `attachment.json` and the manifest; `--clear-overrides`
  withdraws all of them, and is saved even when that leaves a blocker.

  | Code                                                                                                                  | Overridable | Refused because / risk accepted                                                                                                                                               |
  | -----------------------------------------------------------------------------                                         | ----------- | -----------------------------------------------------------------------------------------------------------------                                                             |
  | `TORQ_VERSION`                                                                                                        | yes         | the process runs a TorQ other than the tested one; its internals are unverified there (prefer `--runtime-only`)                                                               |
  | `SORT_CONFIG`                                                                                                         | yes         | the wdb's `sort.csv` cannot sort the table; end of day would fail. Declare a [sort rule](#sort-rules), or add a row first                                                     |
  | `SORT_DECLARATION`, `SORT_CONFLICT`, `SORT_FILE`, `SORT_DRIFT`, `SORT_RELOAD_UNAVAILABLE`                             | no          | the declared sort rule is invalid, conflicts with the stack's rows, cannot be written or cannot be reloaded safely; see [Sort rules](#sort-rules)                             |
  | `PORT_CONFLICT`                                                                                                       | yes         | the publisher's port is held by another process; free it before starting                                                                                                      |
  | `PUBLISHER_RUNNING`                                                                                                   | yes         | the publisher is running; rows published during registration may be lost                                                                                                      |
  | `SCHEMA_INCOMPATIBLE`, `NOT_PUBLISHED`                                                                                | no          | the table exists with other columns or order (no conversion), or is not a pub/sub table                                                                                       |
  | `UNTYPED_COLUMN`                                                                                                      | no          | a saved attachment has an untyped `()` column (a bundle cannot: the bundle itself is refused); declare a type and plan again                                                  |
  | `NAME_RESERVED`                                                                                                       | no          | the name is a global, namespace, keyword or partition name on a process; it would be overwritten. Rename the table                                                            |
  | `STACK_MISMATCH`                                                                                                      | no          | a process answers but belongs to another stack                                                                                                                                |
  | `LOGGING_MODE`, `LOGS_DISABLED`, `CHAINED_TP`                                                                         | no          | the tickerplant does not log one file per table, or at all, or is chained; fixing it needs a restart                                                                          |
  | `CONSUMER_FILTERED`, `CONSUMER_IGNORES`, `WDB_MODE`                                                                   | no          | an rdb/wdb filters or ignores the table, or the wdb does not save: it would never persist                                                                                     |
  | `PERSIST_UNAVAILABLE`                                                                                                 | no          | the registration could not survive a tickerplant restart (no `-schemafile` this machine, or with `--remote-schema-file` the tickerplant, can append to); use `--runtime-only` |
  | `STORAGE_MISMATCH`                                                                                                    | no          | a pinned location is not what the stack reports; `--replace` with the right one                                                                                               |
  | `DISCOVERY_UNREACHABLE`, `UNREACHABLE`, `NO_TICKERPLANT`, `AMBIGUOUS_TICKERPLANT`, `NO_CONSUMER`, `UNKNOWN_CONSUMER`  | no          | an endpoint does not answer or rejects the credential, or there is nothing (or more than one thing) to publish to or persist with                                             |
  | `ADVERTISED_LOOPBACK`, `ADVERTISED_UNRESOLVABLE`, `PUBLISHER_UNREACHABLE_FROM_STACK`                                  | no          | a stack on another machine could not connect back to the publisher: a loopback name, a name it cannot resolve, or a running publisher it cannot open                          |
  | `STACK_UNREACHABLE_FROM_PUBLISHER`                                                                                    | no          | the running publisher could not open discovery or the tickerplant (or could not be asked)                                                                                     |
  | `PUBLISHER_UNRESPONSIVE`                                                                                              | no          | the publisher listens but did not answer within a few seconds: its TorQ startup is stuck. Find where in its log, then attach again                                            |
  | `STACK_REFUSES_ATTACH_CREDENTIALS`, `STACK_REFUSES_PUBLISHER_CREDENTIALS`, `PUBLISHER_REFUSES_STACK_CREDENTIALS`      | no          | a credential was refused: the attachment's by the stack, the publisher's by the stack, or the stack's by the publisher; see [Credentials](#credentials-in-each-direction)     |

Detach's refusals are destructive-cleanup protections and cannot be overridden.

## Backfilling into the attached stack's HDB

`uqs job backfill` writes into the HDB of the uqf runtime it belongs to
(`KDBHDB`); `--base-port` changes which stack it registers with, not where the
rows go. `--attach NAME` names the destination from the saved attachment
instead:

```bash
uqs job backfill piggybank_ledger_backfill --attach pb-uat \
    --from 2026-10-01 --to 2026-10-02 --source-version historical-v1 \
    --on-conflict replace --mode plan          # then --mode dry-run, then run
```

`validate` resolves the destination offline; `plan` also asks the stack
read-only questions and reports what a run would do. `dry-run` and `run` (the
default) admit **afresh** (everything `plan` checks, again, immediately before
anything starts) and then start the runner, `scripts/dev/attached_backfill.q`.
It is the bounded-worker lifecycle and the HDB writer, not a second engine: a
window's rows are published, then its coverage is recorded, then the checkpoint
saved, and a killed run leaves an under-claim that the next run redoes.
`--wait`, `--debug`, `--trace` and `--on-conflict` work as they do elsewhere.

**A historical HDB backfill does not populate today's RDB and publishes nothing
through the tickerplant.** The rows exist only in the HDB, in the partition of
the day they happened, and only readers that reload see them.

### Extracting on one machine, writing on the stack's (#1650)

`--attach` writes the HDB from the machine that runs the worker, so the HDB must
be on that machine. When the source's driver and credentials must stay on an
extraction machine instead, the stack's machine writes and the extraction
machine sends. The stack's machine needs none of the job's code (#1717). It
writes under a **receiver contract**: data exported from the worker's
declarations on the extraction machine and approved by the stack's operator.

#### Piggybank: what each machine holds

The Piggybank ledger is extracted on `etl-host`, which has the source's ODBC
driver and credentials, and written into the HDB of the stack on `uat-host`:

  |            | `etl-host` (extraction)                                                                             | `uat-host` (the stack)                                                                                |
  | ---        | ---                                                                                                 | ---                                                                                                   |
  | installed  | uqf, KDB-X, the Piggybank bundle (`uqs job install ../piggybank`): its source, transform and worker | uqf and KDB-X only: no Piggybank source, transform or worker                                          |
  | configured | the source's credential (`UQF_SOURCE_CRED_...`); the attachment `pb-uat`                            | the attachment `pb-uat`, copied from `etl-host`; the approved contract; the stack's password variable |
  | runs       | `uqs job backfill --attach pb-uat --writer uat-host:7090`                                           | the TorQ stack, and `uqs job receive`                                                                 |
  | holds      | coverage, the checkpoint and the run ledger                                                         | the HDB and the receiver's batch journal                                                              |

```bash
# etl-host: attach (this records the stack, its HDB and the table), then export
# the worker's receiver contract
uqs job attach pb-uat --job pb_general_ledger --bundle ../piggybank --stack-id 6000 \
    --host uat-host --consumer rdb1 --consumer wdb1 --yes
uqs job contract export piggybank_ledger_backfill --output ledger.contract.json

# carry both to uat-host, into its $UQS_DATA_ROOT/attachments (here /srv/uqs): the
# attachment's directory holds no secret - the password comes from the variable
# it names - and the contract is data
scp -r "$UQS_DATA_ROOT/attachments/pb-uat" uat-host:/srv/uqs/attachments/
scp ledger.contract.json uat-host:

# uat-host: approve the contract for the attachment, then start the receiver
uqs job contract approve ledger.contract.json --attach pb-uat --conflict upsert --conflict replace
uqs job contract list --attach pb-uat          # its identity, e.g. 3f9a0c2e7b1d4a56
uqs job receive --contract 3f9a0c2e --attach pb-uat --listen-port 7090

# etl-host: extract, and send each window to the receiver
uqs job backfill piggybank_ledger_backfill --attach pb-uat --writer uat-host:7090 \
    --from 2026-10-01 --to 2026-10-02 --source-version historical-v1 --wait
```

#### The receiver contract

`uqs job contract export WORKER` runs a q that loads the worker
(`scripts/dev/receiver_contract.q`) and derives the contract from its
declarations (`.qetl.io.remote.contract.of`). It is never written by hand:

  | Field                                                       | From                                                                                                                       |
  | ---                                                         | ---                                                                                                                        |
  | `format`, `version`                                         | `uqf-receiver-contract`, 1. A receiver refuses a version it does not read                                                  |
  | `identity`                                                  | the first 16 hex digits of the SHA-256 of the canonical JSON. Any change is a new contract                                 |
  | `dataset`                                                   | the worker's dataset                                                                                                       |
  | `columns`                                                   | the transform's output columns and `meta` types. A nested `()` column takes the element type `.qetl.plant.nested` declares |
  | `time_column`                                               | the source's time column, which the HDB is partitioned by                                                                  |
  | `window_column`, `row_key`, `partition_column`, `partition` | the worker's window column, target key and partition, as its batches carry them                                            |
  | `conflict_policies`                                         | every strategy the declarations admit. `replace` needs a window column, and a partition column for a partitioned worker    |
  | `origin`                                                    | the worker, source and transform it came from, for the operator to read                                                    |

`uqs job contract approve FILE --attach NAME` runs on the stack's machine. It
refuses, and stores nothing, in any of these cases:

- the contract is malformed, of an unsupported version, or altered since export;
- the attachment is not `verified`;
- the attachment does not publish the dataset in the contract's shape, with the
  `time` the HDB writer adds;
- the attachment pins no HDB that exists here.

Otherwise it stores the contract beside the attachment
(`attachments/NAME/receiver_contracts/IDENTITY.json`), bound to what the
operator controls, not the contract:

- the destination (host, stack and canonical HDB);
- the attachment's stack user, the only writer;
- the conflict policies (`--conflict` narrows the contract's, never widens
  them);
- the most rows one message may carry (`--max-rows`, 100000).

A worker whose output changes has a new identity, so it must be exported and
approved again. An attachment pointed at another HDB since the approval is
refused by `receive` until the contract is approved again.

#### What the receiver does

`receive --contract ID` reads the approved record, never a worker. It checks
everything a local attached run checks: the verified attachment, its pinned HDB,
the stack's writers and readers, and the end-of-day fence. It then starts
`scripts/dev/remote_receiver.q`, which loads no declaration at all: what the
batches look like comes from the contract (`.qetl.io.remote.contract.read`). The
receiver writes through `.qetl.io.hdb_scoped`, so the lock, the date bounds, the
fence and recovery are those of a direct write. It reloads the readers as rows
finish, and stops itself where the fence begins. It authenticates the
attachment's stack user through q's `-U` file, written into the destination's
state directory and readable only by its owner. The extractor connects as that
user.

The extractor (`--writer`) needs no HDB: the attachment names it, and only the
receiver opens it. It plans the windows against its own coverage. Each window's
batch is one synchronous message (`.qetl.io.remote.manager`), and coverage and
the checkpoint move only once the receiver has committed it. The windows are
released as queryable only once the receiver reports that its readers reloaded.
Otherwise the run ends `written_not_queryable`, as a local one does.

Before writing anything, the receiver refuses:

- an ungranted dataset, or a user the grant does not name;
- columns or types other than the contract's, or a batch over the approved row
  limit;
- a window column, row key, partition column or partition other than the
  contract's, or a conflict policy the approval does not permit;
- rows outside the window;
- a request that carries anything but data (a path, a function, an extra key).

Its `.z.pg` admits the receiver's entry points (`.qetl.io.remote.permitted`),
and its `.z.ps` refuses everything.

A batch is identified by an md5 of its destination, window and rows. The
receiver's journal records each one, persisted in the destination's state
directory:

- A batch resent after a lost acknowledgement is answered `duplicate`, and
  nothing is appended.
- Under `append` or `fail`, a different batch for a window already committed is
  refused. So is a batch the receiver was writing when it stopped, because it is
  in doubt: check the target, then `.qetl.io.remote.forget` it.
- `upsert`, `replace` and `ignore` write it again, which is harmless.

A window larger than one message (the approval's `--max-rows`, 100000 by
default) is sent in chunks of 50000 rows. The receiver stages them and commits
the window only when its last chunk arrives and the reassembled rows match the
batch's id, so a window that stops half-way writes nothing and its coverage does
not move. Each chunk is a synchronous call, so the extractor never runs ahead of
the receiver. The receiver holds at most `-max_staged_rows` rows staged across
batches in flight (5000000 by default) and refuses more as busy, and it drops a
batch staged for longer than ten minutes. Resending is safe: a chunk sent twice
replaces itself, and a committed batch answers `duplicate` at its first chunk.

A running receiver holds the attachment's owner record on the stack's machine,
so `uqs job detach` there refuses while it runs.
`uqs job receive --attach NAME --stop` stops it. An extractor elsewhere then
fails its next window without covering it, and the next run, once a receiver is
back, sends it again.

`scripts/test.py q-remote-writer` runs the receiver and an extractor between two
processes.

Reactions of the backfilled dataset (`rebuild_positions`, for example) write
through the same manager and are refused, and recorded as owed: they belong on
the stack's side.

#### Without a receiver: `--force`

`--force` with `--writer` skips the receiver and everything it checks. HOST:PORT
is then any q process of the stack, usually its wdb. The stack's machine runs
none of uqf's code and needs no approved contract, and the attachment need not
be `verified`, pin an HDB or publish the dataset: it is read only for the
stack's user and password.

```bash
uqs job backfill piggybank_ledger_backfill --attach pb-uat \
    --writer uat-host:6055 --force \
    --from 2026-10-01 --to 2026-10-02 --source-version historical-v1 --wait
```

The extractor sends each date's rows as a q lambda that the wdb evaluates. The
lambda enumerates the rows against the HDB's own `sym` file and appends them to
`<hdb>/<date>/<table>/`. The HDB is the wdb's `.wdb.hdbdir`, or the attachment's
pinned HDB directory when it has one. Once every window is sent, one more call
sorts each partition it wrote by `sym` and `time` and puts `p#` back on `sym`.

Nothing is checked on the stack's side:

- **Re-runs and retries append twice.** There is no batch journal. Coverage is
  filed on the extraction machine under the writer's address, apart from a
  checked destination's, so a re-run of a covered range sends nothing. But a
  window whose acknowledgement was lost is sent again and appended again.
- **Append only.** Any other `--on-conflict` is refused before anything runs,
  because resolving keys would mean reading what the wdb holds. Without one, the
  run appends whatever the worker declares.
- **No schema or date checks.** Columns that differ from a partition already on
  disk fail inside the wdb, possibly after some dates are written. Rows dated
  today land in a partition that the wdb's own end of day then writes over.
- **The wdb blocks** while it evaluates each call, so live ingestion queues
  behind a large date.
- **No reader is told to reload.** Run `\l .` on the HDB processes yourself.

### What `plan` reports, and what it refuses

The source, the half-open UTC range, the windows and which are already covered
at this `--source-version` (read from the destination's own ledger), the
conflict policy, the native date partitions the fetched windows fall on and
whether each exists, the processes that write the destination, the HDB readers a
reload would reach, and where the run would write beside the root. `--json`
prints the same. It starts no worker and creates no directory, ledger,
checkpoint or symbol file.

It refuses, naming the cause: an attachment that is not `verified` (including a
detached one), a stack that `--base-port` names differently, no pinned or
existing HDB directory, a dataset the attachment does not publish, a table whose
columns differ from the worker's, a worker with its own IO manager, `--fixture`,
a source with no credential configured (its fixture would be written into a
native HDB), a wdb or rdb reporting another HDB directory, no wdb reporting the
pinned one, an HDB reader that listens but does not answer, and everything under
coordination below. Nothing falls back to uqf's own HDB. An HDB reader that is
not running is not an error: it serves nothing now and maps the partitions when
it starts.

### Coordination with the native writers

No native writer takes a lock a backfill could take, and the vendored TorQ is
never edited to add one. uqf's own HDB write lock is honoured by uqf writers and
`uqs data migrate` only. So `plan` and `run` check, per writer, what *is* safe,
and refuse (listing every blocker together) when they cannot establish it:

- **Symbols.** The wdb, the rdb, the sort workers and uqf's writer all enumerate
  with `.Q.en`, and kdb+ serialises extension of the HDB's `sym` file between
  processes (a test starts six processes racing on the same new symbols and
  checks no symbol is duplicated or mis-indexed). This holds on a **local
  filesystem**: a network filesystem, a stack on another host and a segmented
  HDB (`par.txt`) are refused.
- **Completed history only.** Every affected date must be before the date any
  writer is filling and before its trading date; the native active write date is
  never touched, and an unreported date is a refusal.
- **End-of-day fence.** Within an hour before the next roll, within an hour
  after the last, or while a reload is open (all on the wdb's clock), a run is
  refused; a run that is under way stops writing when the fence begins.
- **Nothing working in place.** A `compression` process is a blocker;
  `housekeeping` is a warning. A `sort.csv` that would order or attribute the
  table differently from the finished partitions is a blocker.

Not coordinated, and not claimed to be: a person running `dbmaint` or q against
the directory, or an end of day started by hand during a run. Attachment safety
checks do not, by themselves, certify direct native-HDB writes.

### How the writes are made

- The destination is a flag, never `KDBHDB`; the root must exist and is not
  created. Writes go through `.qetl.io.hdb_scoped`: staged in `<hdb>.staging`
  beside the root (a reader maps every directory *in* the root), swapped in by
  rename, and refused past the last completed date.
- `replace` clears only its own window: rows outside it are kept, and other
  dates and other tables are not rewritten. Finishing a partition sorts it by
  sym then time with `p#sym` and does **not** fill other dates with the table
  (`.Q.chk`).
- Coverage, checkpoint, lock and run ledger live under
  `<status dir>/attached/<attachment>-<id>/`, where the id is a digest of the
  host, stack id and canonical HDB path, so coverage recorded for one HDB cannot
  suppress a run into another. The run's log is in its `logs/`.
- While a run is alive its owner record sits in the attachment's folder
  (`backfills/<worker>.owner.json`) and `uqs job detach` refuses; the run
  removes it when it finishes, and a record whose process is gone owns nothing.

### Written, covered, queryable

These are three different things and the outcome says which one a run reached.

  | outcome                 | exit | meaning                                                                        |
  | ----------------------- | ---- | ------------------------------------------------------------------------------ |
  | `queryable`             | 0    | written, covered, and every verified reader reloaded and serves the table      |
  | `dry_run`               | 0    | nothing written                                                                |
  | `written_not_queryable` | 3    | written and covered; a reader has not reloaded or does not serve the table     |
  | `partial` / `failed`    | 1    | a window failed: neither written nor covered; covered windows are skipped      |

Only the HDB readers that the stack's discovery lists *and* that report the
destination's directory as the one they serve are asked to reload; one serving
another directory is left alone. After a reload the runner asks the reader
whether it serves the table, because **a partitioned HDB takes its table list
from its latest partition**: a table written only into an older date stays
invisible until the latest partition holds it too. On a freshly attached table
that is the case until the stack's next end of day writes it (or
`uqs data hdb-check --attach NAME --apply` fills it), and the run says so by
name instead of reporting success.

`written_not_queryable` is retryable without writing anything: run the same
command again. Every window is covered, so the worker is idle, the readers are
asked again, and the readiness record advances only when each has answered.

### Boundaries

- Exercised against TorQ 5.2.12 (the starter pack as it ships) on KDB-X, by the
  `attach-stack` lane: plan, dry-run, run, a query against the native HDB
  process and a retry, on a disposable stack. Another TorQ version is accepted
  only through the attach workflow's `TORQ_VERSION` override, which accepts a
  risk and certifies nothing about backfills.
- One unsegmented HDB on a local filesystem of the machine running `uqs`;
  completed historical dates only; the dataset of one worker per run.
- Hosted CI cannot run the stack lane; `uqs` records a KDB-X attestation on the
  PR.

### Seeing what happened

- `uqs list backfills` - per destination: attachment, worker, state (`running`,
  `completed`, ...), outcome, whether the rows are queryable, the log, and what
  to do next when they are not.
- `uqs logs --attach NAME --worker WORKER [-f]` - a run's log, coloured as a
  process's is, with `--lines` and `--level`. It reads the latest run of that
  worker (or `<dataset>.receiver`) across the attachment's destinations, and
  `--run` picks an older one by its log's file name or a prefix of its stamp. q
  errors and the closing `RESULT` line are shown whatever `--level` says.
- `uqs job runs list | status | show --attach NAME` - the destination's run
  ledger; `show` prints the re-run command with `--attach` in it.
- `uqs job backfill ... --attach NAME --mode plan` shows `reload owed` when an
  earlier run left rows written and covered but not yet queryable.

## Restart behaviour

The registration lives in memory. By default (`schema-file`) apply appends one
marked block to the tickerplant's `-schemafile` and leaves every other byte
untouched, line endings included; removing the block restores the file exactly,
whatever order attachments sharing the file are removed in.
`--remote-schema-file` has the tickerplant append the same block to its own file
([above](#durable-registration-through-the-tickerplant)). `--runtime-only` skips
it.

  | State                                                  | A tickerplant restart                                                                                       |
  | ------------------------------                         | -------------------------------------------------------------------------------------------                 |
  | applied, `schema-file` or `remote-schema-file` persist | the block recreates the table and its ownership record                                                      |
  | applied, `--runtime-only`                              | the table is gone; `--dry-run` reports `registration-lost`, verify fails, `uqs job attach NAME` restores it |
  | rolled back                                            | the block went with the registration: the table does not come back                                          |
  | detached after publication                             | the block stayed, so the table comes back and its logs replay                                               |

End of day needs nothing extra: the tickerplant re-reads its tables then, and
the wdb hands its table list to the sort process.

## Testing against a remote stack

`python3 scripts/test.py remote-stack` puts the stack on another machine. It
needs Docker, and is run when asked for, never as part of `all`. The machines
are containers built from `tests/remote/Dockerfile`: Linux x86_64, with PeachQ
built from the pin.

- `uat-stack` runs discovery, the tickerplant, rdb1, hdb1 and wdb1
  (`uqs --runtime peachq start`). Its ports are published to this host. TorQ's
  wdb calls `.Q.chk` as it starts, which PeachQ lacks; a PeachQ runtime's
  processes load `scripts/peachq_init.q` first (`QINIT`), which defines it.
- `uat-client` runs `uqs`: the attachment, the publisher and the extractor.
- `uat-laptop` is on a network of its own and reaches the stack only through
  this host, so the stack cannot resolve its name.

`uqs` runs in a container, not on this host. From this host the stack is
reachable only through ports forwarded to an address this host owns, and `uqs`
rightly calls such a stack local, which turns the remote checks off. Colima's VM
would need `--network-address` for this host to be the client.

Each scenario checks its effect on the stack's own processes, through their
published ports, not through `uqs`:

  | Scenario                                                                                  | Checked on the stack                                                                                                                                                                                                                                                                                                                                                                       |
  | --------------------------------------------------------------------------                | --------------------------------------------------------------------------                                                                                                                                                                                                                                                                                                                 |
  | a publisher on uat-client, attached `--runtime-only`                                      | the table on stp1, its rows counted there and in rdb1, a handle back from discovery                                                                                                                                                                                                                                                                                                        |
  | a publisher name the stack cannot resolve: `ADVERTISED_UNRESOLVABLE`                      | nothing registered, no ownership record                                                                                                                                                                                                                                                                                                                                                    |
  | a name that resolves to an address nothing answers on: `PUBLISHER_UNREACHABLE_FROM_STACK` | discovery lists the publisher with no handle; the publisher is stopped and the attachment back to `applied`                                                                                                                                                                                                                                                                                |
  | `uqs stop` of the publisher on uat-client, then the tickerplant, rdb and wdb restarted    | the publisher gone from uat-client while every stack process still answers; then the table gone; `--dry-run` reports `registration-lost`; `uqs job attach` registers it again and rows flow                                                                                                                                                                                                |
  | a bare `uqs start` after that restart                                                     | refused, naming the re-apply: the start path re-asks the stack ([#1673](https://github.com/kwojdalski/uqf/pull/1673))                                                                                                                                                                                                                                                                      |
  | `uqs job receive` on uat-stack, `uqs job backfill --writer` on uat-client                 | uat-stack has no copy of the bundle: the contract is exported on uat-client and approved on uat-stack. The day's rows counted on uat-stack's disk, coverage on uat-client, a rerun that writes nothing, no HDB on uat-client. hdb1 serves them where it maps a partitioned HDB; PeachQ's maps none, so there the run ends `written_not_queryable` with the reader's failure reported apart |

The lane found that a publisher attached to a stack elsewhere looked for
discovery on its own machine; the discovery address above is the fix. It also
found that on Linux a writer's clock (`.z.p`) carries nanoseconds, which
`receive` refused as a reading it could not hold; the clock is now read to the
microsecond.

What the containers do not show:

- **A real network.** Every hop stays inside one VM and this host: no latency,
  packet loss, firewall or NAT other than Docker's own. The unreachable
  publisher is an address nothing routes to, not a firewall that drops.
- **Clock skew.** The containers share the VM's clock, so the end-of-day fence
  is never judged on two clocks.
- **KDB-X.** Both sides run PeachQ, so the lane shows the workflow on PeachQ.
  KDB-X stays the reference, through the `attach-stack` lane on one machine.
  They differ where it shows: PeachQ answered `access`, not a timeout, for a
  publisher that accepted the connection and never completed the handshake. A
  PeachQ hdb1 maps no partitioned table, so the remote writer's rows are shown
  durable on the stack's disk but never queryable there; queryable after a
  remote write is shown on KDB-X, in `test_attach_backfill_remote.py`.
- **Users, permissions and credentials.** Everything runs as root, with TorQ's
  default credentials.

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
  day: `uqs data hdb-check --attach NAME --apply` writes an empty table into
  just the partitions lacking it, on the stack's host ([one table, in any
  HDB](uqs.md#one-table-in-any-hdb)). The plan warns with `HDB_HISTORY`.
- **Connection cap.** Under a licence with a connection limit (the KDB-X
  community licence allows 16 per process), an attachment adds one persistent
  publisher connection to the tickerplant and one to discovery. Preflight does
  not check the headroom.
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
