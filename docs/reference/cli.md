# The uqs command line

This page lists the rules every `uqs` command follows, so you can predict a
command's name, flags, exit code and `--json` output. For one command's own
options, run `uqs <command> --help`. `-h` is `--help` everywhere.

## CLI conventions

- A group is a noun and a leaf is a verb on it:
  `uqs job backfill demo_deals_backfill`. The stack's own lifecycle (`start`,
  `stop`, `restart`, `status`, `logs`, and `clean` for what it wrote) is a
  top-level verb.
- The thing acted on is positional and a filter is an option:
  `uqs job gaps fx_markout --from 2026-09-13 --to 2026-09-14`.
- An option name has one meaning and one type of value on every command.
  `--port` is one process's port (`uqs query --port 6052 'count quote'`), the
  stack's base port is `--base-port`, and `-n` is `--dry-run`.
- A word in an option name means one thing: `target` is a deploy target and
  `runtime` a uqs runtime. `--release` is a release's name, never a path.
- Help shows a positional as an upper-case word from one vocabulary: `PROC`,
  `JOB`, `TARGET`, `TABLE`, `DATASET`, `SOURCE`, `ATTACHMENT`, `CONNECTOR`,
  `RUNTIME`, `RUN`, `SNAPSHOT_ID`.

Every verb comes from one fixed vocabulary:

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

Three verbs sit beside a near neighbour. `log` is a record's change history,
`logs` is processes' output and `history` is what was deployed. `revert` undoes
one recorded change and is itself recorded, where `rollback` returns a server to
its previous release. `unset` removes a setting so the layer beneath applies.

### Shared flags

- `--json` is on every command that reports. See [Machine-readable
  output](#machine-readable-output).
- `--dry-run`/`-n` is on every command that changes state (`uqs clean -n`). It
  checks everything, changes nothing, and ends with the line
  `dry run: nothing was changed. Run it again without --dry-run.`
- `--yes`/`-y` is on every command that asks (`uqs job remove NAME -y`).
- `--override NAME --reason TEXT` bypasses a check. NAME is one of the command's
  own, which `uqs override list` shows. The option repeats, one reason covers
  all of them, and each use is warned about and recorded.
- `--skip-<id>` is the same bypass under its own flag, on the commands that have
  one: `uqs job backfill --skip-eod-fence` is `--override eod-fence`.

A command that rewrites data in place (`data check`, `data alter`,
`data compress`, `data migrate`, `data replay`, `data seed`, `target adopt`) is
a dry run by default. It writes only with `--apply`, which shows the plan and
asks. `--yes`/`-y` answers that question.

Every deletion that cannot be undone asks first and takes `--yes`/`-y`: `clean`
(the whole data directory, the HDB with it, unless `--match` narrows it),
`deploy prune`, `job checkpoint remove`, `target remove` (which `target revert`
can undo), beside `job remove` and `job attachment remove`. On `uqs-mcp`, which
cannot ask, the tool for each is a dry run unless it is given `confirm=true`.

A prompt is `[y/N]`, so the default is no. An EOF or Ctrl-C is a no. With no
terminal to ask on, the command refuses at once and names `--yes`.

A backfill rehearsal (`--dry-run`, `--mode plan` or `--mode validate`) starts
its worker against the real data directory and coverage ledger. Run `uqs start`
first: the rehearsal is refused when that directory is not bootstrapped. The
worker's log and status file are the rehearsal's output.

### Exit codes

  | code | means                                                                               |
  | ---  | ---                                                                                 |
  | 0    | done, or nothing to show or do: no run recorded, no log written yet, no HDB         |
  | 1    | refused, an unknown process, target, run or job included, or a question answered no |
  | 2    | the command line is wrong: a missing argument, an unknown or contradictory option   |
  | 3    | done in part, with the rest to retry                                                |
  | 4    | stopped on request                                                                  |
  | 130  | Ctrl-C ended the stream of `job backfill --attachment --follow`                     |

Only `job backfill` exits 3, 4 or 130:

- 3 comes from `--attachment --wait` (or `--follow`). The rows are written and
  covered, but a reader has not reloaded them. Run the same command again to ask
  the readers again.
- 4 means a bounded run was asked to stop and did, between two windows. The
  windows it finished are kept, and the same command again does the rest.
- 130 leaves the run going. With `--stop-on-exit` the first Ctrl-C asks the run
  to stop and keeps following, so the command exits 4. A second Ctrl-C exits 130
  and leaves the request in place.

### Output in a pipe

Output into a pipe or a file is neither cut nor wrapped. A table is as wide as
its widest row and a line is never broken, so `uqs status | grep discovery1`
finds it and `ls "$(uqs config show rdb1 U)"` gets the whole path.

Every log line, refusals and warnings included, goes to stderr. Stdout holds
only what the command prints as its output. So `uqs process list > f` writes the
list alone, `$(uqs config show ...)` captures the config, and `2>/dev/null`
silences the log. `uqs logs` and `start -f` are the exception: the processes'
log lines are their output, on stdout.

### Time ranges

Every command that takes a time range takes the same four options:

- `--from A --to B` is `[A, B)`: `B` is excluded. A bound is a date or a
  timestamp, ISO-8601 or a q literal: `2026-09-13`, `2026-09-13T06:00`,
  `2026-09-13T06:00+02:00`, `2026.09.13D06:00`. It is UTC without an offset.
- `--date D` is one day, `--from D --to D+1`.
- `--last DURATION` is up to now, `--from now-DURATION --to now`. A DURATION is
  `300` (seconds), `30s`, `1.5m`, `2h`, `7d` or `500ms`.

The commands that act on whole HDB partitions (`data replay`, `data seed`,
`data compress`, `data migrate`) take whole days only. A `--from` or `--to` that
is not a UTC midnight is refused, and `--last` must be whole days. `--last 3d`
is the three days before today, so today's live partition is never in it.
`data replay` and `data migrate` take one day: `--date D`, `--from D --to D+1`,
or `--last 1d` (yesterday).

`config sources check` is the only command with a default range, `--last 1h`.
Check a source that is quiet now over a window that had data, with `--date D` or
`--from`/`--to`.

### Vocabulary

One concept has one name, one type and one unit on every command.

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

A flag that turns a default off is one side of a pair, such as
`--gateway/--no-gateway`, `--start/--no-start` or `--register/--no-register`.
`--every` re-reads every 2 seconds by default on `status` and `schema` alike.

## Machine-readable output

Every command that reports something takes `--json` and prints one JSON document
on stdout, holding the same data as the table it would print. Each command's
`--help` gives its document's shape, after the line
`Print one JSON document on stdout instead:`.

Logs go to stderr, and a refusal is the document
`{"ok": false, "error": "..."}`, so stdout always parses. The exit code is 1 for
a refusal and 2 for a usage error.

`--json` never asks a question. A command that would ask before changing
something (`data check`/`alter`/`compress`/`migrate`/`replay`/`seed --apply`,
`deploy prune`, `job attachment add`/`remove`) is refused under `--json` unless
you add `--yes`, even at a terminal. Nothing is changed.

These rules hold for every document:

- It is an object, never a bare list. A list command prints `{<kind>: [...]}`:
  `uqs process list --json` is `{processes: [...]}`. Its records have snake_case
  keys: the `Depends on` column is `depends_on`.
- Values are typed: a port is a number, a flag `true`/`false`, a set of names a
  list. Where the table shows words for a missing value, such as
  `(chosen at start)` or `no cap`, the document has `null` or `[]`.
- One concept has one key at every depth: `process`, `job`, `base_port`,
  `start_with_all`, and `range_from`/`range_to` for a time range.
- A verdict is the boolean `ok`, `false` exactly when the command exits
  non-zero. A row with a richer result says it under `outcome` beside `ok`.
- Data the document carries, such as a `query`'s result or `job preview`'s
  sampled rows, keeps the data's own column names.
- A time is ISO-8601 with an explicit offset and keeps every fractional digit.
- A log line is one record, `{time, level, message}`, plus what the command
  adds: `logs` adds `host`, `process`, `proctype` and `id`.
- Under `--json`, `deploy push`, `prune` and `rollback` print their dry-run note
  on stderr, after the document on stdout.

```bash
uqs job runs list --json | jq -r '.runs[] | select(.status == "failed") | .run_id'
uqs status --json | jq -r '.processes[] | select(.status == "down") | .process'
```
