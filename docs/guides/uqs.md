# Running the uqf stack

`uqs` starts, inspects and stops the TorQ fleet this tree runs: the vendored
`lib/torq/` and `lib/torq-finance-starter-pack/` plus uqf's own jobs. It writes
nothing into either `lib/` directory. [The stack page](../architecture/stack.md)
draws the topology.

  | To                                                                     | Read                                                      |
  | ---                                                                    | ---                                                       |
  | Choose which processes start, read `status`, change a process's config | [Runtimes, profiles and status](runtimes-and-profiles.md) |
  | Read logs and find where a process stopped                             | [Logs](logs.md)                                           |
  | Query a process, here or on a server                                   | [Connecting](connecting.md)                               |
  | Inspect, migrate, compress or compare HDB tables                       | [Inspecting and maintaining the HDB](hdb.md)              |
  | Script `uqs`: verbs, `--json`, exit codes                              | [The uqs command line](../reference/cli.md)               |

## Quick start

```bash
./install.sh               # once: checks prerequisites, puts `uqs` on your PATH
uqs start all              # every startwithall=1 process
uqs status                 # status table
uqs stop all               # stop every startwithall=1 process
```

You need KDB-X (`q` on `PATH`) and `envsubst` (on macOS:
`brew install gettext`).

Without the install, in CI or a fresh checkout, prefix each command with
`uv run --project python/uqs`. Either form runs from any directory.

The first `start` creates the data directory `output/uqs/` (gitignored) and
copies the sample `hdb/` and `dqe/` data into it. Logs, tickerplant logs and
every process's writes stay inside it. [`uqs clean`](#cleaning-up) wipes it.

## Commands

`uqs --help` lists every command, and `uqs <command> --help` gives its arguments
and options.

`PROCS` is `all` (the default) or one or more process names, each its own word:

```bash
uqs start posbook1 crypto_markout1
```

Four groups hold what is not a lifecycle command:

- `uqs job` writes a job into the tree (`new`, `remove`, `install`), operates it
  (`backfill`, `preview`, `gaps`, `runs`) and attaches it to a TorQ stack that
  is already running ([attaching to an existing TorQ](attach-existing-torq.md)).
- `uqs setup odbc` installs, loads and rolls back a private ODBC setup
  ([ODBC](odbc.md)).
- `uqs connector` runs the external publishers (databento, kafka, the cryptorust
  recorders), which are processes outside TorQ.
- `uqs cron` runs uqs commands on a schedule from your crontab ([scheduling
  commands with cron](#scheduling-commands-with-cron)).

```bash
uqs connector start kafka --topics uqf.client.flow
uqs connector restart kafka --group uqf-replay     # start's options, given again
uqs connector logs kafka -f
uqs connector stop kafka
eval "$(uqs setup odbc env)"                       # this shell, on the private ODBC setup
```

`uqs connector restart` does not remember the options `start` was given.

Putting a release on a server is `uqs deploy`: see [Deploying to a
server](deploy.md).

### Configuration snapshots

Markout rows name the configuration they were computed with in their `config_id`
column. `uqs data snapshot` reads those snapshots:

```bash
uqs data snapshot list --dataset fx_markout
uqs data snapshot show markout_5cefbe2bdcc61b7f --json | jq '.parameters.rate_timing'
```

`list` exits 1 when a snapshot's digest does not match its content.

### Stopping a process that will not stop

`uqs stop` asks each process to exit, then waits up to 15 seconds. A process
still running after that is named, and the command exits 1. `uqs restart` starts
nothing until the stop is confirmed.

```bash
uqs stop rdb1 --kill
```

`--kill` sends SIGKILL to whatever is still running after the wait. Nothing
shuts down cleanly, and a tickerplant killed mid-write may leave its log's last
message torn. `uqs connector stop`, `uqs job receiver stop` and
`uqs job attachment remove` take the same `--kill`.

### Cleaning up

`uqs clean` wipes `output/uqs/`, the HDB included. None of it is reversible, so
run `--dry-run` first. It lists what would go, with sizes, and asks before
removing.

```bash
uqs clean --dry-run                           # what a full wipe would remove
uqs clean --match '^logs$' --dry-run          # the logs alone, still removing nothing
uqs clean --match '^(logs|tplogs)$'           # and now remove them, after asking
uqs clean --match 'out_rdb1'                  # one process's files, wherever they sit
uqs job checkpoint remove demo_deals_backfill # one backfill worker's resume point
```

`--match REGEX` searches each path under `output/uqs/`. Anchor with `^` and `$`
to match exactly. A directory the pattern matches goes whole. The tickerplant
logs are the exception: a pattern reaches `tplogs/` only by naming it from the
start of the path (`^tplogs`, `tplogs/tp1`), so `--match logs` takes `logs/` and
leaves them. `--yes` skips the question, and a script without a terminal needs
it.

`uqs clean` refuses while any of the stack's processes is up, or while a start,
stop or restart is under way: the running stack is writing these files, and
today's tickerplant log may be the only copy of data not yet in the HDB. Run
`uqs stop` first. `--dry-run` still lists.

## Listing things

Each noun lists its own records:

  | Command                        | Lists                                                                             |
  | ---                            | ---                                                                               |
  | `uqs process list`             | Every declared process: name, type, port, the tables it reads and writes          |
  | `uqs profile list`             | The named start sets `--profile` takes                                            |
  | `uqs job list`                 | Every job, its kind, what it reads and writes, and whether it is still a scaffold |
  | `uqs job attachment list`      | Jobs attached to a running TorQ stack, and whether each publisher is up           |
  | `uqs job attachment status`    | Attached destinations a backfill has run into, and what to do next                |
  | `uqs runtime list`             | Each runtime, whether it is here, and whether it is up                            |
  | `uqs target list`              | This machine's deploy targets                                                     |
  | `uqs config list`              | Every `uqs config set` setting in effect                                          |
  | `uqs config field list`        | The `process.csv` columns `uqs config set` can change                             |
  | `uqs config env`               | The resolved `KDBBASEPORT`, `KDBHDB` and the rest                                 |
  | `uqs config housekeeping show` | When `housekeeping1` runs, and each rule                                          |

`uqs process list` shows what is declared. `uqs status` asks the fleet what is
up.

Every listing takes the same flags:

```bash
uqs process list --sort port --reverse
uqs process list --export processes.parquet    # or .csv
uqs job list --sort state                      # unfinished scaffolds together
uqs process list --json
```

`-i` opens the table in a browser that filters as you type. A space between
terms means every term must match. Enter prints the highlighted row and Escape
prints nothing.

```bash
uqs process list -i      # type "hdb" to keep the hdb rows
uqs status -i            # "down" for what is down
uqs job runs list -i     # Enter prints the run you picked, for `uqs job runs show`
```

`uqs status -i` also acts on the highlighted process: `s` starts it, `x` stops
it, `r` restarts it and `R` re-reads the table. There `/` opens the filter. The
table re-reads every 2 seconds, and `--every N` changes that (`--every 0` turns
it off).

`uqs graph` draws every process and who feeds whom, with each edge labelled by
the tables it carries:

```bash
uqs graph                      # from the feeds down: "if this stops, what starves"
uqs graph posbook1 --upstream  # what must be up for posbook1 to work
uqs graph --upstream --offline # the same for every process, without asking the fleet
uqs graph -i                   # browse it: type to filter, ctrl+t to turn it over
```

## Adding a process

Declare a job in q and let `uqs job new` scaffold it: see [adding a
pipeline](new-pipeline.md). What each running process does is in
[Services](../services/README.md).

### Installing jobs from elsewhere

Jobs written outside the tree, in a `sidecars/` folder or another repository,
are installed with:

```bash
uqs job install ../sidecars                        # the wizard
uqs job install ../sidecars --dry-run              # just the plan
uqs job install ../sidecars --link --yes           # no questions, for scripts
```

The plan table shows every `.q` file and what happens to it. Each file goes to
`src/etl/sources`, `src/etl/workers` or `src/etl/streaming` according to what it
declares. A helper that declares nothing, a file that declares more than one
kind, a name the tree already declares and any `test_*.q` are skipped, with the
reason.

`--copy` takes a snapshot the tree owns: edit the sidecar and install again.
`--link` keeps the sidecar the source of truth, so moving or deleting it breaks
the tree. Use it on a development checkout only. To ship jobs to a server, see
[sidecar bundles](sidecar-bundles.md). A file already in place that differs is
replaced only with `--overwrite`, or when you agree.

A source that queries its handle directly (`h"select ..."`) is refused, because
`--trace` could not show the query. Send each query through
`.qetl.source.ipc_call`, `.qetl.source.ipc`, `.qetl.source.local` or
`.qetl.io.odbc.run_sql`, or end the line with `/ untraced: <why>`.

Installing regenerates the derived files. Then check the job runs:

```bash
uqs process list                      # the registry sees the new processes
uqs start --dry-run <procname>        # the exact start line
uqs start <procname>                  # streaming jobs
uqs job backfill <worker> --source-version v1 --from 2026-09-01 --to 2026-09-02 --mode plan
uqs status --columns status           # up, and Responds: yes
uqs logs <procname> -f
```

Commit what `git status` shows afterwards, derived files included.

## Scheduling commands with cron

`uqs cron` runs uqs commands on a schedule from your own crontab. An entry is a
five-field cron expression, a time zone (UTC when you give none) and the uqs
command, exactly as you would type it after `uqs`:

```bash
uqs cron add deals-daily --cron '10 6 * * 1-5' -- job backfill demo_deals_backfill --last 3d --source-version v1
uqs cron add ldn-feed --cron '0 8 * * *' --tz Europe/London -- job run demo_deals_feed --once
uqs cron list                 # each entry, its next runs in its zone and in UTC
uqs cron install --dry-run    # the crontab diff
uqs cron install
uqs cron status               # last outcome per entry; overdue, failing, stray lines
uqs cron remove ldn-feed      # then install again
```

Only finite units can be scheduled: `job backfill` (coverage skips windows
already done, so a `--last` range also picks up a missed run) and
`job run --once`. `add` refuses a streaming job (TorQ supervises it), a reaction
(it runs when its window is published), an unknown job or attachment, and
options that make no sense under cron (`--dry-run`, `--mode`, `--follow`,
`--json`). It parses the command with uqs itself and runs a backfill once with
`--mode plan`, so the stack must be up. With `--attachment`, a time within the
end-of-day fence of the attachment's writers is refused.

Entries are kept per machine in `cron/cron.toml`, beside the attachments
directory, with each entry's log (`<entry>.log`) and one outcome record per run
(`<entry>.outcomes.jsonl`). The crontab calls `uqs cron run ENTRY`, which adds
`--wait` to a backfill, logs the command's output and exits with its code. The
environment comes from `.env` and `.envrc`, which uqs loads itself.

`install` writes one managed block and leaves every other line as it was. Each
schedule line has an ownership comment above it, then a readable comment:

```
# >>> uqs cron (managed by `uqs cron install`; edit with `uqs cron`, not by hand) >>>
# uqs-cron-block: sha=3f1c0e9a42b7
# uqs-cron: entry=deals-daily managed-by=uqs schedules=/data/cron/cron.toml repo=/srv/uqf tz=UTC sha=9b2d4c1e07fa
# deals-daily: job backfill demo_deals_backfill --last 3d --source-version v1
#   10 6 * * 1-5 UTC (06:10, Mon-Fri, UTC); log: /data/cron/deals-daily.log
10 6 * * 1-5 cd /srv/uqf && UQS_CRON_ENTRY=deals-daily /srv/uqf/.venv/bin/uqs cron run deals-daily
# <<< uqs cron <<<
```

- The `uqs-cron:` line is shell-quoted `key=value` words: the entry, the
  schedules file, the checkout, the zone and a hash of the entry as installed.
  cron has no trailing comments, so ownership is recorded on comment lines.
- `UQS_CRON_ENTRY` is set on the command, after the `cd`, so uqs and every
  process it starts carry the entry's name.
- `uqs-cron-block` is a hash of the block as written. If the block was edited by
  hand, `install` refuses and shows it. Make the change with `uqs cron`, delete
  the block with `crontab -e`, then install again.
- `status` reports a block line that no entry owns (unknown), a line whose entry
  changed since it was installed (stale), an entry missing from the block, and
  any line outside the block that calls `uqs cron run` (an orphan, which uqs
  never changes).

Every entry has an explicit zone. cron runs lines in the host's zone, and not
every cron reads `CRON_TZ` (macOS's does not). In the host's zone the line is
written as given. In any other zone the line fires every hour at the entry's
minute, or every minute for a zone a fraction of an hour away, and
`uqs cron run` checks the five fields in the entry's zone. A firing that is not
due exits 0 before `.env` or the command line loads, and records nothing, so a
daylight-saving change needs no crontab rewrite.

Retries, alerting and dependencies between entries are left to an orchestrator.

## Other `torq.sh` verbs

`uqs exec -- <args>` passes straight through to `lib/torq/torq.sh`:

```bash
uqs exec -- debug rdb1     # run one process in the foreground
uqs exec -- top rdb1
```

`debug` starts a q process, so it is refused as `uqs start` is: an unknown name,
a q too old for the tree, a port another stack holds, and an attachment's
publisher that is not verified under its stack id. It names exactly one process.

## Installing

`./install.sh` checks the prerequisites, installs `uqs` as an editable tool and
checks that it runs. It is safe to run again, and `./install.sh --help` lists
its options. Source edits are live at once. Re-run the script after the package
is renamed or moved.

Tab completion, once per shell, from an interactive terminal:

```bash
uqs --install-completion      # bash, zsh, fish or PowerShell, detected
```
