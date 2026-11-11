# Running the uqf stack

`uqs` starts, inspects and stops the TorQ fleet this tree runs - the vendored
`lib/torq/` and `lib/torq-finance-starter-pack/` plus uqf's own jobs - without
writing into either `lib/` directory. [The stack page](../architecture/stack.md)
draws the topology.

## Contents

- [Quick start](#quick-start)
- [Reading the database's shape](#reading-the-databases-shape) --- including
  [one table, in any HDB](#one-table-in-any-hdb)
- [Commands](#commands)
- [Compressing the HDB](#compressing-the-hdb)
- [Replaying a tickerplant log](#replaying-a-tickerplant-log)
- [Checking a dataset's producers](#checking-a-datasets-producers)
- [Comparing a table on two endpoints](#comparing-a-table-on-two-endpoints)
- [Listing things](#listing-things)
- [What actually starts](#what-actually-starts) --- including
  [runtimes](#runtimes) and [profiles](#profiles)
- [Changing a process's config](#changing-a-processs-config)
- [Logs](#logs) --- including [the frontend's activity
  history](#the-frontends-activity-history)
- [Connecting](#connecting)
- [Services](#services)
- [Adding a process](#adding-a-process) --- including [installing jobs from
  elsewhere](#installing-jobs-from-elsewhere)
- [MCP server](#mcp-server)
- [Other commands](#other-commands)
- [CLI conventions](#cli-conventions)
- [Machine-readable output](#machine-readable-output)
- [Installing](#installing)

## Quick start

```
./install.sh               # once: checks prerequisites, puts `uqs` on your PATH
uqs start all              # every startwithall=1 process
uqs status                # status table
uqs stop all               # stop every startwithall=1 process
```

Without the install - in CI or a fresh checkout - prefix each command with
`uv run --project python/uqs`, which resolves the package's dependencies on
demand. Either form runs from any directory.

The first `start` creates the data directory `output/uqs/` (gitignored) and
copies the app's sample `hdb/`/`dqe/` data into it. Logs, tickerplant logs, the
write-down database and every process's reads and writes stay inside it, never
inside `lib/`. `uqs clean` wipes it (see [Cleaning up](#cleaning-up)).

Requires KDB-X (`q` on `PATH`), plus `envsubst` and `rlwrap`, which TorQ's
`torq.sh` needs (on macOS: `brew install gettext rlwrap`). See
[Installing](#installing) for tab completion and reinstalling after a rename.

## Reading the database's shape

`schema` answers "what tables are there, and what shape are they" without anyone
typing `meta` at a q prompt:

```
uqs schema                  # every table on rdb1, with row and column counts
uqs schema fx_orderbook         # one table's columns, types and attributes
uqs schema 'crypto*'        # every table matching a pattern, one block each
uqs schema hdb1             # the history instead of today
uqs schema --port 6052      # a port directly, skipping PROC resolution
uqs schema -i               # browse it, re-read every 2 seconds
uqs schema -i --every 500ms # faster; --every 0 re-reads only on R
```

With `-i` the browser keeps asking the process: row counts climb as a feed
publishes, and a table that turns up after the browser opened appears in the
list. The filter and the highlighted row survive each re-read. The line under
the filter box gives the time of the last one. A failed read is reported and the
next tick tries again. `R` re-reads at once.

### Missing partitions

```
$ uqs schema hdb1
could not read the schema from hdb1 (6053):
  "./2015.01.07/arbitrage. OS reports: No such file or directory"
```

That is not a missing table. A partitioned kdb+ database requires every table to
exist in **every** partition, and one absent directory fails the whole query -
so the error names whichever table sorts first rather than the partition that is
actually short.

```
uqs data check           # which partitions are missing what
uqs data check --apply   # write each missing table and column, via vendored KX dbmaint
```

`--apply` shows what it would write and asks first (`--yes` skips the question,
and without a terminal `--apply` needs it). `--fix`, its old spelling, is
refused with a message naming `--apply`. It is additive and idempotent: a table
directory that exists is never touched, and a table a partition holds that
`database.q` no longer declares is left alone - that is history. Running it
twice changes nothing the second time.

The partitions it judges are the ones q maps: it loads the HDB in one q process
(`scripts/gates/hdb_layout.q`) and reads `.Q.pv` and `.Q.pd` back. In a
segmented HDB, whose root holds `par.txt`, each partition is named by the
segment holding it (`<segment>/<date>`), and a date held by two segments is
checked in both, as every query reads both. A segment `par.txt` names that does
not exist or cannot be read, and partition directories in a root beside
`par.txt`, are refused by name before anything is written - q loads past a
missing segment without a word. Without a KDB-X q (none on `PATH`, or `$QCMD`
naming PeachQ, whose `\l` maps no partitioned database) the check reads the
root's own date directories instead, and refuses a segmented HDB rather than
judging it by its root (#1783).

It should rarely be needed by hand, because `bootstrap` now does it on every
`uqs` command. It is there because the situation is easy to create: each day's
partition holds whatever tables existed when it was written, so **every table
added leaves every earlier partition short**, and the vendored sample partitions
ship holding `quote` and `trade` alone. TorQ fills only the partition the wdb is
currently writing (`lib/torq/code/processes/wdb.q`'s `filldb`), so nothing ever
goes back (#348).

It reads the live process, not `src/etl/plant_tables.q`'s declarations.

Two things the output says that `meta` alone does not:

- **Case is the vector/atom distinction.** `f` is a float column; `F` is a float
  *vector* column, one list per row - the shape `fx_orderbook` and
  `mkt_orderbook` are built around and that every pricing function in `src/`
  expects. They render as `float` and `float vector`.
- **A column's type can change with its contents.** An empty vector column
  reports as `general`, because q cannot know the element type until a row
  exists. The same table reads `general` before its first publish and
  `float vector` after. Not a bug, but worth knowing before treating one reading
  as the schema.

The table argument is a shell-style pattern (`crypto*`, `*trade*`), matched
case-sensitively against the names the process reported. An exact name is just a
pattern that matches itself, so there is one behaviour rather than two - quote
the pattern, or the shell will try to expand it against your filenames first.

### How the HDB is compressed

```
uqs data compress --report            # per table and column: algorithm, block size, ratio
uqs data compress --report --json     # the same, as one document
uqs --target uat data compress --report
uqs --target kdb_prd data compress --report --hdb /data/hdb   # a TorQ server without uqf
```

`--report` reads what the HDB holds now and changes nothing (#2149). It walks
the HDB with TorQ's own `.cmp.hdbstructure` - the walker `.cmp` compresses by -
and reads each column file's header with `-21!`, through uqf's wrapper over
`.cmp` (`scripts/processes/torq_compress.q`). Per table and column it lists the
algorithm, the block size (log2, as `compressionconfig.csv`'s `cblocksize`) and
the ratio over the compressed files; then the largest uncompressed partitions
(`--limit`, 10 by default); then every partition that is not in the order, or
lacks the attribute, that `sort.csv` declares - the order and attribute TorQ's
wdb gives a partition at end of day, read from the first config layer that has
the file, as TorQ reads it. The plant schema's `g#sym` is an in-memory index the
tickerplant and RDB keep, not what a partition on disk carries, so it is not
what this judges. Unsorted data compresses far worse - KX's whitepaper
(<https://code.kx.com/q/wp/compress/>) measures 669x against 10x - but the
report never re-sorts: it names the partitions, and the fix is the wdb's. It
writes nothing, but on a large HDB reading every header and every partition's
sort columns is still substantial read I/O.

`--hdb DIR` names the directory instead. Under `--target`, DIR is on that target
and nowhere is it checked against this machine (#2219): the command runs here,
and reaches the target by its own declared `ssh`, account and connect timeout,
with one ssh session that writes the report script, `torq_compress.q` and the
two TorQ files they load into a temporary directory there, runs the target's q
(its `q_cmd`, else the account's `$QCMD`, else `q` on its `PATH`, else
`$QHOME`'s), and removes the directory however it ends. So the target needs q
and the HDB - an existing TorQ deployment - and no uqf, release or `deploy.env`.
Sorting is judged only against `--sort-csv FILE` (a path on the target); without
it the report says "sorting not checked", and `--json` gives
`sort_checked: false` and `unsorted: null`, never the empty list that means
every partition is sorted. It is a read, so an inspection-only target runs it;
`--apply` with `--hdb` under `--target` is refused before anything reaches the
target, because compressing goes through a managed deployment's writer lock,
policy and reload. Without `--hdb`, `--target` runs the release's own command on
its configured HDB, as before.

### The `clock` column on `executions` and `market_data` (#1925)

Every row of `executions` and `market_data` says whose clock its `source_time`
is: `venue` (the venue stamped it) or `receipt` (this stack stamped it on
arrival). A partition written before the column existed gets it from
`uqs data check --apply`, which for this one column **attributes** instead of
writing null, from the rows' own columns and the rules in
`scripts/gates/hdb_clock_rules.csv`:

  | Table         | Looked up by       | Rule shipped                                                                               |
  | ---           | ---                | ---                                                                                        |
  | `market_data` | `(market, source)` | `fx` + `UQFFX` or `UQFDEPTH`: `receipt`; any `crypto` source: `venue`                      |
  | `executions`  | `venue`            | `fx`, `binance_spot`, `bybit_spot`: `receipt` (the FX feeds and the polled crypto fills)   |
  | either        | no rule matches    | `unknown`                                                                                  |

A row the rules do not name is `unknown`, never null and never a guess: a source
name such as an FX liquidity provider can have come from the quote feed
(`receipt`) or from a vendor book (`venue`), and only the site knows which.
`unknown` is refused by the normalizers' checks and by the markout contract
(#1923), so history that matters needs a rule row (`table,market,name,clock`;
`*` as the name matches any source of that market) BEFORE the first `--apply`:
the fill is additive and writes the column once, so a partition already filled
`unknown` is not revisited by a later run. Correcting it is a decision for a
person (a column rewrite, as for any stored value). A partition with no
`market`/`source` (or `venue`) column to read is `unknown` throughout. The run
prints how many rows of each partition got each clock.

### The markout configuration columns: folded into snapshots (#1999)

A markout row used to repeat `contract_version`, `rate_timing` and `report_ccy`.
It now carries one `config_id`, the write-once configuration snapshot it was
produced under (`uqs data snapshot list`, `uqs data snapshot show ID`). A
partition of `fx_markout`, `fx_markout_wide` or `crypto_markout` written the old
way is migrated by `uqs data check --apply`, which says so when it asks:

1. one **legacy** snapshot is recorded for each distinct
   `(contract_version, rate_timing, report_ccy)` in the partition, in the status
   directory (`markout_legacy_...`; the same triple in two partitions is one
   snapshot);
2. a `config_id` column is written naming each row's;
3. only then are the three columns dropped.

An interruption between 2 and 3 leaves the old columns beside a good
`config_id`, and the next run only drops them. A partition older than the
columns holds none to read: its rows are contract version 1, with the rate
timing and reporting currency unknown. The bootstrap fill that runs with every
`uqs` command **never** does this - it leaves an existing markout partition as
it was, and `uqs data check` reports its missing `config_id` until `--apply` -
because it is the one repair here that deletes a column. The run prints each
partition's snapshots and the columns it dropped.

### `crypto_trades.source_time`: history has no venue time (#1936)

cryptorust's fills recorder began sending the venue's own time for each fill as
`crypto_trades.source_time`. A partition written before that has no such column,
and `uqs data check --apply` adds it as **null**, never as a copy of `time`:
`time` is when the plant received the fill, and a receipt time in a venue-time
column is what the markout contract (#1923) exists to refuse. The `executions`
mapping reads a null `source_time` as "no venue stamp" and puts the fill on the
`receipt` clock, so history scores as it always did and is never compared
against venue-clock books. `clock` is the only column the fill derives; this one
is deliberately not.

### `market_data`: the flat top, the order and the row numbers (#2288)

A markout joins against the memory-mapped `market_data` and must never copy it
into a heap (`docs/architecture/markout-memory.md`), which needs three things of
a partition that older ones lack:

- `bid` and `ask`, the level-0 top as plain floats - what `.qf.mkt.top_sides`
  takes from the ladders, null for a side the source withdrew or a level that is
  no price;
- `rid`, the row's position in its partition;
- the rows sorted by `sym`, `source`, `source_time` with `p#` on `sym`.

`uqs data check` lists, per partition, the columns it lacks and the partitions
out of that order (`unsorted` in `--json`), and changes nothing.
`uqs data check --apply` asks - **stop the stack first**, as for any migration -
and then, per partition, derives `bid` and `ask` from the stored ladders (a
chunk of rows at a time, so the ladders stay mapped), rewrites a partition that
is not in order in the HDB writer's staging area and swaps it in whole under the
write lock (a kill leaves the old partition or the new one, never a half-sorted
one), and numbers its rows. A partition already in order with its numbers right
is not touched, and a second run changes nothing. Memory needed is a partition's
three key columns and the permutation, not its ladders.

The bootstrap fill that runs with every `uqs` command **never** does this: a
null `bid` would read as a withdrawn book, and deriving it reads every ladder.
Partitions written from now on arrive in this shape: the mappings write `bid`
and `ask`, and the sort that finishes a partition - uqf's own, or TorQ's
end-of-day sort through `scripts/torqconfig/sort.csv` - writes the order and the
numbers. `uqs data compress --report` reads the same `sort.csv`, so it names a
partition that does not follow it.

### Renamed or retyped HDB columns

`data check --apply` does not rewrite existing columns. When a release renames
or changes one, stop the stack and migrate each affected date/table explicitly:

```
uqs data migrate trade --date 2026-01-01 --rename old_price:price --cast price
uqs data migrate trade --date 2026-01-01 --rename old_price:price --cast price --apply
uqs data check
uqs restart hdb1
```

The first command is a dry run: without `--apply` it always is (#1854).
`--apply` shows the same plan and asks before writing; `--yes` does not ask.
`--cast` authorises a type change only when every value survives conversion back
to its original type. The applied command builds and checks a complete table
partition before exchanging it with the old one; it prints the old directory's
backup path for rollback. A source column not named in the current schema must
be mapped with `--rename` rather than silently dropped. The command refuses
segmented HDBs.

An empty table that kept an obsolete schema (so a backfill cannot publish into
it) is rebuilt from its current declaration instead (#2251):

```
uqs data migrate trade --date 2026-01-01 --reset-empty          # eligibility and the schema change; writes nothing
uqs data migrate trade --date 2026-01-01 --reset-empty --apply  # after asking; --yes does not ask
```

q refuses unless every stored column file, obsolete ones included, is readable
and holds no rows, and checks again under the writer lock just before staging.
It cannot be combined with `--rename` or `--cast`, uses the shared `sym` file,
leaves other partitions alone, and keeps the old table at the reported backup
path.

### One table, in any HDB

`data migrate` rebuilds one date of THIS runtime's HDB to its declared schema.
Two commands work on one table across a whole HDB instead - this runtime's, a
release's, or an attached TorQ stack's, such as a production HDB a backfill
wrote a table into on a few dates only (#1755):

```
uqs data check --attachment pb-prod                          # the plan: which dates lack the table
uqs data check --attachment pb-prod --apply                  # write an empty one there, after asking
uqs data alter --attachment pb-prod --reorder time,sym,amount,note
uqs data alter --attachment pb-prod --reorder time,sym,amount,note --apply
uqs data alter --attachment pb-prod --undo /data/hdb.uqf-alter/20261028D101500123-pb.json --apply
```

**Which HDB** is named once, by what already knows it - give exactly one:

  | handle                             | the HDB                                                                                     | runs                                     |
  | ---                                | ---                                                                                         | ---                                      |
  | `uqs --target NAME data ...`       | the deployed release's own configured HDB                                                   | on the server, from the release          |
  | `uqs data ... --attachment NAME`   | the attachment's pinned `storage.hdb_dir`, else its live wdb's `.wdb.hdbdir`                | on the stack's host, over ssh            |
  | `uqs data ... --hdb DIR`           | that directory                                                                              | on this machine                          |
  | none                               | this runtime's own HDB (what `--target` reaches on a server)                                | on this machine                          |

The plan says which was used. Two handles are refused, and so is an attachment
whose pinned HDB its live wdb contradicts. With `--attachment`, the table
defaults to the attachment's own. A segmented HDB (`par.txt`) is followed into
its segments.

**`data check --table T`** (or `--attachment`, `--hdb`) checks and fills ONE
table: only partitions lacking it get an empty one, enumerated against the HDB's
sym file, which an empty table adds nothing to. Its schema is this runtime's
declaration when the runtime declares the table, else one partition holding it -
the latest, or `--template YYYY.MM.DD` - taken in its column order and types.
Partitions holding the table with another shape are refused, never rewritten.
Without a handle or `--table` it is the whole-runtime check above.

**`data alter [TABLE]`** makes one change per run, in each partition holding the
table and nowhere else:

  | operation                     | writes                                                     | `--undo`                                                                                                                      |
  | ---                           | ---                                                        | ---                                                                                                                           |
  | `--reorder c1,c2,...`         | each partition's `.d`; it must list every existing column  | the journalled `.d`                                                                                                           |
  | `--rename old:new`            | the column's files renamed, and `.d`                       | renamed back                                                                                                                  |
  | `--attr col:p\|s\|g\|u\|none` | the column's attribute (its header)                        | the journalled attribute                                                                                                      |
  | `--cast col:TYPE`             | the column, written beside it and renamed over it          | the original while the run is unfinished; once complete, a cast back (exact only for a lossless cast, which the plan reports) |

A cast to or from symbol is refused: that changes the enumeration. Every run
checks first that the partitions holding the table agree on its columns, order
and types, and refuses - naming each shape and the partitions that hold it -
before any write; `data check --table` repairs a missing table, anything else is
for a person to decide.

**Dry run first.** Both commands print the plan and write nothing - the HDB and
how it was resolved, the partitions touched and skipped, each file written or
renamed, the bytes a cast rewrites, and the journal's path - exiting 0 when the
plan is valid and 1 when it is refused. `--apply` shows the same plan and asks
once before the first write; `--yes` skips the question, and without a terminal
`--apply` without `--yes` is refused. `--json` prints the plan and, after
`--apply`, the result.

**The journal** is written before the first write, BESIDE the HDB:
`<hdb>.uqf-alter/<stamp>-<table>.json`, holding the operation and each touched
partition's previous `.d` and attributes. Not inside it: `\l` loads every
non-partition entry of a database root as a variable, so a journal directory
there would stop the HDB loading. A run that stops part way leaves the journal
`started`; running the same operation again resumes it, skipping partitions
already changed, and any other change to the table is refused until
`--undo JOURNAL` reverses it. `--undo` is previewed like any run until
`--apply`. After the last write, every partition holding the table is read
again, and the run exits 1 naming any that does not have the target shape.

**Writers and readers**, by handle:

- `--attachment`: refused while an attached backfill owns the attachment, or
  while a wdb writing that HDB reports an end-of-day reload in progress.
  Afterwards each HDB reader serving the directory is asked to reload, and the
  ones that did not are reported.
- `--target` (the release's own HDB): the write holds the HDB write lock uqf's
  own writers take, so it is refused while one of the release's backfills runs.
  Afterwards the release's HDB processes are asked to reload.
- `--hdb`: the plan warns that writers were not checked, and prints the reload
  command.

None of this coordinates with a native end of day beyond the check above: run it
outside end of day.

**On the stack's host.** `--attachment` runs the commands where the files are.
The q script (`scripts/gates/hdb_alter.q`) and the vendored `dbmaint.q` it loads
are copied to a temporary directory there over one ssh session, run with the
server's own q (`$QCMD`, else `q`, from the account's login shell), and removed
afterwards - so the host needs q and nothing of uqf. The destination is the
attachment's `ssh` setting (`uqs job attachment add NAME --ssh USER@HOST`, which
changes a saved attachment without `--replace`), else `stack.host` as the
current user; a stack on this machine needs none. ssh runs with `BatchMode`: a
destination it cannot reach is refused, naming the setting. The same script runs
by hand:

```
q scripts/gates/hdb_alter.q -hdb /data/hdb -table pb -reorder time,sym,amount,note [-apply]
```

KDB-X only: PeachQ cannot write a partitioned HDB natively, and the script
refuses it.

#### On production

For a table a backfill wrote into a production TorQ HDB - present on the dates
it covered, missing from the rest:

1. Attach the job if it is not attached (`uqs job attachment add`), with `--ssh`
   naming an account on the HDB's host that can write it. Check that
   `ssh -o BatchMode=yes USER@HOST true` works.
2. Make sure nothing else writes the table: let any attached backfill finish
   (`uqs job attachment status`), and pick a time well away from end of day.
3. Read the plan: `uqs data check --attachment NAME`. It names the HDB and where
   that came from - check it is the directory you mean - and the dates that lack
   the table.
4. `uqs data check --attachment NAME --apply` writes the empty table there; the
   final check and the readers' reload follow.
5. To change the table's layout, plan it with
   `uqs data alter --attachment NAME --reorder ...` (or `--rename`, `--attr`,
   `--cast`), read the plan, then add `--apply`. Keep the journal path the plan
   prints.
6. If the result is wrong, `uqs data alter --attachment NAME --undo JOURNAL`
   shows what it would put back; add `--apply`.

## Commands

`uqs --help` lists every command, and `uqs <command> --help` gives its arguments
and options. The sections below cover what `--help` cannot.

Three groups gather what is not a lifecycle command. `uqs job` is everything
about one ETL job: writing it into the tree (`new`, `remove`, `install`) and
operating it (`backfill`, `preview`, `gaps`, and `runs` for the run ledger), and
putting it on a TorQ stack that is already running (`attachment add` and
`attachment remove`, see [attaching to an existing
TorQ](attach-existing-torq.md)). `uqs setup` prepares the machine the stack runs
on - `uqs setup odbc` installs, loads and rolls back a private ODBC setup.
`uqs connector` runs the whole lifecycle of the external publishers - databento,
kafka, the cryptorust recorders - which are processes outside TorQ, not the q
processes `uqs start` manages: `start`, `stop`, `restart` (with `start`'s
options, which are not remembered), `status`, and `logs NAME [-f] [-l N]`, which
shows the one plain log file `status` names:

```bash
uqs connector start kafka --topics uqf.client.flow
uqs connector restart kafka --group uqf-replay     # start's options, given again
uqs connector logs kafka -f
uqs connector stop kafka
eval "$(uqs setup odbc env)"                       # this shell, on the private ODBC setup
```

`uqs data snapshot` reads the write-once configuration snapshots that markout
rows name by `config_id` (#1999). Both commands only read:
`list [--dataset NAME]` gives each snapshot's id, the datasets it configures,
when it was recorded (UTC), by whom and why, and whether its digest still
matches its content - one that does not is shown in red and the command exits 1 -
and `show SNAPSHOT_ID` prints every field and parameter of one, refusing a
snapshot whose digest fails. An id nothing is recorded under is a usage error
naming it. Both read q's own store in the status directory; the other direction,
from a result to its snapshot, is the `config_id` column.

```bash
uqs data snapshot list --dataset fx_markout
uqs data snapshot show markout_5cefbe2bdcc61b7f --json | jq '.parameters.rate_timing'
```

Putting a release on a server is `uqs deploy` (`history` lists what this machine
deployed): see [Deploying to a server](deploy.md).

`PROCS` is `all` (the default) or one or more process names, each its own word -
`uqs start posbook1 crypto_markout1` - which is what lets TAB complete them. A
single quoted `"posbook1 crypto_markout1"` still works. `--base-port` sets
`KDBBASEPORT` (default: the runtime's own base port, `6050` for `uqf`; see the
port table below). `--export FILE` (on `status`, `query`, `schema`, `list`,
`config show`, and every noun's `list`: `runtime`, `job`, `job attachment`,
`target` and `override`) additionally writes the same rows to `FILE` as CSV or
Parquet, format inferred from the extension; a result that is not a table, such
as `count t`, is refused.

### Stopping a process that will not stop

`uqs stop` asks each process to exit (`kill -15`), through the port it listens
on, so it cannot reach a process stuck before it opened its port, a duplicate
started by hand, or one that ignores the request. `uqs stop --kill [PROCS]`
finds every instance by its command line instead and kills it outright
(SIGKILL), touching nothing outside this stack. It is the last resort: nothing
shuts down cleanly, and a tickerplant killed mid-write may leave its log's last
message torn.

### Cleaning up

`uqs clean` wipes `output/uqs/`, the HDB included. It lists what would go, with
sizes, and asks first; `--yes` does not ask, and without a terminal it is
needed. `--match REGEX` keeps only the entries whose path *under* `output/uqs/`
the regex finds:

```
uqs clean --dry-run                           # what a full wipe would remove
uqs clean --match '^logs$' --dry-run          # the logs alone, still removing nothing
uqs clean --match '^(logs|tplogs)$'           # and now remove them, after asking
uqs clean --match 'out_rdb1'                  # one process's files, wherever they sit
uqs job checkpoint remove demo_deals_backfill # one backfill worker's resume point
```

A directory the pattern matches goes whole; one it does not is descended into,
which is how `out_rdb1` is found without naming `logs`. The match is a search,
so anchor with `^`/`$` to be exact. `--dry-run` (`-n`) lists what would go, with
sizes - worth running first, since none of this is reversible.

## Compressing the HDB

`data compress` brings the HDB's older partitions to the compression policy
([docs/reference/compression-policy.md](../reference/compression-policy.md)) -
and uqs compresses nothing itself. TorQ's `.cmp` plans and compresses, in
`housekeeping1`, TorQ's own HDB-maintenance process; `.qtorq.compress`
(`scripts/processes/torq_compress.q`) checks every file it changed and asks the
HDBs to reload. Without `--apply` nothing changes (`--report`, earlier on this
page, reads how the HDB is compressed now):

```bash
uqs data compress                              # the plan, the estimate, the disk it needs
uqs data compress --table market_data --older-than 30d
uqs data compress --date 2026-11-02 --apply    # that day now, whatever the policy's minage
uqs data compress --apply                      # asks, then one partition at a time
uqs --target prod data compress                # a release's own HDB, run there
```

The plan names each table's **policy** - its own, or the stack default when none
is declared - and, per table partition, the columns `.cmp` would compress or
decompress and those `-21!` already shows compressed. The **estimate** comes
from `.cmp` itself: the newest planned partition of each table, its first
100,000 rows of each column copied into a temporary directory and compressed
there through the same wrapper. The **disk** line sets each filesystem's free
space against the most one partition writes there - its new files, held beside
the old until the HDB reloads.

Partitions the coverage ledger says are **still being written** - rows a
backfill wrote that its readers have not been released to see
(`.qetl.coverage.unreleased`) - are `held`, and never run, until it finishes.

`--table` (repeatable) and `--older-than` (a DURATION in whole days, on top of
the policy's own minimum age) narrow a run. **Named days** - `--date D`,
`--from`/`--to` (half-open: `--to`'s day is not in; whole days only) or `--last`
(whole days before today) - compress the partitions in them on demand, the
policy's minimum age set aside; the plan says so for each ("compressing it
although the policy's minage is 7"). `--algo`, `--blocksize` and `--gzip-level`
change one run's algorithm, block size or gzip level. Every one of these selects
or sets rows of `.cmp`'s own plan table (`.cmp.showcomp`), which
`.cmp.compressfromtable` then compresses, so the plan shown and the run are the
same rows; a one-off setting is checked by `.cmp.checkcsv` first.

`--apply` asks first (`--yes` answers), and is refused before anything is
written when a filesystem is short of the space it needs. It then holds the HDB
write lock - a backfill or `data migrate` waits - and runs one partition at a
time, oldest first - `.cmp`'s swap, then the read-back check - and asks the HDBs
to reload once, after the last. The first file that did not reach its target
state stops the run with `.qtorq.compress`'s error, naming it; every partition
already done is valid, and a second run continues where this one stopped. Each
run is one row of the activity history, with an item per partition:
`uqs activity list --action hdb.compress`.

Under `uqs --target T` it runs on the release and asks there; an inspection-only
target refuses `--apply` before it leaves this machine.

## Replaying a tickerplant log

`data replay` is TorQ's own `tickerlogreplay` - the `tpreplay1` process - with
the aiming done for you:

```
uqs data replay                                                   # the plan; runs nothing
uqs data replay --date 2026-09-22 --table quote --table trade --apply
```

A replay empties the tables it replays in the partitions it touches before it
writes them, so it is a dry run unless `--apply`, which shows the plan and asks
first; `--yes` answers that (#2235). `--dry-run` is refused: it is the default.

What it replays, and into what, is read off the processes that are **running**,
not off the configuration that describes them:

  | what              | where it comes from                                            |
  | ---               | ---                                                            |
  | the log directory | the running plant's own `-tplogdir`, newest day for that plant |
  | the schema        | that plant's `-schemafile`                                     |
  | the database      | the `-load` of the hdb process on the same stack               |
  | the base port     | the runtime's own, which must be the plant's `-stackid`        |

A process's start line cannot drift from the process, which is why they are read
rather than configured. `--base-port` defaults to the runtime's own, as on every
command, and the plant's `-stackid` must agree with it: a plant running under
another base port is refused, naming the `--base-port` that replays it.

Every row in that table is an option (`--tplogdir`, `--schema-file`, `--hdb`,
`--base-port`), and an option that is given wins - supply the first three and
nothing is asked of the machine at all, which is how to replay a log after the
stack it came from has been stopped. `uqs data replay PROC` picks between plants
when more than one is up with a log of its own; with exactly one, it is not
needed, and with several the command refuses rather than ranking them.

**It empties what it writes.** TorQ's replay defaults are kept whole, so the
tables being replayed are cleared in the partitions the replay touches before it
writes them. Without `--apply` it prints the resolved plan and the start line
and runs nothing.

## Checking a dataset's producers

A dataset can be filled by more than one job - a polling feed and a backfill of
the same table. `uqs job check DATASET` asks, read only, whether they fit the
destination, agree with each other and between them miss nothing:

```
uqs job check demo_deals --date 2026-09-11 --scope sym=EURUSD --gateway gw1:6060
```

It reports four results, each `PASS`, `FAIL` or `UNVERIFIED`, with the dataset,
scope, time bounds and the basis it rests on:

- **schema** - each producer's declared output, and the rows it returned,
  against the plant table: column names, order, q types and nested element
  types, and the row keys. A finding names the producer, destination, column,
  and expected against actual.
- **behaviour** - cursor progression and recovery from a failed publish
  (streaming); window tiling, retries, conflict handling and coverage (bounded).
  They run on fixtures, in a throwaway status directory, never against a live
  source or a running process. Slow queries, startup failure and late arrivals
  have no harness yet and read `UNVERIFIED` with the reason.
- **cross_mode** - the producers' rows for the same closed window, paired by
  business key and compared by value and multiplicity: missing, extra, changed
  and duplicated rows are each a `FAIL`, and equal counts alone never pass. What
  "the same" means is the dataset's declared basis (`events`, `revisions` or
  `final_state`); without one it is `UNVERIFIED`. Columns the destination
  generates (`time`) are left out.
- **completeness** - the source, read through its own adapter, against the
  destination in `--scope` and the range (`--gateway` reads the RDB and HDB
  through `.dataaccess.getdata`). It catches a record both producers omit.

Verification is read only, and proves it: the status directory, cursors,
coverage ledger and the dataset's tables are snapshotted before and after the
capture and must be equal. An empty window, a producer that cannot be captured
(a job that subscribes has no page to fetch), a backfill window that failed its
fetch, readiness, transform or quality check, one producer only, an unknown
cutoff or a check that did not run is `UNVERIFIED`, never `PASS`. A dataset with
a single producer verifies without a second.

The producers come from the job graph (`.qetl.dag.producers`), and everything
above is decided in q (`.qetl.verify.run`); the command only reads the range and
prints. It exits 1 on `FAIL`, and with `--fail-unverified` on `UNVERIFIED`.
`--json` prints the report.

## Comparing a table on two endpoints

Validating local processing against production used to mean querying both and
comparing by hand. `uqs data diff` compares one table, or several, on two
endpoints named explicitly - `local` for this machine's stack, or a deploy
target, whose gateway is reached through an ssh tunnel for the call:

```
uqs data diff --left local --right prod --table demo_deals --date 2026-09-11 \
    --filter sym=EURUSD --limit 5000 --source-version v1
```

It compares three things, each read through the contract the stack already
serves:

- **schema** - `meta` on each side's RDB: a column one side lacks, or holds as
  another type.
- **rows** - at most `--limit` rows per side in the window (and `--filter`s),
  read with `.uqf.browse` under the gateway's query policy, then paired on the
  row key and equivalence basis the table's producers declare, as
  `uqs job check` pairs them (a table nobody keys is matched row by whole row).
  `time` is stamped on receipt, so it is left out: two environments receive a
  row at different moments.
- **coverage** - with `--source-version` (and `--slice`), the coverage ledger's
  current claims for the window on each side, composed and gapped by
  `.qetl.coverage`.

Each table reaches one outcome, the first that applies:

  | outcome                | means                                                                                      |
  | ---------------------- | ------------------------------------------------------------------------------------------ |
  | `endpoint_unavailable` | a side could not be reached, or did not answer - with its reason                           |
  | `missing_table`        | a side has no such table                                                                   |
  | `differs`              | the schemas, the published coverage or the rows differ - each difference named             |
  | `not_comparable`       | the table declares no row key and a sample was cut at `--limit`: no row could be judged    |
  | `incomplete_coverage`  | the sides agree, but neither has published the whole window                                |
  | `equal_in_sample`      | nothing differs in what was read                                                           |

`equal_in_sample` is never "the tables are equal". Its label says whether every
row in the window was read on both sides, or the read stopped at `--limit` - a
truncated sample says it is NOT proof, and because each side was cut at its own
place, a key only one sample holds is counted as unpaired rather than reported
missing. Narrow the window or raise `--limit` to read it all.

Everything that decides the result is q's (`.qetl.compare.run`,
src/etl/core/compare.q); the command opens the tunnels and prints. It exits 1 on
anything but `equal_in_sample`, and `--json` prints the report.

## Listing things

Each noun lists its own records - `uqs process list`, `uqs profile list`,
`uqs job list`, `uqs job attachment list`, `uqs target list`,
`uqs runtime list`, `uqs config list` - and every listing takes the same flags:
`--json`, `--export FILE`, `--sort`, `--reverse` and `-i`, `deploy history` and
`target log` among them. `uqs list KIND` was the last top-level verb with nouns
under it, and each kind is its noun's now (#1858).

```
uqs process list
uqs profile list
uqs job list
```

The stack's own tables:

- `uqs process list` - every process's `procname`/`proctype`/`port`/
  `startwithall`, resolved and with any `config set` overrides applied - the
  full set `config show`/`config set`/`start <procname>` accept - plus the
  `inputs` and `outputs` its job declares: the tables it subscribes to and
  publishes, or for a backfill worker the dataset it fills. Declared, not
  running: `uqs status` asks the fleet what is up
- `uqs config field list` - `process.csv`'s valid columns (what `config set`'s
  `FIELD` accepts). A value is held to its field's domain too: `port` 1-65535 or
  `{KDBBASEPORT}+N`, `T`/`w`/`g`/`localtime` a whole number, `startwithall` 0 or
  1
- `uqs config env` - `build_env()`'s resolved `KDBBASEPORT`/`KDBHDB`/... values
  (the same env `config show`'s placeholder resolution and `torq.sh` use)
- `uqs config housekeeping show` - `housekeeping1`'s effective schedule: when it
  runs each day, and each rule - its function, what it matches, and how old a
  match must be - with paths resolved as TorQ resolves them
- `uqs graph --upstream --offline` - what each process needs, and who publishes
  it, before starting it on its own
- `uqs job attachment status` - every attached destination a
  `uqs job backfill --attachment` has run into: attachment, destination id,
  worker, `state` (`running`, `completed`, ...), `outcome` and `queryable` -
  written rows are not queryable until the readers reload - the latest log, and
  what to do next. Read-only

A noun's own records:

- `uqs job list` - every streaming job, normalizer and bounded worker, read from
  its own q declaration: its kind - one of `uqs job new --kind`'s (`streaming`,
  `normalizer`, `horizon`, `backfill`) or `reaction`, so a job is listed as the
  kind it was made as (#1889) - whether it `subscribes` to anything (a streaming
  job that does not is a feed), its process, what it reads and writes (a
  backfill's source, and its dataset and partition), whether it starts with the
  stack, and whether it is still a `scaffolded` job `uqs job new` wrote or
  `written` work. `uqs job list --sort state` puts the unfinished scaffolds
  together
- `uqs job attachment list` - every `uqs job attachment add` attachment: its
  job, publisher, stack and host, its lifecycle `state` and persist mode, and -
  separately - `publisher_state`: `running`, `stopped`, or
  `unreachable`/`unknown` when the host or port cannot be checked. Read-only
- `uqs runtime list` - each runtime: whether it is here, and up
- `uqs target list` - this machine's deploy targets
- `uqs config list` - every `config set` setting in effect, from
  `process_overrides.csv` (`uqs list overrides` before #1855: an override is a
  bypassed check, `uqs override list`)

`--sort` orders by any column the chosen kind produces, `--reverse` flips it:

```
uqs process list --sort port              # 6050, 6051, 6052, ...
uqs process list --sort proctype
uqs process list --sort port --reverse
uqs config env --sort name
```

Numeric columns sort as numbers (`6100` after `659`) and empty cells go last. An
unknown column is refused with the ones that listing has. The order reaches
`--export` too.

`--interactive` (`-i`) opens the table in a browser instead of printing it, and
filters its rows as you type. On `list`, `status`, `job runs list`,
`job runs list --unfinished`, `job gaps` and `schema`:

```
uqs process list -i      # type "hdb" to keep the hdb rows
uqs status -i             # "down" for what is down, "up warn" for both at once
uqs job runs list -i       # Enter prints the run you picked, for `uqs job runs show`
```

A term matches any one cell as a substring or, failing that, as a subsequence -
`upq` finds `uqs_plant_q` - and a space between terms means every one must
match. Matching is case-insensitive and ignores colour. Up, down and the page
keys move through the rows; Enter exits printing the highlighted row,
tab-separated; Escape exits printing nothing. It needs a terminal, and refuses
in a pipe rather than hang. `--sort` still decides the order, and `--export`
writes every row, not just the ones a filter left.

`uqs status -i` also acts on the highlighted process: `s` starts it, `x` stops
it, `r` restarts it, and the table re-reads itself when that finishes; `R`
re-reads it on demand. It also re-reads the fleet every 2 seconds on its own,
keeping the filter and the highlighted row; `--every 10` slows that down and
`--every 0` leaves only `R`. A re-read slower than the interval skips the ticks
it overlaps rather than piling them up. There, letters are commands, so `/`
opens the filter and Enter or Escape goes back to the table.

`uqs graph` draws every process and who feeds whom, as trees, with each edge
labelled by the tables it carries and each process coloured up or down:

```
uqs graph                      # from the feeds down: "if this stops, what starves"
uqs graph posbook1 --upstream  # what must be up for posbook1 to work
uqs graph -i                   # browse it: type to filter, ctrl+t to turn it over
```

The graph is not a tree - one feed reaches much of the fleet - so a process
reached a second time is drawn once and marked `↺ shown above` where it would
hide a subtree. Processes with no declared edges, most of the vendored TorQ
fleet, are listed together at the end. `--offline` skips asking the fleet what
is up. In the browser, a filter keeps every match and the path to it, the side
panel describes the highlighted process, and Enter exits printing its name.

## What actually starts

By default (`start all`) the processes marked `startwithall=1` come up: the
vendored rows in `lib/torq-finance-starter-pack/appconfig/process.csv`, plus
uqf's own pipelines, appended as extra rows to a *copy* of that csv that
`uqs.stack.runtime.bootstrap()` generates on the fly (never editing the vendored
file itself). The vendored README explains why the rest stay off: the KDB-X
community edition's connection limits mean `reporter1`, `filealerter1`,
`dqc1`/`dqcdb1`, `dqe1`/`dqedb1` stay off unless you have a fully-licensed
kdb+/KDB-X. `killtick` and `tpreplay1` are on-demand utility processes, not part
of the standing stack, so they also don't auto-start - `tpreplay1` is what
[`data replay`](#replaying-a-tickerplant-log) starts, for one replay, and it
exits when the replay is done.

### Runtimes

All of the above is the `uqf` runtime, the default. The `torq` runtime is the
starter pack as it ships, with nothing of this tree's added. Each runtime is a
declaration in
[`python/uqs/src/uqs/runtimes.py`](../../python/uqs/src/uqs/runtimes.py): its
data directory, whether it has this tree's pipelines, and whether it has this
tree's overlays on the starter pack. A new runtime is one entry there.

```
uqs --runtime torq start      # or UQS_RUNTIME=torq uqs start
uqs --runtime torq stop
uqs runtime list             # each one: processes, tables, layers, data, ports, what is up
uqs runtime diff uqf torq     # what one has that the other lacks
uqs --runtime crypto runtime prepare   # install the bundles it declares
```

`runtime list` marks the default runtime and the one selected now, and counts
what each one's process.csv and schema would hold. It reads the declarations
rather than starting anything. `runtime diff A B` lists, in each direction, the
processes, tables, config layers and commands that one runtime has and the other
doesn't. Both take `--json`. `runtime prepare` installs the [sidecar
bundles](sidecar-bundles.md#bundles-a-runtime-declares) a runtime declares.

`runtime prepare` and `job install` are one installer with two ways in.
`uqs job install FOLDER` installs one bundle, and asks first;
`uqs --runtime R runtime prepare` does the same install, without asking, for
every bundle `runtime_bundles.json` lists for R, and takes R off any installed
bundle it no longer lists. `job install` also takes a plain folder of jobs that
is not a bundle ([below](#installing-jobs-from-elsewhere)); `runtime prepare`
takes only bundles.

  |                | `uqf` (default)                                                         | `torq`                                                            | `peachq`            | `crypto`                                                                                        | `fx`                                                                                        | `peachq-etl`                                                                        |
  | ---            | ---                                                                     | ---                                                               | ---                 | ---                                                                                             | ---                                                                                         | ---                                                                                 |
  | processes      | the vendored rows, with overlays, plus every pipeline                   | the vendored `process.csv`, unchanged: `feed1` on, `monitor1` off | as `torq`           | the vendored rows, with overlays, plus the `crypto` profile's pipelines and what they depend on | the vendored rows, with overlays, plus the `fx` profile's pipelines and what they depend on | as `peachq`, with overlays, plus its declared bundles' jobs and what they depend on |
  | tables         | `database.q` plus this tree's (`fx_orderbook`, the crypto mocks, ...)   | the starter pack's `database.q`: `trade`, `quote`, `packets`      | as `torq`           | `database.q` plus the tables those pipelines read and write                                     | `database.q` plus the tables those pipelines read and write                                 | `database.q` plus the tables those jobs read and write                              |
  | config layers  | TorQ's, `scripts/torqconfig` and `scripts/torqcode`, the starter pack's | TorQ's and the starter pack's                                     | as `torq`           | as `uqf`                                                                                        | as `uqf`                                                                                    | as `uqf`, from a flattened copy (below)                                             |
  | data directory | `output/uqs`                                                            | `output/uqs-torq`                                                 | `output/uqs-peachq` | `output/uqs-crypto`                                                                             | `output/uqs-fx`                                                                             | `output/uqs-peachq-etl`                                                             |
  | base port      | `6050`                                                                  | `6150`                                                            | `6450`              | `6250`                                                                                          | `6350`                                                                                      | `6550`                                                                              |
  | interpreter    | KDB-X                                                                   | KDB-X                                                             | PeachQ              | KDB-X                                                                                           | KDB-X                                                                                       | PeachQ                                                                              |

`crypto` and `fx` are focused stacks: the starter pack, this tree's layers, and
one profile's pipelines with everything they depend on in the job graph - the
same walk `uqs start --profile` takes, so the runtime and the profile cannot
disagree about what the profile needs. Their schemas define only the tables
those pipelines read and write, so their HDBs hold nothing they never write, and
they fit the licence's connection cap with room to spare:

```
uqs --runtime crypto start --profile crypto   # cryptomock1, crypto_markout1 and what they read, on 6250
uqs --runtime crypto process list
```

A plain `start` starts only the rows marked `startwithall`, the TorQ core and
the always-on jobs; the profile's own leaves are not among them, so name the
profile.

`peachq-etl` is experimental: PeachQ's capture stack with this tree's layers and
the sidecar bundles declared for it, loaded from a copy of the q tree converted
so PeachQ can load it. See [sidecar bundles on
PeachQ](sidecar-bundles.md#peachq) for what runs there and what is refused.

Each runtime declares its own profiles (`profiles` in `runtimes.py`).
`--profile` takes only those, `list profiles` shows only those, and any other is
refused, naming the runtime's own:

  | runtime  | profiles                                                                                                      |
  | ---      | ---                                                                                                           |
  | `uqf`    | `all`, `arbitrage`, `crypto`, `default`, `depth`, `essential`, `fx`                                           |
  | `torq`   | `essential`; `feed`, which is `essential` plus `feed1`; `full`, the starter pack's default fleet plus `feed1` |
  | `peachq` | `capture`, what runs on PeachQ today (below)                                                                  |
  | `crypto` | `crypto`, `essential`                                                                                         |
  | `fx`     | `essential`, `fx`                                                                                             |

A test resolves every declared profile against its runtime's processes. A
profile that names a process its runtime doesn't have fails that test, not your
start.

`peachq` is `torq`'s stack on [PeachQ](https://github.com/peachq-org/peachq),
the MIT-licensed q interpreter, rather than KDB-X: the starter pack as it ships,
with no connection cap and no licence needed. Its binary is your `QCMD` when
that is PeachQ, otherwise the pinned build from `scripts/peachq/resolve.py`'s
cache, built into the cache the first time (`uqs.interpreter.peachq_binary`).
That stack alone gets that `QCMD`; your shell's own `QCMD` and `PATH` are left
alone, and `status` names the interpreter on its first line. KDB-X stays the
default and the interpreter everything here is verified against.

```
uqs --runtime peachq start --profile capture
```

Its one profile, `capture`, is what runs on PeachQ today: `discovery1`, `stp1`,
`rdb1`, `hdb1`, `gateway1`, `housekeeping1`, `sctp1`, `sortworker1`, `metrics1`
and `feed1`. Started that way here, every one came up and answered, `feed1`'s
`trade` and `quote` reached `rdb1`, and `stop` stopped them all. It leaves out
the processes that hit PeachQ gaps, none of them in this tree:

- `wdb1` fails to load `wdb.q`: PeachQ has no `.Q.chk`.
- `sort1` won't start: PeachQ refuses the `-s -2` it is given.
- `reporter1` fails to load `reporter.q` with `type`.
- `hdb1` runs, but its partitioned tables aren't defined, so a gateway query
  that routes to it fails.
- `monitor1` doesn't exit on `stop`.

So end of day and the HDB don't work on it yet. It is a capture-and-query stack
for today's data.

A runtime on PeachQ can't load this tree's pipelines as written. PeachQ can't
load the nested namespaces the ETL tree is built from (peachq-org/peachq#80), so
declaring one with `pipelines=True` is refused when `runtimes.py` loads, unless
the tree is flattened and the runtime marked experimental, as `peachq-etl` is.

A new runtime's HDB holds only the starter pack's two sample partitions. To
compare it with `uqf` on the same history, copy `uqf`'s partitions of the tables
both declare:

```
uqs --runtime torq data seed --from-runtime uqf             # the partitions it would copy
uqs --runtime torq data seed --from-runtime uqf --apply     # every table torq declares
uqs --runtime torq data seed --from-runtime uqf --table trade --table quote --from 2026-09-01 --to 2026-10-01 --apply
```

Like `data replay`, it is a dry run unless `--apply`, which names the partitions
and asks first; `--yes` answers that. With `--on-conflict replace` it copies
over each table already here, every partition when no range is given.

The partitions copied are those whose date lies in the [time
range](#cli-conventions); either bound may be left off. `--from RUNTIME` and
`--dates FIRST..LAST` are the old spellings, kept for one release.

Each symbol column is re-enumerated against the target's sym file. A plain copy
of the partition directories would decode, in the target, to whatever its sym
file holds at the source's indices: plausible values, all wrong. The source is
only read. Everything is checked before anything is written, so a table the
target doesn't declare, or a column whose type differs from its declaration, is
refused by name with the target untouched. A partition the target already holds
is skipped unless `--overwrite`. Afterwards the target's partitions are filled
as at bootstrap, and a running HDB sees them once restarted.

Without the service layer, `torq` has none of the [query
policies](../architecture/query-policies.md): no data-access API, no `.pm` on
the gateway, and the starter pack's access list everywhere.

Each runtime keeps its own data directory, because an HDB one runtime wrote
holds tables the other does not declare. Each also has its own base port, and
the spans of ports the runtimes' processes use do not overlap, so `uqs start`
and `uqs --runtime torq start` run side by side with no flags. That is the
quickest way to tell whether a problem is TorQ's or this tree's: the same query
against both at once. `--base-port` still moves either anywhere.

Before starting, `start` and `restart` check whether another stack already holds
a port they need: another runtime, the same runtime on another `--base-port`, or
a stack from another checkout of this repository. If one does, the command
refuses before anything starts, naming the stack and how to stop it:

```
$ uqs start
ERROR    | the torq runtime's stack (base port 6050) is already using ports this start needs: stp1 :6050 (pid 4242), ... Stop it with `uqs --runtime torq stop --base-port 6050`, or start this one elsewhere with --base-port.
```

A port held by anything else - a q started by hand, which answers like an empty
stack - is refused the same way, with the pid and command line of what listens
on it (#2180).

torq.sh returns once each process is forked, not once it is up. So after the
launch, `start` and `restart` (without `--follow`) wait `--grace` (3 seconds by
default) and check every process they started the way `uqs status` does: by its
pid, and by who listens on its port. One that died while loading, or whose port
another process answers, fails the command with a non-zero exit, naming it, why,
and the last lines of its log. A one-shot such as `tpreplay1` is not checked.

`uqs status` names the runtime and base port it is reporting on in its title.

On `torq`, `list processes` shows the 23 starter-pack processes. `essential`
leaves out `feed1`, so nothing publishes; `feed` adds it, so `trade` and `quote`
come in. Commands that work only on this tree's pipelines refuse, with the
reason - `graph`, `job backfill`, `job gaps`, `job preview`, `job runs`,
`job check`, `job checkpoint remove` and `connector`;
`uqs runtime diff uqf torq` lists them, read off the commands themselves. So
does a profile `torq` doesn't declare:

```
$ uqs --runtime torq start --profile fx
ERROR    | profile(s) fx are not declared for the torq runtime - its profiles are essential, feed, full, or --runtime uqf
```

### Profiles

`start all` is one answer to "what should be running", and on this licence it is
nearly the only one you can afford. Fourteen tickerplant slots are available
(sixteen on the licence, two held back for ad-hoc handles), and the table below
says what each profile holds of them - so which profiles fit, and which only fit
alone.

A profile names the processes you actually came for; everything they read is
derived from the same dependency graph `status`'s **Depends on** column uses:

```
uqs profile list
uqs start --profile arbitrage
uqs start --profile depth,arbitrage   # two profiles that fit together
uqs start --profile essential      # the TorQ stack alone, no uqf jobs
uqs start --profile essential vectorize1 cross1   # a profile plus named processes
UQS_LICENCE_CONNECTIONS=32 uqs start --profile all   # on a licence that allows it
```

Process names given beside `--profile` are added to the set: the profile's
members, infrastructure first, then the names, each started once. They are not
widened to what they read - a name whose producer is missing is warned about, as
on a plain start - and `all` cannot be added. The budget below is checked on the
whole combined set, so a profile that fits plus names that take it past the cap
is refused before anything starts. `restart`, `start --follow` and
`stop --profile` take the same combination; `stop` resolves the set without the
budget, so a set too big to start can still be stopped.

The uqf runtime's profiles, against the community licence's fourteen slots
(`uqs profile list` reports the same against this machine's licence):

<!-- BEGIN GENERATED profile table - scripts/generate/generate_operational_docs.py; CI runs it with --check, so edit the profiles, not this table -->

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

**A profile over the cap is refused**; a positional `start` over it is only
warned about. Two profiles that each fit can be over the cap together - `fx` and
`arbitrage` are - and the refusal says by how much:

```
$ uqs start --profile fx,arbitrage
profile(s) arbitrage, fx need … tickerplant connections, and only 14 are
available (16 on this licence, 2 held back for ad-hoc handles). ...
```

**`all` needs a larger licence, and says so.** It is every standing set at once -
the union of the other profiles' leaves, derived so a new leaf joins it
automatically - and that holds more plant connections than the community licence
has (see the table). On that licence it is refused like any profile over the
cap. On a q licence that allows more concurrent connections, say how many with
`UQS_LICENCE_CONNECTIONS` and it starts: that setting is the budget every start
is held to - `--profile`, `uqs profile list`' `fits` column and the
positional-start warning. On PeachQ (`QCMD` naming a PeachQ binary, see the
README's Requirements) there is no cap at all, so `all` starts as it is. `all`
leaves out `crypto`, because the mock replaces cryptorust's recorders rather
than joining them; ask for it with `--profile all,crypto`.

Profiles never change `startwithall`, so `start all` is untouched. A closure
stops at a table fed from outside the stack: `fx` needs `crypto_book` but does
not start `cryptomock1`, which replaces cryptorust's recorder rather than
joining it.

**`essential` is the TorQ stack with nothing on top** - `uqs profile list` names
what it resolves to, and the table above what it holds. It is the infrastructure
every other profile starts less its second HDB (`hdb2`) and second sort worker
(`sortworker2`), plus `reporter1` and `tpreplay1`, which only `essential`
starts. At end of day `wdb1` hands its writedown to `sort1`, which sorts it into
the HDB with `sortworker1`, so `wdb1` stays free. `reporter1` holds no plant
slot, but it does open handles to `gateway1`, `rdb1` and `hdb1`. On a runtime
with this tree's overlays it doesn't read the site's `appconfig/reporter.csv`.
It reads `reporter.csv` in the data directory, which bootstrap writes on every
start from `UQF_REPORTER_CHECKS` (`python/uqs/src/uqs/stack/reporter.py`). That
list is a memory check plus activity checks, each with its own window and row
threshold, and a check on a table the runtime doesn't publish is left out with a
log line saying why. A table name that nothing in the tree defines stops the
bootstrap with an error. `tpreplay1` starts and exits: it is the one-shot replay
[`data replay`](#replaying-a-tickerplant-log) aims with a log, a schema and an
HDB, and started without them it exits at startup, before it reads or empties
anything - so expect it shown down. Composing `essential` with a job profile -
`--profile essential,fx` - starts both sets.

**`housekeeping1` keeps tickerplant logs replayable.** On a runtime with this
tree's overlays it doesn't read the site's `appconfig/housekeeping.csv`, which
tars tickerplant log directories a day after they are written, into a form
replay cannot read. It reads `housekeeping.csv` in the data directory, which
bootstrap writes on every start (`python/uqs/src/uqs/stack/housekeeping.py`).
Process logs are kept as the starter pack keeps them. A tickerplant log
directory is left exactly as written for `TPLOG_REPLAY_RETENTION_DAYS` (7) days,
then gzipped, and only on Linux, where TorQ's replay reads a gzipped log. No
rule deletes or tars a tickerplant log.

**`housekeeping1` compresses the HDB by the declared policy.** Its last rule,
`hdbcompress`, runs TorQ's `.cmp` with the compression policy
(`compressionconfig.csv` in the data directory, declared in
`src/etl/compression_policy.q`) through `.qtorq.compress.run_held`, which
selects exactly what `uqs data compress --apply` would: each file is checked
after it is swapped, and the HDBs are told to reload when anything changed. A
partition younger than its table's `minage` is left alone, and so is one the
coverage ledger says a backfill is still writing; each partition is compressed
holding the HDB write lock backfills take. A failed run is logged as an error
and doesn't stop the other rules; the next run continues from where it stopped.
It never uses housekeeping's own `kdbzip`, which compresses every file one
hard-coded way. The rules run daily at `HOUSEKEEPING_RUNTIMES` (02:00), and
`uqs config housekeeping show` shows the schedule in effect.

**TorQ discovers only what the profile started.** `process.csv` still lists
every process, but TorQ's discovery would hand all of them out, so under
`essential` `sort1` would keep retrying `sortworker2`'s port. A profile start
writes its resolved set to `deployment.txt` in the data directory, and every
TorQ process (through `scripts/torqcode/handlers/deployment.q`) skips the
`process.csv` rows outside it. Any later start adds to the set -
`uqs start sortworker2` makes it discoverable from then on - and stopping a
process does not take it out, so a selected process that goes down is still
retried and its failures still logged. A stack started without a profile has no
set, and TorQ discovers as it always has.

Profiles are declared in `python/uqs/src/uqs/model/profiles.py`, by their
leaves.

### Skipping a start check, for one command

Two of a start's refusals guard against a start that would go wrong, rather than
one that cannot run. To try it anyway, for instance testing compatibility,
override that one check by name and give the reason:

```bash
uqs start rdb1 --override q-version-check --reason "checking which jobs load on uat's 4.1"
uqs start --profile fx --override profile-budget-check --reason "full licence on this host"
uqs restart rdb1 --override q-version-check --reason "..."     # takes both too
```

- `q-version-check` starts despite a KDB-X older than 5.0 for the tree as
  written. The tree is not converted. A process that cannot load it fails, and
  the failure is in its log.
- `profile-budget-check` starts (or `--dry-run`s) a `--profile` past the
  connection-budget estimate. The licence's own limit is still q's to enforce.

Each also has a generated alias, `--skip-q-version-check` and
`--skip-profile-budget-check`, which is the same override: the same `--reason`
is required, and the same warning and record follow. `uqs start --help` shows
what each waives, the risk and what to do, from the guardrail's declaration, and
`uqs --target` sends it to a server as `--override NAME`.

`uqs override list` names every override and the commands that take it.

Each override:

- needs a nonempty reason;
- applies to this command only;
- prints a warning naming what may go wrong;
- is appended to `start_overrides.jsonl` in the runtime's data directory, with
  the command, the processes and the runtime.

An override that would skip nothing is refused: `q-version-check` with
`--dry-run`, or `profile-budget-check` without `--profile`. Nothing else can be
skipped. Port ownership, process names, attachment verification,
converted-source integrity and the launcher's checks still refuse. The MCP
server and the browser never skip anything. Through `uqs --target`, the reason
is forwarded as given, and the start asks first as always.

### Idle subscribers

A subscriber started without its producer subscribes **successfully**. The table
is defined on the tickerplant whether or not anybody publishes to it, so the
process comes up, heartbeats, reports `up`, and receives nothing for as long as
you leave it. There is no error and no symptom except an output table that stays
empty.

So `start` and `restart` warn when what you start has an input nothing running
publishes:

```
$ uqs start superbook1
warning superbook1 subscribes to `market_data`, which no running process
        publishes - start `uqs start marketdata1`.
```

and `status` says the same about processes that are already up:

```
2 running process(es) have an input nothing running publishes - up, but idle:
  · executions1 subscribes to `crypto_trades`, which no running process
    publishes - start `uqs start cryptomock1`, unless it is coming
    from cryptorust's kdb recorder, which cryptomock1 stands in for.
```

Both warnings are **advisory and never block a start**. Bringing a subscriber up
before its feed is how you avoid missing the first batch, and some tables come
from outside the process list entirely - the Databento feed handler,
cryptorust's recorders, a backfill run - which is why those are named as context
rather than reported as faults. Processes you name in the same command count as
present, so the recommended form for a chain is silent:

```bash
uqs start marketdata1 superbook1 arbitrage1
```

`crossarb1` is a second consumer of that chain, answering the other arbitrage
question - the direct book against a synthetic route rather than two sources on
one pair. The chain plus BOTH detectors is four plant connections on top of
whatever is already running, which is what [`--profile arbitrage`](#profiles)
exists for: it starts that set and is checked against the budget first, where a
positional start is only warned about. See [the cross-arbitrage
service](../services/cross-arbitrage.md).

`status` opens with one line saying which q runs the fleet, and the connection
budget that follows from it:

```
interpreter: kdbx (/Users/me/.kx/bin/q) - 16 connections
interpreter: peachq (/opt/peachq/q) - no connection cap
```

The implementation is asked of the binary, which is the only thing that says it
(#1753); a binary that will not say is named in a warning, and so is a PeachQ
other than the pinned build. `uqs config env` carries the answer as its
`q_impl (binary)` row.

`status` shows the whole graph, not just the unsatisfied part of it, in columns
derived from the same declarations:

```
uqs status                     # every column
uqs status --columns status    # the status ones, for a narrow terminal
uqs status --columns "Process,Depends on,Inputs,Outputs"
uqs status --sort Port         # by any column, numeric-aware; --reverse for descending
uqs status --sort Status --columns status
```

`--sort` takes any column, shown or not, and sorts as `uqs list --sort` does.

```
┃ Process      ┃ Depends on                  ┃ Inputs                ┃ Outputs        ┃
│ posbook1     │ executions1, marketdata1    │ executions,           │ position       │
│              │                             │ market_data           │                │
│ databento1   │ (databento_mbp10: external) │ databento_mbp10       │ eq_orderbook │
│ executions1  │ fxtradesfeed1, cryptomock1, │ trades, crypto_trades │ executions     │
│              │ (crypto_trades: external)   │                       │                │
│ upstream_    │ (upstream_trades: external) │ upstream_trades       │ imported_trades│
│ backfill1    │                             │ (source)              │                │
```

`Inputs` and `Outputs` are the tables a process subscribes to and publishes;
`Depends on` resolves those inputs to the **processes** that produce them, which
is the question behind every `up, but idle` line above. A table produced from
outside the process list is named as external. A backfill subscribes and
publishes nothing on the plant, so its row shows what its bounded worker
declares instead: the `source` it reads, marked `(source)`, and the dataset it
writes (its one `outputs`). A process with no declared edges - every vendored
TorQ one - shows a dash. On a narrow terminal, `--columns status` drops the
graph columns.

### Responds column

`Status` comes from a PID lookup, and a hung process still has a PID.
`Heartbeat` notices only after a tolerance of missed beats. `Responds` asks
every `up` process directly, all at once, whether it can complete the kdb+
handshake within `--probe-timeout` (0.5s by default):

  | Responds   | Meaning                                                                                                                                                      |
  | ---        | ---                                                                                                                                                          |
  | `4ms`      | it answered, in that long                                                                                                                                    |
  | `timeout`  | it accepted the connection but did not answer in time - busy in a long query, a timer or its load. A process at its licence connection cap can look the same |
  | `reset`    | it dropped the connection - what a process past its connection cap does to a new handle                                                                      |
  | `rejected` | it closed the connection without answering: it refused the credentials                                                                                       |
  | `refused`  | nothing is listening on its port                                                                                                                             |
  | `-`        | not probed: the process is down, or `status`'s own budget ran out                                                                                            |

Any `up` process that does not answer is also named in a red line under the
table.

The probe is the handshake, not a query, so no q code runs on the process.
`--probe-timeout 0` skips it.

### Batches column

A streaming job whose batch handler throws keeps its PID, its heartbeat and its
handshake: the transport traps the error, and the job drops the batch. `Batches`
says what each job's batches did since its last uptime beat (once a minute):
`ok`, `failing (N)` with N its failed batches so far, or `-` for a process that
runs no streaming job. A failing job is also named under the table with its last
error, and `uqs job gaps <job>` lists the span it dropped.

### status --timeout

`status` gets **one budget for the whole command** (120s by default): listing
the processes, asking `monitor1` for heartbeats, and probing each process. Any
of them could otherwise hang - `monitor1` at its connection cap accepts a
connection and never answers.

```
uqs status --timeout 10   # fail fast
uqs status --timeout 0    # wait forever
```

The listing goes first and the heartbeat lookup gets what is left (at least one
second); the probe takes the smaller of `--probe-timeout` and the remainder, or
is skipped, showing `-`. A listing that runs out is a refusal, not a traceback:

```
listing processes did not finish within 120s
```

A heartbeat lookup that runs out degrades to the column's usual "monitor1 could
not be reached".

### monitor1

Upstream ships `monitor1` with `startwithall=0`; uqs turns it on
(`VENDORED_STARTWITHALL_OVERLAY` in `stack/procs.py`). It is the only process
that collects heartbeats, so without it `status`'s Heartbeat column reads "not
collected" on a healthy stack.

- **Under the community licence, coverage is partial.** A q process may hold 16
  connections, and a monitor with none left cannot accept the query `status`
  reads heartbeats with. So `stack/monitor_budget.py` stops monitoring
  `sortworker`, `reporter`, `housekeeping`, `feed`, then `metrics` processes
  until the rest fit. Those show `-` in the Heartbeat column. `uqs start`,
  `restart` and `status` each print one line naming the proctypes given up,
  counting only types something in the fleet runs as. The fleet is the one the
  start brings up - a profile's resolved set, or the set the last profile start
  recorded - not the `startwithall=1` rows, so `--profile crypto`, whose
  nineteen processes fit, keeps `feed` and hears `cryptomock1`. On a fully
  licensed kdb+/KDB-X, or on PeachQ, nothing is dropped.
- **uqf's standing ETLs (`metrics`) are monitored too**; `backfill` workers are
  not, because a finished job's heartbeat would age into a false `error`.

To get the upstream behaviour back:

```bash
uqs config set monitor1 startwithall=0
```

### Default ports

Every port is the stack's base port plus a fixed offset. The base is the
runtime's own (`uqs list --help` names each), and `--base-port <n>` overrides
it. `uqs process list` prints every process's port at the base in effect,
vendored and uqf's alike; uqf's own take the offsets the vendored ones leave
free. The generated [process table](../reference/processes.md) lists both, each
with its port at the default base.

## Changing a process's config

`config set` writes an override to `python/uqs/process_overrides.csv` (ignored
by git because it belongs to this host), applied every time `process.csv` is
regenerated - which is every command, so the generated file itself is never the
place to edit. The vendored `process.csv` is never touched. Installed sidecar
bundles keep their rows in `python/uqs/bundle_overrides.csv`; `config set`
changes only this host's file and takes precedence over bundle rows.

```
uqs config show fxfeed1
uqs config show fxfeed1 startwithall
uqs config set fxfeed1 startwithall=0
```

`config show` resolves placeholders as `torq.sh` does at start time -
`load=${KDBHDB}` to the real path, `port={KDBBASEPORT}+3` to `6053`;
`--unresolved` shows the literal value.

Valid `FIELD`s are `process.csv`'s own columns: `host`, `port`, `proctype`,
`procname`, `U`, `localtime`, `g`, `T`, `w`, `load`, `startwithall`, `extras`,
`qcmd`. A change takes effect on the next `start`/`restart` of that process (the
running process itself isn't touched).

A site launcher that reads a column TorQ lacks, such as `taskset_no` for CPU
affinity, has it declared in `UQS_PROCESS_FIELDS`. Each declared column is added
to the generated `process.csv`, after `qcmd`, and every row has it, empty unless
set:

```
export UQS_PROCESS_FIELDS=taskset_no
uqs config set stp1 taskset_no=3:5
```

Every command regenerates `process.csv`, so the variable has to be set for each
one. An override for a column that isn't declared is refused, naming the
variable. A deployment declares it per target, in its `deploy.env` ([columns the
site launcher reads](deploy.md#columns-the-site-launcher-reads)).

A value with a comma, a double quote or a newline is refused. `torq.sh` reads
`process.csv` by splitting each line on commas with `awk`, so it can't carry a
quoted comma: the rest of the value would land in the next column. To pass a
list, separate it with spaces:

```
uqs config set fxfeed1 "extras=-pairs EURUSD USDJPY"
```

An override like that already in the file stops the next command, and the error
names the process and field.

### What sources connect to

`config sources` shows the `sources.csv` the stack reads: the first of TorQ's
application, service and base config layers that has one, the rule
`.proc.getconfigfile` applies. For each row it shows where that source's
credential comes from and what would stop it resolving: a stub, an unset secret
variable, a `${VAR}` outside the list. A secret shows only as set or not set.
`config sources stub SOURCE` adds a `SCAFFOLDED` row for `SOURCE` to the
gitignored application layer's file, to fill in. If that file doesn't exist yet,
it starts as a copy of the one the stack reads, so no row is hidden. A row
already there is never changed. See [where a source
connects](../reference/pipeline-declarations.md#where-a-source-connects-sourcescsv).

```
uqs config sources
uqs config sources stub duckdb_deals
```

## Logs

Every process writes `out_<procname>.log` and `err_<procname>.log` in
`output/uqs/logs/` - check these first when a process shows `down`. `logs` reads
them through the CLI's own coloured logger:

```
uqs logs                          # last 20 lines per process, all processes
uqs logs stp1 rdb1 -l 50          # last 50 lines each, merged and time-sorted
uqs logs -f                       # the last 20 lines, then live, Ctrl-C to stop
uqs logs fxorderbookfeed1 -f --level WARNING   # live tail, warnings/errors only
```

Lines are sorted by the log's own timestamp. `-f` keeps following across
restarts and the daily roll. `--level` filters to that level and above.

Values are coloured by what they look like: numbers magenta (red when negative),
q symbols yellow, dates and timestamps green, UUIDs and 32/40/64-digit hex
digests light blue, and other `key=value` values light cyan. Each element of a
vector such as `sizes=1000000 2e+07` or `` symbols=`EURUSD`GBPUSD `` is coloured
on its own, and so is each atom of a list such as `` x=(1;`a) ``. Text in double
quotes is never looked inside. The colours change nothing else: with colour off
the line is the log's text exactly.

An attached backfill or receiver is no TorQ process and has no `out_` log.
`uqs logs --attachment NAME --worker WORKER` reads its latest run instead, with
the same `-f`, `-n` and `--level`; `--run` picks an older run
([attach-existing-torq](attach-existing-torq.md#seeing-what-happened)).

`uqs logs --multitail` follows the same files in
[multitail](https://www.vanheusden.com/multitail/), one pane per file, titled
with its file name, instead of merging them - so two busy processes stay side by
side rather than interleaved. It takes the same process names and needs the
`multitail` binary (`brew install multitail`, `apt install multitail`):

```
uqs logs --multitail rdb1 fxpositions1          # a pane for each out_/err_ log, stacked
uqs logs --multitail all --stream err --pane-columns 2  # every process's err_ log, in two columns
uqs logs --multitail stp1 -l 100 --show-command        # show the multitail command, run nothing
```

A process that has never started has no log and gets no pane; a name that is not
a process is refused. Press `q` to leave multitail.

### Channels: which subsystem a line concerns

A line has three things to say apart: **which process** wrote it, **how severe**
it is (`--level`), and **which subsystem** it concerns - its *channel*. TorQ's
log line has a column for each, `time|host|proctype|proc|level|id|message`; the
ETL layer fills `proc` with the process that actually wrote the line (its
procname) and `id` with the channel. The worker, source, run and window a line
is about stay in the message as `key=value` fields, never in a channel of their
own.

  | Channel     | Scope                                                          |
  | ---         | ---                                                            |
  | `lifecycle` | process and job startup, shutdown, run and window completion   |
  | `ipc`       | connections, requests, responses, timeouts, retries            |
  | `source`    | source readiness, fetching, queries                            |
  | `transform` | normalisation, matching, calculations, rejected rows           |
  | `storage`   | writes, persistence, sorting, compression                      |
  | `coverage`  | coverage updates, checkpoints, replay decisions                |
  | `eod`       | rollover, handover, reload coordination                        |
  | `config`    | configuration, file loading, declarations                      |

`uqs logs` shows the channel beside the process, and `--channel NAME` keeps only
that subsystem's lines. It repeats - `--channel source --channel ipc` keeps
either - and it combines with the process names and `--level`, for the recent
lines and for `-f` alike:

```
uqs logs rdb1 --channel source                       # recent lines of one subsystem
uqs logs rdb1 -f --channel source --channel ipc --level DEBUG
uqs logs --json --channel coverage                   # each line carries "channel"
```

**`--channel` filters what you view. It does not stop a record being generated
or rendered:** whether a line is written at all is its level's decision
(`.qetl.log.debug[1b]`, `.qetl.log.trace[1b]`), and a hidden channel's lines are
still produced, still in the file and still published where the level is
published. Per-channel verbosity, a runtime switch per channel and a file per
channel are not built.

A channel name has to be one of the eight above; anything else is refused with
the list, because a misspelt channel would read as a quiet subsystem. A line
whose `id` is not a channel - one TorQ's own code wrote (`main`, `dotz`, ...),
or an older line that still names a worker - is shown as it is, never dropped.
It has no channel to select, so `--channel` leaves it out. In the file the
channel is the `id` field: a TorQ process started with `-jsonlogs` writes it as
`"id"`, and a published `logmsg` row carries it in its `id` column, with the
emitting process in `sym`.

Two names changed with this (#2274), with no alias: `--id` was the filter on the
same column and is refused, naming `--channel`; the old `--channel`, which took
no value and subscribed, is now `--subscribe`.

`uqs logs --subscribe` subscribes to the lines the processes publish instead of
reading their files. Every TorQ process publishes its log to a `logmsg` table
through its own pub/sub, which is what TorQ's monitor reads. A subscription
picks the processes, the channels (`--channel`, repeatable) and a minimum
`--level`, and prints matching lines live until Ctrl-C:

```
uqs logs all --subscribe --level ERROR                  # every error, from every process
uqs logs fxpositions1 --subscribe --channel source      # one process's source warnings and errors
```

Only what a process publishes reaches a subscription. WARNING and ERROR are
published by default, INFO and below are not
(`scripts/torqconfig/settings/default.q` maps `.lg.pubmap` onto these names);
`--channel` here is again only a filter on the arriving rows. A process that is
down, or restarts, is reported once and resubscribed. Each subscribed process
holds one more connection, so on a stack near its licence connection limit, name
processes rather than `all`. `-f` reads the files, so it is the one to use when
a process is not running or is not a TorQ process, such as the external feeds.

**`uqs start --follow` (`-f`) is the foreground form of all this**: it starts
what `start` would - the same names, `all`, or `--profile` - then streams those
processes' logs to this console until Ctrl-C. Ctrl-C ends the stream, as it does
for `uqs logs -f`, and the processes keep running (#1854). `--stop-on-exit`
makes the console the run, the way `docker compose up` does: Ctrl-C then stops
what it started. `restart -f` does the same after a restart.

```
uqs start -f                          # the default set, streamed; Ctrl-C leaves it up
uqs start -f --stop-on-exit           # ... and Ctrl-C stops what it started
uqs start rdb1 fxpositions1 -f        # just these
uqs start --profile fx -f --level WARNING
uqs start fxmarkout1 --trace          # trace its source queries (see below)
```

What a process prints while it loads is shown too. With `--stop-on-exit`,
processes already running when it began are left running at Ctrl-C.

`start --json` (and `restart --json`) prints one document instead (#2687):
`started` (what it launched), `already_running` (what a start left alone, as
torq.sh does; a restart has none), `untraced` (what `--trace` did not reach),
`warnings` (the advisory lines a start prints), torq.sh's `output`, and `ok`,
with the `error` the grace check names when a process did not come up. With
`--dry-run` it is `{dry_run, would_stop, start_lines: [{process, line, log}]}`,
composed and never bootstrapped. It is refused with `--follow`, as on
`job backfill`: a stream is not one document.

The same launch options - `--follow`/`-f`, `--level`, `--stop-on-exit`,
`--trace`, `--json`, `--dry-run`, `--override`/`--reason` and `--base-port` -
are `uqs job backfill`'s and `uqs job attachment add`'s too, declared once
(`cli/launch_options.py`) and meaning the same on each: on a backfill they act
on the run it starts, on an attachment on the publisher it starts once verified
([attach-existing-torq.md](attach-existing-torq.md)).

### Where a process stopped

Every uqf process script - `torq_stream.q` (every streaming job),
`torq_backfill.q`, `torq_tap.q`, `run_stream.q` - logs the stages where it can
stall, so the last line in `uqs logs <procname>` says where it stopped:

  | Last line                                                                    | What it means                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
  | ---                                                                          | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `qtorq: loading uqf tree from ...` with no `loaded in` after it              | a q file failed to load - the error follows, or is in `err_<procname>.log`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
  | `starting streaming job`                                                     | the job and what it subscribes to and publishes, logged before anything can block                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
  | `waiting for the tickerplant - if this is the last line, it is not running`  | `stp1` is down: `uqs start stp1`. This used to wait forever in silence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
  | `subscribing` / `streaming job wired - running`                              | subscribed and running                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
  | no `first batch received` for a table                                        | nothing is arriving on it: its publisher is down or publishes nothing                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | `first batch received` but no `first rows published`                         | input arrives and the job publishes nothing from it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `on_batch failed`                                                            | the job's handler threw, with the table and the error                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | `backfill process failed` then `backtrace`                                   | a backfill's error, and where it happened                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `has no credential - refusing to write its fixture` (init)                   | a backfill whose source has no credential was not asked to write the fixture (#1082). Set the source's credential to run live, or pass `--fixture` (`UQF_FIXTURE_WRITES=1`) for a demo run, whose coverage is recorded under `<version>~fixture`                                                                                                                                                                                                                                                                                                                                                                                                     |
  | `no credential - running on the source's fixture` (WARNING)                  | a backfill is publishing the fixture, not live data. The line gives the `sources.csv` row to add (`settings_row`), the variable that overrides it (`UQF_SOURCE_CRED_<SOURCE>`), what the value should be for that source - an ODBC connection string, or `host:port` for kdb+ - and an example. Add the row (`uqs config sources stub SOURCE` starts one), or export the variable in the shell you run `uqs job backfill` from                                                                                                                                                                                                                       |
  | `hdb reload: could not open a handle to every registered hdb` (ERROR)        | a backfill wrote its rows and the running HDB has not reloaded them. `registered` is how many HDBs discovery knows, `opened` how many let this process in; TorQ's own `connection to ... failed: access` line just before says it was refused. A backfill connects as the ETL identity (`appconfig/passwords/metrics.txt`) unless `passwords/backfill.txt` or `<procname>.txt` gives it its own - see its `outbound credential` line at startup. `hdb reload requested` now reports `registered`, `opened` and `reloaded`, so `0 0 0` is "no HDB running"                                                                                            |
  | `backfill finished with rows a running hdb has not reloaded` (ERROR)         | the run's last reload still fell short of every registered HDB (#1084). The rows are on disk and covered; a running HDB serves them once it reloads or restarts. Until then the run's report says `hdb_unreloaded 1b`, and a refused or failed reload is retried at each later window rather than forgotten. The `hdb reload` line before it says which HDBs were registered, opened and reloaded                                                                                                                                                                                                                                                    |
  | `idle - every window in the range is already covered`                        | nothing to do at this `--source-version`: coverage says the range is done. A new source release is a new version                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
  | `checkpoint is for another run - starting from the beginning`                | the range or version changed since the last run, so its checkpoint does not apply                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
  | `state file unreadable - using the previous generation` (WARNING)            | a cursor, checkpoint or ledger file did not parse - a crash mid-write, a full disk, a hand edit - and its `.bak` was used instead: at most one page or window is done again                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | `load_checkpoint: ... is unreadable ... and so is its .bak`                  | neither generation of a backfill's checkpoint parses. `uqs job checkpoint remove WORKER` starts the run over; windows already in the coverage ledger are still skipped                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
  | `retrying after a transport error`                                           | the source failed transiently; the attempt, backoff and error follow                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

**More detail - the DEBUG level** - adds the process's pid, port and cwd, the
subscription result, the tables the tickerplant defines, every timer installed,
and every batch and publish with running totals. A backfill adds its
declaration, stage timings, and the ETL core's own decisions: the coverage gaps
it planned against, each fetch (live or fixture, the bounds sent to the source,
rows fetched and kept, time taken), contract checks, coercion failures, every
retry, lock and checkpoint, and each coverage record. Streaming jobs add their
registration and publish wiring, and every transform call its rows in and out.
Two ways to switch it on:

```
uqs --debug job backfill <worker> ...                 # a backfill: passes -verbose
uqs exec -- start <procname> -extras -verbose         # any process, at start
uqs query ".qetl.log.debug 1b" --port <port>              # a process already running, no restart
```

`-verbose` is uqf's own flag, taken by every process script. It is not TorQ's
`-debug`, which also stops the log going to its file.

One level below that, `--trace` logs every query the source is sent (the SQL
statement, or the q lambda and its bounds) and includes everything `--debug`
shows. The same flag switches it on everywhere, before the process's first
source request:

```
uqs job backfill <worker> ... --trace       # a backfill: passes -trace
uqs start <procname> --trace                # live jobs, at start (also restart)
uqs job attachment add <name> --trace       # an attached publisher, as it starts
uqs start <procname> --dry-run --trace      # the start line, with -trace on it
uqs query <procname> ".qetl.log.trace 1b"   # a process already running, no restart
```

`uqs start --trace` adds `-trace` to each start line through torq.sh's
`-extras`, and does not need `--follow`; `--level` stays only a filter on what
`--follow` shows. torq.sh skips a process that is already up, so `--trace`
cannot reach it: the start names each one it skipped, with the two ways to trace
it, `uqs restart <procname> --trace` or the `uqs query` above. TorQ's own
processes (rdb, tickerplant) take no `-trace` and ignore it.

The log record stays one line, so `-f` and `grep` keep working, with the query
as a full-length q string. `uqs logs` prints it as a header and an indented
block:

```
query sent range_from=2026.09.10D00:00:00.000000000 range_to=2026.09.11D00:00:00.000000000
    {[from_ts;to_ts]
            select deal_id from `demo_deals
                where deal_time>=from_ts, deal_time<to_ts
          }
```

`uqs logs --level DEBUG` hides these lines again.

Every line logged while a window runs carries its `worker`, `run` and
`range_from`/`range_to`, so one window's lines can be grepped together; request
traces add a `request` number shared by that request's `sent`, `returned` and
`failed` lines.

Two limits apply when grouping lines by these fields:

- **A reaction's lines carry the window that fired it.** Reactions run
  synchronously when a window publishes, so their lines carry that window's
  `worker`, `run` and range unless the reaction sets those fields itself.
  Reactions replayed at the start of a run carry none of them. Timers and IPC
  handlers never run in the middle of a window, so they never pick up its
  fields.
- **`request` restarts at 1 in every process.** When reading logs from more than
  one process, identify a request by its process and `request` together. The
  process is given by the log file (`out_<procname>.log`), not by a field on the
  line.

### CLI's own logging

`logs --level` filters what the *q processes* wrote. `--debug` shows what `uqs`
itself is doing:

```
uqs --debug status        # this invocation only
LOG_LEVEL=DEBUG uqs status   # same, for a shell session
```

`NO_COLOR=1` turns colour off; `FORCE_COLOR=1` keeps it through a pipe. On
`status`, `--debug` says why a column is blank:

```
summary base_port=6050 torqdata=.../output/uqs
process listing: 47 line(s)
configured ports for 46 process(es)
monitor1 not reached; Heartbeat column is a monitoring gap, not a verdict
parsed 46 row(s): 23 up, 23 down
starved process(es): executions1, marketdata1
```

It also prints each process's load time on its latest start:

```
     Load time on each process's latest start, from its own log
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Process      ┃ Started             ┃ Load time ┃ Note                        ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ fxpositions1 │ 2026-09-24 08:00:00 │ 41.70s    │                             │
│ rdb1         │ 2026-09-24 08:00:00 │ 3.25s     │                             │
│ posbook1     │ 2026-09-24 08:00:00 │           │ no closing banner yet -     │
│              │                     │           │ still loading, or it        │
│              │                     │           │ stopped while loading       │
└──────────────┴─────────────────────┴───────────┴─────────────────────────────┘
```

### The frontend's activity history

The frontend API keeps a history of what operators did in the browser and an
audit of every request that changed something (python/uqf_frontend/README.md,
"Activity history and the action audit"). `uqs activity` reads the same SQLite
file, read-only, with no API running: `--db FILE`, else the file
`UQF_FRONTEND_ACTIVITY_DB` names, else this stack's
`$TORQDATA/frontend/activity.sqlite3`.

```bash
uqs activity list --last 1h --level interactions     # newest first, as the Activity view
uqs activity list --request 0a1b2c3d... --json       # one operation, browser and server
uqs activity export --date 2026-11-03 --format csv -o day.csv
uqs activity export --recorded-by server             # the audit alone, as a timeline
```

Both take the Activity view's filters - `--from/--to/--date/--last`,
`--session`, `--request`, `--action` (a dotted prefix matches: `process` is
every `process.*`), `--subject` (what was acted on: a process selector, a cfg
key, a worker, a view - not the deployment target), `--outcome`, `--recorded-by` -
and `--level`, the most detailed level shown: `actions`, `interactions` (and
actions) or `debug` (everything). `export` writes `--format text` (the default:
one event a line,
`UTC time · session · level · action · target · subject · outcome · duration`,
each browser action grouped under the server request it caused), `csv` or
`jsonl`, oldest first - the document `GET /activity/export` serves, byte for
byte. Events a level's bounds evicted, or the browser had to sample or drop, are
counted, and both commands say how many.

## Services

What each process does, one short card each, is [Services -
Showcase](../services/README.md); a card links to a full page on how the process
is built and why where there is one. This guide covers operating the stack as a
whole. `tap1`, the diagnostic subscriber that logs every batch, is [there
too](../services/tap.md).

## Adding a process

There is one way: declare a job in q and let `uqs job new` scaffold it. See
[adding a pipeline](new-pipeline.md).

### Installing jobs from elsewhere

Jobs written outside the tree - a `sidecars/` folder, another repository - are
installed with:

```
uqs job install ../sidecars                        # the wizard
uqs job install ../sidecars --dry-run              # just the plan
uqs job install ../sidecars --link --yes            # no questions, for scripts
```

A file in `src/etl/sources`, `src/etl/workers` or `src/etl/streaming` is already
registered - `src/etl/init.q` and the registry glob those folders - so
installing is putting each file in the right one. Which one is decided by what
the file declares, not its name or where it sits in the sidecar:
`.qetl.source.define` is a source, `.qetl.job.bounded.define` a worker,
`.qetl.job.stream.define` or `.qetl.job.stream.normalize` a streaming job. The
plan table shows every `.q` file and what will happen to it; these are skipped,
with the reason:

- a file declaring nothing (a helper), or more than one kind (split it: the
  three folders load in a fixed order);
- a job or worker name the tree already declares - q refuses the second
  declaration, so the tree would stop loading;
- `test_*.q` - a q test in `tests/q/` joins the tree's suite on its own (the
  runner finds a suite by the tests it holds), so putting one there is a
  decision rather than a copy.

The wizard asks **copy** or **symlink** (`--copy` or `--link` to say up front).
A copy is a snapshot the tree owns: edit the sidecar and install again. A
symlink keeps the sidecar the source of truth (edits are live on the next
start), but moving or deleting the sidecar breaks the tree, so it suits a
development checkout only. To ship jobs to a server, give the folder a
`bundle.json`: see [sidecar bundles](sidecar-bundles.md). A file already in
place and identical is left alone; one that differs is replaced only if you
agree, or with `--overwrite` (`--yes` alone keeps it).

**Every query must be one `--trace` can see.** A polling feed or a backfill
source that calls its handle directly, as `h(...)`, `h@...`, `h[...]` or
`h"select ..."`, is refused before anything is copied, `--dry-run` included. The
refusal names each line. Send each query through `.qetl.source.ipc_call` (a
polling feed's cursor), `.qetl.source.ipc` (a backfill window),
`.qetl.source.local` or `.qetl.io.odbc.run_sql`. Those log it at TRACE for
`uqs job preview --trace` and `uqs job backfill --trace`. A line that must stay
direct ends with `/ untraced: <why>`.

After installing it regenerates the derived files (`process_ports.csv`,
`pipeline_dag.q`, `processes.md`, `docs/man.q`) - a declaration the generators
refuse is reported here, with what they said - and warns about any table a job
reads, publishes or fills that `src/etl/plant_tables.q` does not define. Then
check it is running:

```
uqs process list                      # the registry sees the new processes
uqs start --dry-run <procname>          # the exact start line
uqs start <procname>                    # streaming jobs
uqs job backfill <worker> --source-version v1 --from 2026-09-01 --to 2026-09-02 --mode plan
uqs job backfill <worker> --source-version v1 --from 2026-09-01 --to 2026-09-02
uqs status --columns status            # up, and Responds: yes
uqs --debug status                     # how long each took to load
uqs logs <procname> -f
q tests/run_tests.q
```

A process that is `down` or not answering: read
`output/uqs/logs/err_<procname>.log`, or run it in the foreground with
`uqs exec -- debug <procname>`. Commit what `git status` shows afterwards,
derived files included - CI checks they are current.

## Connecting

Every process (except the passwordless `feed1`) is protected by
`lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt` - placeholder
demo credentials, `admin:admin` works for everything, and is what `uqs query`
and `uqs schema` log in as by default. For another login, give `--user` and put
the password in a variable whose NAME `--password-env` takes - never on the
command line, where `ps` and shell history would show it:

```
read -rs Q_PW && export Q_PW
uqs query rdb1 "count trade" --user ops --password-env Q_PW
```

The old `--passwd PASSWORD` still parses for one release, hidden, and warns.

```
uqs query \
    "select count i by sym from quote" --port 6052        # rdb1
uqs query \
    "select from quote where sym in \`EURUSD\`GBPUSD\`USDJPY\`AUDUSD" --port 6052
```

For an interactive session, name the process and let `uqs` find its port, or
name none and get the gateway:

```
uqs query                          # a session on gateway1, every line routed
uqs query --no-gateway             # plain qcon on gateway1, nothing routed
uqs query rdb1                     # qcon localhost:6052:admin:admin, under rlwrap if installed
uqs query --base-port 7000         # gateway1 in a stack started with --base-port 7000
```

The gateway holds no tables of its own, so what is sent to it is routed: `uqs`
wraps it in TorQ's `.gw.syncexec`, which runs it on the RDB and HDB and joins
what they return. That holds for a one-off expression and for every line of the
gateway session, which is uqs's own console for that reason - qcon is a separate
program, and what is typed into it never passes through `uqs`. `--servers` names
other process types. Three things are not wrapped:

- a `.gw.*` call of your own, and a `\` system command, are sent as typed;
- an in-place change is **refused** wherever it appears outside a string:
  routed, it would change the RDB's live data. That is an `update` or `delete`
  from a table named with a backtick, `` `trade upsert ... `` (or `insert`,
  `set`), and the functional `` ![`trade;...] ``; the copy form,
  `update ... from trade`, is routed like any query. To change a table, name its
  process first (`uqs query rdb1 ...`). This guards against a slip, not a
  determined caller: q can build any expression at run time;
- with `--no-gateway`, nothing is: the gateway gets exactly what you typed.

```
uqs query "select count i by sym from trade"   # .gw.syncexec[...;`rdb`hdb]
uqs query "select from trade" --servers hdb    # history only
uqs query "tables[]" --no-gateway              # the gateway's own tables[]
uqs query rdb1 "select from trade"             # rdb1 directly, as typed
```

In the session, `\\` (q's own), `exit` or Ctrl-D leaves; Ctrl-C drops the line
being typed. A line that fails is reported and the session goes on.

It refuses a process that is not running, naming the `uqs start` to fix it.
`qcon` ships with kdb+, not with this repository. qcon takes the password on its
command line, so with a password from `--password-env` the session on a process
is uqs's own console instead, each line sent as typed.

Results print as q's own console prints them: the process formats its answer
with `.Q.s`, laid out to this terminal's size (or in full when the output is
piped), and puts its own `\c` back afterwards. To see what `kola` - the IPC
library `uqs` connects with - makes of a result instead, a Polars DataFrame for
a table, Python objects for the rest, choose the `kola` renderer:

```
uqs query "select from trade" --render kola   # this call
export UQS_QUERY_RENDER=kola                  # every call, and the gateway session
```

`--render` wins over `UQS_QUERY_RENDER`; the default is `q`. `--export` always
writes the data itself, whichever is chosen. From a plain q session instead:
`q)h:hopen \`:localhost:6052:admin:admin`, then `h "..."`, then `hclose h\`.

### A deployed target, from here

`--target NAME` queries a target declared in `deploy_targets.toml` from this
machine. For the call, `uqs` opens an ssh tunnel
(`ssh -N -L 127.0.0.1:<free port>:127.0.0.1:<port>`, with `BatchMode` and
`ExitOnForwardFailure`) and closes it afterwards, also on an error or Ctrl-C.

```
uqs --runtime peachq-etl query --tunnel dev 'tables[]'               # the gateway, routed
uqs --runtime peachq-etl query --tunnel dev rdb1 'tables[]'          # rdb1, as typed
uqs query --tunnel dev --port 6552 'tables[]'                        # a port on the target
uqs query --tunnel dev                                               # a gateway session
```

The port of PROC (or of the gateway) comes from the target's deployed release:
`uqs process list --json`, run there with its `deploy.env` and the `--runtime`
given here. It never comes from this checkout. `--port` is a port on the target,
so neither PROC with `--port` nor `--tunnel` with `--host` is accepted. Routing
works as it does locally. An interactive PROC session runs qcon as a child
process, so the tunnel stays open while the session lasts.

This is not `uqs --target NAME query ...`, which runs `uqs query` on the server
([deploy guide](deploy.md#driving-a-deployed-release-from-your-machine)). Each
form names a different machine to run the query, so giving both `--target`
options is refused.

### A standing tunnel: plan and check

`query --tunnel` opens a tunnel for one call. For a tunnel that stays open while
you work, `uqs target tunnel plan` prints which processes it forwards and the
ssh command that opens it. The command holds the ssh destination and the ports,
never a password. `uqs target tunnel check` then tests each forward.

```
uqs target tunnel plan prod                        # every rdb, hdb, wdb and gateway
uqs target tunnel plan prod rdb1 hdb1 --local rdb1:7002 --json
uqs target tunnel check prod --password-env PROD_Q_PASSWORD
```

The processes, their proctypes and their remote ports come from the target's
release, as `query --tunnel` reads them. A local port is the remote port plus
`--local-offset` (10000) unless `--local PROC:PORT` names one. The plan refuses
a process that is not declared there, one that is not an rdb, hdb, wdb or
gateway, a port out of range, and two forwards sharing one local port. Each
refusal under `--json` carries a `kind`: `discovery`, `unknown_process`,
`unsupported_proctype`, `invalid_port` or `port_conflict`.

The check tests each endpoint in the order a connection is made, and the first
layer that fails gives the endpoint's status:

- `ssh`: the target cannot be reached.
- `unreachable`: nothing listens on the remote port. This is asked on the
  server, so a closed port is never mistaken for a refused login.
- `auth_refused`: the q login was refused.
- `identity_mismatch`: `.proc.procname` named another process.
- `not_ready`: a gateway has no active server.
- `local_bind`: the planned local port is held by something other than this
  process's tunnel.

Every endpoint is checked, so one failure does not hide the others. Each one
that passes is recorded in `tunnel_checks.json`, beside `deploy_targets.toml`
(or wherever `UQS_TUNNEL_CHECKS` points). The plan reads that record to mark
each mapping `verified`, `stale` (checked more than an hour ago, or for other
ports) or `unverified`. The frontend's Services view calls the same two
functions.

## MCP server

`uqs-mcp` exposes the CLI's operations as MCP tools, for an MCP client to drive
the stack. `exec` is not exposed. Point the client's server command at:

```
uv run uqs-mcp
```

(stdio transport). Each tool is one CLI command, named after it, and takes that
command's options spelled as identifiers (#2120):

  | Tool                                                                                                                                                                                                                | CLI command                         |
  | ---                                                                                                                                                                                                                 | ---                                 |
  | `uqs_start`, `uqs_stop`, `uqs_restart`, `uqs_status`, `uqs_clean`                                                                                                                                                   | `uqs start`, ...                    |
  | `uqs_query`                                                                                                                                                                                                         | `uqs query`                         |
  | `uqs_logs`                                                                                                                                                                                                          | `uqs logs --json`                   |
  | `uqs_config_show`, `uqs_config_set`                                                                                                                                                                                 | `uqs config show`, `uqs config set` |
  | `uqs_process_list`, `uqs_profile_list`, `uqs_config_field_list`, `uqs_config_list`, `uqs_config_env`, `uqs_job_list`, `uqs_target_list`, `uqs_runtime_list`, `uqs_job_attachment_list`, `uqs_job_attachment_status` | each noun's `list`                  |
  | `uqs_connector_start`, `uqs_connector_stop`, `uqs_connector_status`                                                                                                                                                 | `uqs connector ...`                 |

So `base_port` is the stack's base port wherever a stack is meant, and `port` is
only ever one process's - `uqs_query`'s, as `--port` is on the CLI. `procs` is a
list of process names, `proc` one; `uqs_query` takes `password_env`, the name of
the variable holding the password, never the password; `uqs_start` takes
`profile`, and `dry_run=true` for the start lines alone. `uqs_start` refuses
what `uqs start` refuses: an unknown process or an unrunnable profile.
`uqs_clean` is a dry run: it returns each entry it would remove, with its bytes,
and deletes nothing. MCP has no terminal to ask on, so `confirm=true` is
`uqs clean`'s `--yes`, the only way it deletes (#2257); a call that still gives
`dry_run` is refused, naming `confirm`.

Every tool returns one JSON object - a list tool its noun's `--json` document,
`uqs_query` `{"expression", "result"}` - and every failure is the CLI's one
refusal, `{"ok": false, "error": "..."}`, marked as an error: a refused start,
an unknown tool, a bad argument and a query that cannot connect alike. A retired
tool or parameter (`uqs_summary`, `uqs_list`, `uqs_print`, `uqs_get_config`,
`uqs_crypto_*`, `port` for a base port, `passwd`, `procname`, `min_level`) is
refused naming what replaced it. `python/uqs/tests/test_mcp_vocabulary.py` holds
every tool to its CLI command's names and types.

## Other commands

Anything `torq.sh` itself supports but isn't wrapped as its own subcommand above -
`debug <processname>` to run one process in the foreground for troubleshooting,
`top <processname>` - is available via `exec -- <args>`, which passes straight
through to `lib/torq/torq.sh`. It was `raw`, which still works for a release.

## CLI conventions

How `uqs` commands are named and what they take. Not every command follows them
yet: [`test_cli_conventions.py`](../../python/uqs/tests/test_cli_conventions.py)
holds rules 1, 3, 4, 5 and 7 over the command tree, with a list of today's
exceptions, each naming the issue that removes it (#1738). It fails on a new
command that breaks a rule, and on a listed exception that has since been fixed.

1. **Noun, then verb.** A group is a noun and a leaf is a verb on it:
   `uqs job backfill demo_deals_backfill`. Only the stack's own lifecycle
   (`start`, `stop`, `restart`, `status`, `logs`, and `clean` for what it wrote)
   is a top-level verb.
2. **The thing acted on is positional; a filter is an option.**
   `uqs job gaps fx_markout --from 2026-09-13 --to 2026-09-14`.
3. **One meaning per option name**, and one long name per short flag: `--port`
   is one process's port (`uqs query --port 6052 'count quote'`), the stack's
   base port is `--base-port`, and `-n` is `--dry-run`. A name takes one type of
   value everywhere (`--release` is a release's name, never a path); a word in
   an option name means one thing (`target` is a deploy target, `runtime` a uqs
   runtime); and one concept has one name (the base port is never `--stack-id`)
   (#1855).
4. **A fixed verb vocabulary**: the table below.
5. **Uniform flags.** `--json`, one JSON document on stdout, on every command
   that reports (`uqs runtime diff uqf torq --json`); `--dry-run`/`-n` on every
   command that changes state (`uqs clean -n`), which checks everything and
   changes nothing, and ends with one line,
   `dry run: nothing was changed. Run it again without --dry-run.`
   (`safety.dry_run_note`). A test runs each such command's dry run against a
   copy of the tree and an empty data directory and fails on any file written
   (`tests/test_dry_run_tree.py`); `--yes`/`-y` on every command that asks
   (`uqs job remove NAME -y`). A check is bypassed only with
   `--override NAME --reason TEXT`: NAME is one of the command's own, which
   `uqs override list` shows, repeatable, with one reason for all of them,
   warned about and recorded. A guardrail is declared once, as a `Guardrail`
   beside the code that enforces it (`uqs/guardrails.py` lists the owners), and
   one declared with `skip_flag=True` also gives each command it names
   `--skip-<id>`, generated with its help and completion from the declaration
   (`cli/skip_options.py`): `uqs job backfill --skip-eod-fence` is
   `--override eod-fence`, read by the same `safety.given`, and `uqs --target`
   sends it as that. A guardrail gets the flag only once its command honours the
   waiver at run time. Through `uqs --target`, a `--dry-run` asks nothing first,
   except a backfill's, which starts a process to fetch. That is sound only
   because of the test above: `start --dry-run` used to bootstrap the data
   directory, unasked, on the server (#1880), and `job backfill --dry-run` did
   too, until #1902. A backfill rehearsal - `--dry-run`, `--mode plan` or
   `--mode validate` - now starts its worker against the data directory as the
   fleet's last start left it, and is refused, naming `uqs start`, when that
   directory is not bootstrapped; it rehearses against the real coverage ledger,
   which a scratch directory would not hold. What the worker itself writes - its
   log and its status file - is the rehearsal's output. Under `--json`,
   `deploy push`, `prune` and `rollback` put their note on stderr, after the one
   document on stdout; their dry runs read the deploy lock and take nothing
   ([deploy.md](deploy.md#rolling-back)).
6. **A rename ships with a hidden alias for one release**, which prints the new
   spelling; the release after removes it.
7. **Help says one thing one way** (#1883). A positional is shown as an
   upper-case word of one vocabulary - `PROC`, `JOB`, `TARGET`, `TABLE`,
   `DATASET`, `SOURCE`, `ATTACHMENT`, `CONNECTOR`, `RUNTIME`, `RUN`,
   `SNAPSHOT_ID`, ... - and a placeholder in a command's help is one of that
   command's own (no `WORKER`, no bare `NAME`). A command's first help paragraph
   is one line, the one its group lists; option help starts with a capital or a
   backtick; and help names no issue numbers. Help is text, not Rich markup, so
   `[process]` in a documented JSON shape stays in it. `-h` is `--help`
   everywhere.

Output into a pipe or a file is neither cut nor wrapped (#1883): a table is as
wide as its widest row and a line is never broken, so
`uqs status | grep discovery1` finds it and `ls "$(uqs config show rdb1 U)"`
gets the whole path. What a q process or TorQ prints is shown as it is, never
read as markup.

Every command exits the same way for the same outcome (#1885), and
`tests/test_exit_codes.py` holds it:

- **0**: done, or nothing to show or do. An empty result - no run recorded, no
  log written yet, no attachment, no HDB - is not a failure.
- **1**: refused. An unknown name - a process, a target, a run, a job - is
  refused with one message each; an unknown process, by every command, as
  `no process NAME in this runtime`, pointing at `uqs list processes`.
- **2**: the command line is wrong as given: a missing argument, an unknown or
  contradictory option, a time range given two ways. Click's own usage errors
  and the command's (`UqsUsageError`) alike.
- **1** also when a question is answered no, or not at all.
- **3**: done in part, with the rest to retry (`EXIT_PARTIAL`, #2226). Only
  `job backfill --attachment --wait` (or `--follow`) exits 3: its rows are
  written and covered, but a reader has not reloaded them, and running the same
  command again asks the readers again. The attached runner's own refusal (its
  exit 2) is a refusal here, 1, never a usage error.
- **4**: stopped on request (`EXIT_STOPPED`, #2679). A bounded run was asked to
  stop - a stop file beside its lock, which
  `job backfill --attachment --follow --stop-on-exit` writes on Ctrl-C - and
  did, between two windows, with the windows it finished kept; the same command
  again does the rest. The run's outcome is `stopped`. `job backfill --wait`
  exits 4 for such a run too.
- **130**: `job backfill --attachment --follow` whose stream Ctrl-C ended. The
  run goes on, and nothing is yet known of how it ends. With `--stop-on-exit`
  the first Ctrl-C asks the run to stop and keeps following, so the command
  exits with the run's own code (4); a second Ctrl-C ends it with 130 and leaves
  the request in place.

A command asks through one helper, `safety.ask`/`safety.confirm`: `[y/N]`, so
the default is no; an EOF or Ctrl-C is a no; and with no terminal to ask on it
refuses at once, naming `--yes`, instead of prompting into the void.

A time range is given one way, on every command that takes one (`job backfill`,
`job gaps`, `job preview`, `job check`, `job runs list`, `job runs audit`,
`data seed`, `data replay`, `data compress`, `data migrate`, `data diff`,
`activity list`, `activity export`, `config sources check`), all four options or
none (#2249), and read by one parser, `uqs.timerange`:

- `--from A --to B` is `[A, B)`: `B` is excluded. A bound is a date or a
  timestamp, ISO-8601 or a q literal - `2026-09-13`, `2026-09-13T06:00`,
  `2026-09-13T06:00+02:00`, `2026.09.13D06:00` - and is UTC without an offset;
- `--date D` is one day, `--from D --to D+1`;
- `--last DURATION` is up to now, `--from now-DURATION --to now`: `300`
  (seconds), `30s`, `1.5m`, `2h`, `7d`, `500ms` - the DURATION every span of
  time takes.

A command that acts on whole HDB partitions - `data replay`, `data seed`,
`data compress`, `data migrate` - takes whole days only, and refuses rather than
rounds anything else (#2245): a `--from` or `--to` that is not a UTC midnight is
refused naming the days either side ("`data seed` copies whole partitions:
--from 2026-09-13T06:00:00 is not a day boundary; give --from 2026-09-13 or
2026-09-14"), and `--last` must be whole days - `--last 3d` is the three days
before today, so today's live partition is never in it. `data replay` replays
one day's log, and `data migrate` rebuilds one partition, so each takes one day:
`--date D`, `--from D --to D+1`, or `--last 1d` (yesterday).

`config sources check` reads a bounded window over the same range, and only it
has a default: with no range at all, `--last 1h`. A source quiet now - a
weekend, out of hours - is checked over a window that had data with `--date D`
or `--from`/`--to` (#2249).

The verbs of rule 4:

  | verb                                          | means                                                                            |
  | ---                                           | ---                                                                              |
  | `status`                                      | the state of something running or installed                                      |
  | `check`                                       | actively probe; exit non-zero on a problem                                       |
  | `list`                                        | many records                                                                     |
  | `show`                                        | one record                                                                       |
  | `set`                                         | change one setting                                                               |
  | `add` `remove`                                | create or delete one thing                                                       |
  | `start` `stop` `restart` `logs`               | the stack's lifecycle, and a connector's                                         |
  | `clean`                                       | delete the runtime state the stack wrote                                         |
  | `exec`                                        | run any other torq.sh verb, passed straight through                              |
  | `query` `graph` `schema`                      | read a running stack                                                             |
  | `new` `install`                               | write a job into the tree, or place one built elsewhere                          |
  | `backfill` `preview` `gaps` `audit` `receive` | operate a job and its runs                                                       |
  | `export` `approve`                            | a receiver contract, on each side                                                |
  | `replay` `seed` `migrate`                     | move data into the HDB                                                           |
  | `upgrade`                                     | bring a store an older uqs wrote up to today's layout                            |
  | `alter`                                       | change one table in every HDB partition holding it                               |
  | `compress`                                    | an HDB's compression: report it, plan it, or run TorQ's .cmp over it             |
  | `diff` `prepare`                              | compare runtimes, or a table on two endpoints; install a runtime's bundles       |
  | `stub`                                        | add a placeholder source row                                                     |
  | `env`                                         | print variables to source                                                        |
  | `build` `push` `rollback` `prune` `history`   | releases and their deployment                                                    |
  | `shell`                                       | a shell on a target                                                              |
  | `unset` `rename` `edit`                       | a target's settings: remove one, rename it, edit the file                        |
  | `log` `revert`                                | a target's change log, and undoing one change in it                              |
  | `adopt`                                       | take over a release copied onto a server by hand                                 |
  | `restore`                                     | a config push's files back as they were, as a new push                           |
  | `plan`                                        | what a tunnel to a target forwards, without opening it                           |

Three of these sit beside a near neighbour on purpose (#1756): `log` is a
record's change history, where `logs` is processes' output and `history` what
was deployed; `revert` undoes one recorded change and is itself recorded, where
`rollback` returns a server to its previous release; `unset` removes a setting
so the layer beneath applies, which no value given to `set` can say.

### Vocabulary

One concept has one name, one type and one unit on every command
([`cli/vocabulary.py`](../../python/uqs/src/uqs/cli/vocabulary.py), #1857);
`test_cli_conventions.py` holds every command to it, and refuses a spelling it
retired.

  | concept                                       | spelt                                | takes                                     |
  | ---                                           | ---                                  | ---                                       |
  | a process acted on                            | `PROC` / `PROCS` (`all`)             | a name                                    |
  | a process as a property or a filter           | `--proc NAME`                        | a name; repeatable where several fit      |
  | a job acted on                                | `JOB`                                | a name                                    |
  | a span of time                                | `--*-timeout`, `--every`, `--soak`   | a DURATION: `30` (seconds), `500ms`, `5m` |
  | a time range up to now                        | `--last DURATION`                    | a DURATION                                |
  | the readiness wait of a deploy                | `--verify-timeout`                   | a DURATION                                |
  | a span of time written into q                 | `job new --period/--horizon/--width` | a DURATION, or a q timespan as written    |
  | a source with no credential on its fixture    | `--fixture/--live`                   | a flag pair; the default is the command's |
  | what a write does with a row already there    | `--on-conflict POLICY`               | upsert, replace, ignore, append, fail     |
  | a private ODBC setup                          | `--odbc-home DIR`                    | a path                                    |
  | q's home and executable                       | `--q-home DIR`, `--q-cmd PATH`       | as `--torq-home`                          |
  | the kdb+ version                              | `--q-version`                        | `4.0`, `>=4.1`                            |
  | how many records to show                      | `--limit N`                          | a count                                   |
  | a FILE written instead of stdout              | `--output FILE` / `-o`               | a path                                    |
  | a directory written into                      | `--output-dir DIR`                   | a path                                    |
  | a sidecar bundle                              | `--bundle DIR`, `BUNDLE`             | a path                                    |

A flag that turns a default off is one side of a pair -
`--gateway/--no-gateway`, `--start/--no-start`, `--register/--no-register` - and
`--every` re-reads every 2 seconds by default on `status` and `schema` alike. A
connector's own start options (`--interval-ms`) are the connector's, and name
their unit.

### Renamed spellings

Each old spelling below still works for one release, hidden from `--help`, and
logs its new spelling when used (rule 6). The release after removes it.

`query` and `schema` take `[PROC] [X]`: a lone word that names a process of this
stack is PROC, any other is the expression or table, so
`uqs query 'count quote'` still asks the gateway and `uqs schema 'crypto*'`
still reads rdb1. A job or worker is JOB in every command (`job backfill JOB`,
`job attachment contract export JOB`), a process PROC (`config show PROC`).

`start`, `restart` and `stop` take one option set: the processes positionally,
`--profile` and `--base-port` on all three; `start` and `restart` add the same
`--follow`/`-f`, `--level`, `--stop-on-exit`, `--trace`, `--json`, `--dry-run`
and overrides, so `restart -f` is `start -f` for a fleet already up - the launch
options `job backfill` and `job attachment add` take too (#2687). `stop` adds
`--kill` instead. A connector's whole lifecycle is its own group,
`uqs connector start|stop|restart|status|logs NAME`, not the stack's verbs with
a `connector:` prefix: a connector is no torq.sh process, so it takes none of
`start`'s options and has start options of its own, and rule 1 puts the noun
first.

Every command that changes state now takes `--dry-run`/`-n`, among them `stop`,
`restart`, `config set`, `job receiver start`, `job backfill` (`--dry-run`),
`job runs upgrade`, `job attachment contract approve`,
`connector start`/`stop`/`restart`, `setup odbc install`/`rollback`,
`deploy component build` and `target add`/`remove`.

One write model per kind of change (#1854). A command that changes this machine
writes by default and takes `--dry-run`/`-n`. A command that rewrites data in
place - `data check`, `data alter`, `data compress`, `data migrate`,
`data replay`, `data seed`, `target adopt` - is a dry run by default and writes
only with `--apply`, which shows the plan and asks; `--yes`/`-y` answers that
question, and nothing else. Their `--dry-run` says nothing more than the
default: it still parses for one release, hidden, and is refused beside
`--apply`. `data migrate`'s default, which was to flip to writing, stays a dry
run. Every deletion that cannot be undone asks first and takes `--yes`/`-y`:
`clean` (the whole data directory, the HDB with it, unless `--match` narrows
it), `deploy prune`, `job checkpoint remove`, `target remove` (which
`target revert` can undo), beside `job remove` and `job attachment remove`. On
`uqs-mcp`, which cannot ask, the tool for each is a dry run unless it is given
`confirm=true`.

Every bypass is a named override with a reason (#1852): the bare flags that
lifted a refusal and recorded no reason are `--override NAME --reason TEXT`,
each in `uqs override list`, warned about and recorded where the command records
anything (`deploy push` in its plan's EXCEPTIONS and the release's report):

  | old                                                  | new                                          |
  | ---                                                  | ---                                          |
  | `deploy build` `deploy push --allow-dirty`           | `--override allow-dirty --reason R`          |
  | `deploy build --allow-computed-names`                | `--override allow-computed-names --reason R` |
  | `deploy push --fix-hdb`                              | `--override fix-hdb --reason R`              |
  | `deploy push` `deploy rollback --break-lock`         | `--override break-lock --reason R`           |
  | `job backfill --any-writer`                          | `--override any-writer --reason R`           |
  | `job remove --even-if-edited`                        | `--override even-if-edited --reason R`       |

An old flag given without `--reason` still works for one release: its record
says it was given as the old flag, which records no reason.
`job attachment add --override CODE:COMPONENT` is checked as every other
command's is.

An attachment is a noun now, with `add`, `remove`, `show` and `list`, and the
receiver contracts approved for one under it. `job runs upgrade` brings the run
ledger up to today's columns; it is not `data migrate`, which changes one HDB
partition's schema. Each group's help names every command in it, held by
`test_cli_conventions.py`.

`status` is the state of something running, `check` a probe that exits non-zero
on a problem, `list` many records and `show` one. A push or rollback asks a
release for `deploy check`, or for `deploy verify` on a release from before it.
The MCP tool `uqs_summary` is `uqs_status`, and is refused naming it.

One vocabulary (#1857):

  | old                                                           | new                                   |
  | ---                                                           | ---                                   |
  | `job new --procname P`                                        | `--proc P`                            |
  | `deploy check --procs A,B`                                    | `--proc A --proc B`                   |
  | `deploy check --deadline S`                                   | `--verify-timeout S`                  |
  | `config sources check --window MINUTES`                       | `--last DURATION` (`--last 60m`)      |
  | `data seed --overwrite`                                       | `--on-conflict replace`               |
  | `job attachment contract approve --conflict P`                | `--on-conflict P`                     |
  | `job attachment contract approve --max-rows N`                | `--max-message-rows N`                |
  | `job preview --sample N`                                      | `--limit N`                           |
  | `deploy build --output DIR`, `deploy component build`'s too   | `--output-dir DIR`                    |
  | `deploy push --qcmd`, `--qhome`; `setup odbc install --qhome` | `--q-cmd`, `--q-home`                 |
  | `setup odbc ... --home DIR`                                   | `--odbc-home DIR`                     |

A duration that was a bare number still is one - seconds - so only `--window`'s
minutes changed meaning; its old spelling was converted for one release, and is
refused now, naming the time range that replaced it (#2249). `deploy push` asks
the release it verifies which spelling its `deploy check` takes, so a push or a
rollback to a release from before this still verifies.

One grammar per option across `uqs data` (#1856):

  | old                                   | new                                          |
  | ---                                   | ---                                          |
  | `data alter --rename OLD=NEW`         | `--rename OLD:NEW`, as `data migrate` has it |
  | `data seed --tables A,B`              | `--table A --table B` (or `--table A,B`)     |
  | `deploy check --tables A,B`           | `--table A --table B`                        |

A pair an option takes is `A:B` on every command - `--rename OLD:NEW`,
`--cast COL:TYPE`, `--attr COL:p` - and `--rename`, `--cast` and `--attr` are
repeatable on `data alter` as on `data migrate`; an alter is still one operation
per run, and refuses a second. `data migrate --cast COL` takes no type because
its target is always the type this release declares. Tables are chosen one way:
a repeatable `--table`, with `NAME,NAME` in one also read, on `data replay`,
`data check`, `data seed` and `deploy check`; `data alter` and `data migrate`
take the one table they act on as TABLE. `data check --apply` is the one command
that fills missing HDB tables and columns: `deploy push --override fix-hdb` runs
the release's own, and says so.

An even command surface (#1858):

  | old                                              | new                                           |
  | ---                                              | ---                                           |
  | `uqs list processes`                             | `uqs process list`                            |
  | `uqs list profiles`                              | `uqs profile list`                            |
  | `uqs list fields`                                | `uqs config field list`                       |
  | `uqs list env`                                   | `uqs config env`                              |
  | `uqs list dependencies`                          | `uqs graph --upstream --offline`              |
  | `uqs list backfills`                             | `uqs job attachment status`                   |
  | `uqs job receive ...`                            | `uqs job receiver start ...`                  |
  | `uqs job receive --attachment NAME --stop`       | `uqs job receiver stop --attachment NAME`     |
  | `uqs config set PROC FIELD VALUE`                | `uqs config set PROC FIELD=VALUE ...`         |
  | `uqs job backfill --mode dry-run`                | `uqs job backfill --dry-run`                  |

`config set` takes settings as `target set` does, several at once, and
`config unset PROC FIELD...` removes an override so process.csv's own value
applies. `target edit NAME` opens the file at that target's table. `schema`
takes `query`'s `--tunnel`; `data replay` and `job runs list` take the whole
time range (`job runs list` keeps the runs whose window overlaps it);
`job backfill --json` reports a local run as well as an attached plan; and
`connector start --help` groups each connector's options under its name. Three
pairs of verbs stay apart on purpose: `clean` deletes what the stack wrote where
`remove` deletes one declared thing, `deploy prune` removes old releases by a
rule, and `job runs upgrade` changes the ledger's layout where `data migrate`
changes an HDB partition's schema. Each old spelling above goes after its one
release (`cli/rename_expiry.py`).

"Partition" is an HDB date partition (#2239), as kdb+ has it; the old spelling
is refused naming the new one, with no alias:

  | old                                      | new                                      |
  | ---                                      | ---                                      |
  | `data diff --partition KEY`              | `--slice KEY`, the coverage slice        |
  | `job new --partition SYM`                | `--slice SYM`                            |
  | `job new --partition-column COL`         | `--slice-column COL`                     |
  | `data migrate TABLE PARTITION`           | `data migrate TABLE --date D`            |

`data check --template DATE` keeps its name: it is a date partition.

Every `data` command that reads or writes one HDB names it the same ways
(#2237): `--hdb DIR` (a directory on this machine), `--attachment NAME` (an
attached stack's pinned HDB, else its live wdb's), `uqs --target TARGET` (a
release's own), and otherwise this runtime's - one help each, from
`cli/hdb_scope.py`. `data migrate --hdb` and `data seed --hdb` (the destination)
are new. A command that cannot act on an attachment's HDB knows the option and
refuses it saying why: `data replay`, `data migrate` and `data seed` cannot
check or pause an attached stack's writers, as `data check` and `data alter` do,
and `data compress` plans and runs through this stack's housekeeping1 (its
`--report` reads an attachment's HDB when it is on this machine). `data diff`
compares two endpoints, and `data snapshot` reads the configuration snapshots,
so neither takes them.

One meaning per word (#1855):

  | old                                                                     | new                                    |
  | ---                                                                     | ---                                    |
  | `--attach NAME` on `logs`, `job backfill`, `job receive`, `job runs`,   | `--attachment NAME`                    |
  | `job attachment contract approve`/`list`, `data check`/`alter`          |                                        |
  | `job attachment add --stack-id PORT`                                    | `--base-port PORT`                     |
  | `job attachment add --runtime-only`                                     | `--transient`                          |
  | `deploy build --q-target 4.0`                                           | `--q-version 4.0`                      |
  | `deploy build --python-runtime FILE`                                    | `--python-component FILE`              |
  | `deploy push --{q,torq,python}-runtime[-sha256]`                        | `--{q,torq,python}-component[-sha256]` |
  | `target adopt --release DIR`                                            | `--release-dir DIR`                    |
  | `data replay --schema FILE`                                             | `--schema-file FILE`                   |
  | `uqs list overrides`                                                    | `uqs config list`                      |

An attachment is a noun, so the option naming one is too, whatever the command
does with it - reads its ledger, writes its HDB, reads its logs. `target` is a
deploy target (`uqs --target`, `deploy push TARGET`, `query --tunnel TARGET`),
so the kdb+ version a build converts for is `--q-version`, as `deploy push`
already called it. `runtime` is a uqs runtime (`uqs --runtime`,
`uqs runtime list`), so what `uqs deploy component build` makes is a component
on every command that takes one; deploy_targets.toml keeps its `*_runtime` keys.
An override is a bypassed check (`uqs override list`), so `config set`'s
settings are `uqs config list`. `data replay --base-port` defaults to the
runtime's own, like every command's, and refuses a plant running under another.

One name for the ssh destination (#1888):

  | old                                                                | new                                         |
  | ---                                                                | ---                                         |
  | deploy_targets.toml `host = "..."`, `target set NAME host=`        | `ssh`, as `--ssh` names it                  |
  | deploy_targets.toml `qcmd`, `qhome`                                | `q_cmd`, `q_home`, as `--q-cmd`/`--q-home`  |
  | deploy_targets.toml `{q,torq,python}_runtime[_sha256]`             | `{q,torq,python}_component[_sha256]`        |
  | JSON `"host"` in `target check`, `deploy history`, `deploy status` | `"ssh"`                                     |
  | `target list` JSON `"as"`, `"last deployed"`                       | `"remote_user"`, `"last_deployed"`          |
  | `target log` JSON `"host"` (the machine a change was made on)      | `"machine"`                                 |

deploy_targets.toml spells every setting as push's option names it, so a key
spelt the old way is refused, naming the key to write - `uqs target edit` opens
the file. deploy_history.json entries written before this keep `host`, read as
`ssh`; new ones are written with `ssh`. Every key a `--json` document holds is
snake_case.

One vocabulary for job kinds (#1889): `job list --json`'s `kind` was `etl` or
`feed` for a job `job new --kind streaming` wrote; it is `streaming` now - or
`normalizer`, `horizon`, `backfill`, `reaction` - and a new `subscribes`
(`yes`/`no`) says whether a streaming job reads anything, which is what etl/feed
meant. A script filtering on the old values needs the new ones.

One name per thing across surfaces (#1890):

  | old                                          | new                                              |
  | ---                                          | ---                                              |
  | `data replay --dir DIR`                      | `--tplogdir DIR`, as `job attachment add` has it |
  | `logs --attachment NAME --run STAMP`         | `--stamp STAMP` - a log's stamp, not a run_id    |
  | `deploy build --kafka-home DIR`              | `--kfk-home DIR` (KX's kfk, `UQF_KFK_HOME`)      |
  | `deploy build --with-kafka-connector`        | `--kafka-connector/--no-kafka-connector`         |
  | `connector start/restart kafka --topic T,U`  | `--topics T,U`, plural as every list is          |
  | `job new --kind external`                    | `--kind connector`                               |
  | `job new --start-with-all` (a lone flag)     | `--start-with-all/--no-start-with-all`           |
  | `UQS_REQUIRE_LIVE_SOURCES=1`                 | `UQF_LIVE_SOURCES_REQUIRED=1` (refused)          |
  | `UQF_FRONTEND_{GATEWAY,DATA}_PASSWD`         | `..._PASSWORD` (refused)                         |
  | `UQS_DEPLOYMENT_FILE` (set by uqs itself)    | `UQF_DEPLOYMENT_FILE`                            |

A connector is the Python process outside the stack that `uqs connector` runs; a
feed is a q streaming job that only publishes, and the connector commands' help
no longer calls one the other. A renamed variable is refused, naming the new
one, as a removed one is. `UQS_` is the uqs Python package's own prefix;
whatever q or another package reads is `UQF_`.

`--port` is now only ever one process's port (`query`, `schema`);
`query --tunnel` keeps querying a target through an ssh tunnel from here, so
`--export` writes here, where `uqs --target NAME query` runs the whole command
there.

## Machine-readable output

Every command that reports something takes `--json` and prints one JSON document
on stdout, holding the same data as the table it would print (#1731). Its help
reads the same on every command - "Print one JSON document on stdout instead:",
then the document's shape (#1859). Its lists are records with snake_case keys:
the `Depends on` column is `depends_on`. Logs go to stderr, and a refusal is the
document `{"ok": false, "error": "..."}`, so stdout always parses: exit code 1
for a refusal, 2 for a usage error - Click's own, a missing argument or an
unknown option, and the command's (#1882, #1885).

`--json` never asks a question. A command that would ask before changing
something - `data check`/`alter`/`compress`/`migrate`/`replay`/`seed --apply`,
`deploy prune`, `job attachment add`/`remove` - is refused under `--json`
without `--yes`, even at a terminal: exit 1,
`{"ok": false, "error": "--json cannot ask for confirmation: add --yes to ..."}`,
nothing changed (#2229).

One contract holds for every document (#1882):

- it is an object, never a bare list: a list command prints `{<kind>: [...]}` -
  `uqs process list --json` is `{processes: [...]}`, `uqs target list --json`
  `{targets: [...]}`, `uqs config env --json` `{variables: [...]}`;
  `config sources check --json` is `{ok, sources: [...]}`;
- its values are typed, read from the row rather than from the text its table
  cell shows: a port is a number, a flag `true`/`false`, a set of names a list.
  Where the table shows words for a missing value - `(chosen at start)`,
  `no cap`, `(nothing declares it)` - the document has `null` or `[]`;
- one concept has one key, at every depth of the document, not only on a list's
  records (#2122): a process is `process` wherever the table calls it
  `procname`, the start-all flag `start_with_all` (process.csv's
  `startwithall`), a job `job` (the run ledger's `worker`), a stack's base port
  `base_port` (an attached destination's `stack_id`), and a time range
  `range_from`/`range_to`, never a `range` object;
- a document that reports a verdict says it as `ok`, `false` exactly when the
  command exits non-zero (`deploy check` keeps `passed` beside it, which a
  deploying uqs of another version reads, and so does `deploy status`'s
  `health`; `job check` keeps `worst`). `ok` is a bool wherever it appears: a
  row with a richer result says it under `outcome` beside it - `data alter`'s
  readers are `{process, ok, outcome, detail}`, `outcome` one of `reloaded`,
  `failed`, `down`, `skipped`, `not asked`, and only `reloaded` is `ok`. `uqs`
  refuses to print a document that breaks this, rather than print one a script
  would misread;
- what a document carries as data - a `query`'s result, `job preview`'s sampled
  rows, `data diff`'s rows - keeps the data's own column names;
- a time is ISO-8601 with an explicit offset: q's local `.z.P` (a log line,
  TorQ's clock) carries this machine's, a ledger's `.z.p` `+00:00`, and a
  fraction of a second keeps all its digits;
- a log line is one record, `{time, level, message}`, plus what the command adds
  (#2241): `logs` adds `host`, `process`, `proctype` and `id`, and
  `connector logs` parses its connector's own lines into the same three, with
  `time` and `level` null where a line names none. Its level is `level`, the
  name of the `--level` that filters it, never TorQ's `loglevel`; and
  `activity list`'s events say when and how severe under the same `time` and
  `level`, as the frontend API's record and the history file do (#2254).

```bash
uqs job runs list --json | jq -r '.runs[] | select(.status == "failed") | .run_id'
uqs status --json | jq -r '.processes[] | select(.status == "down") | .process'
```

Without `--json` the split is the same: every log line, refusals and warnings
included, goes to stderr, and stdout holds only what the command prints as its
output (#1881). So `uqs list processes > f` writes the list alone,
`$(uqs config show ...)` captures the config, and `2>/dev/null` silences the
log. `uqs logs` and `start -f` are the exception by design: the processes' log
lines are their output, on stdout.

An attached backfill that cannot be coordinated with the stack's native writers
adds `findings` beside `error`: one record per blocker with its `code` (such as
`eod-fence` or `clock-unknown`), the `component` it is about, the observed
`facts`, and whether its policy would ever let it be accepted (`overridable`)
(#1827).

Each command's `--help` names its document. The commands #1731 added:

  | command                                     | document                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
  | ---                                         | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `status`                                    | `{interpreter: {binary, actual, problem, runnable, connection_limit, summary}, processes: [{time, process, status, pid, port, heartbeat, responds, batches, depends_on, inputs, outputs, port_source}], silent, failing, starved: [{process, reasons}], heartbeats_collected}`                                                                                                                                                                                                                                                                                                                                       |
  | `graph`                                     | `{direction, trees: [{process, status, tables, repeat, children}]}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `query [PROC] EXPR`                         | `{expression, result}`: a table is a list of records                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | `schema [PROC] [TABLE]`                     | `{process, port, tables: [{table, rows, columns}]}`, or with a TABLE `{process, port, columns: [{table, column, type, q, attribute}]}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
  | `logs`                                      | `{lines: [{time, host, process, proctype, level, id, message}]}`; not with `--follow`, `--channel` or `--multitail`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `config show PROC [FIELD]`                  | `{process, fields: [{field, value}]}`, or `{process, field, value}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `job gaps`                                  | `{job, gaps: [{range_from, range_to}], sessions, refillable, refills: [{job, range_from, range_to, command}], today_not_refillable}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | `job preview`                               | the preview's own result: `{job, mode, state, live, cursor, next_cursor, advances, fetched, rows, sample, failures, trace}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | `job check`                                 | `{dataset, range_from, range_to, producers: [{producer, mode, source}], readonly, checks: [{category, status, dataset, scope, range_from, range_to, basis, reason, findings}], behaviours: [...], worst, ok}`                                                                                                                                                                                                                                                                                                                                                                                                        |
  | `job runs list [--unfinished]`              | `{runs: [{run_id, job, dataset, status, range_from, range_to, ...}]}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | `job runs show RUN`                         | `{run, facts, logs, rerun}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | `job runs audit`                            | `{facts: [{run_id, dataset, range_from, range_to, label, text}]}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
  | `job backfill --wait`                       | `{..., state, ok, exit_code, error}` locally, `{mode, job, plan, ..., result, state, ok, exit_code}` with `--attachment`: `ok` false exactly when it exits non-zero; `exit_code` 0, 1, or 3 (`--attachment`: written, not yet queryable)                                                                                                                                                                                                                                                                                                                                                                             |
  | `data snapshot list`                        | `{snapshots: [{id, kind, datasets, digest, created_at, actor, reason, digest_ok}], ok}`; exits 1 when a digest does not match its content                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `data snapshot show SNAPSHOT_ID`            | `{snapshot: {id, kind, datasets, digest, created_at, actor, reason, digest_ok}, parameters: {name: value as q text}}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | `connector status`                          | `{connectors: [{name, running, pid, log, ...}]}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
  | `connector logs NAME`                       | `{name, log, lines: [{time, level, message}]}`: a line's time and level are null where it names none, and its message the whole line; not with `--follow`                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `setup odbc status`                         | `{home, current, previous, versions}`; without `--json` it is now a table                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `deploy check`                              | its report without the `DEPLOY_VERIFY_OK`/`FAILED` marker line                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
  | `deploy push`                               | `{ok, deployments: [{target, release, revision, artifact, ssh, dest, profile, status, stage, error, rollback, ...}]}`: a report per target, `status` `planned` on a dry run                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | `deploy rollback`                           | `{ok, rollback, from, to, stop, start, current}`: `rollback` is `planned`, `done` or `failed`, with `reason` and `restore` when it failed                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `deploy prune`                              | `{ok, prune, kept: [{release, why}], removed, freed_bytes, runtimes}`: `prune` is `planned`, `done` or `declined`; ok false when declined                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `activity list`                             | `{db, events: [{event_id, time, recorded_at, level, source, kind, action, target, subject, outcome, session_id, request_id, claimed_user, trigger, method, status_code, duration_ms, items, detail, dropped_detail}], drops: [{level, reason, count, last_at}]}`, events newest first                                                                                                                                                                                                                                                                                                                                |
  | `deploy history`                            | `{deployments: [{default, at, target, ssh, dest, release, profile, status, stage}]}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | `target check`                              | `{ok, target, ssh, dest, remote_user, release}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
  | `data check`                                | `{ok, hdb, present, segmented, partitions, missing_tables, missing_columns, types_checked, type_changes}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | `config sources check SOURCE...`            | `{ok, sources: [{source, status, stage, rows, elapsed_ms, diagnostic}]}` - one document, not a line per source                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
  | `data check --table`, `data alter`          | `{ok, hdb, resolved_from, ssh, warnings, plan, result}`: `plan` and `result` are `scripts/gates/hdb_alter.q`'s own documents - partitions, shapes, steps, journal, final check                                                                                                                                                                                                                                                                                                                                                                                                                                       |
  | `data compress --report`                    | `{hdb, target, present, columns: [{table, column, files, compressed, algos, blocksizes, bytes, raw_bytes, ratio, compressed_ratio}], largest_uncompressed: [{table, partition, files, bytes}], sort_checked, sort_csv, unsorted: [{table, partition, rows, sort_by, sorted, missing_attr, declared_attr}]}`: `unsorted` is null when `sort_checked` is false                                                                                                                                                                                                                                                         |
  | `data compress`                             | `{ok, hdb, process, scope: {tables, older_than_days, days, change}, config, policies: [{table, policy, algo, blocksize, level, minage, columns}], held: [{dataset, date}], partitions: [{table, partition, age, status, policy_minage, early, dir, compress, decompress, skip, compressed, files, bytes_now, bytes_after, bytes_written}], sample, ratios, disk: [{filesystem, needed_bytes, free_bytes, ok}], totals, result}`: `result` is null without `--apply`, else `{partitions: [{partition, tables, files, bytes_before, bytes_after}], failed, reload, audit}`; ok false when a partition failed           |

## Installing

`./install.sh` checks the prerequisites, then runs
`uv tool install --force --editable python/uqs`, first removing any install
registered under a former package name, and checks that `uqs` runs. It is
idempotent; `./install.sh --help` lists `--dev`, `--web` and `--check`.

Editable means every source edit is live at once. The console script and the
package path are written at install time, though, so after a package rename or
move `uqs` keeps its old name, or fails to import: re-run the script.

**Tab completion**, once per shell, from an interactive terminal:

```
uqs --install-completion      # bash, zsh, fish or PowerShell - detected
```

TAB then completes process names, `--profile` names, `list` kinds and `--sort`
columns, `config show`/`config set` fields and the plant's tables, all read from
the same registry the commands resolve against. `uqs --show-completion` prints
the script instead. From a script or CI there is no tty to detect the shell
from, and the install fails with `Shell None is not supported.`
